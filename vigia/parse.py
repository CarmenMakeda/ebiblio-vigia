"""Lectura del HTML de eBiblio (plataforma De Marque / Cantook Station).

Todas las bibliotecas eBiblio autonómicas usan la misma plataforma, así que este
lector sirve para Madrid, Castilla y León, Andalucía, etc.

Se apoya primero en la estructura (clases CSS de botones) y después en el texto,
para seguir funcionando si cambia uno de los dos.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .models import Estado, Libro, PaginaSeccion, Seccion


class ErrorDeLectura(Exception):
    """La página no tiene la forma esperada: probablemente la web ha cambiado."""


_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "sep": 9, "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}
_RE_FECHA = re.compile(
    r"(\d{1,2})\s+([a-záéíóú]+)\.?\s+(\d{4})(?:\s+a\s+las\s+(\d{1,2}):(\d{2}))?", re.I
)
_RE_ID = re.compile(r"/resources/([0-9a-f]{24})")
_RE_BUNDLE = re.compile(r"/bundles/([0-9a-f]{24})")


def _limpio(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def normaliza(texto: str) -> str:
    """minúsculas, sin tildes ni signos: para comparar textos de forma tolerante."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def fecha_es(texto: str) -> str | None:
    """'7 dic 2026 a las 23:16' -> '2026-12-07T23:16' (hora local de la web)."""
    m = _RE_FECHA.search(texto or "")
    if not m:
        return None
    dia, mes_txt, anio, hh, mm = m.groups()
    mes = _MESES.get(normaliza(mes_txt)[:4]) or _MESES.get(normaliza(mes_txt)[:3])
    if not mes:
        return None
    try:
        dt = datetime(int(anio), mes, int(dia), int(hh or 0), int(mm or 0))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M")


def _estado(bloque: Tag | None) -> tuple[Estado, int | None, str | None, str]:
    if bloque is None:
        return Estado.DESCONOCIDO, None, None, ""
    texto = _limpio(bloque.get_text(" "))
    norm = normaliza(texto)

    ejemplares = None
    cuenta = bloque.select_one(".availability .count")
    if cuenta and cuenta.get_text(strip=True).isdigit():
        ejemplares = int(cuenta.get_text(strip=True))
    else:
        m = re.search(r"(\d+)\s+ejemplares?\s+disponibles?", norm)
        if m:
            ejemplares = int(m.group(1))

    if bloque.select_one(".button-borrow") or (
        re.search(r"ejemplares? disponibles?\b", norm) and "proximo" not in norm
    ):
        return Estado.DISPONIBLE, ejemplares, None, texto
    if bloque.select_one(".button-booking") or "proximo ejemplar disponible" in norm or "disponible el" in norm:
        fecha_tag = bloque.select_one(".next-availability .date")
        fecha = fecha_es(fecha_tag.get_text(" ") if fecha_tag else texto)
        return Estado.RESERVABLE, None, fecha, texto
    if "no hay reservas libres" in norm:
        return Estado.SIN_RESERVAS, None, None, texto
    if "acceso en linea" in norm or "ver el video" in norm:
        return Estado.EN_LINEA, None, None, texto
    return Estado.DESCONOCIDO, None, None, texto


def _formato(art: Tag) -> str:
    h4 = art.select_one(".transaction__title")
    return _limpio(h4.get_text(" ")).upper() if h4 else ""


def _tipo(art: Tag, formato: str) -> str:
    itemtype = (art.get("itemtype") or "").lower()
    if "audiobook" in itemtype or formato.startswith("AUDIO"):
        return "audiolibro"
    if "book" in itemtype:
        return "libro"
    return "otro"


def lee_seccion(html: str, base_url: str) -> PaginaSeccion:
    """Lee una página de un bundle (/bundles/<id>?page=N) en vista de lista."""
    sopa = BeautifulSoup(html, "lxml")
    pagina = PaginaSeccion()
    h1 = sopa.select_one("h1")
    pagina.titulo = _limpio(h1.get_text()) if h1 else ""

    articulos = sopa.select("article.view-details") or sopa.select("article.view-covers")
    for art in articulos:
        enlace = art.find("a", href=_RE_ID)
        if not enlace:
            continue
        rid = _RE_ID.search(enlace["href"]).group(1)
        titulo_tag = art.select_one("h3")
        titulo = _limpio(titulo_tag.get_text()) if titulo_tag else ""
        if not titulo:
            img = art.select_one("img.cover")
            titulo = _limpio(img.get("alt")) if img else ""
        autores = [
            _limpio(a.get_text())
            for a in art.select('[itemprop="author"] [itemprop="name"]')
        ]
        autores = list(dict.fromkeys(a for a in autores if a))
        img = art.select_one("img.cover")
        sinopsis_tag = art.select_one(".details-content__body")
        formato = _formato(art)
        bloque = art.select_one(".details-content__aside") or art.select_one(".display-cover-img__action")
        estado, ejemplares, fecha, texto = _estado(bloque)
        pagina.libros.append(
            Libro(
                id=rid,
                titulo=titulo,
                autores=autores,
                url=urljoin(base_url + "/", f"resources/{rid}"),
                portada=img.get("src") if img else None,
                sinopsis=_limpio(sinopsis_tag.get_text()) if sinopsis_tag else "",
                formato=formato,
                tipo=_tipo(art, formato),
                estado=estado,
                ejemplares=ejemplares,
                disponible_el=fecha,
                estado_texto=texto[:200],
            )
        )

    pagina.hay_siguiente = sopa.select_one("nav.pagination .next a") is not None
    return pagina


def lee_secciones_portada(html: str) -> list[Seccion]:
    """Lista las secciones (bundles) destacadas en la portada de la biblioteca."""
    sopa = BeautifulSoup(html, "lxml")
    secciones: list[Seccion] = []
    for h2 in sopa.select("h2.view-all__title"):
        a = h2.find("a", href=_RE_BUNDLE)
        if not a:
            continue
        sub = a.select_one(".subtitle")
        total = None
        if sub:
            m = re.search(r"(\d[\d.]*)", sub.get_text())
            total = int(m.group(1).replace(".", "")) if m else None
            sub.extract()
        nombre = _limpio(a.get_text()).replace("\xa0", " ").strip()
        secciones.append(Seccion(nombre=nombre, bundle=_RE_BUNDLE.search(a["href"]).group(1), total=total))
    if not secciones:
        raise ErrorDeLectura("No encuentro secciones en la portada")
    return secciones


def id_de_url(texto: str) -> str | None:
    """Extrae el id de recurso de un enlace de eBiblio (…/resources/<id>)."""
    m = _RE_ID.search(texto or "")
    return m.group(1) if m else None


def lee_ficha(html: str, base_url: str, rid: str) -> Libro:
    """Lee la ficha de un libro (/resources/<id>)."""
    sopa = BeautifulSoup(html, "lxml")

    def meta(prop: str) -> str:
        m = sopa.find("meta", attrs={"property": prop})
        return _limpio(m.get("content")) if m else ""

    h1 = sopa.select_one("h1")
    titulo = _limpio(h1.get_text()) if h1 else meta("og:title")
    if not titulo:
        raise ErrorDeLectura(f"La ficha {rid} no tiene título: puede que la web haya cambiado")
    autores = list(dict.fromkeys(
        _limpio(a.get_text()) for a in sopa.select('[itemprop="author"] [itemprop="name"]') if _limpio(a.get_text())
    ))
    img = sopa.select_one("img.cover")
    bloque = sopa.select_one("section.transactions")
    estado, ejemplares, fecha, texto = _estado(bloque)
    formato = _formato(sopa)
    tipo_tag = sopa.select_one('[itemtype*="schema.org/Audiobook"]')
    tipo = "audiolibro" if tipo_tag or formato.startswith("AUDIO") else "libro"
    return Libro(
        id=rid, titulo=titulo, autores=autores, url=urljoin(base_url + "/", f"resources/{rid}"),
        portada=(img.get("src") if img else None) or meta("og:image") or None,
        sinopsis=meta("og:description"), formato=formato, tipo=tipo, estado=estado,
        ejemplares=ejemplares, disponible_el=fecha, estado_texto=texto[:200],
    )
