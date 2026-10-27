"""FASE 1 — Exploración de la pantalla "Generar Nuevo Pedido" (SOLO LECTURA).

Entra a GBP, navega a "Generar Nuevo Pedido", vuelca todos los campos del
formulario (inputs, selects con sus opciones, botones/links) a un JSON + una
captura en logs/, y hace logout. No completa ni guarda nada.

    .venv\\Scripts\\python scripts\\explorar_pedido.py           # vuelca y sale
    .venv\\Scripts\\python scripts\\explorar_pedido.py --pausa   # además abre el
        Playwright Inspector para explorar a mano (NO guardar nada; para salir
        tocar "Resume" en el Inspector, así el bot hace logout)
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import ROOT, load_config  # noqa: E402
from bot.gbp import GBPSession  # noqa: E402

log = logging.getLogger("explorar")

VOLCAR_CAMPOS_JS = r"""
() => {
    const txt = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    const label = el => {
        if (el.id) {
            const l = document.querySelector(`label[for="${el.id}"]`);
            if (l) return txt(l);
        }
        // texto de la celda anterior (GBP arma los formularios con tablas)
        const td = el.closest('td');
        const prev = td && td.previousElementSibling;
        return prev ? txt(prev).slice(0, 80) : '';
    };
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const out = [];
    for (const el of document.querySelectorAll('input, select, textarea, button, a[id]')) {
        if (el.type === 'hidden') continue;
        const item = {
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            name: el.getAttribute('name'),
            type: el.getAttribute('type'),
            label: label(el),
            visible: visible(el),
        };
        if (el.tagName === 'SELECT') {
            item.options = Array.from(el.options).map(o => ({value: o.value, text: o.text.trim(), selected: o.selected}));
            item.onchange_postback = (el.getAttribute('onchange') || '').includes('__doPostBack');
        } else if (el.tagName === 'A' || el.tagName === 'BUTTON') {
            item.text = txt(el).slice(0, 80);
            item.href = (el.getAttribute('href') || '').slice(0, 120);
        } else {
            item.value = el.value;
            item.autocomplete = el.getAttribute('autocomplete');
            item.onkeyup = (el.getAttribute('onkeyup') || '').slice(0, 120);
        }
        out.push(item);
    }
    return {url: location.href, title: document.title, campos: out};
}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pausa", action="store_true", help="abrir el Inspector para explorar a mano")
    parser.add_argument("--nodo", default="Generar Nuevo Pedido", help="texto del nodo del menú")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    salida = ROOT / "logs"
    salida.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with GBPSession(cfg) as gbp:
        paginas_nuevas = []
        gbp.context.on("page", paginas_nuevas.append)

        gbp.navegar_a(args.nodo)
        gbp.page.wait_for_timeout(3000)

        volcado = {"frames": [], "popups": []}
        for frame in gbp.page.frames:
            try:
                volcado["frames"].append({"name": frame.name, **frame.evaluate(VOLCAR_CAMPOS_JS)})
            except Exception as e:
                volcado["frames"].append({"name": frame.name, "url": frame.url, "error": str(e)})
        # Por si "Generar Nuevo Pedido" abre una ventana nueva en vez de cargar en el frame
        for pg in paginas_nuevas:
            try:
                pg.wait_for_load_state("domcontentloaded")
                volcado["popups"].append(pg.evaluate(VOLCAR_CAMPOS_JS))
            except Exception as e:
                volcado["popups"].append({"url": pg.url, "error": str(e)})

        ruta_json = salida / f"explorar_{stamp}.json"
        ruta_json.write_text(json.dumps(volcado, indent=2, ensure_ascii=False), encoding="utf-8")
        ruta_png = salida / f"explorar_{stamp}.png"
        (paginas_nuevas[-1] if paginas_nuevas else gbp.page).screenshot(path=str(ruta_png), full_page=True)
        log.info("Volcado: %s", ruta_json)
        log.info("Captura: %s", ruta_png)

        if args.pausa:
            log.info("Inspector abierto. NO guardes nada. Tocá 'Resume' para salir (hace logout).")
            gbp.page.pause()


if __name__ == "__main__":
    main()
