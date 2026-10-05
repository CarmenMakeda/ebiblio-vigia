"""Envío por Telegram (Bot API)."""
from __future__ import annotations

import logging
import time

import requests

from .mensajes import Foto, Texto

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{metodo}"


class ErrorTelegram(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, pausa: float = 1.1):
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.pausa = pausa

    def _llama(self, metodo: str, datos: dict) -> dict:
        for intento in range(4):
            try:
                r = requests.post(API.format(token=self.token, metodo=metodo), data=datos, timeout=30)
            except requests.RequestException as e:
                log.warning("Telegram no responde (%s), reintento", e)
                time.sleep(3 * (intento + 1))
                continue
            cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 429:
                time.sleep(int(cuerpo.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            if not cuerpo.get("ok"):
                raise ErrorTelegram(f"{metodo}: {cuerpo.get('description') or r.status_code}")
            return cuerpo["result"]
        raise ErrorTelegram(f"{metodo}: Telegram no responde tras varios intentos")

    def texto(self, html: str) -> None:
        self._llama("sendMessage", {
            "chat_id": self.chat_id, "text": html, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })

    def foto(self, url: str, pie: str) -> None:
        self._llama("sendPhoto", {"chat_id": self.chat_id, "photo": url, "caption": pie, "parse_mode": "HTML"})

    def envia(self, piezas: list[Foto | Texto]) -> int:
        enviados = 0
        for p in piezas:
            if isinstance(p, Foto):
                try:
                    self.foto(p.url, p.texto)
                except ErrorTelegram as e:
                    log.warning("No pude enviar la portada (%s); mando sólo el texto", e)
                    self.texto(p.respaldo)
            else:
                self.texto(p.texto)
            enviados += 1
            time.sleep(self.pausa)
        return enviados


def chats_recientes(token: str) -> list[dict]:
    """Para averiguar tu chat_id: lista los chats que han escrito al bot recientemente."""
    r = requests.get(API.format(token=token.strip(), metodo="getUpdates"), timeout=30).json()
    if not r.get("ok"):
        raise ErrorTelegram(r.get("description", "token no válido"))
    vistos = {}
    for u in r["result"]:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            vistos[chat["id"]] = {
                "id": chat["id"],
                "nombre": chat.get("first_name") or chat.get("title") or "",
                "tipo": chat.get("type"),
            }
    return list(vistos.values())
