import hashlib
import html
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
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
if not (TG_TOKEN and TG_CHAT):
    print("ERROR: faltan los secrets TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID.")
    sys.exit(1)


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

    tickets = page.eval_on_selector_all(
        "tbody tr",
        """rows => rows.map((tr, i) => {
            const td = tr.querySelectorAll('td');
            if (td.length < 4) return null;
            const t = td[0];
            const meta = [...t.querySelectorAll('div > span')]
                .map(s => s.textContent.trim()).filter(Boolean);
            const ps = td[1].querySelectorAll('p');
            return {
                fila:      i,
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
    return tickets


def leer_detalle(page, t):
    t.update(id="", creado=t["fecha"], descripcion="")
    try:
        page.locator("tbody tr").nth(t["fila"]).locator("td").first.click()
        dialogo = page.locator("[role=dialog]").filter(has_text="Información del Ticket").first
        dialogo.wait_for(state="visible", timeout=10000)

        def campo(nombre):
            loc = dialogo.locator("label", has_text=nombre).first.locator("xpath=following-sibling::*[1]")
            return loc.inner_text(timeout=3000).strip() if loc.count() else ""

        t["id"] = campo("ID")
        t["creado"] = campo("Fecha de creación") or t["fecha"]
        t["descripcion"] = campo("Descripción")
    except Exception:
        pass
    finally:
        page.keyboard.press("Escape")
        try:
            page.locator("[role=dialog]").first.wait_for(state="hidden", timeout=5000)
        except PWTimeout:
            pass


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


# ---------------------------------------------------------------- telegram
def telegram(texto, silencioso):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silencioso,
        },
        timeout=20,
    )
    if not r.ok:
        print(f"ERROR Telegram: {r.status_code} {r.json().get('description', '')}")
        r.raise_for_status()


def bloque(t, es_nuevo):
    e = html.escape
    desc = t.get("descripcion", "")
    if len(desc) > 2500:
        desc = desc[:2500] + "…"
    marca = "🆕 " if es_nuevo else ""
    lineas = [
        f"{marca}<b>{e(t['titulo'])}</b>" + (f"  <code>#{e(t['id'])}</code>" if t.get("id") else ""),
        f"📁 {e(t['proyecto'])}",
        f"📌 {e(t['estado'])} · {e(t['prioridad'])} · {e(t['tipo'])}",
        f"👤 {e(t['solicita'])} · 🕒 {e(t.get('creado') or t['fecha'])}",
    ]
    if desc:
        lineas.append(f"\n{e(desc)}")
    return "\n".join(lineas)


def enviar_resumen(pendientes, nuevos_claves):
    alta = sum("alta" in t["prioridad"].lower() for t in pendientes)
    hay_nuevos = any(clave(t) in nuevos_claves for t in pendientes)
    pendientes = sorted(pendientes, key=lambda t: (clave(t) not in nuevos_claves,
                                                   "alta" not in t["prioridad"].lower()))

    nombre = ", ".join(sorted({t["cliente"] or t["proyecto"] for t in pendientes}))
    encabezado = f"<b>{html.escape(nombre)}: {len(pendientes)} pendientes ({alta} alta)</b>"
    separador = "\n\n━━━━━━━━━━\n\n"

    mensajes, actual = [], encabezado
    for t in pendientes:
        b = bloque(t, clave(t) in nuevos_claves)
        if len(actual) + len(separador) + len(b) > 4000:
            mensajes.append(actual)
            actual = b
        else:
            actual += separador + b
    mensajes.append(actual)

    for i, m in enumerate(mensajes):
        telegram(m, silencioso=not (hay_nuevos and i == 0))
        time.sleep(0.5)


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
        if not tickets:
            print("ERROR: no se leyó ningún ticket (¿cambió la página?).")
            sys.exit(1)

        filtrados = [t for t in tickets if pasa_filtro(t)]
        pendientes = [t for t in filtrados if pendiente(t)]
        for t in pendientes:
            leer_detalle(page, t)
        browser.close()

    nuevos_claves = set() if primera_vez else {clave(t) for t in filtrados} - vistos

    if pendientes:
        enviar_resumen(pendientes, nuevos_claves)

    con_desc = sum(bool(t.get("descripcion")) for t in pendientes)
    print(f"Tickets leídos: {len(tickets)} · del filtro: {len(filtrados)} · "
          f"pendientes enviados: {len(pendientes)} (con descripción: {con_desc}) · "
          f"nuevos: {len(nuevos_claves)}")

    vistos |= {clave(t) for t in filtrados}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(vistos)))


if __name__ == "__main__":
    main()