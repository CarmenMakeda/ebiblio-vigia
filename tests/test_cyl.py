"""eBiblio Castilla y León: no tiene listas de «Novedades», así que se vigila el
catálogo de libros electrónicos ordenado por fecha de llegada."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from vigia import estado as est
from vigia.buzon import responde
from vigia.config import Avisos, Biblioteca, Comprobacion, Config, Consulta, ErrorDeConfiguracion, Intereses, carga
from vigia.fetch import NoEncontrado
from vigia.lista import Lista
from vigia.models import Estado
from vigia.motor import Vigia
from vigia.parse import lee_seccion

from simulador import ficha, libro, pagina_bundle

CYL = "https://castillayleon.ebiblio.es"
MAD = "https://madrid.ebiblio.es"
FIX = Path(__file__).parent / "fixtures"
RAIZ = Path(__file__).parent.parent


class Reloj:
    def __init__(self):
        self.t = datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def avanza(self, **kw):
        self.t += timedelta(**kw)


class CatalogoFalso:
    """Imita el catálogo de CyL: /resources?nature=ebook&sort_by=created_at_desc&page=N (40 por página)."""

    def __init__(self, base=CYL):
        self.base = base
        self.libros: list[dict] = []     # el más reciente primero
        self.urls: list[str] = []

    @property
    def peticiones(self) -> int:
        return len(self.urls)

    def get(self, url: str) -> str:
        self.urls.append(url)
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/resources" and q.get("nature") == "ebook":
            assert q.get("sort_by") == "created_at_desc"
            libros = self.libros
            if "audience" in q:
                libros = [l for l in libros if l.get("publico") == q["audience"]]
            return pagina_bundle("Catálogo", "x", libros, int(q.get("page", 1)), por_pagina=40)
        if u.path == "/resources" and ("q" in q or "author_keyword" in q):
            from vigia.parse import normaliza
            if "q" in q:
                palabras = normaliza(q["q"]).split()
                res = [l for l in self.libros if all(p in normaliza(l["titulo"] + " " + l["autor"]).split() for p in palabras)]
            else:
                res = [l for l in self.libros if normaliza(q["author_keyword"]) == normaliza(l["autor"])]
            return pagina_bundle("Catálogo", "x", res, 1)
        if u.path.startswith("/resources/"):
            l = next((l for l in self.libros if l["id"] == u.path.split("/")[2]), None)
            if l:
                return ficha(l)
        raise NoEncontrado(url)


def montar(paginas=3, n=200, infantil=True):
    web = CatalogoFalso()
    web.libros = [libro(i, f"Libro {i}", "Autora") for i in range(n, 0, -1)]
    bib = Biblioteca("castillayleon", "eBiblio Castilla y León", CYL,
                     consultas=[Consulta("Novedades libros electrónicos", "nature=ebook", paginas)])
    cfg = Config([bib], Intereses(autores=["Carmen Mola"], tipos=["libro"], infantil=infantil), Avisos(),
                 Comprobacion(pausa_segundos=0))
    reloj = Reloj()
    return web, Vigia(cfg, est.vacio(), cliente=web, ahora=reloj), reloj, bib


# ---------------------------------------------------------------- lector

def test_el_catalogo_real_de_cyl_se_lee_igual_que_madrid():
    pag = lee_seccion((FIX / "cyl_catalogo_ebook_p1.html").read_text(encoding="utf-8"), CYL)
    assert [l.titulo for l in pag.libros] == ["Salirse del tablero", "Un estallido", "Matar un reino"]
    assert [l.estado for l in pag.libros] == [Estado.DISPONIBLE, Estado.RESERVABLE, Estado.DISPONIBLE]
    salirse, estallido, matar = pag.libros
    assert salirse.ejemplares == 2 and matar.ejemplares == 1
    assert estallido.disponible_el == "2026-10-23T21:04"
    assert estallido.autores == ["Varios Autores", "Raúl Molina Gil"]
    assert salirse.url == CYL + "/resources/6abf92947ce307333db14d07"
    assert all(l.tipo == "libro" and l.formato == "EPUB" for l in pag.libros)
    assert pag.hay_siguiente


# ---------------------------------------------------------- configuración

def test_consulta_normaliza_el_filtro_y_arma_la_url():
    c = Consulta("x", "https://castillayleon.ebiblio.es/resources?nature=ebook&sort_by=title_sort&page=3")
    assert c.filtro == "nature=ebook" and c.clave == "q:nature=ebook"
    assert c.url(CYL) == CYL + "/resources?nature=ebook&sort_by=created_at_desc"
    assert c.url(CYL, 2) == CYL + "/resources?nature=ebook&sort_by=created_at_desc&page=2"
    with pytest.raises(ErrorDeConfiguracion):
        Consulta("x", "nature=ebook", paginas=99)


def test_config_del_repositorio_vigila_madrid_y_castilla_y_leon():
    cfg = carga(RAIZ / "config.yaml")
    ids = [b.id for b in cfg.activas]
    assert ids == ["madrid", "castillayleon"]
    assert cfg.intereses.infantil is False
    cyl = cfg.activas[1]
    assert not cyl.usa_portada and cyl.consultas[0].filtro == "nature=ebook"
    assert cfg.activas[0].usa_portada and not cfg.activas[0].consultas


# ----------------------------------------------------------------- motor

def test_primera_vez_silenciosa_lee_solo_la_ventana_y_no_la_portada():
    web, v, reloj, bib = montar(paginas=3)
    inf = v.comprueba(bib)
    assert inf.inicial and not inf.nuevos and inf.error is None
    assert inf.total_activos == 120                       # 3 páginas de 40
    assert inf.secciones == ["Novedades libros electrónicos"]
    assert not any(u.rstrip("/") == CYL for u in web.urls)   # no busca listas en la portada
    assert not any("page=4" in u for u in web.urls)


def test_tanda_nueva_se_avisa_leyendo_dos_paginas():
    web, v, reloj, bib = montar()
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.urls.clear()
    nuevos = [libro(900, "Los Huérfanos", "Carmen Mola"), libro(901, "Otra novela", "Alguien")]
    web.libros[:0] = nuevos
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Los Huérfanos", "Otra novela"]
    assert inf.nuevos[0].motivos == ["Carmen Mola"]
    assert inf.nuevos[0].seccion == "Novedades libros electrónicos"
    assert len(web.urls) == 2


def test_tanda_grande_sigue_paginando_hasta_algo_conocido():
    web, v, reloj, bib = montar(paginas=5)
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.libros[:0] = [libro(1000 + i, f"Nuevo {i}", "X") for i in range(100)]
    inf = v.comprueba(bib)
    assert len(inf.nuevos) == 100


def test_lectura_completa_retira_lo_que_sale_de_la_ventana():
    web, v, reloj, bib = montar(paginas=3)
    v.comprueba(bib)
    viejo = web.libros[-81]["id"]                     # el último de la página 3
    assert v.estado["bibliotecas"]["castillayleon"]["libros"][viejo]["activo"]
    web.libros[:0] = [libro(2000 + i, f"Llega {i}", "Y") for i in range(10)]
    reloj.avanza(hours=21)                             # toca lectura completa
    inf = v.comprueba(bib)
    assert inf.completa and len(inf.nuevos) == 10
    assert not v.estado["bibliotecas"]["castillayleon"]["libros"][viejo]["activo"]
    assert inf.total_activos == 120


def test_libro_que_interesa_y_se_libera_avisa():
    web, v, reloj, bib = montar()
    web.libros[0] = libro(777, "La Bestia", "Carmen Mola", "sin_reservas")
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.libros[0]["estado"] = "disponible"
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.liberados] == ["La Bestia"]


# ------------------------------------------------- lista con dos bibliotecas

class DosWebs:
    def __init__(self, *webs):
        self.webs = {w.base: w for w in webs}

    def get(self, url):
        for base, w in self.webs.items():
            if url.startswith(base):
                return w.get(url)
        raise NoEncontrado(url)


def test_un_titulo_se_sigue_en_las_dos_bibliotecas_y_se_dice_en_cual():
    mad, cyl = CatalogoFalso(MAD), CatalogoFalso(CYL)
    mad.libros = [libro(1, "Los huérfanos", "Carmen Mola", "sin_reservas")]
    cyl.libros = [libro(2, "Los huérfanos", "Carmen Mola", "disponible")]
    cfg = Config(
        [Biblioteca("madrid", "eBiblio Madrid", MAD),
         Biblioteca("castillayleon", "eBiblio Castilla y León", CYL, consultas=[Consulta("N", "nature=ebook")])],
        Intereses(tipos=["libro"]), Avisos(), Comprobacion(pausa_segundos=0))
    lista = Lista(cfg, est.vacio(), DosWebs(mad, cyl), ahora=Reloj())
    r = responde(lista, "Los huérfanos de Carmen Mola")
    assert len(lista.libros) == 2
    assert "eBiblio Madrid" in r and "eBiblio Castilla y León" in r
    assert "castillayleon:" + cyl.libros[0]["id"] in lista.libros
    assert "eBiblio Castilla y León" in responde(lista, "/lista")


# ------------------------------------------------------------ sin infantil

def _infantil(n, titulo, autor="Autora infantil", estado="sin_reservas"):
    l = libro(n, titulo, autor, estado)
    l["publico"] = "youth"
    return l


def test_sin_infantil_no_avisa_de_libros_infantiles_ni_cuando_se_liberan():
    web, v, reloj, bib = montar(infantil=False)
    web.libros[5] = _infantil(600, "Mi gato")
    inf = v.comprueba(bib)
    assert inf.error is None
    assert any("audience=youth" in u for u in web.urls)
    libros = v.estado["bibliotecas"]["castillayleon"]["libros"]
    assert libros[web.libros[5]["id"]]["excluido"]

    reloj.avanza(hours=1)
    web.libros[:0] = [_infantil(601, "Mi perro", "Carmen Mola"), libro(602, "Novela adulta", "Alguien")]
    web.libros[7]["estado"] = "disponible"          # «Mi gato» queda libre
    web.urls.clear()
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Novela adulta"]
    assert not inf.liberados
    assert sum("audience=youth" in u for u in web.urls) == 1   # una sola página: ya conocía los anteriores


def test_con_infantil_activado_no_se_consulta_nada_mas():
    web, v, reloj, bib = montar(infantil=True)
    web.libros[0] = _infantil(600, "Mi gato")
    v.comprueba(bib)
    reloj.avanza(hours=1)
    web.libros[:0] = [_infantil(601, "Mi perro")]
    inf = v.comprueba(bib)
    assert [e.libro["titulo"] for e in inf.nuevos] == ["Mi perro"]
    assert not any("audience" in u for u in web.urls)
