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


@dataclass
class Biblioteca:
    id: str
    nombre: str
    url: str
    activa: bool = True
    secciones: list[str] = field(default_factory=list)  # nombres o "re:<regex>"; vacío = automático

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
    )
    a = datos.get("avisos") or {}
    avisos = Avisos(**{k: v for k, v in a.items() if k in Avisos.__dataclass_fields__ and v is not None})
    c = datos.get("comprobacion") or {}
    comprobacion = Comprobacion(**{k: v for k, v in c.items() if k in Comprobacion.__dataclass_fields__ and v is not None})
    # La URL de la web puede venir de una variable de entorno (la pone el workflow)
    if not avisos.web and os.environ.get("VIGIA_URL_WEB"):
        avisos.web = os.environ["VIGIA_URL_WEB"]
    return Config(bibliotecas, intereses, avisos, comprobacion)
