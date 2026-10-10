# Notificador de tickets

Revisa automáticamente un portal de tickets cada 10 minutos y envía un resumen de los tickets pendientes al celular mediante [ntfy](https://ntfy.sh). Corre gratis en GitHub Actions, sin servidor propio.

## Cómo funciona

1. **GitHub Actions** ejecuta `monitor.py` cada 10 minutos (o a mano con el botón *Run workflow*).
2. El script abre un Chromium sin interfaz con **Playwright**, inicia sesión en el portal (`/login`) y entra a `/tickets/all-tickets`.
3. Lee la tabla de tickets: título, tipo, solicitante, fecha, proyecto, cliente, prioridad y estado.
4. Aplica los filtros:
   - `FILTRO`: solo conserva los tickets cuyo proyecto o cliente contenga alguna de las palabras indicadas.
   - `ESTADOS_EXCLUIDOS`: descarta los tickets en esos estados (por defecto `Solucionado`).
5. Compara contra los tickets ya vistos (`state/seen.json`) para detectar los **nuevos**.
6. Si hay pendientes, envía una notificación a ntfy:
   - Título: `Cliente: N pendientes (X alta)`.
   - Los tickets nuevos aparecen primero, marcados con `[NUEVO]`, seguidos de los de prioridad alta.
   - Prioridad 5 (urgente) si hay tickets nuevos, 3 (normal) si no.
   - Al tocar la notificación se abre el portal.
7. Guarda el estado en la caché de GitHub Actions para la siguiente ejecución.

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

### 1. Recibir notificaciones en el celular

1. Instala la app **ntfy** (Android o iOS).
2. Suscríbete a un tema con un nombre difícil de adivinar, por ejemplo `tickets-ivan-8f3k2x`. Cualquiera que conozca el nombre puede leer los mensajes.

### 2. Secrets del repositorio

En GitHub ve a **Settings → Secrets and variables → Actions → New repository secret** y crea:

| Secret            | Obligatorio | Descripción                                              |
| ----------------- | ----------- | -------------------------------------------------------- |
| `PORTAL_URL`      | Sí          | Dirección del portal, ej. `https://tu-portal.com`        |
| `PORTAL_EMAIL`    | Sí          | Correo de acceso al portal                               |
| `PORTAL_PASSWORD` | Sí          | Contraseña del portal                                    |
| `NTFY_TOPIC`      | Sí          | Nombre del tema de ntfy                                  |
| `FILTRO`          | No          | Palabras separadas por coma. Vacío = todos los tickets   |

`ESTADOS_EXCLUIDOS` no está conectado en el workflow; para cambiarlo, agrégalo en el bloque `env` de `notificador.yml`.

### 3. Activarlo

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
export NTFY_TOPIC="tu-tema"
export FILTRO=""                       # opcional
export ESTADOS_EXCLUIDOS="Solucionado" # opcional

python monitor.py
```

Las credenciales locales pueden guardarse en `.env.local`, que está en `.gitignore` y nunca debe subirse al repositorio.

## Solución de problemas

| Mensaje                                | Causa probable                                                  |
| -------------------------------------- | --------------------------------------------------------------- |
| `falta el secret PORTAL_URL`           | No se configuró `PORTAL_URL`                                    |
| `no se pudo iniciar sesión`            | Correo o contraseña incorrectos (el diagnóstico muestra un 401) |
| `no se leyó ningún ticket`             | Cambió el diseño de la página de tickets                        |
| No llega la notificación               | No hay pendientes tras el filtro, o el tema de ntfy no coincide |
