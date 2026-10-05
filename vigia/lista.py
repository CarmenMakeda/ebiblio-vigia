"""Lista «quiero leer»: libros concretos que la lectora sigue, estén o no en novedades."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import quote_plus

from .config import Biblioteca, Config
from .fetch import Cliente, ErrorDeRed, NoEncontrado
from .models import Estado, Libro
from .parse import ErrorDeLectura, lee_ficha, lee_seccion, normaliza

log = logging.getLogger(__name__)

HORAS_ENFRIAMIENTO = 12
HORAS_ENTRE_BUSQUEDAS = 20
MAX_OPCIONES = 5


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _de_iso(s: str | None) -> datetime | None:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


def _palabras_titulo(titulo: str) -> set[str]:
    """Palabras del título sin lo que va entre paréntesis (sagas, «castellano»…)."""
    return set(normaliza(re.sub(r"\([^)]*\)", " ", titulo)).split())


def clave_busqueda(texto: str) -> str:
    return hashlib.sha1(normaliza(texto).encode()).hexdigest()[:8]


@dataclass
class EventoLista:
    tipo: str            # disponible | reservable | retirado | encontrado
    libro: dict          # registro de la lista (o {} en «encontrado»)
    anterior: str | None = None
    busqueda: str = ""
    opciones: list[tuple[Biblioteca, Libro]] = field(default_factory=list)
    seguidos: list[dict] = field(default_factory=list)   # libros que se han empezado a seguir solos


class Lista:
    def __init__(self, cfg: Config, estado: dict, cliente: Cliente, ahora=None):
        self.cfg = cfg
        self.estado = estado
        self.cliente = cliente
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))
        self.libros: dict = estado.setdefault("lista", {})
        self.busquedas: dict = estado.setdefault("busquedas", {})
        self.opciones: dict = estado.setdefault("opciones", {})   # id -> id de biblioteca (de la última búsqueda)

    # ---------------------------------------------------------------- bibliotecas
    def biblioteca(self, bid: str) -> Biblioteca | None:
        return next((b for b in self.cfg.activas if b.id == bid), None)

    @property
    def claves_seguidas(self) -> set[str]:
        return set(self.libros)

    # --------------------------------------------------------------- altas/bajas
    def anade(self, bib: Biblioteca, rid: str, origen: str = "telegram") -> tuple[dict, bool]:
        clave = f"{bib.id}:{rid}"
        if clave in self.libros:
            return self.libros[clave], False
        libro = lee_ficha(self.cliente.get(f"{bib.url}/resources/{rid}"), bib.url, rid)
        rec = libro.a_dict()
        rec.update(biblioteca=bib.id, biblioteca_nombre=bib.nombre, anadido=_iso(self.ahora()),
                   comprobado=_iso(self.ahora()), origen=origen)
        self.libros[clave] = rec
        return rec, True

    def quita(self, rid: str) -> dict | None:
        for clave in [c for c in self.libros if c.endswith(":" + rid)]:
            return self.libros.pop(clave)
        return None

    # ------------------------------------------------------------------ búsqueda
    def busca(self, texto: str) -> list[tuple[Biblioteca, Libro]]:
        q = normaliza(texto)
        encontrados: list[tuple[int, int, Biblioteca, Libro]] = []
        for bib in self.cfg.activas:
            pag = lee_seccion(self.cliente.get(f"{bib.url}/resources?q={quote_plus(texto)}"), bib.url)
            for orden, l in enumerate(pag.libros):
                if l.tipo not in ("libro", "audiolibro") or l.tipo not in self.cfg.intereses.tipos:
                    continue
                t = normaliza(l.titulo)
                todo = normaliza(l.titulo + " " + " ".join(l.autores))
                rango = 0 if t == q or todo == q else 1 if (t.startswith(q) or all(p in todo.split() for p in q.split())) else 2
                encontrados.append((rango, orden, bib, l))
        encontrados.sort(key=lambda x: (x[0], x[1]))
        res = [(b, l) for _, _, b, l in encontrados[:MAX_OPCIONES]]
        for b, l in res:
            self.opciones[l.id] = b.id
        if len(self.opciones) > 200:
            for k in list(self.opciones)[:-200]:
                self.opciones.pop(k)
        return res

    def coincidentes(self, texto: str, res: list[tuple[Biblioteca, Libro]]) -> list[tuple[Biblioteca, Libro]]:
        """Resultados cuyo título es lo escrito (admitiendo además palabras del autor)."""
        q = set(normaliza(texto).split())
        fuertes = []
        for bib, l in res:
            titulo = _palabras_titulo(l.titulo)
            autor = set(normaliza(" ".join(l.autores)).split())
            if titulo and titulo <= q and q <= titulo | autor:
                fuertes.append((bib, l))
        return fuertes

    def claros(self, texto: str, res: list[tuple[Biblioteca, Libro]]) -> list[tuple[Biblioteca, Libro]]:
        """Resultados que son, sin duda, el libro pedido (todas sus ediciones: EPUB, audio…).

        Lo son si el título coincide con lo escrito (más, quizá, palabras del autor) y todos
        son del mismo autor. Si hay dos libros con el mismo título de autores distintos,
        no hay certeza y se devuelve una lista vacía para preguntar.
        """
        fuertes = self.coincidentes(texto, res)
        # Se compara el autor principal: los audiolibros a veces añaden al narrador.
        autores = {normaliza(l.autores[0]) if l.autores else "" for _, l in fuertes}
        return fuertes if fuertes and len(autores) == 1 else []

    def sigue(self, encontrados: list[tuple[Biblioteca, Libro]]) -> list[dict]:
        """Empieza a seguir varios resultados; devuelve los registros (nuevos o ya existentes)."""
        recs = []
        for bib, l in encontrados:
            rec, _ = self.anade(bib, l.id)
            recs.append(rec)
        return recs

    def pendiente(self, texto: str) -> str:
        clave = clave_busqueda(texto)
        self.busquedas.setdefault(clave, {"texto": texto.strip(), "anadida": _iso(self.ahora()), "ultima": _iso(self.ahora())})
        return clave

    def olvida(self, clave: str) -> dict | None:
        return self.busquedas.pop(clave, None)

    # -------------------------------------------------------------- comprobación
    def comprueba(self, vistos: dict[str, Libro] | None = None) -> list[EventoLista]:
        """Revisa cada libro de la lista. `vistos` son los leídos ya en esta vuelta (clave bib:id)."""
        vistos = vistos or {}
        eventos: list[EventoLista] = []
        ahora = self.ahora()
        claves = sorted(self.libros, key=lambda c: self.libros[c].get("comprobado") or "")
        for clave in claves[: self.cfg.comprobacion.lista_max]:
            rec = self.libros[clave]
            bib = self.biblioteca(rec["biblioteca"])
            if not bib:
                continue
            libro = vistos.get(clave)
            if libro is None:
                try:
                    libro = lee_ficha(self.cliente.get(rec["url"]), bib.url, rec["id"])
                except NoEncontrado:
                    eventos.append(EventoLista("retirado", self.libros.pop(clave)))
                    continue
                except (ErrorDeRed, ErrorDeLectura) as e:
                    log.warning("No pude revisar «%s»: %s", rec.get("titulo"), e)
                    continue
            anterior = Estado(rec.get("estado", "desconocido"))
            datos = libro.a_dict()
            if not datos.get("sinopsis"):
                datos.pop("sinopsis", None)
            rec.update(datos)
            rec["comprobado"] = _iso(ahora)
            if libro.estado is anterior:
                continue
            rec["cambio_estado"] = _iso(ahora)
            mejora = libro.estado.rango > anterior.rango and libro.estado in (Estado.DISPONIBLE, Estado.RESERVABLE)
            if not mejora:
                continue
            marca = f"avisado_{libro.estado.value}"
            ultimo = _de_iso(rec.get(marca))
            if ultimo and ahora - ultimo < timedelta(hours=HORAS_ENFRIAMIENTO):
                continue
            rec[marca] = _iso(ahora)
            eventos.append(EventoLista(libro.estado.value, rec, anterior.value))
        return eventos

    def revisa_busquedas(self) -> list[EventoLista]:
        """Repite una vez al día las búsquedas de títulos que aún no estaban en eBiblio."""
        eventos = []
        ahora = self.ahora()
        for clave, b in list(self.busquedas.items()):
            ultima = _de_iso(b.get("ultima"))
            if ultima and ahora - ultima < timedelta(hours=HORAS_ENTRE_BUSQUEDAS):
                continue
            try:
                res = self.busca(b["texto"])
            except (ErrorDeRed, ErrorDeLectura) as e:
                log.warning("No pude repetir la búsqueda «%s»: %s", b["texto"], e)
                continue
            b["ultima"] = _iso(ahora)
            if not res:
                continue
            self.busquedas.pop(clave)
            claros = self.claros(b["texto"], res)
            if claros:
                try:
                    recs = self.sigue(claros)
                except (ErrorDeRed, ErrorDeLectura) as e:
                    log.warning("No pude abrir «%s»: %s", b["texto"], e)
                    recs = []
                if recs:
                    eventos.append(EventoLista("encontrado", {}, busqueda=b["texto"], seguidos=recs))
                    continue
            eventos.append(EventoLista("encontrado", {}, busqueda=b["texto"], opciones=self.coincidentes(b["texto"], res) or res))
        return eventos
