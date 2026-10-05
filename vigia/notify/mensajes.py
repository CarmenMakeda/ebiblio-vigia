"""Redacta los avisos (independiente del canal: Telegram, email o consola)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from ..config import Config
from ..models import Estado
from ..motor import Evento, Informe

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
LIMITE_TELEGRAM = 4000
LIMITE_PIE_FOTO = 1000


@dataclass
class Foto:
    url: str
    texto: str       # HTML de Telegram
    respaldo: str    # texto si la foto falla


@dataclass
class Texto:
    texto: str


def fecha_corta(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", ""))
    except ValueError:
        return ""
    return f"{d.day} {MESES[d.month - 1]}"


def linea_estado(rec: dict) -> str:
    e = Estado(rec.get("estado", "desconocido"))
    extra = ""
    if e is Estado.DISPONIBLE and rec.get("ejemplares"):
        n = rec["ejemplares"]
        extra = f" · {n} ejemplar{'es' if n != 1 else ''}"
    elif e is Estado.RESERVABLE and rec.get("disponible_el"):
        extra = f" · te llegaría hacia el {fecha_corta(rec['disponible_el'])}"
    return f"{e.emoji} {e.etiqueta}{extra}"


def autores(rec: dict) -> str:
    a = rec.get("autores") or []
    return ", ".join(a[:3]) + (" y otros" if len(a) > 3 else "")


def _ficha(ev: Evento, bib_nombre: str, cabecera: str) -> str:
    r = ev.libro
    titulo = r["titulo"] if len(r["titulo"]) <= 150 else r["titulo"][:147] + "…"
    partes = [cabecera, f"<b>{escape(titulo)}</b>"]
    if r.get("autores"):
        partes.append(f"✍️ {escape(autores(r))}")
    partes.append(linea_estado(r))
    tipo = "🎧 Audiolibro · " if r.get("tipo") == "audiolibro" else ""
    partes.append(f"{tipo}📚 {escape(ev.seccion)} · {escape(bib_nombre)}")
    if ev.motivos:
        partes.append(f"💡 Te interesa por: {escape(', '.join(ev.motivos[:5]))}")
    partes.append(f'<a href="{escape(r["url"])}">Abrir en eBiblio →</a>')
    return "\n".join(partes)


def _orden(ev: Evento):
    e = Estado(ev.libro.get("estado", "desconocido"))
    return (-e.rango, ev.libro["titulo"].lower())


def _lista(eventos: list[Evento], cfg: Config) -> list[str]:
    lineas: list[str] = []
    por_seccion: dict[str, list[Evento]] = {}
    for ev in eventos:
        por_seccion.setdefault(ev.seccion, []).append(ev)
    mostrados = 0
    for seccion, evs in por_seccion.items():
        lineas.append(f"\n<b>{escape(seccion)}</b> ({len(evs)})")
        for ev in sorted(evs, key=_orden):
            if mostrados >= cfg.avisos.max_lista:
                break
            r = ev.libro
            e = Estado(r.get("estado", "desconocido"))
            estrella = "⭐ " if ev.motivos else ""
            audio = "🎧 " if r.get("tipo") == "audiolibro" else ""
            a = f" — <i>{escape(autores(r))}</i>" if r.get("autores") else ""
            lineas.append(f'{e.emoji} {estrella}{audio}<a href="{escape(r["url"])}">{escape(r["titulo"])}</a>{a}')
            mostrados += 1
    resto = len(eventos) - mostrados
    if resto > 0:
        lineas.append(f"\n…y {resto} más.")
    return lineas


def _resumen_estados(eventos: list[Evento]) -> str:
    cuenta = {e: 0 for e in (Estado.DISPONIBLE, Estado.RESERVABLE, Estado.SIN_RESERVAS)}
    for ev in eventos:
        e = Estado(ev.libro.get("estado", "desconocido"))
        if e in cuenta:
            cuenta[e] += 1
    nombres = {
        Estado.DISPONIBLE: ("disponible ya", "disponibles ya"),
        Estado.RESERVABLE: ("reservable", "reservables"),
        Estado.SIN_RESERVAS: ("sin reservas", "sin reservas"),
    }
    return " · ".join(f"{e.emoji} {n} {nombres[e][n != 1]}" for e, n in cuenta.items() if n)


def _web(cfg: Config) -> str:
    return f'\n\n<a href="{escape(cfg.avisos.web)}">Ver todas las novedades en tu web →</a>' if cfg.avisos.web else ""


def trocea(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Parte un mensaje largo por saltos de línea respetando el límite de Telegram."""
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite and actual:
            trozos.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    if actual.strip():
        trozos.append(actual)
    return trozos


def redacta(informes: list[Informe], cfg: Config, estado: dict | None = None) -> list[Foto | Texto]:
    piezas: list[Foto | Texto] = []
    for inf in informes:
        nombre = inf.biblioteca.nombre

        if inf.alerta:
            piezas.append(Texto(
                f"⚠️ <b>Vigía eBiblio · {escape(nombre)}</b>\n{escape(inf.alerta)}\n\n"
                "Si se repite, puede que la web haya cambiado o esté bloqueando el acceso automático. "
                "Mira el apartado «Si algo falla» de la guía."
            ))
        if inf.recuperada:
            piezas.append(Texto(f"✅ Vuelvo a leer <b>{escape(nombre)}</b> con normalidad."))

        if inf.inicial and not inf.error:
            texto = (
                f"✅ <b>Vigía eBiblio activado · {escape(nombre)}</b>\n"
                f"Vigilo {len(inf.secciones)} secciones: {escape(', '.join(inf.secciones))}.\n"
                f"He registrado {inf.total_activos} títulos. A partir de ahora te aviso de cada novedad."
            )
            if estado is not None:
                libros = estado["bibliotecas"][inf.biblioteca.id]["libros"].values()
                fav = [r for r in libros if r.get("activo") and r.get("intereses")]
                if fav:
                    texto += f"\n\n⭐ Ya hay {len(fav)} títulos que encajan con tus intereses:"
                    for r in sorted(fav, key=lambda r: -Estado(r["estado"]).rango)[:15]:
                        texto += f'\n{Estado(r["estado"]).emoji} <a href="{escape(r["url"])}">{escape(r["titulo"])}</a> — <i>{escape(autores(r))}</i>'
            piezas.append(Texto(texto + _web(cfg)))

        for adv in inf.advertencias:
            piezas.append(Texto(f"ℹ️ {escape(adv)}. Revisa los nombres de secciones en config.yaml."))

        for ev in inf.liberados:
            e = Estado(ev.libro["estado"])
            cab = "🔔 <b>¡Ya lo puedes coger!</b>" if e is Estado.DISPONIBLE else "🔔 <b>Ya se puede reservar</b>"
            ficha = _ficha(ev, nombre, cab)
            piezas.append(Foto(ev.libro["portada"], ficha, ficha) if cfg.avisos.portadas and ev.libro.get("portada") else Texto(ficha))

        if inf.nuevos:
            destacados = [ev for ev in inf.nuevos if ev.motivos]
            con_foto = destacados[:10] if cfg.avisos.portadas else []
            for ev in con_foto:
                ficha = _ficha(ev, nombre, "⭐ <b>Novedad que te interesa</b>")
                piezas.append(Foto(ev.libro["portada"], ficha, ficha) if ev.libro.get("portada") else Texto(ficha))
            resto = [ev for ev in inf.nuevos if ev not in con_foto]
            if resto:
                n = len(resto)
                mas = " más" if con_foto else ""
                cab = f"📚 <b>{n} novedad{'es' if n != 1 else ''}{mas} en {escape(nombre)}</b>"
                cab += f"\n{_resumen_estados(resto)}"
                cuerpo = "\n".join([cab] + _lista(resto, cfg)) + _web(cfg)
                piezas.extend(Texto(t) for t in trocea(cuerpo))
    return piezas
