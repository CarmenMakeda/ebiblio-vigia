"""Modelo de datos: libros, estados de disponibilidad y secciones."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Estado(str, Enum):
    """Disponibilidad de un libro tal como la muestra eBiblio."""

    DISPONIBLE = "disponible"      # botón "Prestar": se puede coger ya
    RESERVABLE = "reservable"      # botón "Reservar": cola con fecha estimada
    SIN_RESERVAS = "sin_reservas"  # "En este momento no hay reservas libres"
    EN_LINEA = "en_linea"          # "Acceso en línea" (prensa, recursos…)
    DESCONOCIDO = "desconocido"    # texto que el lector no reconoce

    @property
    def emoji(self) -> str:
        return {
            Estado.DISPONIBLE: "🟢",
            Estado.RESERVABLE: "🟡",
            Estado.SIN_RESERVAS: "🔴",
            Estado.EN_LINEA: "🔵",
            Estado.DESCONOCIDO: "⚪",
        }[self]

    @property
    def etiqueta(self) -> str:
        return {
            Estado.DISPONIBLE: "Disponible ya",
            Estado.RESERVABLE: "Se puede reservar",
            Estado.SIN_RESERVAS: "Sin reservas libres",
            Estado.EN_LINEA: "Acceso en línea",
            Estado.DESCONOCIDO: "Estado desconocido",
        }[self]

    @property
    def rango(self) -> int:
        """Cuanto mayor, más fácil es conseguir el libro."""
        return {
            Estado.SIN_RESERVAS: 0,
            Estado.DESCONOCIDO: 0,
            Estado.RESERVABLE: 1,
            Estado.EN_LINEA: 2,
            Estado.DISPONIBLE: 2,
        }[self]


@dataclass
class Libro:
    id: str                       # id del recurso en eBiblio (ObjectId de 24 hex)
    titulo: str
    autores: list[str]
    url: str
    portada: str | None = None
    sinopsis: str = ""
    formato: str = ""             # EPUB, AUDIO, PDF…
    tipo: str = "libro"           # libro | audiolibro | otro
    estado: Estado = Estado.DESCONOCIDO
    ejemplares: int | None = None  # nº de ejemplares libres si la web lo dice
    disponible_el: str | None = None  # ISO local (Europe/Madrid) de próxima disponibilidad
    estado_texto: str = ""        # texto literal, útil para depurar

    @property
    def alta(self) -> datetime | None:
        """Fecha de alta en el catálogo, codificada en el propio id (ObjectId)."""
        return fecha_de_objectid(self.id)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["estado"] = self.estado.value
        return d


@dataclass
class Seccion:
    nombre: str
    bundle: str                   # id del bundle (lista) en eBiblio
    total: int | None = None      # "427 títulos"

    @property
    def ruta(self) -> str:
        return f"/bundles/{self.bundle}"


@dataclass
class PaginaSeccion:
    libros: list[Libro] = field(default_factory=list)
    hay_siguiente: bool = False
    titulo: str = ""


def fecha_de_objectid(oid: str) -> datetime | None:
    """Los ids de eBiblio son ObjectId de MongoDB: los 8 primeros hex son un timestamp."""
    try:
        if len(oid) != 24:
            return None
        return datetime.fromtimestamp(int(oid[:8], 16), tz=timezone.utc)
    except ValueError:
        return None
