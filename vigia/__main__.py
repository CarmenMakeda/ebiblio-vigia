"""Uso:
    python -m vigia comprobar      # mira novedades, avisa y genera la web (lo que hace GitHub cada hora)
    python -m vigia probar         # envía un aviso de prueba para comprobar Telegram/email
    python -m vigia web            # sólo regenera la web con lo que ya sabe
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import estado as est
from .config import ErrorDeConfiguracion, carga
from .models import Estado
from .buzon import atiende
from .lista import Lista
from .motor import Evento, Informe, Vigia
from .notify import avisa, canales
from .notify.telegram import ErrorTelegram, chats_recientes
from .web import genera


def _resumen(inf: Informe) -> str:
    if inf.error:
        return f"{inf.biblioteca.nombre}: ERROR ({inf.error})"
    tipo = "inicial (silenciosa)" if inf.inicial else ("completa" if inf.completa else "rápida")
    return (f"{inf.biblioteca.nombre}: {len(inf.nuevos)} nuevos, {len(inf.liberados)} liberados, "
            f"{inf.total_activos} títulos vigilados · lectura {tipo} · {inf.peticiones} peticiones")


def comprobar(args) -> int:
    cfg = carga(args.config)
    estado = est.carga(args.estado)
    vigia = Vigia(cfg, estado)
    lista = Lista(cfg, estado, vigia.cliente)

    # 1. Mensajes que me has escrito en Telegram (apuntar libros, /lista…)
    tg = canales().get("telegram")
    if tg:
        n = atiende(tg, tg.chat_id, lista, estado)
        if n:
            logging.info("Telegram: %d mensajes atendidos", n)
            est.guarda(estado, args.estado)

    # 2. Novedades
    vigia.seguidos = lista.claves_seguidas
    informes = [vigia.comprueba(b, forzar_completa=args.completa) for b in cfg.activas]
    for inf in informes:
        logging.info(_resumen(inf))

    # 3. Tu lista «quiero leer» y los títulos que aún no habían llegado
    eventos_lista = lista.comprueba(vigia.vistos) + lista.revisa_busquedas()
    logging.info("Lista «quiero leer»: %d libros seguidos, %d avisos, %d búsquedas pendientes",
                 len(lista.libros), len(eventos_lista), len(lista.busquedas))

    # Se guarda antes de avisar: si un envío falla, no se repetirán avisos en la próxima vuelta.
    est.guarda(estado, args.estado)
    if args.web_activa:
        genera(estado, cfg, args.web)
    errores = avisa(informes, cfg, estado, eventos_lista)
    _resumen_github(informes)
    return 1 if errores else 0


def _resumen_github(informes: list[Informe]) -> None:
    """Escribe un resumen legible en la página de la ejecución de GitHub Actions."""
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if not ruta:
        return
    with open(ruta, "a", encoding="utf-8") as f:
        f.write("## 📚 Vigía eBiblio\n\n")
        for inf in informes:
            f.write(f"- {_resumen(inf)}\n")
            for ev in (inf.nuevos + inf.liberados)[:50]:
                marca = "⭐ " if ev.motivos else ""
                f.write(f"  - {marca}[{ev.libro['titulo']}]({ev.libro['url']}) — {Estado(ev.libro['estado']).etiqueta}\n")


def probar(args) -> int:
    cfg = carga(args.config)
    estado = est.carga(args.estado)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and not chat:
        try:
            chats = chats_recientes(token)
        except ErrorTelegram as e:
            print(f"❌ El token de Telegram no funciona: {e}")
            return 1
        if not chats:
            print("Escribe cualquier mensaje a tu bot en Telegram y vuelve a lanzar la prueba.")
        for c in chats:
            print(f"👉 Tu TELEGRAM_CHAT_ID es: {c['id']}  ({c['nombre']}, {c['tipo']})")
        return 1
    if not canales():
        print("❌ No hay ningún canal configurado (faltan los secretos TELEGRAM_TOKEN/TELEGRAM_CHAT_ID o EMAIL_*).")
        return 1
    bib = cfg.activas[0]
    libros = [r for r in estado.get("bibliotecas", {}).get(bib.id, {}).get("libros", {}).values() if r.get("activo")]
    muestra = sorted(libros, key=lambda r: (bool(r.get("intereses")), r.get("visto_primero", "")), reverse=True)[:1]
    if not muestra:
        muestra = [{
            "id": "6ab61faa015acb463909ac01", "titulo": "Los Huérfanos", "autores": ["Carmen Mola"],
            "url": f"{bib.url}/resources/6ab61faa015acb463909ac01",
            "portada": "https://imagedelivery.net/QDkyDSqaJI1JEO0MqH_3SQ/2d80a48f-81b1-4efc-5470-3d2fb1775100/default",
            "estado": "reservable", "disponible_el": "2026-12-07T23:16", "tipo": "libro",
        }]
    inf = Informe(biblioteca=bib)
    inf.nuevos = [Evento("nuevo", muestra[0], "Prueba de aviso", ["prueba"])]
    errores = avisa([inf], cfg, estado)
    print("✅ Prueba enviada." if not errores else "❌ " + "; ".join(errores))
    return 1 if errores else 0


def web(args) -> int:
    cfg = carga(args.config)
    ruta = genera(est.carga(args.estado), cfg, args.web)
    print(f"Web generada en {ruta}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vigia", description="Avisos de novedades de eBiblio")
    p.add_argument("accion", nargs="?", default="comprobar", choices=["comprobar", "probar", "web"])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--estado", default="data/estado.json")
    p.add_argument("--web", default="_site", help="carpeta donde generar la web")
    p.add_argument("--sin-web", dest="web_activa", action="store_false", help="no generar la web")
    p.add_argument("--completa", action="store_true", help="fuerza una lectura completa de todas las páginas")
    p.add_argument("-v", "--detalle", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.detalle else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        return {"comprobar": comprobar, "probar": probar, "web": web}[args.accion](args)
    except ErrorDeConfiguracion as e:
        logging.error("Problema en la configuración: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
