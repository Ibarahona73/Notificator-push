# Notificador de tickets

Revisa automáticamente un portal de tickets cada 10 minutos y envía los tickets pendientes a **Telegram** mediante un bot propio. Corre gratis en GitHub Actions, sin servidor propio.

## Cómo funciona

1. **GitHub Actions** ejecuta `monitor.py` cada 10 minutos (o a mano con el botón *Run workflow*).
2. El script abre un Chromium sin interfaz con **Playwright**, inicia sesión en el portal (`/login`) y entra a `/tickets/all-tickets`.
3. Lee la tabla de tickets: título, tipo, solicitante, fecha, proyecto, cliente, prioridad y estado.
4. Aplica los filtros:
   - `FILTRO`: solo conserva los tickets cuyo proyecto o cliente contenga alguna de las palabras indicadas.
   - `ESTADOS_EXCLUIDOS`: descarta los tickets en esos estados (por defecto `Solucionado`).
5. Abre el detalle de cada ticket pendiente para obtener su **ID**, **fecha de creación** y **descripción**.
6. Compara contra los tickets ya vistos (`state/seen.json`) para detectar los **nuevos**.
7. Envía el resumen al chat de Telegram:
   - Encabezado: `Cliente: N pendientes (X alta)`.
   - Los tickets nuevos aparecen primero, marcados con 🆕, seguidos de los de prioridad alta.
   - Cada ticket muestra título, ID, proyecto, estado, prioridad, tipo, solicitante, fecha y descripción (hasta 2500 caracteres).
   - Si el resumen supera el límite de Telegram, se divide en varios mensajes.
   - Solo suena la notificación cuando hay tickets nuevos; si no, el mensaje llega en silencio.
8. Guarda el estado en la caché de GitHub Actions para la siguiente ejecución.

En la primera ejecución no se marca ningún ticket como nuevo; solo se registra lo existente.

## Estructura

```
.
├── monitor.py                         # Script principal
├── requirements.txt                   # playwright, requests
├── .github/workflows/notificador.yml  # Programación en GitHub Actions
└── state/seen.json                    # Tickets ya vistos (se genera solo)
```

## Configuración

### 1. Crear el bot de Telegram

1. En Telegram, abre un chat con [@BotFather](https://t.me/BotFather) (el oficial, con la marca de verificación).
2. Envía `/newbot`.
3. Escribe el **nombre** visible del bot, por ejemplo `Notificador de Tickets`.
4. Escribe el **usuario** del bot; debe terminar en `bot`, por ejemplo `tickets_ivan_bot`.
5. BotFather responde con el **token**, algo como `123456789:AAH...`. Ese es el valor de `TELEGRAM_TOKEN`. Guárdalo y no lo compartas: quien lo tenga puede controlar el bot.

Si el token se filtra, genera uno nuevo con `/revoke` en BotFather.

### 2. Obtener el chat ID

**Chat privado (los avisos te llegan solo a ti):**

1. Busca tu bot por su usuario, ábrelo y pulsa **Iniciar** (o envía cualquier mensaje). Un bot no puede escribirte si tú no le escribiste primero.
2. Abre en el navegador, reemplazando `<TOKEN>`:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

3. En la respuesta busca `"chat":{"id":123456789,...}`. Ese número es el `TELEGRAM_CHAT_ID`.

Si la respuesta sale vacía (`"result":[]`), envíale otro mensaje al bot y recarga la página.

**Grupo (para que lo reciba un equipo):**

1. Crea el grupo y agrega el bot como miembro.
2. Escribe un mensaje en el grupo, por ejemplo `/start@tickets_ivan_bot`.
3. Abre la misma URL de `getUpdates`. El ID del grupo es **negativo**, por ejemplo `-1001234567890`; cópialo con el signo.

Si `getUpdates` no muestra los mensajes del grupo, desactiva el modo privacidad del bot en BotFather (`/setprivacy` → elige el bot → `Disable`) y vuelve a escribir en el grupo.

### 3. Probar el bot

Antes de configurar GitHub, confirma que el bot puede escribirte:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d chat_id=<CHAT_ID> \
  -d text="Prueba del notificador"
```

Si responde `"ok":true` y te llega el mensaje, el token y el chat ID son correctos.

### 4. Secrets del repositorio

En GitHub ve a **Settings → Secrets and variables → Actions → New repository secret** y crea:

| Secret              | Obligatorio | Descripción                                                    |
| ------------------- | ----------- | -------------------------------------------------------------- |
| `PORTAL_URL`        | Sí          | Dirección del portal, ej. `https://tu-portal.com`              |
| `PORTAL_EMAIL`      | Sí          | Correo de acceso al portal                                     |
| `PORTAL_PASSWORD`   | Sí          | Contraseña del portal                                          |
| `TELEGRAM_TOKEN`    | Sí          | Token del bot que dio BotFather                                |
| `TELEGRAM_CHAT_ID`  | Sí          | ID del chat o grupo que recibe los avisos                      |
| `FILTRO`            | No          | Palabras separadas por coma. Vacío = todos los tickets         |
| `ESTADOS_EXCLUIDOS` | No          | Estados a ignorar, separados por coma. Vacío = `Solucionado`   |

### 5. Activarlo

Sube el repositorio a GitHub. El workflow empieza a correr solo según el cron. Para probarlo de inmediato: pestaña **Actions → Notificador de tickets → Run workflow**.

> GitHub puede retrasar los cron unos minutos y desactiva los workflows programados en repositorios sin actividad durante 60 días.

## Ejecución local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

export PORTAL_URL="https://tu-portal.com"
export PORTAL_EMAIL="tu-correo@ejemplo.com"
export PORTAL_PASSWORD="tu-contraseña"
export TELEGRAM_TOKEN="123456789:AAH..."
export TELEGRAM_CHAT_ID="123456789"
export FILTRO=""                       # opcional
export ESTADOS_EXCLUIDOS="Solucionado" # opcional

python monitor.py
```

Las credenciales locales pueden guardarse en `.env.local`, que está en `.gitignore` y nunca debe subirse al repositorio.

Para volver a simular la primera ejecución, borra `state/seen.json`.

## Solución de problemas

| Mensaje                                         | Causa probable                                                              |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| `falta el secret PORTAL_URL`                    | No se configuró `PORTAL_URL`                                                |
| `faltan los secrets TELEGRAM_TOKEN y/o ...`     | No se configuró el token o el chat ID                                       |
| `no se pudo iniciar sesión`                     | Correo o contraseña incorrectos (el diagnóstico muestra un 401)             |
| `no se leyó ningún ticket`                      | Cambió el diseño de la página de tickets                                    |
| `ERROR Telegram: 401 Unauthorized`              | Token incorrecto o revocado                                                 |
| `ERROR Telegram: 400 Bad Request: chat not found` | Chat ID incorrecto, o nunca le escribiste al bot / no está en el grupo    |
| `ERROR Telegram: 403 Forbidden: bot was blocked` | Bloqueaste el bot; desbloquéalo y pulsa **Iniciar**                        |
| No llega ningún mensaje                         | No hay pendientes después de aplicar `FILTRO` y `ESTADOS_EXCLUIDOS`         |
| Los tickets llegan sin descripción              | No se pudo abrir el detalle; revisa el log (`con descripción: N`)           |
