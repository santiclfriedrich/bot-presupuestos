"""FASE 1 — Exploración de "Generar Nuevo Pedido" (NO GUARDA NADA).

Entra a GBP, abre "Generar Nuevo Pedido" desde el escritorio y recorre los pasos
del formulario volcando, en cada uno, todos los campos (JSON) + una captura en
logs/explorar_<stamp>/. Al final cierra la ventana con "Salir" y hace logout.

Pasos: abrir → cliente → calendario de Entrega ("Today") → ítem Normal (tipear
SKU, Cancelar) → ítem Manual (Cancelar).
Nunca toca "Guardar PEDIDO" ni "Emitir Comprobante", y la sesión rechaza
cualquier confirm de guardar/emitir.

    .venv\\Scripts\\python scripts\\explorar_pedido.py --cliente 111
    .venv\\Scripts\\python scripts\\explorar_pedido.py --cliente 111 --pausa
        (al final abre el Playwright Inspector; NO guardar nada; salir con "Resume")
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
    for (const el of document.querySelectorAll('input, select, textarea, button, a[id], td[onclick], div[onclick]')) {
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
        } else if (['A', 'BUTTON', 'TD', 'DIV'].includes(el.tagName)) {
            item.text = txt(el).slice(0, 80);
            item.href = (el.getAttribute('href') || '').slice(0, 120);
            item.onclick = (el.getAttribute('onclick') || '').slice(0, 160);
        } else {
            item.value = el.value;
            item.autocomplete = el.getAttribute('autocomplete');
            item.onkeyup = (el.getAttribute('onkeyup') || '').slice(0, 160);
            item.onchange = (el.getAttribute('onchange') || '').slice(0, 160);
        }
        out.push(item);
    }
    return {url: location.href, title: document.title, campos: out};
}
"""


class Volcador:
    def __init__(self, carpeta: Path):
        self.carpeta = carpeta
        carpeta.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def __call__(self, pagina, paso: str) -> None:
        self.n += 1
        nombre = f"{self.n:02d}_{paso}"
        volcado = {"url": pagina.url, "frames": []}
        for frame in pagina.frames:
            try:
                volcado["frames"].append({"name": frame.name, **frame.evaluate(VOLCAR_CAMPOS_JS)})
            except Exception as e:
                volcado["frames"].append({"name": frame.name, "url": frame.url, "error": str(e)})
        (self.carpeta / f"{nombre}.json").write_text(
            json.dumps(volcado, indent=2, ensure_ascii=False), encoding="utf-8")
        pagina.screenshot(path=str(self.carpeta / f"{nombre}.png"), full_page=True)
        log.info("Paso '%s' volcado.", nombre)


def frame_modal_item(ventana):
    """El modal "Actualización de Ítems" es un iframe (wfmSaleOrderDetail.aspx)."""
    for frame in ventana.frames:
        if "wfmSaleOrderDetail" in (frame.url or ""):
            loc = frame.locator("#rbtbyItem")
            if loc.count() > 0 and loc.first.is_visible():
                return frame
    return None


def abrir_modal_item(ventana):
    ventana.locator("#wucIB_AddItemSO_LB").click()
    for _ in range(15):
        ventana.wait_for_timeout(1000)
        modal = frame_modal_item(ventana)
        if modal is not None:
            return modal
    raise RuntimeError("No se abrió el modal de 'Añadir Ítem'.")


def tipear(campo, valor: str) -> None:
    """Pisa el valor de un campo tipeando (los campos de GBP tienen handlers onkeyup/onblur)."""
    campo.click()
    campo.press("Control+a")
    campo.press_sequentially(valor, delay=50)
    campo.press("Tab")


def completar_linea(ventana, cantidad: str, moneda: str, precio: str) -> None:
    """Cantidad → moneda de la línea (postback) → RECIÉN AHÍ el precio."""
    modal = frame_modal_item(ventana)
    tipear(modal.locator("#txtCant"), cantidad)
    ventana.wait_for_timeout(1500)
    modal = frame_modal_item(ventana)
    modal.locator("#wucPrice_ddlCurrency").select_option(label=moneda)
    ventana.wait_for_timeout(3000)  # postback: re-localizar el modal
    modal = frame_modal_item(ventana)
    tipear(modal.locator("#wucPrice_C1Price"), precio)
    ventana.wait_for_timeout(1500)
    modal = frame_modal_item(ventana)
    log.info("Línea: cant=%s moneda=%s precio=%s",
             modal.locator("#txtCant").input_value(),
             modal.locator("#wucPrice_ddlCurrency").evaluate("el => el.options[el.selectedIndex].text"),
             modal.locator("#wucPrice_C1Price").input_value())


def cancelar_modal(ventana) -> None:
    modal = frame_modal_item(ventana)
    if modal is not None:
        modal.locator("#wucIB_Cancel_LB").click()
        ventana.wait_for_timeout(2500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cliente", default="46", help="número de cliente de prueba")
    parser.add_argument("--sku", default="NSVC60P", help="SKU de prueba para el ítem normal")
    parser.add_argument("--pausa", action="store_true", help="abrir el Inspector al final")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    volcar = Volcador(ROOT / "logs" / f"explorar_{datetime.now():%Y%m%d_%H%M%S}")

    with GBPSession(cfg, permitir_guardar=False) as gbp:
        ventana = gbp.abrir_desde_menu("Generar Nuevo Pedido")
        ventana.wait_for_timeout(2000)
        volcar(ventana, "abierto")

        # Cliente: tipear el número y salir del campo (dispara la búsqueda/postback)
        campo = ventana.locator("#wucCustomerSelection_TB_Id")
        campo.click()
        campo.press_sequentially(args.cliente, delay=80)
        campo.press("Enter")
        ventana.wait_for_load_state("domcontentloaded")
        ventana.wait_for_timeout(4000)
        volcar(ventana, "cliente")
        cartel = gbp.cerrar_msgbox(ventana)
        if cartel:
            raise RuntimeError(f"GBP mostró un cartel al elegir el cliente: {cartel}")

        # Calendario de Entrega → "Today". Probamos los íconos de fecha en orden
        # hasta que aparezca un "Today" (todavía no sabemos cuál es el de Entrega).
        for icono in ("#dcDD_I", "#dcCD_I"):
            if ventana.locator(icono).count() == 0:
                continue
            ventana.locator(icono).click()
            ventana.wait_for_timeout(1500)
            today = ventana.get_by_text("Today", exact=False)
            if today.count() > 0 and today.first.is_visible():
                log.info("El calendario de %s muestra 'Today'.", icono)
                volcar(ventana, f"calendario_{icono[1:]}")
                today.first.click()
                ventana.wait_for_timeout(1500)
                volcar(ventana, "fecha_today")
                break
            ventana.keyboard.press("Escape")
        else:
            log.warning("Ningún ícono de calendario mostró 'Today'.")

        docs = ventana.locator("#ddlDocumentsFile").evaluate(
            "el => Array.from(el.options).map(o => `${o.value}=${o.text.trim()}`)")
        log.info("Documentos disponibles: %s", docs)

        # Ítem NORMAL: SKU en el cuadro de al lado de "Descripción" → ver autocomplete → Cancelar
        modal = abrir_modal_item(ventana)
        codigo = modal.locator("#wucItemSelection_TB_Id")
        codigo.click()
        codigo.press_sequentially(args.sku, delay=80)
        codigo.press("Enter")
        ventana.wait_for_timeout(4000)
        modal = frame_modal_item(ventana)
        volcar(ventana, "normal_sku_enter")
        log.info("Descripción que trajo GBP: %r", modal.locator("#txtItemDesc").input_value())
        completar_linea(ventana, cantidad="2", moneda="USD", precio="36")
        volcar(ventana, "normal_completo")
        cancelar_modal(ventana)

        # Ítem MANUAL: elegir "Manual" ANTES de tipear nada → completar → Cancelar
        modal = abrir_modal_item(ventana)
        modal.locator("#rbtManual").click()
        ventana.wait_for_timeout(3000)
        modal = frame_modal_item(ventana)
        modal.locator("#txtItemDesc").fill("Flete (prueba bot)")
        modal.locator("#ddlTax").select_option(label="01-I.V.A. 21 %")
        ventana.wait_for_timeout(3000)
        completar_linea(ventana, cantidad="2", moneda="ARS", precio="5000")
        volcar(ventana, "manual_completo")
        cancelar_modal(ventana)

        if args.pausa:
            log.info("Inspector abierto. NO guardes nada. Tocá 'Resume' para salir.")
            ventana.pause()


if __name__ == "__main__":
    main()
