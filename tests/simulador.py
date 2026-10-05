"""Web eBiblio simulada para probar el motor sin conexión."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from vigia.fetch import ErrorDeRed, NoEncontrado

BASE = "https://madrid.ebiblio.es"


def oid(fecha: datetime, n: int) -> str:
    return f"{int(fecha.timestamp()):08x}{n:016x}"


def libro(n: int, titulo: str, autor: str, estado: str = "sin_reservas",
          fecha: datetime = datetime(2026, 9, 25, tzinfo=timezone.utc)) -> dict:
    return {"id": oid(fecha, n), "titulo": titulo, "autor": autor, "estado": estado}


def _bloque(estado: str) -> str:
    if estado == "disponible":
        return ('<div class="availability"><span class="count">2</span> ejemplares disponibles</div>'
                '<form><button class="button-borrow button"><span class="action">Prestar</span></button></form>')
    if estado == "reservable":
        return ('<p class="next-availability">Próximo ejemplar disponible el <span class="date">23 oct 2026 a las 10:00</span></p>'
                '<form><button class="button-booking button"><span class="action">Reservar</span></button></form>')
    return '<div class="availability">En este momento no hay reservas libres. Inténtelo más adelante</div>'


def pagina_bundle(nombre: str, bundle: str, libros: list[dict], pagina: int, por_pagina: int = 36) -> str:
    trozo = libros[(pagina - 1) * por_pagina: pagina * por_pagina]
    arts = "".join(
        f'''<article class="view-details" itemtype="http://schema.org/Book">
        <a href="/resources/{l['id']}"><img class="cover" alt="{escape(l['titulo'])}" src="https://img/{l['id']}"></a>
        <header><a href="/resources/{l['id']}"><h3>{escape(l['titulo'])}</h3></a>
        <span itemprop="author"><span itemprop="name">{escape(l['autor'])}</span></span></header>
        <p class="details-content__body">Sinopsis de {escape(l['titulo'])}</p>
        <div class="details-content__aside"><h4 class="epub transaction__title"><span>EPUB</span></h4>{_bloque(l['estado'])}</div>
        </article>'''
        for l in trozo
    )
    siguiente = len(libros) > pagina * por_pagina
    nav = '<nav class="pagination"><span class="page current">%d</span>%s</nav>' % (
        pagina, f'<span class="next"><a href="/bundles/{bundle}?page={pagina + 1}">Siguiente ›</a></span>' if siguiente else "")
    return f"<html><body><h1>{escape(nombre)}</h1><main>{arts}</main>{nav}</body></html>"


class WebFalsa:
    """Imita Cliente.get(). Las secciones se describen como {bundle: (nombre, [libros])}."""

    def __init__(self):
        self.secciones: dict[str, tuple[str, list[dict]]] = {}
        self.extra_portada: list[tuple[str, str]] = []  # secciones no vigiladas
        self.caida = False
        self.peticiones = 0
        self.urls: list[str] = []

    def portada(self) -> str:
        h2 = "".join(
            f'<h2 class="alpha view-all__title"><a href="{BASE}/bundles/{b}">{escape(n)}&nbsp;<span class="subtitle">{len(l)} títulos</span></a></h2>'
            for b, (n, l) in self.secciones.items()
        ) + "".join(
            f'<h2 class="alpha view-all__title"><a href="{BASE}/bundles/{b}">{escape(n)}</a></h2>' for b, n in self.extra_portada
        )
        return f"<html><body>{h2}</body></html>"

    def get(self, url: str) -> str:
        self.peticiones += 1
        self.urls.append(url)
        if self.caida:
            raise ErrorDeRed("HTTP 503 simulado")
        if url.rstrip("/") == BASE:
            return self.portada()
        ruta = url[len(BASE):]
        if ruta.startswith("/bundles/"):
            bundle, _, q = ruta[len("/bundles/"):].partition("?page=")
            if bundle not in self.secciones:
                raise NoEncontrado(url)
            nombre, libros = self.secciones[bundle]
            return pagina_bundle(nombre, bundle, libros, int(q or 1))
        raise NoEncontrado(url)
