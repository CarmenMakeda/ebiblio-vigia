"""Cliente HTTP respetuoso: pausa entre peticiones, reintentos y errores claros."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class ErrorDeRed(Exception):
    pass


class NoEncontrado(ErrorDeRed):
    """404: la sección (bundle) ya no existe."""


class Cliente:
    def __init__(self, user_agent: str, pausa: float = 2.0, intentos: int = 3, timeout: float = 30):
        self.sesion = requests.Session()
        self.sesion.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-ES,es;q=0.9",
            }
        )
        self.pausa = pausa
        self.intentos = intentos
        self.timeout = timeout
        self.peticiones = 0
        self._ultima = 0.0

    def _espera(self):
        restante = self.pausa - (time.monotonic() - self._ultima)
        if restante > 0:
            time.sleep(restante)

    def get(self, url: str) -> str:
        ultimo_error = ""
        for intento in range(1, self.intentos + 1):
            self._espera()
            try:
                r = self.sesion.get(url, timeout=self.timeout)
                self._ultima = time.monotonic()
                self.peticiones += 1
            except requests.RequestException as e:
                self._ultima = time.monotonic()
                ultimo_error = f"{type(e).__name__}: {e}"
                log.warning("Intento %s/%s fallido para %s: %s", intento, self.intentos, url, ultimo_error)
                time.sleep(min(30, 5 * intento))
                continue
            if r.status_code == 404:
                raise NoEncontrado(f"404 en {url}")
            if r.status_code in (429, 500, 502, 503, 504):
                espera = r.headers.get("Retry-After")
                segundos = int(espera) if espera and espera.isdigit() else 10 * intento
                ultimo_error = f"HTTP {r.status_code}"
                log.warning("HTTP %s en %s, espero %ss", r.status_code, url, segundos)
                time.sleep(min(segundos, 60))
                continue
            if r.status_code >= 400:
                raise ErrorDeRed(f"HTTP {r.status_code} en {url} (la web puede estar bloqueando el acceso)")
            # Si el servidor no declara el juego de caracteres, requests asume Latin-1
            # y estropea las tildes. eBiblio siempre usa UTF-8.
            if "charset" not in r.headers.get("Content-Type", "").lower():
                r.encoding = "utf-8"
            return r.text
        raise ErrorDeRed(f"No he podido leer {url} tras {self.intentos} intentos ({ultimo_error})")
