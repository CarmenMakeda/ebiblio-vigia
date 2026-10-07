"""Carga y validación de config.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PATRON_AUTO = r"^(novedades|nuev[oa]s|ultimas incorporaciones|recien llegad)"


class ErrorDeConfiguracion(Exception):
    pass


ORDEN_LLEGADA = "sort_by=created_at_desc"


@dataclass
class Consulta:
    """Lista de novedades hecha con el catálogo ordenado por fecha de llegada.

    Sirve para las bibliotecas que no tienen listas («bundles») de novedades, como
    Castilla y León: `/resources?nature=ebook&sort_by=created_at_desc` muestra primero
    lo último que ha llegado. Se vigilan sólo las primeras `paginas` (40 libros cada una).
    """
    nombre: str
    filtro: str                   # parámetros del catálogo, p. ej. "nature=ebook"
    paginas: int = 10

    def __post_init__(self):
        f = self.filtro.strip()
        if "?" in f:
            f = f.split("?", 1)[1]
        partes = [x for x in f.split("&") if x and not x.startswith(("sort_by=", "page=", "view=", "l="))]
        self.filtro = "&".join(partes)
        try:
            self.paginas = int(self.paginas)
        except (TypeError, ValueError):
            raise ErrorDeConfiguracion(f"paginas de «{self.nombre}» debe ser un número") from None
        if not 1 <= self.paginas <= 25:
            raise ErrorDeConfiguracion(f"paginas de «{self.nombre}» debe estar entre 1 y 25")

    @property
    def clave(self) -> str:
        return "q:" + self.filtro

    def url(self, base: str, pagina: int = 1) -> str:
        q = "&".join(x for x in (self.filtro, ORDEN_LLEGADA) if x)
        return f"{base}/resources?{q}" + (f"&page={pagina}" if pagina > 1 else "")


@dataclass
class Biblioteca:
    id: str
    nombre: str
    url: str
    activa: bool = True
    secciones: list[str] = field(default_factory=list)  # nombres o "re:<regex>"; vacío = automático
    consultas: list[Consulta] = field(default_factory=list)  # novedades sacadas del catálogo

    @property
    def usa_portada(self) -> bool:
        """¿Hay que buscar listas de novedades en la portada? No si sólo se usan consultas."""
        return bool(self.secciones) or not self.consultas

    def consulta(self, clave: str) -> Consulta | None:
        return next((c for c in self.consultas if c.clave == clave), None)

    def __post_init__(self):
        self.url = self.url.rstrip("/")
        if not re.fullmatch(r"[a-z0-9_-]+", self.id):
            raise ErrorDeConfiguracion(f"id de biblioteca no válido: {self.id!r} (usa minúsculas, números y guiones)")
        if not self.url.startswith("http"):
            raise ErrorDeConfiguracion(f"url no válida para {self.id}: {self.url!r}")


@dataclass
class Intereses:
    autores: list[str] = field(default_factory=list)
    palabras: list[str] = field(default_factory=list)
    excluir: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=lambda: ["libro", "audiolibro"])
    infantil: bool = True         # False = no avisar de libros infantiles/juveniles


@dataclass
class Avisos:
    novedades: str = "todas"        # todas | intereses | ninguna
    liberados: str = "intereses"    # todas | intereses | ninguna
    portadas: bool = True
    web: str = ""
    max_lista: int = 60             # máximo de títulos listados por mensaje antes de resumir
    fallos_para_alertar: int = 3

    def __post_init__(self):
        for nombre in ("novedades", "liberados"):
            valor = getattr(self, nombre)
            if valor not in ("todas", "intereses", "ninguna"):
                raise ErrorDeConfiguracion(f"avisos.{nombre} debe ser todas, intereses o ninguna (es {valor!r})")


@dataclass
class Comprobacion:
    paginas_estado: int = 2
    paginas_max: int = 25
    pausa_segundos: float = 2.0
    sincronizacion_completa_horas: float = 20
    dias_seccion_nueva: int = 7
    lista_max: int = 40           # libros de «quiero leer» revisados en cada vuelta
    user_agent: str = (
        "Mozilla/5.0 (compatible; VigiaEbiblio/1.0; uso personal de una lectora; "
        "+https://github.com/)"
    )
    zona_horaria: str = "Europe/Madrid"


@dataclass
class Config:
    bibliotecas: list[Biblioteca]
    intereses: Intereses
    avisos: Avisos
    comprobacion: Comprobacion

    @property
    def activas(self) -> list[Biblioteca]:
        return [b for b in self.bibliotecas if b.activa]


def _lista(valor) -> list[str]:
    if valor in (None, "", "auto"):
        return []
    if isinstance(valor, str):
        return [valor]
    return [str(v).strip() for v in valor if str(v).strip()]


def carga(ruta: str | Path = "config.yaml") -> Config:
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorDeConfiguracion(f"No encuentro {ruta}")
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ErrorDeConfiguracion(f"config.yaml tiene un error de formato: {e}") from e

    bibliotecas = []
    for b in datos.get("bibliotecas") or []:
        bibliotecas.append(
            Biblioteca(
                id=str(b["id"]),
                nombre=str(b.get("nombre") or b["id"]),
                url=str(b["url"]),
                activa=bool(b.get("activa", True)),
                secciones=_lista(b.get("secciones")),
                consultas=[
                    Consulta(nombre=str(c.get("nombre") or "Novedades"), filtro=str(c.get("filtro") or ""),
                             paginas=c.get("paginas", 10))
                    for c in (b.get("consultas") or [])
                ],
            )
        )
    if not any(b.activa for b in bibliotecas):
        raise ErrorDeConfiguracion("No hay ninguna biblioteca activa en config.yaml")

    i = datos.get("intereses") or {}
    intereses = Intereses(
        autores=_lista(i.get("autores")),
        palabras=_lista(i.get("palabras")),
        excluir=_lista(i.get("excluir")),
        tipos=_lista(i.get("tipos")) or ["libro", "audiolibro"],
        infantil=i.get("infantil", True) is not False,
    )
    a = datos.get("avisos") or {}
    avisos = Avisos(**{k: v for k, v in a.items() if k in Avisos.__dataclass_fields__ and v is not None})
    c = datos.get("comprobacion") or {}
    comprobacion = Comprobacion(**{k: v for k, v in c.items() if k in Comprobacion.__dataclass_fields__ and v is not None})
    # La URL de la web puede venir de una variable de entorno (la pone el workflow)
    if not avisos.web and os.environ.get("VIGIA_URL_WEB"):
        avisos.web = os.environ["VIGIA_URL_WEB"]
    return Config(bibliotecas, intereses, avisos, comprobacion)
