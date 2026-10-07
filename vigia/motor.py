"""El motor: lee las secciones, compara con lo que recuerda y produce eventos."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import estado as est
from .config import PATRON_AUTO, Biblioteca, Config
from .fetch import Cliente, ErrorDeRed, NoEncontrado
from .intereses import excluido, motivos
from .models import Estado, Libro
from .parse import ErrorDeLectura, lee_seccion, lee_secciones_portada, normaliza

log = logging.getLogger(__name__)

HORAS_REDESCUBRIR = 20
HORAS_ENFRIAMIENTO_LIBERADO = 12
MAX_HISTORIAL = 300
# Libros infantiles/juveniles: el catálogo los marca con «Público: Infantil/Juvenil».
# Las listas de novedades no lo dicen, así que se lee aparte esa parte del catálogo
# (por fecha de llegada) y se recuerdan sus ids.
URL_INFANTIL = "/resources?nature=ebook&audience=youth&sort_by=created_at_desc"
PAGINAS_INFANTIL = 5
MAX_INFANTIL = 3000


@dataclass
class Evento:
    tipo: str               # "nuevo" | "liberado"
    libro: dict             # registro guardado del libro
    seccion: str
    motivos: list[str] = field(default_factory=list)
    anterior: str | None = None

    @property
    def interesa(self) -> bool:
        return bool(self.motivos)


@dataclass
class Informe:
    biblioteca: Biblioteca
    inicial: bool = False
    nuevos: list[Evento] = field(default_factory=list)
    liberados: list[Evento] = field(default_factory=list)
    total_activos: int = 0
    secciones: list[str] = field(default_factory=list)
    error: str | None = None
    alerta: str | None = None       # texto de alerta de salud a enviar
    recuperada: bool = False
    advertencias: list[str] = field(default_factory=list)
    peticiones: int = 0
    completa: bool = False

    @property
    def hay_algo(self) -> bool:
        return bool(self.nuevos or self.liberados or self.alerta or self.recuperada or self.inicial)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _de_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _seccion_elegida(nombre: str, filtros: list[str]) -> bool:
    n = normaliza(nombre)
    if not filtros:
        return re.search(PATRON_AUTO, n) is not None
    for f in filtros:
        if f.startswith("re:"):
            if re.search(f[3:], n, re.I):
                return True
        elif normaliza(f) == n:
            return True
    return False


class Vigia:
    def __init__(self, config: Config, estado: dict, cliente: Cliente | None = None,
                 ahora: Callable[[], datetime] | None = None):
        self.cfg = config
        self.estado = estado
        c = config.comprobacion
        self.cliente = cliente or Cliente(c.user_agent, pausa=c.pausa_segundos)
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))
        self.seguidos: set[str] = set()          # «bib:id» de la lista quiero leer (se avisan aparte)
        self.vistos: dict[str, Libro] = {}       # libros leídos en esta vuelta, para no repetir peticiones
        self.infantiles: set[str] = set()        # ids de libros infantiles de la biblioteca en curso

    # ------------------------------------------------------------------ público
    def comprueba(self, biblioteca: Biblioteca, forzar_completa: bool = False) -> Informe:
        b = est.biblioteca(self.estado, biblioteca.id)
        inf = Informe(biblioteca=biblioteca, inicial=not b["inicializada"])
        peticiones_antes = self.cliente.peticiones
        ahora = self.ahora()
        try:
            ultima = _de_iso(b["ultima_completa"])
            completa = (
                forzar_completa
                or not b["inicializada"]
                or ultima is None
                or ahora - ultima >= timedelta(hours=self.cfg.comprobacion.sincronizacion_completa_horas)
            )
            inf.completa = completa
            self.infantiles = self._infantiles(biblioteca, b)
            hechas: list[str] = []
            for intento in range(2):
                secciones = self._secciones(biblioteca, b, ahora, inf, forzar=intento > 0)
                perdida = False
                for bundle, nombre in secciones:
                    if bundle in hechas:
                        continue
                    try:
                        self._procesa_seccion(biblioteca, b, bundle, nombre, completa, ahora, inf)
                        hechas.append(bundle)
                    except NoEncontrado:
                        log.warning("La sección %s (%s) ya no existe; vuelvo a buscar secciones", nombre, bundle)
                        b["secciones"][bundle]["vigilada"] = False
                        perdida = True
                if not perdida:
                    break
            if not hechas:
                raise ErrorDeLectura("No he podido leer ninguna sección de novedades")
            vacias = [b["secciones"][x]["nombre"] for x in hechas if b["secciones"][x].get("ultimos", 0) == 0]
            if len(vacias) == len(hechas):
                raise ErrorDeLectura("Todas las secciones aparecen vacías: puede que la web haya cambiado")
            inf.secciones = [b["secciones"][x]["nombre"] for x in hechas]
            # Libros que sólo estaban en secciones que ya no vigilas: dejan de contarse
            vigentes = {k for k, v in b["secciones"].items() if v.get("vigilada")}
            for rec in b["libros"].values():
                if rec.get("activo") and not set(rec.get("secciones", [])) & vigentes:
                    rec["activo"] = False
                    rec["retirado"] = _iso(ahora)
            if completa:
                b["ultima_completa"] = _iso(ahora)
            b["inicializada"] = True
            self._salud_ok(b, inf, ahora)
        except (ErrorDeRed, ErrorDeLectura) as e:
            self._salud_fallo(b, inf, str(e))
        inf.total_activos = sum(1 for r in b["libros"].values() if r.get("activo"))
        inf.peticiones = self.cliente.peticiones - peticiones_antes
        self._historial(inf, ahora)
        return inf

    # ------------------------------------------------------------- infantiles
    def _infantiles(self, bib: Biblioteca, b: dict) -> set[str]:
        """Ids de los libros infantiles/juveniles, para no avisar de ellos (si así se ha pedido)."""
        if self.cfg.intereses.infantil:
            return set()
        guardados: list[str] = b.setdefault("infantil", [])
        conocidos = set(guardados)
        try:
            for pagina in range(1, PAGINAS_INFANTIL + 1):
                url = bib.url + URL_INFANTIL + (f"&page={pagina}" if pagina > 1 else "")
                pag = lee_seccion(self.cliente.get(url), bib.url)
                ids = [l.id for l in pag.libros]
                ya = any(i in conocidos for i in ids)
                for i in ids:
                    if i not in conocidos:
                        conocidos.add(i)
                        guardados.append(i)
                # Por orden de llegada: en cuanto aparece uno conocido, lo demás ya se tiene
                if ya or not pag.hay_siguiente:
                    break
        except (ErrorDeRed, ErrorDeLectura) as e:
            log.warning("No pude leer los libros infantiles de %s (%s); uso los ya conocidos", bib.nombre, e)
        del guardados[:-MAX_INFANTIL]
        return set(guardados)

    # ------------------------------------------------------------- secciones
    def _secciones(self, bib: Biblioteca, b: dict, ahora: datetime, inf: Informe, forzar: bool) -> list[tuple[str, str]]:
        """Listas a vigilar: (clave, nombre). La clave es el id del bundle o «q:<filtro>»."""
        elegidas = self._bundles(bib, b, ahora, inf, forzar) if bib.usa_portada else []
        if not bib.usa_portada:
            for k, v in b["secciones"].items():
                if not k.startswith("q:"):
                    v["vigilada"] = False
        claves = {c.clave for c in bib.consultas}
        for k, v in b["secciones"].items():
            if k.startswith("q:") and k not in claves:
                v["vigilada"] = False
        for c in bib.consultas:
            sec = b["secciones"].setdefault(c.clave, {"sincronizada": False})
            sec.update(nombre=c.nombre, vigilada=True)
            elegidas.append((c.clave, c.nombre))
        return elegidas

    def _bundles(self, bib: Biblioteca, b: dict, ahora: datetime, inf: Informe, forzar: bool) -> list[tuple[str, str]]:
        # Las ya conocidas, filtradas por lo que diga config.yaml ahora (por si lo has cambiado)
        vigiladas = [(k, v["nombre"]) for k, v in b["secciones"].items()
                     if not k.startswith("q:") and v.get("vigilada") and _seccion_elegida(v["nombre"], bib.secciones)]
        conocidas = {normaliza(n) for _, n in vigiladas}
        falta_alguna = any(not f.startswith("re:") and normaliza(f) not in conocidas for f in bib.secciones)
        ultima = _de_iso(b["descubierto"])
        if (not forzar and vigiladas and not falta_alguna and ultima
                and ahora - ultima < timedelta(hours=HORAS_REDESCUBRIR)):
            for k, v in b["secciones"].items():
                if not k.startswith("q:"):
                    v["vigilada"] = any(k == x for x, _ in vigiladas)
            return vigiladas
        try:
            todas = lee_secciones_portada(self.cliente.get(bib.url + "/"))
        except (ErrorDeRed, ErrorDeLectura) as e:
            if vigiladas:
                log.warning("No puedo releer la portada (%s); uso las secciones conocidas", e)
                return vigiladas
            raise
        elegidas = [s for s in todas if _seccion_elegida(s.nombre, bib.secciones)]
        for f in bib.secciones:
            if not f.startswith("re:") and not any(normaliza(f) == normaliza(s.nombre) for s in todas):
                inf.advertencias.append(f"No encuentro la sección «{f}» en la portada de {bib.nombre}")
        if not elegidas:
            raise ErrorDeLectura(f"No encuentro secciones de novedades en la portada de {bib.nombre}")
        ids = {s.bundle for s in elegidas}
        for k, v in b["secciones"].items():
            if not k.startswith("q:"):
                v["vigilada"] = k in ids
        for s in elegidas:
            sec = b["secciones"].setdefault(s.bundle, {"sincronizada": False})
            sec.update(nombre=s.nombre, total_web=s.total, vigilada=True)
        b["descubierto"] = _iso(ahora)
        return [(s.bundle, s.nombre) for s in elegidas]

    def _lee_paginas(self, bib: Biblioteca, bundle: str, leer_todo: bool, conocidos: set[str]) -> tuple[list[Libro], bool]:
        c = self.cfg.comprobacion
        consulta = bib.consulta(bundle)
        maximo = consulta.paginas if consulta else c.paginas_max
        libros: dict[str, Libro] = {}
        pagina, terminado = 1, False
        while pagina <= maximo:
            if consulta:
                url = consulta.url(bib.url, pagina)
            else:
                url = f"{bib.url}/bundles/{bundle}" + (f"?page={pagina}" if pagina > 1 else "")
            pag = lee_seccion(self.cliente.get(url), bib.url)
            for l in pag.libros:
                libros.setdefault(l.id, l)
            # En una consulta, la «lista» son sus primeras páginas: llegar al final de ellas
            # es haberla leído entera (lo que queda detrás ya no cuenta como novedad).
            if not pag.hay_siguiente or (consulta and pagina >= maximo):
                terminado = True
                break
            todos_nuevos = pag.libros and all(l.id not in conocidos for l in pag.libros)
            if not leer_todo and pagina >= c.paginas_estado and not todos_nuevos:
                break
            pagina += 1
        return list(libros.values()), terminado

    def _procesa_seccion(self, bib: Biblioteca, b: dict, bundle: str, nombre: str,
                         completa: bool, ahora: datetime, inf: Informe) -> None:
        sec = b["secciones"].setdefault(bundle, {"nombre": nombre, "sincronizada": False, "vigilada": True})
        leer_todo = completa or not sec.get("sincronizada")
        silencioso = not b["inicializada"]
        seccion_nueva = b["inicializada"] and not sec.get("sincronizada")
        conocidos = set(b["libros"])
        libros, terminado = self._lee_paginas(bib, bundle, leer_todo, conocidos)
        log.info("%s · %s: %d libros leídos%s", bib.nombre, nombre, len(libros), " (completa)" if leer_todo else "")

        for libro in libros:
            self._registra(b, libro, bundle, nombre, ahora, silencioso, seccion_nueva, inf)

        if leer_todo and terminado:
            vistos = {l.id for l in libros}
            for rid, rec in b["libros"].items():
                if bundle in rec.get("secciones", []) and rid not in vistos:
                    rec["secciones"].remove(bundle)
                    if not rec["secciones"]:
                        rec["activo"] = False
                        rec["retirado"] = _iso(ahora)
        sec.update(sincronizada=True, ultima_lectura=_iso(ahora), ultimos=len(libros))

    # ------------------------------------------------------------------ libros
    def _registra(self, b: dict, libro: Libro, bundle: str, nombre: str, ahora: datetime,
                  silencioso: bool, seccion_nueva: bool, inf: Informe) -> None:
        cfg = self.cfg
        clave_lista = f"{b['_id']}:{libro.id}"
        self.vistos[clave_lista] = libro
        mot = motivos(libro, cfg.intereses)
        exc = (excluido(libro, cfg.intereses) and not any(a in mot for a in cfg.intereses.autores)) \
            or libro.id in self.infantiles
        rec = b["libros"].get(libro.id)
        alta = libro.alta
        if rec is None:
            rec = libro.a_dict()
            rec.update(
                secciones=[bundle], activo=True, visto_primero=_iso(ahora), visto_ultimo=_iso(ahora)[:10],
                cambio_estado=_iso(ahora), alta=_iso(alta) if alta else None,
                intereses=mot, excluido=exc, base=silencioso,
            )
            b["libros"][libro.id] = rec
            reciente = alta is not None and ahora - alta <= timedelta(days=cfg.comprobacion.dias_seccion_nueva)
            if silencioso or (seccion_nueva and not reciente):
                rec["base"] = True
                return
            if exc:
                return
            if cfg.avisos.novedades == "todas" or (cfg.avisos.novedades == "intereses" and mot):
                inf.nuevos.append(Evento("nuevo", rec, nombre, mot))
            return

        anterior = Estado(rec.get("estado", Estado.DESCONOCIDO.value))
        nuevo = libro.a_dict()
        if not nuevo["sinopsis"]:
            nuevo.pop("sinopsis")
        rec.update(nuevo)
        # Sólo el día: así el archivo de estado apenas cambia de una hora a otra.
        rec.update(visto_ultimo=_iso(ahora)[:10], activo=True, intereses=mot, excluido=exc)
        rec.pop("retirado", None)
        if bundle not in rec.setdefault("secciones", []):
            rec["secciones"].append(bundle)
        if libro.estado is anterior:
            return
        rec["cambio_estado"] = _iso(ahora)
        rec["estado_anterior"] = anterior.value
        mejora = libro.estado.rango > anterior.rango and libro.estado in (Estado.DISPONIBLE, Estado.RESERVABLE)
        if not mejora or silencioso or exc or clave_lista in self.seguidos:
            return
        modo = cfg.avisos.liberados
        if modo == "ninguna" or (modo == "intereses" and not mot):
            return
        clave = f"liberado_{libro.estado.value}"
        ultimo = _de_iso(rec.get(clave))
        if ultimo and ahora - ultimo < timedelta(hours=HORAS_ENFRIAMIENTO_LIBERADO):
            return
        rec[clave] = _iso(ahora)
        inf.liberados.append(Evento("liberado", rec, nombre, mot, anterior.value))

    # ------------------------------------------------------------------- salud
    def _salud_ok(self, b: dict, inf: Informe, ahora: datetime) -> None:
        s = b["salud"]
        if s.get("alertado"):
            inf.recuperada = True
        s.update(fallos=0, ultimo_error="", alertado=False, ultima_ok=_iso(ahora))

    def _salud_fallo(self, b: dict, inf: Informe, error: str) -> None:
        log.error("%s: %s", inf.biblioteca.nombre, error)
        s = b["salud"]
        s["fallos"] = s.get("fallos", 0) + 1
        s["ultimo_error"] = error
        inf.error = error
        if s["fallos"] >= self.cfg.avisos.fallos_para_alertar and not s.get("alertado"):
            s["alertado"] = True
            inf.alerta = (
                f"No consigo leer {inf.biblioteca.nombre} desde hace {s['fallos']} comprobaciones seguidas. "
                f"Último error: {error}"
            )

    def _historial(self, inf: Informe, ahora: datetime) -> None:
        if not (inf.nuevos or inf.liberados):
            return
        h = self.estado.setdefault("historial", [])
        h.append({
            "fecha": _iso(ahora),
            "biblioteca": inf.biblioteca.id,
            "nuevos": len(inf.nuevos),
            "liberados": len(inf.liberados),
            "ejemplos": [e.libro["titulo"] for e in (inf.nuevos + inf.liberados)[:5]],
        })
        del h[:-MAX_HISTORIAL]
