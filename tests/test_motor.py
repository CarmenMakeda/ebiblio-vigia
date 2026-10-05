from datetime import datetime, timedelta, timezone

import pytest

from vigia import estado as est
from vigia.config import Avisos, Biblioteca, Comprobacion, Config, Intereses
from vigia.motor import Vigia

from simulador import BASE, WebFalsa, libro

FIC = "6972422ff7b4bb0e618ee5a5"
NOF = "69830e032580d662bfd9a2a0"


class Reloj:
    def __init__(self):
        self.t = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def avanza(self, **kw):
        self.t += timedelta(**kw)


def montar(intereses=None, avisos=None, secciones=None):
    web = WebFalsa()
    web.secciones[FIC] = ("Novedades ficción", [libro(i, f"Ficción {i}", "Autora Genérica") for i in range(1, 80)])
    web.secciones[NOF] = ("Novedades no ficción", [libro(500 + i, f"Ensayo {i}", "Ensayista") for i in range(1, 10)])
    web.extra_portada = [("aaaaaaaaaaaaaaaaaaaaaaaa", "Recomendados")]
    cfg = Config(
        bibliotecas=[Biblioteca("madrid", "eBiblio Madrid", BASE, secciones=secciones or [])],
        intereses=intereses or Intereses(autores=["Carmen Mola"], palabras=["Fjällbacka"]),
        avisos=avisos or Avisos(),
        comprobacion=Comprobacion(pausa_segundos=0),
    )
    reloj = Reloj()
    v = Vigia(cfg, est.vacio(), cliente=web, ahora=reloj)
    return web, v, reloj, cfg.bibliotecas[0]


def test_primera_vez_es_silenciosa_y_lee_todo():
    web, v, reloj, bib = montar()
    inf = v.comprueba(bib)
    assert inf.inicial and not inf.nuevos and inf.error is None
    assert inf.total_activos == 79 + 9
    assert set(inf.secciones) == {"Novedades ficción", "Novedades no ficción"}  # "Recomendados" no
    assert any("page=3" in u for u in web.urls)  # leyó todas las páginas


def test_detecta_novedades_e_intereses_leyendo_poco():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.urls.clear()
    nuevos = [libro(900, "Los Huérfanos", "Carmen Mola"), libro(901, "Otra novela", "Alguien")]
    web.secciones[FIC][1][:0] = nuevos
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Los Huérfanos", "Otra novela"]
    assert inf.nuevos[0].motivos == ["Carmen Mola"] and not inf.nuevos[1].motivos
    assert not inf.inicial
    # comprobación ligera: sin portada, 2 páginas de ficción + 1 de no ficción
    assert len(web.urls) == 3
    # la segunda vez ya no son nuevos
    reloj.avanza(hours=1)
    assert v.comprueba(bib).nuevos == []


def test_tanda_grande_sigue_paginando():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.secciones[FIC][1][:0] = [libro(1000 + i, f"Tanda {i}", "X") for i in range(100)]
    inf = v.comprueba(bib)
    assert len(inf.nuevos) == 100


def test_aviso_cuando_un_libro_que_interesa_se_libera():
    web, v, reloj, bib = montar()
    web.secciones[FIC][1].insert(0, libro(900, "Los Huérfanos", "Carmen Mola", "sin_reservas"))
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.secciones[FIC][1][0]["estado"] = "reservable"
    inf = v.comprueba(bib)
    assert [(e.libro["titulo"], e.anterior, e.libro["estado"]) for e in inf.liberados] == [
        ("Los Huérfanos", "sin_reservas", "reservable")
    ]
    reloj.avanza(hours=1)
    web.secciones[FIC][1][0]["estado"] = "disponible"
    assert v.comprueba(bib).liberados[0].libro["estado"] == "disponible"
    # va y vuelve en poco tiempo: no se repite el aviso
    reloj.avanza(hours=1)
    web.secciones[FIC][1][0]["estado"] = "sin_reservas"
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.secciones[FIC][1][0]["estado"] = "disponible"
    assert v.comprueba(bib).liberados == []
    # un libro sin interés que se libera no avisa (modo por defecto)
    reloj.avanza(hours=1)
    web.secciones[FIC][1][1]["estado"] = "disponible"
    assert v.comprueba(bib).liberados == []


def test_modo_solo_intereses_y_exclusiones():
    web, v, reloj, bib = montar(
        intereses=Intereses(autores=["mola"], excluir=["infantil"]),
        avisos=Avisos(novedades="intereses"),
    )
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.secciones[FIC][1][:0] = [
        libro(900, "Los Huérfanos", "Carmen Mola"),
        libro(901, "Cuento infantil", "Otra"),
        libro(902, "Novela cualquiera", "Otro"),
    ]
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Los Huérfanos"]
    assert v.estado["bibliotecas"]["madrid"]["libros"][web.secciones[FIC][1][1]["id"]]["excluido"] is True


def test_alerta_tras_fallos_seguidos_y_aviso_de_recuperacion():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    web.caida = True
    alertas = []
    for _ in range(4):
        reloj.avanza(hours=1)
        inf = v.comprueba(bib)
        assert inf.error
        alertas.append(inf.alerta)
    assert alertas[0] is None and alertas[1] is None and alertas[2] and alertas[3] is None
    web.caida = False
    reloj.avanza(hours=1)
    inf = v.comprueba(bib)
    assert inf.recuperada and inf.error is None


def test_si_cambian_la_lista_de_novedades_la_encuentra_sola():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    reloj.avanza(hours=1)
    # La biblioteca sustituye la lista "Novedades ficción" por otra nueva (p. ej. al cambiar de año)
    nombre, libros_viejos = web.secciones.pop(FIC)
    recien = libro(950, "Recién llegado", "Nueva", fecha=reloj.t - timedelta(days=1))
    web.secciones["bbbbbbbbbbbbbbbbbbbbbbbb"] = (nombre, [recien] + libros_viejos[:10])
    inf = v.comprueba(bib)
    assert inf.error is None
    # Sólo avisa del recién llegado, no de los 10 que ya conocía ni de antiguos
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Recién llegado"]


def test_sincronizacion_completa_retira_libros_que_salen_de_la_lista():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    quitado = web.secciones[NOF][1].pop()
    reloj.avanza(hours=21)
    inf = v.comprueba(bib)
    assert inf.completa
    rec = v.estado["bibliotecas"]["madrid"]["libros"][quitado["id"]]
    assert rec["activo"] is False


def test_seccion_configurada_que_no_existe_genera_advertencia():
    web, v, reloj, bib = montar(secciones=["Novedades ficción", "Sección inventada"])
    inf = v.comprueba(bib)
    assert inf.secciones == ["Novedades ficción"]
    assert any("Sección inventada" in a for a in inf.advertencias)


def test_estado_se_guarda_y_recarga(tmp_path):
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    ruta = tmp_path / "estado.json"
    est.guarda(v.estado, ruta)
    assert est.carga(ruta)["bibliotecas"]["madrid"]["inicializada"] is True


def test_dejar_de_vigilar_una_seccion_se_aplica_en_la_siguiente_vuelta():
    web, v, reloj, bib = montar(secciones=["Novedades ficción", "Novedades no ficción"])
    v.comprueba(bib)
    assert v.estado["bibliotecas"]["madrid"]["inicializada"]
    bib.secciones = ["Novedades ficción"]          # la lectora quita una sección de config.yaml
    reloj.avanza(hours=1)
    web.urls.clear()
    inf = v.comprueba(bib)
    assert inf.secciones == ["Novedades ficción"]
    assert not any(NOF in u for u in web.urls)
    assert inf.total_activos == 79                  # los 9 ensayos ya no cuentan
    bib.secciones = ["Novedades ficción", "Novedades no ficción"]   # y si la vuelve a poner…
    reloj.avanza(hours=1)
    inf = v.comprueba(bib)
    assert set(inf.secciones) == {"Novedades ficción", "Novedades no ficción"}
    assert inf.nuevos == []                          # …no avisa como novedades de libros ya conocidos
