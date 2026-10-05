from unittest import mock

from vigia import estado as est
from vigia.buzon import atiende, responde, texto_lista
from vigia.lista import Lista
from vigia.notify.mensajes import Foto, redacta_lista
from vigia.notify.telegram import Telegram

from simulador import libro
from test_motor import FIC, montar


def preparar():
    web, v, reloj, bib = montar()
    deseado = libro(700, "La asistenta", "Freida McFadden", "sin_reservas")
    otro = libro(701, "La boda de la asistenta", "Freida McFadden", "reservable")
    web.catalogo = [deseado, otro]
    lista = Lista(v.cfg, v.estado, web, ahora=reloj)
    return web, v, reloj, bib, lista, deseado


def test_titulo_claro_se_sigue_directamente_y_avisa_al_liberarse():
    web, v, reloj, bib, lista, deseado = preparar()
    r = responde(lista, "La asistenta")
    assert "Apuntado en tu lista" in r and "no admite reservas" in r
    assert list(lista.libros) == [f"madrid:{deseado['id']}"]   # no se apunta «La boda de la asistenta»
    assert "Ya lo seguía" in responde(lista, "la asistenta")
    assert lista.comprueba() == []
    reloj.avanza(hours=1)
    deseado["estado"] = "reservable"
    assert [(e.tipo, e.anterior) for e in lista.comprueba()] == [("reservable", "sin_reservas")]
    reloj.avanza(hours=1)
    deseado["estado"] = "disponible"
    ev = lista.comprueba()
    pieza = redacta_lista(ev, v.cfg)[0]
    assert isinstance(pieza, Foto) and "¡Está libre!" in pieza.texto and f"/quitar_{deseado['id']}" in pieza.texto
    # vaivén rápido: no repite
    reloj.avanza(hours=1); deseado["estado"] = "sin_reservas"; lista.comprueba()
    reloj.avanza(hours=1); deseado["estado"] = "disponible"
    assert lista.comprueba() == []
    assert "Ya no sigo" in responde(lista, f"/quitar_{deseado['id']}")
    assert lista.libros == {}


def test_varias_ediciones_del_mismo_autor_se_siguen_todas():
    web, v, reloj, bib, lista, deseado = preparar()
    audio = libro(703, "La asistenta", "Freida McFadden", "disponible")
    audio["tipo"] = "audiolibro"
    web.catalogo.append(audio)
    r = responde(lista, "La asistenta")
    assert "en sus 2 ediciones" in r and "Corre a cogerla" in r
    assert len(lista.libros) == 2


def test_mismo_titulo_de_autores_distintos_pregunta_y_el_autor_desempata():
    web, v, reloj, bib, lista, deseado = preparar()
    web.catalogo += [libro(710, "Los Huérfanos", "Carmen Mola"), libro(711, "Los huérfanos", "Jorge Carrión")]
    r = responde(lista, "Los huérfanos")
    assert "varios libros" in r and r.count("/s_") == 2 and lista.libros == {}
    r = responde(lista, "/s_" + web.catalogo[-2]["id"])
    assert "Apuntado" in r and "Carmen Mola" in r
    lista.libros.clear()
    assert "Apuntado" in responde(lista, "los huerfanos carmen mola")
    assert "Carmen Mola" in texto_lista(lista)


def test_titulo_con_parentesis_y_tildes():
    web, v, reloj, bib, lista, deseado = preparar()
    web.catalogo.append(libro(720, "El llanto de los muertos (Los crímenes de Fjällbacka)", "Camilla Läckberg"))
    assert "Apuntado" in responde(lista, "el llanto de los muertos")


def test_titulo_que_aun_no_esta_se_busca_cada_dia_y_se_sigue_al_llegar():
    web, v, reloj, bib, lista, deseado = preparar()
    r = responde(lista, "El libro que aún no ha llegado")
    assert "aún no está en eBiblio" in r and "/olvidar_" in r
    assert "Títulos que busco cada día" in texto_lista(lista)
    assert lista.revisa_busquedas() == []            # todavía no toca
    nuevo = libro(702, "El libro que aún no ha llegado", "Autora", "disponible")
    web.catalogo.append(nuevo)
    reloj.avanza(hours=21)
    ev = lista.revisa_busquedas()
    assert ev[0].tipo == "encontrado" and ev[0].seguidos
    texto = redacta_lista(ev, v.cfg)[0].texto
    assert "Ya lo sigo para ti" in texto and "Corre a cogerlo" in texto
    assert lista.busquedas == {} and f"madrid:{nuevo['id']}" in lista.libros


def test_busqueda_pendiente_que_llega_con_dudas_pregunta():
    web, v, reloj, bib, lista, deseado = preparar()
    responde(lista, "Nocturno")
    web.catalogo += [libro(730, "Nocturno", "Autora A"), libro(731, "Nocturno", "Autor B")]
    reloj.avanza(hours=21)
    ev = lista.revisa_busquedas()
    assert ev[0].opciones and not ev[0].seguidos
    assert redacta_lista(ev, v.cfg)[0].texto.count("/s_") == 2


def test_olvidar_busqueda_y_enlaces_no_hacen_falta():
    web, v, reloj, bib, lista, deseado = preparar()
    r = responde(lista, "Un título inventado")
    clave = r.split("/olvidar_")[1].strip()
    assert "Dejo de buscar" in responde(lista, f"/olvidar_{clave}")
    assert "No hace falta el enlace" in responde(lista, "https://madrid.ebiblio.es/resources/6ab61faa015acb463909ac01")


def test_libro_retirado_del_catalogo():
    web, v, reloj, bib, lista, deseado = preparar()
    responde(lista, "La asistenta")
    web.catalogo.clear()
    ev = lista.comprueba()
    assert ev[0].tipo == "retirado" and lista.libros == {}


def test_libro_de_la_lista_en_novedades_no_se_avisa_dos_veces():
    web, v, reloj, bib, lista, deseado = preparar()
    v.comprueba(bib)
    novedad = web.secciones[FIC][1][0]
    responde(lista, novedad["titulo"])
    assert len(lista.libros) == 1
    v.seguidos = lista.claves_seguidas
    v.cfg.avisos.liberados = "todas"
    reloj.avanza(hours=1)
    novedad["estado"] = "disponible"
    v.vistos.clear()
    peticiones = web.peticiones
    inf = v.comprueba(bib)
    assert inf.liberados == []                      # el motor de novedades no lo avisa…
    ev = lista.comprueba(v.vistos)
    assert [e.tipo for e in ev] == ["disponible"]   # …lo avisa la lista, una sola vez
    assert web.peticiones - peticiones == 3         # y sin pedir su ficha otra vez


def test_ordenes():
    web, v, reloj, bib, lista, deseado = preparar()
    assert "lista «quiero leer» está vacía" in responde(lista, "/lista")
    assert "Escríbeme el <b>título</b>" in responde(lista, "/start")
    assert "No conozco esa orden" in responde(lista, "/xyz")


def test_estado_persistente(tmp_path):
    web, v, reloj, bib, lista, deseado = preparar()
    responde(lista, "La asistenta")
    responde(lista, "Otro que no existe")
    ruta = tmp_path / "e.json"
    est.guarda(v.estado, ruta)
    otra = Lista(v.cfg, est.carga(ruta), web, ahora=reloj)
    assert len(otra.libros) == 1 and len(otra.busquedas) == 1


def test_buzon_solo_atiende_a_la_duena_y_no_repite():
    web, v, reloj, bib, lista, deseado = preparar()
    t = Telegram("TOKEN", "252728293", pausa=0)
    enviados, llamadas = [], []
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 252728293}, "text": "La asistenta"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/lista"}},  # un desconocido
        {"update_id": 12, "message": {"chat": {"id": 252728293}, "text": "/lista"}},
    ]

    def falso(metodo, datos):
        llamadas.append((metodo, datos.get("offset")))
        if metodo == "getUpdates":
            return [u for u in updates if u["update_id"] >= datos["offset"]]
        if metodo == "sendMessage":
            enviados.append(datos["text"])
        return {}

    with mock.patch.object(t, "_llama", side_effect=falso):
        assert atiende(t, "252728293", lista, v.estado) == 2
        assert v.estado["telegram"]["offset"] == 13
        assert atiende(t, "252728293", lista, v.estado) == 0   # nada nuevo
    assert len(enviados) == 2 and "Apuntado" in enviados[0] and "Sigo 1 libro" in enviados[1]
    assert ("setMyCommands", None) in llamadas


def test_audiolibro_con_narrador_cuenta_como_el_mismo_libro():
    from vigia.config import Biblioteca
    from vigia.models import Libro
    web, v, reloj, bib, lista, deseado = preparar()
    b = Biblioteca("madrid", "eBiblio Madrid", "https://madrid.ebiblio.es")
    epub = Libro("a" * 24, "La asistenta", ["Freida McFadden"], "u")
    audio = Libro("b" * 24, "La asistenta", ["Freida McFadden", "Ana Narradora"], "u", tipo="audiolibro")
    assert len(lista.claros("La asistenta", [(b, epub), (b, audio)])) == 2


def test_busquedas_reales_de_ebiblio():
    """Resultados reales de madrid.ebiblio.es: decide cuándo seguir y cuándo preguntar."""
    from pathlib import Path
    from vigia.buzon import responde
    fix = Path(__file__).parent / "fixtures"
    paginas = {"La asistenta": (fix / "busqueda_asistenta.html").read_text(encoding="utf-8"),
               "Los huérfanos": (fix / "busqueda_huerfanos.html").read_text(encoding="utf-8")}

    class Cliente:
        peticiones = 0
        def get(self, url):
            from urllib.parse import unquote_plus
            if "?q=" in url:
                return paginas[unquote_plus(url.split("q=", 1)[1])]
            rid = url.rsplit("/", 1)[1]
            return f'<h1>Libro {rid}</h1><section class="transactions"><div class="availability">En este momento no hay reservas libres</div></section>'

    web, v, reloj, bib, _, _ = preparar()
    lista = Lista(v.cfg, v.estado, Cliente(), ahora=reloj)
    r = responde(lista, "La asistenta")
    assert "en sus 3 ediciones" in r and len(lista.libros) == 3
    r = responde(lista, "Los huérfanos")
    assert "Hay varios libros titulados" in r and r.count("/s_") == 2 and "Führer" not in r


def test_sin_audiolibros_la_lista_sigue_solo_el_epub():
    from pathlib import Path
    fix = Path(__file__).parent / "fixtures"
    pagina = (fix / "busqueda_asistenta.html").read_text(encoding="utf-8")

    class Cliente:
        peticiones = 0
        def get(self, url):
            if "?q=" in url:
                return pagina
            rid = url.rsplit("/", 1)[1]
            return f'<h1>Libro {rid}</h1><section class="transactions"><h2 class="transaction__title">EPUB</h2><div class="availability">En este momento no hay reservas libres</div></section>'

    web, v, reloj, bib, _, _ = preparar()
    v.cfg.intereses.tipos = ["libro"]
    lista = Lista(v.cfg, v.estado, Cliente(), ahora=reloj)
    r = responde(lista, "La asistenta")
    assert "Apuntado en tu lista" in r and len(lista.libros) == 1


class ClienteReal:
    """Sirve búsquedas reales guardadas de madrid.ebiblio.es."""
    def __init__(self, mapa):
        from pathlib import Path
        fix = Path(__file__).parent / "fixtures"
        self.mapa = {k: (fix / v).read_text(encoding="utf-8") for k, v in mapa.items()}
        self.peticiones = 0
        self.urls = []

    def get(self, url):
        from urllib.parse import unquote_plus
        self.peticiones += 1
        self.urls.append(unquote_plus(url))
        if "/resources?" in url:
            clave = unquote_plus(url.split("?", 1)[1])
            return self.mapa.get(clave, "<html><body><main></main></body></html>")
        rid = url.rsplit("/", 1)[1]
        return f'<h1>Libro {rid}</h1><section class="transactions"><h2 class="transaction__title">EPUB</h2><div class="availability">En este momento no hay reservas libres</div></section>'


FANTASMA = {
    "q=Fantasma de nerea Pérez de las heras": "busqueda_fantasma_frase.html",
    "q=Fantasma": "busqueda_fantasma.html",
    "author_keyword=nerea Pérez de las heras": "autor_nerea.html",
}


def test_titulo_y_autor_juntos_se_separan_y_detecta_que_solo_hay_audiolibro():
    web, v, reloj, bib, _, _ = preparar()
    v.cfg.intereses.tipos = ["libro"]
    c = ClienteReal(FANTASMA)
    lista = Lista(v.cfg, v.estado, c, ahora=reloj)
    r = responde(lista, "Fantasma de nerea Pérez de las heras")
    assert "sólo está en eBiblio como <b>audiolibro</b>" in r and "Nerea Pérez de las Heras" in r
    assert "Pieces of Her" not in r and lista.libros == {} and len(lista.busquedas) == 1
    assert c.peticiones == 2   # la frase entera (falla) y luego sólo el título


def test_con_audiolibros_activados_lo_sigue():
    web, v, reloj, bib, _, _ = preparar()
    v.cfg.intereses.tipos = ["libro", "audiolibro"]
    lista = Lista(v.cfg, v.estado, ClienteReal(FANTASMA), ahora=reloj)
    assert "Apuntado en tu lista" in responde(lista, "Fantasma de nerea Pérez de las heras")


def test_errata_ofrece_parecidos_y_al_elegir_deja_de_buscar():
    web, v, reloj, bib, _, _ = preparar()
    v.cfg.intereses.tipos = ["libro"]
    lista = Lista(v.cfg, v.estado, ClienteReal({"q=La asistena": "busqueda_asistenta.html"}), ahora=reloj)
    r = responde(lista, "La asistena")
    assert "¿Es alguno de estos?" in r and "La asistenta" in r and len(lista.busquedas) == 1
    rid = r.split("/s_")[1].split()[0]
    assert "Apuntado" in responde(lista, f"/s_{rid}")
    assert lista.busquedas == {}


def test_saludos_no_se_buscan():
    web, v, reloj, bib, lista, _ = preparar()
    for t in ("hola", "Hola!", "buenas tardes", "Gracias"):
        assert "¡Hola!" in responde(lista, t)
    assert lista.libros == {} and lista.busquedas == {} and web.peticiones == 0


def test_consultas_que_se_prueban():
    web, v, reloj, bib, lista, _ = preparar()
    assert lista._consultas("Fantasma de nerea Pérez de las heras")[:3] == [
        ("q", "Fantasma de nerea Pérez de las heras"), ("q", "Fantasma"), ("autor", "nerea Pérez de las heras")]
    assert ("q", "Una noche de 1947") in lista._consultas("Una noche de 1947 - Ángeles González-Sinde")
    assert lista._consultas("La asistenta") == [("q", "La asistenta")]


def test_mismo_titulo_y_el_otro_solo_en_audio_pregunta_en_vez_de_elegir():
    """Caso real: «Fantasma» de Jo Nesbø (EPUB) y de Nerea Pérez de las Heras (sólo audio)."""
    web, v, reloj, bib, _, _ = preparar()
    v.cfg.intereses.tipos = ["libro"]
    lista = Lista(v.cfg, v.estado, ClienteReal(FANTASMA), ahora=reloj)
    r = responde(lista, "Fantasma")
    assert "Hay varios libros titulados «Fantasma»" in r and "Jo Nesbo" in r
    assert "Nerea Pérez de las Heras, pero sólo en audiolibro" in r
    assert lista.libros == {}                       # no elige por ella
    assert r.count("/s_") == 1
    assert "Apuntado" in responde(lista, "Fantasma Jo Nesbo")   # con el autor, sin dudas
