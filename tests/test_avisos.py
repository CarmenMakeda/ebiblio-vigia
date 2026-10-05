from unittest import mock

from vigia.config import Avisos, Biblioteca, Comprobacion, Config, Intereses
from vigia.motor import Evento, Informe
from vigia.notify import correo
from vigia.notify.mensajes import Foto, Texto, redacta, trocea
from vigia.notify.telegram import ErrorTelegram, Telegram

BIB = Biblioteca("madrid", "eBiblio Madrid", "https://madrid.ebiblio.es")


def cfg(**avisos):
    return Config([BIB], Intereses(autores=["Carmen Mola"]), Avisos(**avisos), Comprobacion())


def rec(n, titulo, estado="sin_reservas", autores=("Alguien",), **extra):
    r = {"id": f"{n:024x}", "titulo": titulo, "autores": list(autores), "url": f"https://x/resources/{n}",
         "portada": f"https://img/{n}", "estado": estado, "tipo": "libro"}
    r.update(extra)
    return r


def test_novedades_con_portada_para_intereses_y_lista_para_el_resto():
    inf = Informe(BIB)
    inf.nuevos = [
        Evento("nuevo", rec(1, "Los Huérfanos <&>", autores=["Carmen Mola"]), "Novedades ficción", ["Carmen Mola"]),
        Evento("nuevo", rec(2, "Otro", "disponible", ejemplares=1), "Novedades ficción"),
        Evento("nuevo", rec(3, "Audio", "reservable", disponible_el="2026-12-07T23:16", tipo="audiolibro"), "Nuevos audiolibros"),
    ]
    piezas = redacta([inf], cfg(web="https://yo.github.io/vigia/"))
    assert isinstance(piezas[0], Foto) and "Los Huérfanos &lt;&amp;&gt;" in piezas[0].texto
    assert "Te interesa por: Carmen Mola" in piezas[0].texto
    lista = piezas[1].texto
    assert "2 novedades más en eBiblio Madrid" in lista
    assert "🟢 1 disponible ya · 🟡 1 reservable" in lista
    assert lista.index("Otro") < lista.index("Audio")
    assert "🎧" in lista and "tu web" in lista


def test_sin_portadas_y_lista_larga_se_resume_y_trocea():
    inf = Informe(BIB)
    inf.nuevos = [Evento("nuevo", rec(i, f"Libro número {i} con un título bastante largo"), "Novedades ficción") for i in range(300)]
    piezas = redacta([inf], cfg(portadas=False, max_lista=250))
    assert all(isinstance(p, Texto) and len(p.texto) <= 4000 for p in piezas)
    assert len(piezas) > 1
    assert "…y 50 más." in piezas[-1].texto


def test_trocea_respeta_limite():
    texto = "\n".join("x" * 100 for _ in range(100))
    assert all(len(t) <= 4000 for t in trocea(texto))
    assert sum(t.count("x") for t in trocea(texto)) == 100 * 100


def test_alertas_y_recuperacion():
    inf = Informe(BIB, alerta="No consigo leer eBiblio Madrid", error="HTTP 503")
    assert "⚠️" in redacta([inf], cfg())[0].texto
    assert "Vuelvo a leer" in redacta([Informe(BIB, recuperada=True)], cfg())[0].texto
    assert redacta([Informe(BIB)], cfg()) == []


def test_telegram_cae_a_texto_si_la_portada_falla():
    t = Telegram("TOKEN", "123", pausa=0)
    llamadas = []

    def falso(metodo, datos):
        llamadas.append(metodo)
        if metodo == "sendPhoto":
            raise ErrorTelegram("wrong file identifier")
        return {}

    with mock.patch.object(t, "_llama", side_effect=falso):
        t.envia([Foto("https://img/1", "<b>a</b>", "<b>a</b>"), Texto("hola")])
    assert llamadas == ["sendPhoto", "sendMessage", "sendMessage"]


def test_correo_asunto_y_html():
    inf = Informe(BIB)
    inf.nuevos = [Evento("nuevo", rec(1, "Los Huérfanos", autores=["Carmen Mola"]), "Novedades ficción", ["Carmen Mola"]),
                  Evento("nuevo", rec(2, "Otro"), "Novedades ficción")]
    assert correo.asunto([inf]) == "📚 eBiblio · 2 novedades · ⭐ 1 te interesa"
    html = correo.html([inf], cfg())
    assert "Los Huérfanos" in html and "Te interesa por Carmen Mola" in html
    assert correo.asunto([Informe(BIB)]) is None
