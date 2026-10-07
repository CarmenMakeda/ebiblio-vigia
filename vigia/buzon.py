"""Buzón del bot: atiende lo que la lectora escribe en Telegram.

- Un título (y autor, si quiere)  → lo sigue (o pregunta /s_<id> si hay dudas)
- /lista                          → lo que sigue y cómo está cada libro
- /quitar_<id>, /olvidar_<clave>  → deja de seguir un libro o una búsqueda
"""
from __future__ import annotations

import logging
import re
from html import escape

from .fetch import ErrorDeRed, NoEncontrado
from .lista import Lista
from .models import Estado, Libro
from .notify.mensajes import autores, linea_estado
from .notify.telegram import ErrorTelegram, Telegram, actualizaciones, fija_menu
from .parse import ErrorDeLectura, normaliza

log = logging.getLogger(__name__)

VERSION_MENU = 1
SALUDOS = {"hola", "holi", "hey", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hola buenas",
           "gracias", "muchas gracias", "ok", "vale", "adios", "hi", "hello", "prueba", "test", "que tal", "hola que tal"}

AYUDA = (
    "📚 <b>Tu lista «quiero leer»</b>\n\n"
    "Escríbeme el <b>título</b> del libro que quieras leer (y el autor, si quieres afinar). "
    "Lo busco en eBiblio y lo sigo por ti:\n"
    "🔔 te aviso cuando se pueda reservar\n"
    "🟢 y cuando esté libre para cogerlo.\n\n"
    "Si aún no está en eBiblio, lo busco cada día y te aviso cuando llegue.\n"
    "Si hay varios libros con ese título, te pregunto cuál es.\n\n"
    "/lista: ver lo que sigo para ti\n\n"
    "<i>Leo tus mensajes en cada comprobación (cada hora), así que puedo tardar un rato en contestar.</i>"
)


def _donde(lista: Lista, rec: dict) -> str:
    """« · eBiblio Madrid» cuando se vigila más de una biblioteca (si no, sobra)."""
    if len(lista.cfg.activas) > 1 and rec.get("biblioteca_nombre"):
        return f" · {escape(rec['biblioteca_nombre'])}"
    return ""


def _ficha_corta(rec: dict, donde: str = "") -> str:
    a = f" — <i>{escape(autores(rec))}</i>" if rec.get("autores") else ""
    tipo = " 🎧" if rec.get("tipo") == "audiolibro" else ""
    return f'<a href="{escape(rec["url"])}">{escape(rec["titulo"])}</a>{tipo}{a}{donde}\n{linea_estado(rec)}'


def _opciones(lista: Lista, res: list[tuple], cabecera: str) -> str:
    varias = len(lista.cfg.activas) > 1
    lineas = [cabecera, ""]
    for bib, l in res:
        rec = l.a_dict()
        donde = f" · {escape(bib.nombre)}" if varias else ""
        fmt = f" ({escape(l.formato.title())})" if l.formato else ""
        lineas.append(f"{_ficha_corta(rec)}{fmt}{donde}\n👉 Seguir este: /s_{l.id}\n")
    return "\n".join(lineas)


def texto_lista(lista: Lista) -> str:
    if not lista.libros and not lista.busquedas:
        return "Tu lista «quiero leer» está vacía.\n\n" + AYUDA
    partes = []
    if lista.libros:
        partes.append(f"📌 <b>Sigo {len(lista.libros)} libro{'s' if len(lista.libros) != 1 else ''} para ti</b>\n")
        orden = sorted(lista.libros.values(), key=lambda r: (-Estado(r.get("estado", "desconocido")).rango, r["titulo"].lower()))
        for r in orden:
            partes.append(f"{_ficha_corta(r, _donde(lista, r))}\nQuitar: /quitar_{r['id']}\n")
    if lista.busquedas:
        partes.append("🔎 <b>Títulos que busco cada día</b> (aún no están en eBiblio)\n")
        for clave, b in lista.busquedas.items():
            partes.append(f"«{escape(b['texto'])}»: /olvidar_{clave}")
    return "\n".join(partes)


def _apunta(lista: Lista, bib, rid: str) -> str:
    try:
        rec, nuevo = lista.anade(bib, rid)
    except NoEncontrado:
        return "Ese libro ya no está en eBiblio. Escríbeme otra vez el título para buscarlo."
    except (ErrorDeRed, ErrorDeLectura) as e:
        return f"Ahora no puedo abrir ese libro en eBiblio ({escape(str(e))}). Vuelve a escribirme el título más tarde."
    if not nuevo:
        return f"Ya lo seguía:\n{_ficha_corta(rec, _donde(lista, rec))}"
    e = Estado(rec["estado"])
    extra = {
        Estado.DISPONIBLE: "\n\n¡Está libre ahora mismo! Corre a cogerlo.",
        Estado.RESERVABLE: "\n\nYa se puede reservar. Si lo quieres, resérvalo; si no, te avisaré cuando quede libre.",
        Estado.SIN_RESERVAS: "\n\nAhora no admite reservas. Te aviso en cuanto se pueda reservar o quede libre.",
    }.get(e, "\n\nTe aviso cuando cambie su disponibilidad.")
    return f"📌 <b>Apuntado en tu lista</b>\n{_ficha_corta(rec, _donde(lista, rec))}{extra}"


def responde(lista: Lista, texto: str) -> str:
    """Respuesta (HTML de Telegram) a un mensaje de la lectora."""
    texto = (texto or "").strip()
    if not texto:
        return "Escríbeme el título de un libro y lo sigo por ti. /ayuda"
    orden = texto.split()[0].split("@")[0].lower()

    if orden in ("/start", "/ayuda", "/help"):
        return AYUDA
    if orden == "/lista":
        return texto_lista(lista)
    if orden.startswith("/quitar_"):
        rid = orden[len("/quitar_"):]
        rec = lista.quita(rid)
        return f"🗑 Ya no sigo «{escape(rec['titulo'])}»." if rec else "Ese libro no estaba en tu lista. /lista"
    if orden.startswith("/olvidar_"):
        b = lista.olvida(orden[len("/olvidar_"):])
        return f"🗑 Dejo de buscar «{escape(b['texto'])}»." if b else "No tenía esa búsqueda guardada. /lista"
    if orden.startswith("/s_"):
        rid = orden[3:]
        op = lista.opciones.get(rid, "")
        if isinstance(op, dict):
            lista.olvida(op.get("p", ""))   # ya ha elegido: no hace falta seguir buscando ese título
            op = op.get("b", "")
        bib = lista.biblioteca(op) or lista.cfg.activas[0]
        return _apunta(lista, bib, rid)
    if orden.startswith("/"):
        return "No conozco esa orden. /ayuda"

    if "http://" in texto or "https://" in texto or "ebiblio.es" in texto:
        return "No hace falta el enlace: escríbeme sólo el título del libro (y el autor, si quieres)."

    if normaliza(texto) in SALUDOS:
        return "¡Hola! 👋\n\n" + AYUDA

    # Título (y quizá autor): busco en el catálogo
    try:
        res = lista.busca(texto)
    except (ErrorDeRed, ErrorDeLectura) as e:
        return f"Ahora no puedo buscar en eBiblio ({escape(str(e))}). Escríbeme el título otra vez más tarde."
    if res.claros:
        return _sigue_varios(lista, res.claros)
    if res.dudosos:
        respuesta = _opciones(lista, res.dudosos, f"🔎 Hay varios libros titulados «{escape(texto)}». "
                                                  f"Toca el que quieras seguir:")
        if res.otro_formato:
            otros = "; ".join(
                f"«{escape(l.titulo)}»" + (f" de {escape(l.autores[0])}" if l.autores else "")
                for _, l in res.otro_formato[:3])
            respuesta += (f"\nTambién está {otros}, pero sólo en audiolibro, que no sigues. "
                          f"Si es ese el que buscas, escríbeme también el autor y lo buscaré cada día "
                          f"por si llega como libro electrónico.")
        return respuesta

    clave = lista.pendiente(texto)
    olvidar = f"Dejar de buscarlo: /olvidar_{clave}"
    if res.otro_formato:
        _, l = res.otro_formato[0]
        autor = f" de {escape(l.autores[0])}" if l.autores else ""
        formato = "audiolibro" if l.tipo == "audiolibro" else "libro electrónico"
        return (f"🎧 «{escape(l.titulo)}»{autor} sólo está en eBiblio como <b>{formato}</b>, "
                f"y ese formato no lo sigues.\n"
                f"Lo buscaré cada día por si llega en otro formato, y te avisaré.\n{olvidar}")
    if res.parecidos:
        for b, l in res.parecidos:
            lista.opciones[l.id] = {"b": b.id, "p": clave}
        return _opciones(lista, res.parecidos,
                         f"🔎 No encuentro exactamente «{escape(texto)}». ¿Es alguno de estos?") + (
            "\nSi no es ninguno, revisa cómo está escrito. Mientras, lo buscaré cada día por si llega.\n" + olvidar)
    return (f"🔎 «{escape(texto)}» aún no está en eBiblio.\n"
            f"Lo buscaré cada día y, cuando llegue, empezaré a seguirlo y te avisaré.\n"
            f"Si crees que sí está, revisa cómo está escrito el título.\n{olvidar}")


def _sigue_varios(lista: Lista, claros: list) -> str:
    if len(claros) == 1:
        bib, l = claros[0]
        return _apunta(lista, bib, l.id)
    try:
        recs = lista.sigue(claros)
    except (ErrorDeRed, ErrorDeLectura) as e:
        return f"Ahora no puedo abrir ese libro en eBiblio ({escape(str(e))}). Escríbeme el título otra vez más tarde."
    r0 = recs[0]
    lineas = [f"📌 <b>Sigo «{escape(r0['titulo'])}»</b> en sus {len(recs)} ediciones:"]
    for r in recs:
        fmt = "🎧 Audiolibro" if r.get("tipo") == "audiolibro" else f"📖 {escape((r.get('formato') or 'Libro').title())}"
        lineas.append(f'\n{fmt}{_donde(lista, r)}: <a href="{escape(r["url"])}">{escape(r["titulo"])}</a>\n{linea_estado(r)}\nQuitar: /quitar_{r["id"]}')
    if any(Estado(r["estado"]) is Estado.DISPONIBLE for r in recs):
        lineas.append("\n¡Hay una edición libre ahora mismo! Corre a cogerla.")
    else:
        lineas.append("\nTe aviso en cuanto alguna se pueda reservar o quede libre.")
    return "\n".join(lineas)


def atiende(t: Telegram, chat_id: str, lista: Lista, estado: dict) -> int:
    """Lee los mensajes pendientes del bot y contesta. Devuelve cuántos ha atendido."""
    tg = estado.setdefault("telegram", {"offset": 0})
    try:
        if tg.get("menu") != VERSION_MENU:
            fija_menu(t)
            tg["menu"] = VERSION_MENU
        updates = actualizaciones(t, tg.get("offset", 0))
    except ErrorTelegram as e:
        log.warning("No pude leer los mensajes del bot: %s", e)
        return 0
    atendidos = 0
    for u in updates:
        tg["offset"] = max(tg.get("offset", 0), u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue  # sólo atiendo a la dueña del bot
        texto = msg.get("text") or msg.get("caption") or ""
        try:
            respuesta = responde(lista, texto)
        except Exception as e:  # una respuesta fallida no debe tumbar la comprobación
            log.exception("Error atendiendo «%s»", texto)
            respuesta = f"Algo ha fallado al atender tu mensaje ({escape(type(e).__name__)}). Prueba otra vez más tarde."
        try:
            from .notify.mensajes import trocea
            for trozo in trocea(respuesta):
                t.texto(trozo)
        except ErrorTelegram as e:
            log.warning("No pude contestar: %s", e)
        atendidos += 1
    return atendidos
