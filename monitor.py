"""
Notificador de tickets: revisa el portal y manda una notificación al celular
(vía ntfy) cuando aparece un ticket nuevo que coincide con el filtro.

Todo lo sensible se configura como Secrets en GitHub, nada queda en el código:
  PORTAL_EMAIL      -> correo del portal                          (obligatorio)
  PORTAL_PASSWORD   -> contraseña del portal                      (obligatorio)
  NTFY_TOPIC        -> nombre de tu tema en ntfy                  (obligatorio)
  FILTRO            -> palabras a buscar en Proyecto o Cliente,
                       separadas por coma. Vacío = TODOS los tickets (opcional)
  PORTAL_URL        -> dirección del portal                       (obligatorio)

Los registros (logs) solo muestran conteos: nunca títulos, nombres ni clientes.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# Toma solo el dominio del secret PORTAL_URL (aunque traiga una ruta).
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

EMAIL = os.environ["PORTAL_EMAIL"]
PASSWORD = os.environ["PORTAL_PASSWORD"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
FILTRO = [f.strip().lower() for f in os.environ.get("FILTRO", "").split(",") if f.strip()]


# ---------------------------------------------------------------- login
def login(page):
    # Diagnóstico sin revelar datos: solo largos y si tienen espacios de más
    print(f"[diag] email: {len(EMAIL)} caracteres, espacios extra: {EMAIL != EMAIL.strip()}")
    print(f"[diag] password: {len(PASSWORD)} caracteres, espacios extra: {PASSWORD != PASSWORD.strip()}")

    page.goto(f"{BASE}/login", wait_until="networkidle")
    form = page.locator("form").first
    email_in = form.locator("input[type=email]").first
    pass_in = form.locator("input:not([type=email]):not([type=hidden]):not([type=checkbox])").first
    print(f"[diag] campos encontrados: email={email_in.count()} password={pass_in.count()}")

    # Escribir como persona (algunos formularios React ignoran fill)
    email_in.click()
    email_in.press_sequentially(EMAIL.strip(), delay=30)
    pass_in.click()
    pass_in.press_sequentially(PASSWORD.strip(), delay=30)
    print(f"[diag] largo escrito: email={len(email_in.input_value())} password={len(pass_in.input_value())}")

    form.get_by_role("button", name="Iniciar sesión").click()
    try:
        page.wait_for_url(lambda u: "/login" not in u, timeout=30000)
        return True
    except PWTimeout:
        # Mostrar el mensaje de error del portal (no contiene datos sensibles)
        msgs = page.locator("[role=alert], .text-red-500, .text-red-600, [data-sonner-toast]").all_inner_texts()
        print(f"[diag] sigue en: {urlparse(page.url).path}")
        print(f"[diag] mensajes en pantalla: {[m.strip() for m in msgs if m.strip()][:3]}")
        return False


# ---------------------------------------------------------------- lectura
def leer_tickets(page):
    page.goto(TICKETS_URL, wait_until="networkidle")
    page.wait_for_selector("tbody tr", timeout=60000)

    # Esperar a que la tabla termine de cargar (el número de filas deja de cambiar)
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


def clave(t):
    base = "|".join([t["titulo"], t["tipo"], t["solicita"], t["fecha"], t["proyecto"], t["cliente"]])
    return hashlib.sha256(base.encode()).hexdigest()


# ---------------------------------------------------------------- aviso
def notificar(t):
    requests.post(
        "https://ntfy.sh/",
        json={
            "topic": NTFY_TOPIC,
            "title": f"Nuevo ticket: {t['cliente'] or t['proyecto']}",
            "message": f"{t['titulo']}\n{t['proyecto']}\n{t['tipo']} · {t['solicita']} · {t['fecha']}\n"
                       f"Prioridad: {t['prioridad']} · Estado: {t['estado']}",
            "priority": 5 if "alta" in t["prioridad"].lower() else 4,
            "tags": ["ticket"],
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
    nuevos = [t for t in filtrados if clave(t) not in vistos]

    if primera_vez:
        print(f"Primera ejecución: se guardan {len(filtrados)} tickets existentes sin notificar.")
    else:
        for t in nuevos:
            notificar(t)
        print(f"Tickets leídos: {len(tickets)} · coinciden con el filtro: {len(filtrados)} · "
              f"nuevos notificados: {len(nuevos)}")

    vistos |= {clave(t) for t in filtrados}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(vistos)))


if __name__ == "__main__":
    main()