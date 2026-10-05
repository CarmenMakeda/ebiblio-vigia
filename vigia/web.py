"""Genera la web estática con las novedades (se publica en GitHub Pages)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config

PLANTILLAS = Path(__file__).resolve().parent / "plantillas"


def _datos(estado: dict, cfg: Config) -> dict:
    libros = []
    bibliotecas = []
    for bib in cfg.activas:
        b = estado["bibliotecas"].get(bib.id)
        if not b:
            continue
        nombres = {k: v.get("nombre", "") for k, v in b["secciones"].items()}
        bibliotecas.append({
            "id": bib.id,
            "nombre": bib.nombre,
            "url": bib.url,
            "ultima_ok": b["salud"].get("ultima_ok"),
            "error": b["salud"].get("ultimo_error") if b["salud"].get("fallos") else "",
            "secciones": [v["nombre"] for v in b["secciones"].values() if v.get("vigilada")],
        })
        for r in b["libros"].values():
            if not r.get("activo"):
                continue
            # Fecha de llegada: cuándo lo vio el vigía; para los registrados el primer día, su fecha de alta.
            llegada = r.get("alta") if r.get("base") else r.get("visto_primero")
            libros.append({
                "id": r["id"],
                "b": bib.id,
                "t": r["titulo"],
                "a": r.get("autores", []),
                "u": r["url"],
                "p": r.get("portada"),
                "s": (r.get("sinopsis") or "")[:400],
                "e": r.get("estado"),
                "n": r.get("ejemplares"),
                "d": r.get("disponible_el"),
                "f": (llegada or r.get("visto_primero") or "")[:10],
                "x": [nombres.get(s, "") for s in r.get("secciones", [])],
                "i": r.get("intereses", []),
                "o": r.get("tipo"),
                "c": r.get("cambio_estado"),
                "z": bool(r.get("excluido")),
            })
    libros.sort(key=lambda l: (l["f"], l["id"]), reverse=True)
    return {
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bibliotecas": bibliotecas,
        "libros": libros,
        "intereses": {"autores": cfg.intereses.autores, "palabras": cfg.intereses.palabras},
    }


def genera(estado: dict, cfg: Config, destino: str | Path = "_site") -> Path:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    datos = _datos(estado, cfg)
    env = Environment(loader=FileSystemLoader(PLANTILLAS), autoescape=select_autoescape(["html"]))
    # JSON seguro dentro de <script>: escapamos "<" para que no pueda cerrar la etiqueta
    datos_json = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = env.get_template("web.html").render(datos_json=datos_json, titulo="Vigía eBiblio")
    (destino / "index.html").write_text(html, encoding="utf-8")
    (destino / "novedades.json").write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    (destino / ".nojekyll").write_text("", encoding="utf-8")
    return destino / "index.html"
