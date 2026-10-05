"""Decide si un libro coincide con los intereses de la lectora."""
from __future__ import annotations

import re

from .config import Intereses
from .models import Libro
from .parse import normaliza


def _contiene_frase(texto_norm: str, frase: str) -> bool:
    f = normaliza(frase)
    return bool(f) and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", texto_norm) is not None


def _autor_coincide(buscado: str, autores: list[str]) -> bool:
    """'Carmen Mola' coincide con 'Carmen Mola' y con 'Mola, Carmen' (todas sus palabras)."""
    palabras = set(normaliza(buscado).split())
    if not palabras:
        return False
    return any(palabras <= set(normaliza(a).split()) for a in autores)


def motivos(libro: Libro, intereses: Intereses) -> list[str]:
    """Lista de motivos por los que el libro interesa (vacía si no interesa)."""
    encontrados = []
    for autor in intereses.autores:
        if _autor_coincide(autor, libro.autores):
            encontrados.append(autor)
    texto = normaliza(" ".join([libro.titulo, libro.sinopsis]))
    for palabra in intereses.palabras:
        if _contiene_frase(texto, palabra):
            encontrados.append(palabra)
    return list(dict.fromkeys(encontrados))


def excluido(libro: Libro, intereses: Intereses) -> bool:
    if intereses.tipos and libro.tipo not in intereses.tipos and libro.tipo != "otro":
        return True
    texto = normaliza(" ".join([libro.titulo, libro.sinopsis, " ".join(libro.autores)]))
    return any(_contiene_frase(texto, p) for p in intereses.excluir)
