# 📚 Vigía eBiblio

Te avisa por Telegram (y si quieres, por email) en cuanto entran libros nuevos en eBiblio, y cuando un libro que te interesa se puede coger o reservar. Opcionalmente, mantiene también una página web con todas las novedades y su disponibilidad.

- **Cada hora, de 7:00 a 23:00**, mira las listas «Novedades ficción», «Novedades no ficción» y «Nuevos audiolibros».
- **Te manda la tanda nueva** con el estado de cada libro: 🟢 disponible ya, 🟡 se puede reservar (con fecha) o 🔴 sin reservas libres.
- **Tus autores y temas favoritos** llegan aparte, con portada y ⭐.
- **Tu lista «quiero leer»:** escríbele al bot el título de cualquier libro (sea novedad o no, esté ya en eBiblio o no) y te avisa 🔔 cuando se pueda reservar y 🟢 cuando esté libre.
- Si la web de eBiblio cambia o deja de responder, te avisa ⚠️ en vez de quedarse callado.
- Funciona gratis en GitHub, aunque tengas el ordenador apagado.

---

## Puesta en marcha (unos 20 minutos, se puede hacer desde el móvil)

### 1. El bot de Telegram

1. En Telegram, abre **@BotFather** y escríbele `/newbot`.
2. Ponle un nombre (por ejemplo, *Mi vigía eBiblio*) y un usuario que acabe en `bot` (por ejemplo, `vigia_carmen_bot`).
3. BotFather te dará un **token**, que se parece a `7412345678:AAH…`. Guárdalo: es la llave del bot.
4. Abre tu bot nuevo (BotFather te da el enlace) y pulsa **Iniciar**. Escríbele «hola». Esto es necesario para que pueda escribirte.

### 2. El repositorio en GitHub

1. Crea una cuenta gratuita en [github.com](https://github.com) si no la tienes.
2. Crea un repositorio nuevo llamado `ebiblio-vigia`, **público** (necesario para tener la web gratis) y vacío.
3. Sube el contenido de esta carpeta. Si tu cuenta de GitHub está conectada a Claude, Claude puede subirlo por ti.

### 3. Los secretos

En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**.

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token de BotFather |

Los secretos están cifrados: nadie los ve, aunque el repositorio sea público.

### 4. Averigua tu número de chat

1. Ve a la pestaña **Actions → Vigía eBiblio → Run workflow**, elige `probar` y pulsa **Run workflow**.
2. Saldrá en rojo, y es lo normal en este paso. Abre la ejecución, después el paso «Comprobar eBiblio», y verás `👉 Tu TELEGRAM_CHAT_ID es: 123456789`.
3. Crea otro secreto llamado `TELEGRAM_CHAT_ID` con ese número.
4. Vuelve a lanzar `probar`. Te llegará un mensaje de prueba al bot. ✅

### 5. La web (opcional)

Sólo si quieres una página con todas las novedades y filtros. Requiere que el repositorio sea público.

1. **Settings → Pages → Build and deployment → Source:** elige **GitHub Actions**.
2. **Settings → Secrets and variables → Actions → pestaña Variables → New repository variable:** nombre `PUBLICAR_WEB`, valor `true`.

La web quedará en `https://TU-USUARIO.github.io/ebiblio-vigia/`. Guárdala en la pantalla de inicio del móvil y se abrirá como una app.

### 6. Primera comprobación

**Actions → Vigía eBiblio → Run workflow → `comprobar`.** La primera vez no te manda la lista de los cientos de libros que ya hay: los registra en silencio y te escribe «✅ Vigía activado», con los que ya encajan con tus intereses. A partir de ahí trabaja solo cada hora.

---

## Tus intereses

Edita `config.yaml` desde GitHub (abre el archivo y pulsa el lápiz ✏️):

```yaml
intereses:
  autores:
    - Carmen Mola
    - Rosa Montero
  palabras:
    - novela negra
  excluir:
    - infantil
```

- **autores**: da igual el orden de nombre y apellido, las mayúsculas o las tildes.
- **palabras**: se buscan en el título y la sinopsis.
- **excluir**: libros que no quieres ver en los avisos.

En la misma sección `avisos` puedes elegir que te avise de **todas** las novedades o **sólo de las que te interesan**.

Al guardar, se ejecuta una prueba automática que comprueba que el archivo es correcto. Si te llega un email de GitHub con «Pruebas ❌», es que hay un error de formato. Lo más habitual es una sangría mal puesta: cada guion va con dos espacios delante.

## Tu lista «quiero leer»

En Telegram, escríbele al bot el **título** del libro (y el autor, si quieres afinar):

| Caso | Qué hace |
|---|---|
| El título corresponde a un solo libro | Lo sigue directamente, en todas sus ediciones (EPUB, audiolibro…), y te dice cómo está ahora |
| Hay varios libros con ese título de autores distintos | Te pregunta cuál es; toca `/s_…` en el tuyo (o escribe también el autor) |
| El título **aún no está** en eBiblio | Lo busca cada día; cuando llega, empieza a seguirlo y te avisa |
| `/lista` | Lo que sigue para ti, cómo está cada libro y los títulos que aún busca |
| `/quitar_…` o `/olvidar_…` | Deja de seguir un libro o de buscar un título (vienen en cada mensaje) |

Te avisa 🔔 cuando un libro de tu lista se pueda reservar y 🟢 cuando esté libre.

El bot lee tus mensajes en cada comprobación (cada hora), así que **puede tardar un rato en contestar**. Sólo hace caso a tu chat: si otra persona le escribe, la ignora.

## Varias bibliotecas: Madrid, Castilla y León, Andalucía

En `config.yaml` cambia `activa: false` por `activa: true` en la biblioteca que quieras. Todas usan la misma plataforma. La primera comprobación de cada biblioteca nueva vuelve a ser silenciosa.

Cada biblioteca organiza sus novedades a su manera:

- **Madrid** tiene listas «Novedades ficción» y «Novedades no ficción» en la portada. Se vigilan con `secciones:`.
- **Castilla y León** y **Andalucía** no tienen listas de novedades. Se vigila su catálogo ordenado por «Adquisiciones recientes» con `consultas:` (ver `config.yaml`): `filtro` es lo que va detrás de `?` en la dirección del catálogo y `paginas`, cuántas páginas de 40 libros cuentan como novedad.

Con más de una biblioteca, la lista «quiero leer» busca cada título en todas y te dice en cuál está cada ejemplar.

Con `infantil: false` (en `intereses:`) no te avisa de libros infantiles ni juveniles en ninguna biblioteca. Los reconoce porque el catálogo de eBiblio los marca con «Público: Infantil/Juvenil». Tu lista «quiero leer» no se ve afectada: si sigues un libro infantil, te avisa igual.

---

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| ⚠️ «No consigo leer eBiblio Madrid…» con `HTTP 403` | eBiblio está bloqueando las peticiones que vienen de GitHub. Usa el plan B de abajo. |
| ⚠️ con `HTTP 5xx` o `Timeout` | La web de eBiblio está caída. Cuando vuelva te llegará «✅ Vuelvo a leer…». |
| ⚠️ «Todas las secciones aparecen vacías» | eBiblio ha cambiado su web. Hay que adaptar `vigia/parse.py`. |
| ℹ️ «No encuentro la sección…» | La biblioteca ha renombrado una lista. Corrige el nombre en `config.yaml`, o deja `secciones:` vacío para que las busque solo. |
| No llega nada y en Actions todo sale en verde | Comprueba los secretos con `probar`. |
| GitHub dice que ha desactivado el workflow | Pasa si un repositorio pasa 60 días sin actividad. Entra en Actions y pulsa *Enable workflow*. |

En cada ejecución, la página de Actions muestra un resumen con lo que ha encontrado.

### Plan B: ejecutarlo en casa

Si eBiblio bloqueara a GitHub, el mismo programa funciona en cualquier ordenador con Python 3.10 o superior, o en una Raspberry Pi:

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=…  TELEGRAM_CHAT_ID=…
python -m vigia comprobar
```

Prográmalo cada hora con el Programador de tareas de Windows, o con `cron` en Linux o Raspberry Pi.

---

## Cómo funciona por dentro

- Lee las listas «Novedades» de la web pública, como haría una persona. **No necesita tu carné ni tu contraseña.**
- Cada hora lee sólo la primera o las dos primeras páginas de cada lista, porque los libros nuevos aparecen arriba: son unas 3 peticiones cada vez. Una vez al día repasa las listas enteras, unas 30 peticiones con pausas, para actualizar estados y retirar lo que ya no está.
- Una vez al día también relee la portada. Si la biblioteca sustituye una lista por otra nueva (por ejemplo, al empezar el año), la encuentra sola.
- La fecha de llegada de cada libro va codificada en su identificador de eBiblio. Por eso la web puede agrupar los libros por tandas desde el primer día.
- La memoria del vigía se guarda en `data/estado.json`, dentro del propio repositorio.

Una nota de cortesía: el archivo `robots.txt` de eBiblio pide a los programas automáticos que no recorran la web. Este vigía la consulta con la frecuencia de una persona que entra a mirar varias veces al día, y se identifica como herramienta de uso personal. Mantén las pausas y la frecuencia tal como están.

| Carpeta | Qué hay |
|---|---|
| `vigia/` | el programa (lector, motor, avisos y web) |
| `config.yaml` | tu configuración |
| `data/estado.json` | la memoria del vigía |
| `.github/workflows/` | las tareas automáticas de GitHub |
| `tests/` | pruebas, con páginas reales de eBiblio |
