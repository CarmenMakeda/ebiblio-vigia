"""Envío por email (SMTP, p. ej. Gmail con contraseña de aplicación)."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..models import Estado
from ..motor import Informe
from .mensajes import autores, fecha_corta, linea_estado

PLANTILLAS = Path(__file__).resolve().parent.parent / "plantillas"


def asunto(informes: list[Informe]) -> str | None:
    nuevos = sum(len(i.nuevos) for i in informes)
    fav = sum(1 for i in informes for e in i.nuevos if e.motivos)
    lib = sum(len(i.liberados) for i in informes)
    partes = []
    if nuevos:
        partes.append(f"{nuevos} novedad{'es' if nuevos != 1 else ''}")
    if fav:
        partes.append(f"⭐ {fav} te interesa{'n' if fav != 1 else ''}")
    if lib:
        partes.append(f"🔔 {lib} ya disponible{'s' if lib != 1 else ''}")
    if any(i.alerta for i in informes):
        partes.append("⚠️ problema al leer la web")
    if any(i.inicial and not i.error for i in informes):
        partes.append("vigía activado")
    if any(i.recuperada for i in informes):
        partes.append("✅ vuelve a funcionar")
    if not partes:
        return None
    return "📚 eBiblio · " + " · ".join(partes)


def html(informes: list[Informe], cfg: Config) -> str:
    env = Environment(loader=FileSystemLoader(PLANTILLAS), autoescape=select_autoescape(["html"]))
    env.filters.update(estado=linea_estado, autores=autores, fecha=fecha_corta)
    env.globals["Estado"] = Estado
    return env.get_template("correo.html").render(informes=informes, cfg=cfg)


class Correo:
    def __init__(self, servidor: str, puerto: int, usuario: str, contrasena: str, para: str, de: str | None = None):
        self.servidor, self.puerto = servidor, int(puerto)
        self.usuario, self.contrasena = usuario, contrasena
        self.para = [p.strip() for p in para.split(",") if p.strip()]
        self.de = de or usuario

    def envia(self, informes: list[Informe], cfg: Config) -> bool:
        tema = asunto(informes)
        if not tema:
            return False
        msg = EmailMessage()
        msg["Subject"] = tema
        msg["From"] = f"Vigía eBiblio <{self.de}>"
        msg["To"] = ", ".join(self.para)
        msg.set_content("Tienes novedades en eBiblio. Abre este correo en un lector que muestre HTML.")
        msg.add_alternative(html(informes, cfg), subtype="html")
        contexto = ssl.create_default_context()
        if self.puerto == 465:
            with smtplib.SMTP_SSL(self.servidor, self.puerto, context=contexto, timeout=30) as s:
                s.login(self.usuario, self.contrasena)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.servidor, self.puerto, timeout=30) as s:
                s.starttls(context=contexto)
                s.login(self.usuario, self.contrasena)
                s.send_message(msg)
        return True
