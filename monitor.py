import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------- configuración
_url = os.environ.get("PORTAL_URL", "").strip()
if _url and "://" not in _url:
    _url = "https://" + _url
_p = urlparse(_url)
if not _p.netloc:
    print("ERROR: falta el secret PORTAL_URL (ej. https://tu-portal.com).")
    sys.exit(1)
BASE = f"{_p.scheme}://{_p.netloc}"
TICKETS_URL = f"{BASE}/tickets/all-tickets"
STATE_FILE = Path("state/seen.json")

EMAIL = os.environ["PORTAL_EMAIL"].strip()
PASSWORD = os.environ["PORTAL_PASSWORD"].strip()
NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()


def _lista(nombre, defecto=""):
    valor = os.environ.get(nombre) or defecto
    return [x.strip().lower() for x in valor.split(",") if x.strip()]


FILTRO = _lista("FILTRO")
EXCLUIDOS = _lista("ESTADOS_EXCLUIDOS", "Solucionado")


# ---------------------------------------------------------------- login
def login(page):
    page.goto(f"{BASE}/login", wait_until="networkidle")
    form = page.locator("form").first
    email_in = form.locator("input[type=email]").first
    pass_in = form.locator("input:not([type=email]):not([type=hidden]):not([type=checkbox])").first

    email_in.click()
    email_in.press_sequentially(EMAIL, delay=30)
    pass_in.click()
    pass_in.press_sequentially(PASSWORD, delay=30)

    respuestas = []
    page.on("response", lambda r: respuestas.append(f"{urlparse(r.url).path} -> {r.status}")
            if "/api/auth" in r.url else None)

    form.get_by_role("button", name="Iniciar sesión").click()
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
        return True
    except PWTimeout:
        pass

    pass_in.press("Enter")
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        return True
    except PWTimeout:
        print(f"[diag] respuestas auth: {respuestas[-4:]}  (401 = correo o contraseña incorrectos)")
        return False


# ---------------------------------------------------------------- lectura
def leer_tickets(page):
    page.goto(TICKETS_URL, wait_until="networkidle")
    page.wait_for_selector("tbody tr", timeout=60000)

    prev = -1
    for _ in range(10):
        n = page.locator("tbody tr").count()
        if n == prev:
            break
        prev = n
        time.sleep(1.5)

    return page.eval_on_selector_all(
        "tbody tr",
        """rows => rows.map(tr => {
            const td = tr.querySelectorAll('td');
            if (td.length < 4) return null;
            const t = td[0];
            const meta = [...t.querySelectorAll('div > span')]
                .map(s => s.textContent.trim()).filter(Boolean);
            const ps = td[1].querySelectorAll('p');
            return {
                titulo:    (t.querySelector('p')?.textContent || '').trim(),
                tipo:      meta[0] || '',
                solicita:  meta[1] || '',
                fecha:     meta[2] || '',
                proyecto:  (ps[0]?.textContent || '').trim(),
                cliente:   (ps[1]?.textContent || '').trim(),
                prioridad: td[2].textContent.trim(),
                estado:    td[3].textContent.trim(),
            };
        }).filter(Boolean)""",
    )


def pasa_filtro(t):
    if not FILTRO:
        return True
    texto = f"{t['proyecto']} {t['cliente']}".lower()
    return any(f in texto for f in FILTRO)


def pendiente(t):
    estado = t["estado"].lower()
    return not any(e in estado for e in EXCLUIDOS)


def clave(t):
    base = "|".join([t["titulo"], t["tipo"], t["solicita"], t["fecha"], t["proyecto"], t["cliente"]])
    return hashlib.sha256(base.encode()).hexdigest()


# ---------------------------------------------------------------- aviso
def enviar_resumen(pendientes, nuevos_claves):
    alta = sum("alta" in t["prioridad"].lower() for t in pendientes)
    hay_nuevos = any(clave(t) in nuevos_claves for t in pendientes)

    pendientes = sorted(pendientes, key=lambda t: (clave(t) not in nuevos_claves,
                                                   "alta" not in t["prioridad"].lower()))
    lineas = []
    for t in pendientes:
        marca = "[NUEVO] " if clave(t) in nuevos_claves else ""
        lineas.append(f"• {marca}{t['titulo']} — {t['estado']} · {t['prioridad']} · {t['fecha']}")

    mensaje = "\n".join(lineas)
    if len(mensaje.encode()) > 3800:
        mensaje = mensaje.encode()[:3800].decode(errors="ignore").rsplit("\n", 1)[0] + "\n… (ver portal)"

    nombre = ", ".join(sorted({t["cliente"] or t["proyecto"] for t in pendientes})) or "Tickets"
    requests.post(
        "https://ntfy.sh/",
        json={
            "topic": NTFY_TOPIC,
            "title": f"{nombre}: {len(pendientes)} pendientes ({alta} alta)",
            "message": mensaje,
            "priority": 5 if hay_nuevos else 3,
            "tags": ["rotating_light"] if hay_nuevos else ["clipboard"],
            "click": TICKETS_URL,
        },
        timeout=20,
    ).raise_for_status()


# ---------------------------------------------------------------- main
def main():
    primera_vez = not STATE_FILE.exists()
    vistos = set() if primera_vez else set(json.loads(STATE_FILE.read_text()))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(locale="es-HN").new_page()
        if not login(page):
            print("ERROR: no se pudo iniciar sesión (revisa PORTAL_EMAIL / PORTAL_PASSWORD).")
            sys.exit(1)
        tickets = leer_tickets(page)
        browser.close()

    if not tickets:
        print("ERROR: no se leyó ningún ticket (¿cambió la página?).")
        sys.exit(1)

    filtrados = [t for t in tickets if pasa_filtro(t)]
    pendientes = [t for t in filtrados if pendiente(t)]
    nuevos_claves = set() if primera_vez else {clave(t) for t in filtrados} - vistos

    if pendientes:
        enviar_resumen(pendientes, nuevos_claves)

    print(f"Tickets leídos: {len(tickets)} · del filtro: {len(filtrados)} · "
          f"pendientes enviados: {len(pendientes)} · nuevos: {len(nuevos_claves)}")

    vistos |= {clave(t) for t in filtrados}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(vistos)))


if __name__ == "__main__":
    main()