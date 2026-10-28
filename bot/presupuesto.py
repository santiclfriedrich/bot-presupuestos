"""Carga de un presupuesto en GBP a partir de un job del CRM.

Flujo (pantalla "Generar Nuevo Pedido", ventana wfmSaleOrder.aspx):
cliente (+Enter) → documento → entregar por → entrega "Today" → ítems
(modal "Actualización de Ítems", un iframe wfmSaleOrderDetail.aspx) →
"Imprimir al Guardar" → validaciones → Guardar (o dry-run: captura + Salir).

Cualquier cosa inesperada levanta ErrorCarga con un mensaje para el CRM; la
ventana se cierra con "Salir" sin guardar (lo hace GBPSession al desconectar).
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Frame, Page

from bot.gbp import GBPSession

log = logging.getLogger(__name__)

# Valores del CRM → texto de la opción en GBP
DOCUMENTOS = {
    "Presupuesto USD": "Presupuesto U$D",
    "Presupuesto ARS": "Presupuesto ARS",
}
ENTREGAR_POR = {
    "Retiro TML - Garin": "*Retiro - TML - Deposito Garin*",
    "TML Corpo": "TML Corpo",
}
IVAS = {
    21: "01-I.V.A. 21 %",
    10.5: "02-I.V.A. 10.5 %",
}
MONEDAS = ("ARS", "USD")

PLACEHOLDER_CLIENTE = "Clientes"


class ErrorCarga(Exception):
    """Error de negocio al cargar el presupuesto: se reporta tal cual al CRM."""


class ErrorPostGuardado(ErrorCarga):
    """El pedido SE GUARDÓ en GBP pero algo falló después (PDF / número).
    No reintentar a ciegas: hay que verificar en GBP para no duplicar."""


# "#1-598170", "#00001-00598170", "A00001-00598170", "1-598170". El lookahead
# descarta CUITs (30-71427582-4) y exigir >=5 dígitos descarta teléfonos/uuids.
RE_NUMERO = re.compile(r"(?<![\d-])(\d{1,5})\s*-\s*(\d{5,})(?!\d)(?!\s*-\s*\d)")
# En el PDF el número aparece pegado a "Documento a Emitir" (ej. "1-598171 Documento a Emitir:")
RE_NUMERO_PDF = re.compile(r"(?<![\d-])(\d{1,5})-(\d{5,})\s*Documento a Emitir")


def numero_gbp(valor: float | int) -> str:
    """36 → '36', 36.5 → '36,5' (GBP usa coma decimal; sin separador de miles)."""
    return f"{float(valor):.6f}".rstrip("0").rstrip(".").replace(".", ",")


def _texto_pdf(pdf: bytes | None) -> str:
    """Texto plano del PDF (para buscar el número de presupuesto). '' si no se puede."""
    if not pdf:
        return ""
    try:
        from io import BytesIO

        from pypdf import PdfReader
        return " ".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages[:2])
    except Exception as e:
        log.warning("No se pudo leer el texto del PDF: %s", e)
        return ""


def _tipear(campo, valor: str, tecla: str = "Tab") -> None:
    """Pisa el valor tipeando (los campos de GBP tienen handlers onkeyup/onblur)."""
    campo.click()
    campo.press("Control+a")
    campo.press_sequentially(valor, delay=50)
    campo.press(tecla)


class CargadorPresupuesto:
    def __init__(self, gbp: GBPSession, carpeta_logs: Path, dry_run: bool = True):
        self.gbp = gbp
        self.carpeta = carpeta_logs
        self.dry_run = dry_run
        self.v: Page | None = None  # ventana del pedido
        self.guardado = False  # True apenas se clickea "Guardar PEDIDO"

    # -- Helpers ----------------------------------------------------------

    def _esperar(self, ms: int = 1500) -> None:
        self.v.wait_for_timeout(ms)

    def _procesando(self) -> bool:
        """True si GBP muestra el overlay "Procesando, espere por favor..." en algún frame."""
        for frame in self.v.frames:
            try:
                loc = frame.get_by_text("Procesando, espere por favor", exact=False)
                for i in range(loc.count()):
                    if loc.nth(i).is_visible():
                        return True
            except Exception:
                pass
        return False

    def _esperar_procesando(self, timeout_s: int = 60) -> None:
        """Espera a que GBP termine de procesar (desaparece el overlay)."""
        self._esperar(500)
        for _ in range(timeout_s * 2):
            if not self._procesando():
                return
            self._esperar(500)
        raise ErrorCarga(f"GBP siguió 'Procesando...' más de {timeout_s}s")

    def _texto_ventana(self) -> str:
        textos = []
        for frame in self.v.frames:
            try:
                textos.append(frame.inner_text("body"))
            except Exception:
                pass
        return " ".join(" ".join(textos).split())

    def _chequear_cartel(self, contexto: str) -> None:
        cartel = self.gbp.cerrar_msgbox(self.v)
        if cartel:
            raise ErrorCarga(f"{contexto}: GBP mostró '{cartel}'")

    def _modal(self) -> Frame | None:
        """El modal de ítems es un iframe wfmSaleOrderDetail.aspx."""
        for frame in self.v.frames:
            if "wfmSaleOrderDetail" in (frame.url or ""):
                loc = frame.locator("#rbtbyItem")
                if loc.count() > 0 and loc.first.is_visible():
                    return frame
        return None

    def _modal_o_error(self, contexto: str) -> Frame:
        modal = self._modal()
        if modal is None:
            raise ErrorCarga(f"{contexto}: se cerró el modal de ítems inesperadamente")
        return modal

    def _seleccionar_por_texto(self, selector: str, texto: str, contexto: str) -> None:
        """Elige la opción por texto. Si no está exacta, acepta la misma con prefijo de
        sucursal (en SKOP los documentos se llaman "05.Presupuesto U$D")."""
        sel = self.v.locator(selector)
        opciones = sel.evaluate("el => Array.from(el.options).map(o => o.text.trim())")
        if texto not in opciones:
            candidatas = [o for o in opciones if re.sub(r"^\d+\.\s*", "", o) == texto]
            if len(candidatas) != 1:
                raise ErrorCarga(f"{contexto}: no existe la opción '{texto}' en GBP"
                                 f"{' (ambigua: ' + str(candidatas) + ')' if candidatas else ''}. "
                                 f"Opciones: {opciones}")
            log.info("%s: '%s' → '%s'", contexto, texto, candidatas[0])
            texto = candidatas[0]
        sel.select_option(label=texto)
        self._esperar(3000)  # onchange → __doPostBack
        self._chequear_cartel(contexto)
        actual = self.v.locator(selector).evaluate("el => el.options[el.selectedIndex].text.trim()")
        if actual != texto:
            raise ErrorCarga(f"{contexto}: quedó '{actual}' en vez de '{texto}'")

    # -- Pasos ------------------------------------------------------------

    def _cliente(self, numero: str) -> str:
        campo = self.v.locator("#wucCustomerSelection_TB_Id")
        _tipear(campo, numero, tecla="Enter")
        self._esperar(4000)
        self._chequear_cartel(f"Cliente {numero}")
        nombre = self.v.locator("#wucCustomerSelection_TB_Desc_act_txt").input_value().strip()
        id_actual = self.v.locator("#wucCustomerSelection_TB_Id").input_value().strip()
        if id_actual != numero or not nombre or nombre.startswith(PLACEHOLDER_CLIENTE):
            raise ErrorCarga(f"Cliente {numero}: GBP no lo resolvió (id='{id_actual}', nombre='{nombre}')")
        log.info("Cliente %s → %s", numero, nombre)
        return nombre

    def _entrega_today(self) -> None:
        self.v.locator("#dcDD_I").click()
        self._esperar()
        today = self.v.get_by_text("Today", exact=False)
        if today.count() == 0 or not today.first.is_visible():
            raise ErrorCarga("Entrega: el calendario no mostró 'Today'")
        today.first.click()
        self._esperar()
        hoy = datetime.now().strftime("%d/%m/%Y")
        actual = self.v.locator("#dcDD_TB").input_value().strip()
        if actual != hoy:
            raise ErrorCarga(f"Entrega: quedó '{actual}' en vez de hoy ({hoy})")

    def _abrir_modal_item(self) -> Frame:
        self.v.locator("#wucIB_AddItemSO_LB").click()
        for _ in range(15):
            self._esperar(1000)
            modal = self._modal()
            if modal is not None:
                return modal
        self._chequear_cartel("Añadir Ítem")
        raise ErrorCarga("Añadir Ítem: no se abrió el modal")

    def _item(self, n: int, item: dict) -> str:
        """Carga un ítem y lo acepta. Devuelve la descripción que quedó en GBP."""
        ctx = f"Ítem {n} ({item.get('sku') or item.get('descripcion')})"
        moneda = item.get("moneda")
        if moneda not in MONEDAS:
            raise ErrorCarga(f"{ctx}: moneda inválida '{moneda}'")

        modal = self._abrir_modal_item()
        if item["tipo"] == "normal":
            if not item.get("sku"):
                raise ErrorCarga(f"{ctx}: ítem normal sin SKU")
            # El tipo se elige ANTES de tipear nada (después GBP no deja cambiarlo)
            codigo = modal.locator("#wucItemSelection_TB_Id")
            _tipear(codigo, item["sku"], tecla="Enter")
            self._esperar(4000)
            modal = self._modal_o_error(ctx)
            descripcion = modal.locator("#txtItemDesc").input_value().strip()
            if not descripcion:
                raise ErrorCarga(f"{ctx}: el SKU no existe en GBP (no trajo descripción)")
        elif item["tipo"] == "manual":
            iva = item.get("iva")
            if iva not in IVAS:
                raise ErrorCarga(f"{ctx}: IVA '{iva}' no soportado (solo {list(IVAS)})")
            modal.locator("#rbtManual").click()
            self._esperar(3000)
            modal = self._modal_o_error(ctx)
            descripcion = item["descripcion"].strip()
            modal.locator("#txtItemDesc").fill(descripcion)
            modal.locator("#ddlTax").select_option(label=IVAS[iva])
            self._esperar(3000)
        else:
            raise ErrorCarga(f"{ctx}: tipo de ítem desconocido '{item['tipo']}'")

        # Cantidad → moneda de la línea (postback) → RECIÉN AHÍ el precio
        modal = self._modal_o_error(ctx)
        _tipear(modal.locator("#txtCant"), numero_gbp(item["cantidad"]))
        self._esperar()
        modal = self._modal_o_error(ctx)
        modal.locator("#wucPrice_ddlCurrency").select_option(label=moneda)
        self._esperar(3000)
        modal = self._modal_o_error(ctx)
        _tipear(modal.locator("#wucPrice_C1Price"), numero_gbp(item["precio"]))
        self._esperar()

        modal = self._modal_o_error(ctx)
        moneda_actual = modal.locator("#wucPrice_ddlCurrency").evaluate(
            "el => el.options[el.selectedIndex].text.trim()")
        if moneda_actual != moneda:
            raise ErrorCarga(f"{ctx}: la moneda quedó en {moneda_actual} en vez de {moneda}")
        log.info("%s: %s | cant=%s %s %s", ctx, descripcion,
                 modal.locator("#txtCant").input_value(), moneda,
                 modal.locator("#wucPrice_C1Price").input_value())

        modal.locator("#wucIB_Ok_LB").click()
        for _ in range(10):
            self._esperar(1000)
            if self._modal() is None:
                break
        else:
            texto = " ".join(self._modal().inner_text("body").split())[-300:]
            raise ErrorCarga(f"{ctx}: GBP no aceptó el ítem: {texto}")
        self._esperar_procesando()
        self._chequear_cartel(ctx)
        return descripcion

    def _validar_grilla(self, items: list[dict]) -> None:
        """Los totalizadores del pie tienen que cuadrar con el job:
        "Ingresos : N" = cantidad de líneas y "Cantidad : X" = suma de cantidades.
        (No se valida por descripción: la grilla muestra ~10 filas y el resto
        queda fuera del DOM visible.)"""
        esperado_lineas = len(items)
        esperado_cant = round(sum(float(i["cantidad"]) for i in items), 2)
        ingresos = cantidad = None
        for _ in range(10):  # la grilla puede tardar en refrescar
            self._esperar_procesando()
            texto = self._texto_ventana()
            m = re.search(r"Ingresos\s*:\s*(\d+)", texto)
            ingresos = int(m.group(1)) if m else None
            m = re.search(r"Cantidad\s*:\s*([\d.]+,\d+)", texto)
            cantidad = float(m.group(1).replace(".", "").replace(",", ".")) if m else None
            if ingresos == esperado_lineas and cantidad == esperado_cant:
                return
            self._esperar(1000)
        raise ErrorCarga(f"Validación: el pedido no cuadra con el job (líneas: job {esperado_lineas} / "
                         f"GBP {ingresos}; unidades: job {esperado_cant} / GBP {cantidad})")

    def _evidencia(self, job: dict, resumen: dict) -> Path:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        captura = self.carpeta / f"job_{job['id']}.png"
        self.v.screenshot(path=str(captura), full_page=True)
        (self.carpeta / f"job_{job['id']}.json").write_text(
            json.dumps({"job": job, "resumen": resumen}, indent=2, ensure_ascii=False), encoding="utf-8")
        return captura

    # -- Entrada ----------------------------------------------------------

    def cargar(self, job: dict) -> dict:
        """Carga el job. En dry-run devuelve el resumen sin guardar (la ventana la
        cierra GBPSession con "Salir"). Ante error deja captura en la carpeta de logs."""
        try:
            return self._cargar(job)
        except Exception:
            if self.v is not None and not self.v.is_closed():
                try:
                    self.carpeta.mkdir(parents=True, exist_ok=True)
                    captura = self.carpeta / f"job_{job.get('id')}_ERROR.png"
                    self.v.screenshot(path=str(captura), full_page=True)
                    log.error("Captura del error: %s", captura)
                except Exception as e:
                    log.warning("No se pudo sacar la captura del error: %s", e)
            raise

    def _cargar(self, job: dict) -> dict:
        documento = DOCUMENTOS.get(job.get("documento"))
        if documento is None:
            raise ErrorCarga(f"Documento '{job.get('documento')}' no soportado (solo {list(DOCUMENTOS)})")
        entregar_por = ENTREGAR_POR.get(job.get("entrega_por"))
        if entregar_por is None:
            raise ErrorCarga(f"Entregar por '{job.get('entrega_por')}' no soportado (solo {list(ENTREGAR_POR)})")
        if not job.get("items"):
            raise ErrorCarga("El job no trae ítems")

        self.v = self.gbp.abrir_desde_menu("Generar Nuevo Pedido")
        self._esperar(2000)

        nombre_cliente = self._cliente(str(job["cliente_numero"]))
        self._seleccionar_por_texto("#ddlDocumentsFile", documento, "Documento a Emitir")
        self._seleccionar_por_texto("#ddlDelivery", entregar_por, "Entregar Por")
        self._entrega_today()

        descripciones = [self._item(n, item) for n, item in enumerate(job["items"], 1)]
        self._validar_grilla(job["items"])

        imprimir = self.v.locator("#chkPrintSaleOrder")
        if not imprimir.is_checked():
            imprimir.click()  # .click() (dispara el onclick), no .check()
            self._esperar()
        if not self.v.locator("#chkPrintSaleOrder").is_checked():
            raise ErrorCarga("No se pudo tildar 'Imprimir al Guardar'")

        resumen = {
            "cliente": f"{job['cliente_numero']} - {nombre_cliente}",
            "documento": documento,
            "entregar_por": entregar_por,
            "entrega": self.v.locator("#dcDD_TB").input_value(),
            "items": descripciones,
            "total": self.v.locator("#txtTotalCustomer").input_value(),
        }
        resumen["captura"] = str(self._evidencia(job, resumen))
        log.info("Presupuesto listo para guardar: %s", resumen)

        if self.dry_run:
            log.warning("DRY-RUN: no se guarda. Revisá la captura: %s", resumen["captura"])
            return resumen
        return self._guardar(job, resumen)

    # -- Guardar + PDF ----------------------------------------------------

    def _guardar(self, job: dict, resumen: dict) -> dict:
        """Click en "Guardar PEDIDO" (UNA sola vez) y captura del PDF + número.

        Con "Imprimir al Guardar" GBP abre el PDF en una pestaña nueva. Se captura:
        A) cualquier respuesta application/pdf → se vuelve a bajar con la sesión
           (context.request.get); B) una descarga, si la pestaña trae "descargar".
        Todo lo que pase (carteles, URLs, campos) queda en logs para diagnóstico.
        """
        ctx = self.gbp.context
        urls_pdf: list[str] = []
        paginas: list[Page] = []
        descargas = []

        def _on_response(resp) -> None:
            try:
                if "application/pdf" in (resp.headers.get("content-type") or "").lower():
                    urls_pdf.append(resp.url)
            except Exception:
                pass

        def _on_page(pg: Page) -> None:
            paginas.append(pg)
            pg.on("download", lambda d: descargas.append(d))

        ctx.on("response", _on_response)
        ctx.on("page", _on_page)
        self.v.on("download", lambda d: descargas.append(d))

        self._esperar_procesando()
        log.warning("GUARDANDO presupuesto en GBP (job %s)...", job["id"])
        self.v.locator("#wucIB_Save_LB").click()
        self.guardado = True

        # Después de guardar GBP CIERRA SOLA la ventana del pedido: las esperas se
        # hacen sobre la página principal de la sesión, no sobre self.v.
        principal = self.gbp.page
        carteles: list[str] = []
        for _ in range(60):
            principal.wait_for_timeout(1000)
            if not self.v.is_closed():
                cartel = self.gbp.cerrar_msgbox(self.v)
                if cartel:
                    carteles.append(cartel)
            if urls_pdf or descargas:
                break
        principal.wait_for_timeout(3000)
        log.info("Después de guardar: ventana del pedido %s; pestañas nuevas: %s",
                 "cerrada" if self.v.is_closed() else "abierta", [pg.url for pg in paginas])

        pdf = self._bajar_pdf(urls_pdf, descargas, paginas)
        numero = self._numero(carteles, urls_pdf, paginas, pdf)
        self._esperar_procesos_gbp(paginas)

        diag = {"carteles": carteles, "urls_pdf": urls_pdf,
                "paginas": [pg.url for pg in paginas], "numero": numero,
                "pdf_bytes": len(pdf) if pdf else 0}
        self._evidencia_post_guardado(job, diag, pdf, paginas)
        log.info("Post-guardado: %s", diag)

        if not pdf:
            raise ErrorPostGuardado(
                f"GUARDADO en GBP (número: {numero or 'desconocido'}) pero no se pudo capturar el PDF. "
                "Verificar en GBP antes de reintentar.")
        if not numero:
            raise ErrorPostGuardado(
                "GUARDADO en GBP pero no se pudo leer el número de presupuesto. "
                "Verificar en GBP antes de reintentar.")
        return {**resumen, "numero_gbp": numero, "pdf": pdf}

    def _esperar_procesos_gbp(self, paginas: list[Page], timeout_s: int = 60) -> None:
        """Al guardar, GBP puede abrir ventanas de proceso que se cierran solas
        (p. ej. wfmMercadoLibre_processQueue.aspx?...CloseWindow=1). No se cierran
        a mano: se espera a que terminen antes de seguir (o de hacer logout)."""
        principal = self.gbp.page
        for _ in range(timeout_s):
            abiertas = [pg for pg in paginas if not pg.is_closed() and ".pdf" not in pg.url
                        and "CloseWindow=1" in pg.url]
            if not abiertas:
                return
            principal.wait_for_timeout(1000)
        log.warning("Ventanas de proceso de GBP siguen abiertas tras %ss: %s",
                    timeout_s, [pg.url for pg in abiertas])

    def _bajar_pdf(self, urls_pdf: list[str], descargas: list, paginas: list[Page]) -> bytes | None:
        ctx = self.gbp.context
        # Plan A: re-bajar con la sesión autenticada
        for url in urls_pdf:
            if not url.startswith("http"):
                continue
            try:
                body = ctx.request.get(url).body()
                if body.startswith(b"%PDF"):
                    log.info("PDF capturado (plan A): %s (%d bytes)", url, len(body))
                    return body
            except Exception as e:
                log.warning("Plan A falló para %s: %s", url, e)
        # Plan B: botón "descargar" en la pestaña nueva
        if not descargas:
            for pg in paginas:
                if pg.is_closed():
                    continue
                boton = pg.get_by_text("descargar", exact=False)
                if boton.count() == 0:
                    continue
                try:
                    with pg.expect_download(timeout=30000) as info:
                        boton.first.click()
                    descargas.append(info.value)
                    break
                except Exception as e:
                    log.warning("Plan B falló en %s: %s", pg.url, e)
        for d in descargas:
            try:
                body = Path(d.path()).read_bytes()
                if body.startswith(b"%PDF"):
                    log.info("PDF capturado (plan B): %s (%d bytes)", d.suggested_filename, len(body))
                    return body
            except Exception as e:
                log.warning("No se pudo leer la descarga: %s", e)
        return None

    def _numero(self, carteles: list[str], urls_pdf: list[str], paginas: list[Page],
                pdf: bytes | None) -> str | None:
        """Número de presupuesto con formato '#<sucursal>-<número>' (ej. #1-598168)."""
        if not self.v.is_closed():
            suc = self.v.locator("#txtSaleOrderBranch").input_value().strip()
            nro = self.v.locator("#txtSaleOrderNumber").input_value().strip()
            if suc.isdigit() and nro.isdigit():
                return f"#{int(suc)}-{int(nro)}"
        texto_pdf = _texto_pdf(pdf)
        m = RE_NUMERO_PDF.search(texto_pdf)
        if m:
            return f"#{int(m.group(1))}-{int(m.group(2))}"
        # Fallbacks. Sólo URLs http (las chrome-extension:// del visor traen uuids).
        fuentes = [texto_pdf] + carteles + [u for u in urls_pdf if u.startswith("http")]
        for pg in paginas:
            if not pg.is_closed() and pg.url.startswith("http"):
                fuentes.append(pg.url)
                try:
                    fuentes.append(pg.title())
                except Exception:
                    pass
        for texto in fuentes:
            m = RE_NUMERO.search(texto or "")
            if m:
                return f"#{int(m.group(1))}-{int(m.group(2))}"
        return None

    def _evidencia_post_guardado(self, job: dict, diag: dict, pdf: bytes | None, paginas: list[Page]) -> None:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        base = self.carpeta / f"job_{job['id']}_guardado"
        try:
            if not self.v.is_closed():
                self.v.screenshot(path=f"{base}.png", full_page=True)
            for i, pg in enumerate(paginas):
                if not pg.is_closed():
                    pg.screenshot(path=f"{base}_pestana{i}.png")
        except Exception as e:
            log.warning("No se pudo capturar la evidencia post-guardado: %s", e)
        if pdf:
            Path(f"{base}.pdf").write_bytes(pdf)
        Path(f"{base}.json").write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
