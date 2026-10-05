"""Lista «quiero leer»: libros concretos que la lectora sigue, estén o no en novedades."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from difflib import SequenceMatcher
from urllib.parse import quote_plus

from .config import Biblioteca, Config
from .fetch import Cliente, ErrorDeRed, NoEncontrado
from .models import Estado, Libro
from .parse import ErrorDeLectura, lee_ficha, lee_seccion, normaliza

log = logging.getLogger(__name__)

HORAS_ENFRIAMIENTO = 12
HORAS_ENTRE_BUSQUEDAS = 20
MAX_OPCIONES = 5
MAX_CONSULTAS = 6
CONECTORES = {"de", "del", "por"}


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
class Resultado:
    claros: list = field(default_factory=list)        # es ese libro: se sigue directamente
    dudosos: list = field(default_factory=list)       # mismo título, autores distintos: preguntar
    parecidos: list = field(default_factory=list)     # no está tal cual, pero se parece
    otro_formato: list = field(default_factory=list)  # existe, pero sólo en un formato que no sigues

    @property
    def opciones(self) -> list:
        return self.dudosos or self.parecidos


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
    def _consultas(self, texto: str) -> list[tuple[str, str]]:
        """Qué buscar en eBiblio, en orden. Su buscador funciona mal con frases largas
        (título + autor), así que si hace falta se prueba también a separarlos."""
        palabras = texto.split()
        consultas = [("q", texto)]
        for i, p in enumerate(palabras):
            if 0 < i < len(palabras) - 1 and normaliza(p) in CONECTORES:
                consultas.append(("q", " ".join(palabras[:i])))
                consultas.append(("autor", " ".join(palabras[i + 1:])))
        if " - " in texto or " – " in texto or "," in texto:
            partes = re.split(r"\s+[-–]\s+|\s*,\s*", texto, maxsplit=1)
            if len(partes) == 2 and all(partes):
                consultas += [("q", partes[0]), ("autor", partes[1])]
        for n in (3, 2, 1):
            # Primeras palabras como posible título, si dicen algo (no sólo «La», «El»…)
            if len(palabras) > n and any(len(normaliza(w)) >= 4 for w in palabras[:n]):
                consultas.append(("q", " ".join(palabras[:n])))
        vistas, unicas = set(), []
        for c in consultas:
            if c not in vistas:
                vistas.add(c)
                unicas.append(c)
        return unicas[:MAX_CONSULTAS]

    def busca(self, texto: str) -> "Resultado":
        """Busca un título (con o sin autor) y clasifica lo encontrado."""
        permitidos_tipos = {t for t in self.cfg.intereses.tipos if t in ("libro", "audiolibro")}
        todos: dict[str, tuple[Biblioteca, Libro]] = {}
        for bib in self.cfg.activas:
            for tipo, valor in self._consultas(texto):
                param = "author_keyword" if tipo == "autor" else "q"
                pag = lee_seccion(self.cliente.get(f"{bib.url}/resources?{param}={quote_plus(valor)}"), bib.url)
                for l in pag.libros:
                    if l.tipo in ("libro", "audiolibro"):
                        todos.setdefault(l.id, (bib, l))
                if self.coincidentes(texto, list(todos.values())):
                    break
        lista = list(todos.values())
        permitidos = [(b, l) for b, l in lista if l.tipo in permitidos_tipos]
        coinc = self.coincidentes(texto, permitidos)
        # Para decidir si hay dudas se miran TODOS los formatos: si existe otro libro con ese
        # título de otro autor (aunque sólo esté en audio), no se elige por la lectora.
        coinc_todos = self.coincidentes(texto, lista)
        autores = {normaliza(l.autores[0]) if l.autores else "" for _, l in coinc_todos}
        res = Resultado()
        if coinc:
            if len(autores) == 1:
                res.claros = coinc
            else:
                res.dudosos = coinc[:MAX_OPCIONES]
                res.otro_formato = [x for x in coinc_todos if x not in permitidos]
        else:
            res.otro_formato = [x for x in self.coincidentes(texto, lista) if x not in permitidos]
            res.parecidos = self._parecidos(texto, permitidos)
        for b, l in res.dudosos + res.parecidos:
            self.opciones[l.id] = b.id
        if len(self.opciones) > 200:
            for k in list(self.opciones)[:-200]:
                self.opciones.pop(k)
        return res

    def _parecidos(self, texto: str, res: list[tuple[Biblioteca, Libro]]) -> list[tuple[Biblioteca, Libro]]:
        """Resultados cuyo título se parece de verdad a lo escrito (admite erratas)."""
        q = [w for w in normaliza(texto).split() if len(w) >= 4]
        puntuados = []
        for orden, (b, l) in enumerate(res):
            titulo = [w for w in _palabras_titulo(l.titulo) if len(w) >= 4]
            aciertos = sum(1 for w in q if any(SequenceMatcher(None, w, t).ratio() >= 0.8 for t in titulo))
            if aciertos:
                puntuados.append((-aciertos, orden, b, l))
        puntuados.sort(key=lambda x: (x[0], x[1]))
        return [(b, l) for _, _, b, l in puntuados[:MAX_OPCIONES]]

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
            if res.claros:
                try:
                    recs = self.sigue(res.claros)
                except (ErrorDeRed, ErrorDeLectura) as e:
                    log.warning("No pude abrir «%s»: %s", b["texto"], e)
                    continue
                self.busquedas.pop(clave)
                eventos.append(EventoLista("encontrado", {}, busqueda=b["texto"], seguidos=recs))
            elif res.dudosos:
                self.busquedas.pop(clave)
                eventos.append(EventoLista("encontrado", {}, busqueda=b["texto"], opciones=res.dudosos))
        return eventos
