"""Memoria del vigía: qué libros se han visto y cómo estaban. Se guarda en JSON."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

VERSION = 1


def vacio() -> dict:
    return {"version": VERSION, "bibliotecas": {}, "historial": []}


def biblioteca(estado: dict, bid: str) -> dict:
    b = estado["bibliotecas"].setdefault(bid, {})
    b["_id"] = bid
    b.setdefault("inicializada", False)
    b.setdefault("secciones", {})
    b.setdefault("libros", {})
    b.setdefault("salud", {"fallos": 0, "ultimo_error": "", "alertado": False, "ultima_ok": None})
    b.setdefault("descubierto", None)
    b.setdefault("ultima_completa", None)
    return b


def carga(ruta: str | Path) -> dict:
    ruta = Path(ruta)
    if not ruta.exists():
        return vacio()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if datos.get("version") != VERSION:
        raise ValueError(f"Versión de estado no soportada: {datos.get('version')}")
    return datos


def guarda(estado: dict, ruta: str | Path) -> None:
    """Escritura atómica: nunca deja un archivo a medias aunque el proceso muera."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".estado-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, ruta)
