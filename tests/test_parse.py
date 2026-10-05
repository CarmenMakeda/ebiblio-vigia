from pathlib import Path

from vigia.models import Estado, fecha_de_objectid
from vigia.parse import fecha_es, lee_seccion, lee_secciones_portada, normaliza

FIX = Path(__file__).parent / "fixtures"
BASE = "https://madrid.ebiblio.es"


def _lee(nombre):
    return (FIX / nombre).read_text(encoding="utf-8")


def test_portada_descubre_secciones_de_novedades():
    secciones = lee_secciones_portada(_lee("home_madrid.html"))
    por_nombre = {s.nombre: s for s in secciones}
    assert por_nombre["Novedades ficción"].bundle == "6972422ff7b4bb0e618ee5a5"
    assert por_nombre["Novedades ficción"].total == 427
    assert por_nombre["Novedades no ficción"].total == 322
    assert "Nuevos audiolibros" in por_nombre
    assert len(secciones) > 10


def test_seccion_lee_libros_y_estados():
    pag = lee_seccion(_lee("bundle_ficcion_p1.html"), BASE)
    assert pag.titulo == "Novedades ficción"
    assert pag.hay_siguiente is True
    libros = {l.titulo: l for l in pag.libros}
    assert len(libros) == 5

    h = libros["Los Huérfanos"]
    assert h.id == "6ab61faa015acb463909ac01"
    assert h.autores == ["Carmen Mola"]
    assert h.url == "https://madrid.ebiblio.es/resources/6ab61faa015acb463909ac01"
    assert h.portada.startswith("https://imagedelivery.net/")
    assert h.estado is Estado.SIN_RESERVAS
    assert h.formato == "EPUB" and h.tipo == "libro"
    assert h.sinopsis.startswith("Hay heridas")

    n = libros["Una noche de 1947"]
    assert n.estado is Estado.RESERVABLE
    assert n.disponible_el == "2026-12-07T23:16"

    b = libros["El botín"]
    assert b.estado is Estado.DISPONIBLE and b.ejemplares == 1

    c = libros["Mi nombre es Celestina"]
    assert c.estado is Estado.DISPONIBLE and c.ejemplares == 6

    v = libros["Las crines"]
    assert v.estado is Estado.DISPONIBLE and v.ejemplares is None
    assert v.autores == ["Autora Uno", "Autor Dos"]


def test_audiolibros_y_ultima_pagina():
    pag = lee_seccion(_lee("bundle_audio_last.html"), BASE)
    assert pag.hay_siguiente is False
    e, i = pag.libros
    assert e.tipo == "audiolibro" and e.formato == "AUDIO"
    assert e.estado is Estado.RESERVABLE and e.disponible_el == "2026-10-23T06:54"
    assert i.estado is Estado.SIN_RESERVAS


def test_pagina_vacia_no_rompe():
    pag = lee_seccion("<html><body><main></main></body></html>", BASE)
    assert pag.libros == [] and pag.hay_siguiente is False


def test_utilidades():
    assert fecha_es("Disponible el 23 oct 2026 a las 00:19") == "2026-10-23T00:19"
    assert fecha_es("1 sept 2026") == "2026-09-01T00:00"
    assert fecha_es("sin fecha") is None
    assert normaliza("Ángeles González-Sinde") == "angeles gonzalez sinde"
    assert fecha_de_objectid("6ab61faa015acb463909ac01").date().isoformat() == "2026-09-25"
    assert fecha_de_objectid("xx") is None


def test_config_del_repositorio_es_valida():
    from vigia.config import carga
    c = carga(Path(__file__).parent.parent / "config.yaml")
    assert c.activas  # al menos una biblioteca activa (si no, carga() ya habría fallado)
