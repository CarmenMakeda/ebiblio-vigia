"""Reparte los avisos por los canales configurados mediante variables de entorno."""
from __future__ import annotations

import logging
import os
import re

from ..config import Config
from ..motor import Informe
from .correo import Correo
from .mensajes import Foto, Texto, redacta, redacta_lista
from .telegram import ErrorTelegram, Telegram

log = logging.getLogger(__name__)


def canales() -> dict:
    env = os.environ
    c = {}
    if env.get("TELEGRAM_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        c["telegram"] = Telegram(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"])
    if env.get("EMAIL_USUARIO") and env.get("EMAIL_CONTRASENA") and env.get("EMAIL_PARA"):
        c["correo"] = Correo(
            env.get("EMAIL_SERVIDOR", "smtp.gmail.com"), int(env.get("EMAIL_PUERTO", "465")),
            env["EMAIL_USUARIO"], env["EMAIL_CONTRASENA"], env["EMAIL_PARA"],
        )
    return c


def _consola(piezas) -> None:
    for p in piezas:
        texto = p.texto if isinstance(p, (Texto, Foto)) else str(p)
        print(re.sub(r"<[^>]+>", "", texto), end="\n\n")


def avisa(informes: list[Informe], cfg: Config, estado: dict, eventos_lista=None) -> list[str]:
    """Envía los avisos. Devuelve la lista de errores de envío (vacía si todo fue bien)."""
    errores: list[str] = []
    eventos_lista = eventos_lista or []
    # Primero lo de tu lista «quiero leer»: es lo más urgente
    piezas = redacta_lista(eventos_lista, cfg) + redacta(informes, cfg, estado)
    if not piezas:
        log.info("Nada que avisar")
        return errores
    disponibles = canales()
    if not disponibles:
        log.warning("No hay canales configurados: muestro los avisos aquí")
    _consola(piezas)
    if "telegram" in disponibles:
        try:
            n = disponibles["telegram"].envia(piezas)
            log.info("Telegram: %d mensajes enviados", n)
        except ErrorTelegram as e:
            errores.append(f"Telegram: {e}")
    if "correo" in disponibles:
        try:
            if disponibles["correo"].envia(informes, cfg, eventos_lista):
                log.info("Email enviado")
        except Exception as e:  # smtplib lanza muchos tipos distintos
            errores.append(f"Email: {type(e).__name__}: {e}")
    for e in errores:
        log.error(e)
    return errores
