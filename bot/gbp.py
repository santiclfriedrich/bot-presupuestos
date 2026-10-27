"""Sesión de GBP (Global Blue Point) con Playwright.

Login + perfil + sucursal + logout seguro + navegación por el menú.
El flujo de carga de presupuestos se agrega en la FASE 1.

Reglas del ERP que esto respeta:
- Salir SIEMPRE con #lnkLogout (nunca cerrar el navegador sin logout: bloquea ~5 min).
- Todo vive en un popup que GBP abre al navegar a la URL.
- Cada postback reemplaza frameWorkingArea: re-obtenerlo con frame_trabajo().
- Los nodos del menú (TreeView) se activan por JS buscándolos por texto.
"""
import logging

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Frame,
    Page,
    Playwright,
    sync_playwright,
)

from bot.config import Config

log = logging.getLogger(__name__)


def _aceptar_dialogo(dialog) -> None:
    """Acepta cualquier alert/confirm/prompt del navegador (si no, bloquean)."""
    log.info("Diálogo detectado: '%s' — aceptando.", dialog.message)
    dialog.accept()


def es_error_frame_perdido(e: Exception) -> bool:
    """True si la excepción indica que el frame/page/browser dejó de existir."""
    msg = str(e).lower()
    return any(
        s in msg for s in (
            "frame was detached",
            "target closed",
            "target page, context or browser has been closed",
            "browser has been closed",
            "page has been closed",
        )
    )


def _buscar_en_frames(page: Page, selector: str):
    loc = page.locator(selector)
    if loc.count() == 0:
        for frame in page.frames:
            loc = frame.locator(selector)
            if loc.count() > 0:
                break
    return loc


def _seleccionar_perfil(page: Page, perfil_requerido: str, timeout_ms: int) -> None:
    """Verifica el perfil activo (#ddlProfile) y lo cambia si es necesario."""
    selector = _buscar_en_frames(page, "#ddlProfile")
    if selector.count() == 0:
        log.warning("No se encontró el selector de perfil (#ddlProfile).")
        return

    perfil_actual = selector.evaluate("el => el.options[el.selectedIndex].text")
    log.info("Perfil actual: '%s'", perfil_actual)
    if perfil_requerido in perfil_actual:
        return

    log.info("Cambiando perfil a '%s'...", perfil_requerido)
    selector.select_option(label=perfil_requerido)
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2000)

    selector = _buscar_en_frames(page, "#ddlProfile")
    perfil_nuevo = selector.evaluate("el => el.options[el.selectedIndex].text")
    if perfil_requerido not in perfil_nuevo:
        raise RuntimeError(f"No se pudo cambiar el perfil. Actual: '{perfil_nuevo}'")


def _seleccionar_sucursal(page: Page, sucursal_value: str, timeout_ms: int) -> None:
    """Verifica la sucursal activa (#ddlBranch) y la cambia si es necesario.

    Compara por `value` del <option> (más estable que el texto visible). El cambio
    dispara __doPostBack que recarga la página.
    """
    selector = _buscar_en_frames(page, "#ddlBranch")
    if selector.count() == 0:
        log.warning("No se encontró el selector de sucursal (#ddlBranch).")
        return

    value_actual = selector.evaluate("el => el.value")
    log.info("Sucursal actual: value=%s", value_actual)
    if value_actual == sucursal_value:
        return

    log.info("Cambiando sucursal a value=%s...", sucursal_value)
    selector.select_option(value=sucursal_value)
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(2000)

    # Re-localizar tras el postback (el DOM se reemplaza)
    selector = _buscar_en_frames(page, "#ddlBranch")
    value_nuevo = selector.evaluate("el => el.value") if selector.count() else None
    if value_nuevo != sucursal_value:
        raise RuntimeError(f"No se pudo cambiar la sucursal. Actual: value={value_nuevo}")


class GBPSession:
    """Uso:

        with GBPSession(cfg) as gbp:
            gbp.navegar_a("Generar Nuevo Pedido")
            frame = gbp.frame_trabajo()
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._timeout_ms = cfg.gbp_timeout * 1000

        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None  # popup del ERP (donde vive la sesión)

    def __enter__(self) -> "GBPSession":
        try:
            self.conectar()
        except Exception:
            self.desconectar()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.desconectar()

    # -- Ciclo de vida --------------------------------------------------

    def conectar(self) -> None:
        """Lanza el navegador, hace login y selecciona perfil/sucursal si están configurados."""
        cfg = self._cfg
        log.info("Lanzando navegador (headless=%s)...", cfg.headless)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=cfg.headless,
            args=["--start-maximized"],
        )
        # accept_downloads: para capturar el PDF del presupuesto sin diálogo del SO
        self.context = self._browser.new_context(no_viewport=True, accept_downloads=True)
        page = self.context.new_page()
        page.on("dialog", _aceptar_dialogo)

        # Navegar → GBP abre un popup automático
        log.info("Navegando a GBP...")
        with self.context.expect_page(timeout=self._timeout_ms) as popup_info:
            page.goto(cfg.gbp_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        popup = popup_info.value
        popup.on("dialog", _aceptar_dialogo)
        popup.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        # Guardarlo ya, para que desconectar() pueda hacer logout aunque falle algo después
        self.page = popup

        popup.locator("#lnkLogin").wait_for(state="visible", timeout=self._timeout_ms)
        popup.locator("#lnkLogin").click()
        popup.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)

        log.info("Ingresando credenciales...")
        campo_usuario = popup.locator("#UserName")
        campo_usuario.wait_for(state="visible", timeout=self._timeout_ms)
        campo_usuario.fill(cfg.gbp_user)

        campo_password = popup.locator("#Password")
        campo_password.wait_for(state="visible", timeout=self._timeout_ms)
        # press_sequentially porque el campo tiene onkeyup JS handler
        campo_password.press_sequentially(cfg.gbp_pass, delay=50)

        boton_login = popup.locator("#wucIB_Login_LB")
        if boton_login.count() == 0:
            boton_login = popup.get_by_role("link", name="LogIn")
        if boton_login.count() == 0:
            boton_login = popup.get_by_role("link", name="Entrar")
        boton_login.click(timeout=self._timeout_ms)

        popup.wait_for_timeout(2000)
        if popup.locator("text=not valid").count() > 0:
            raise RuntimeError("Login fallido: usuario o contraseña incorrectos.")

        # Si GBP fuerza cambio de contraseña, nunca llega a wfmMain → timeout acá.
        log.info("Esperando carga de wfmMain...")
        try:
            popup.wait_for_url("**/wfmMain**", timeout=self._timeout_ms)
        except Exception as e:
            raise RuntimeError(
                "El login no llegó a wfmMain. ¿Expiró la contraseña de GBP? "
                "Revisá ERP_PASSWORD en el .env."
            ) from e
        log.info("Login exitoso.")

        if cfg.gbp_perfil:
            _seleccionar_perfil(popup, cfg.gbp_perfil, self._timeout_ms)
        if cfg.gbp_sucursal:
            _seleccionar_sucursal(popup, cfg.gbp_sucursal, self._timeout_ms)

    def desconectar(self) -> None:
        """Logout (#lnkLogout) y cierre del navegador. SIEMPRE llamarlo, incluso ante error."""
        if self.page is not None:
            try:
                for frame in [self.page] + self.page.frames:
                    salir = frame.locator("#lnkLogout")
                    if salir.count() > 0:
                        salir.click(timeout=self._timeout_ms)
                        log.info("Logout exitoso.")
                        self.page.wait_for_timeout(2000)
                        break
                else:
                    log.warning("No se encontró #lnkLogout. Revisá que la sesión se haya cerrado.")
            except Exception as e:
                log.warning("Error al intentar logout: %s", e)

        if self._browser is not None:
            try:
                self._browser.close()
            except Exception as e:
                log.warning("Error al cerrar navegador: %s", e)

        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass

        self.page = None
        self._browser = None
        self.context = None
        self._pw = None

    # -- Navegación -----------------------------------------------------

    def frame_trabajo(self) -> Frame:
        """Devuelve frameWorkingArea actual (re-obtenerlo después de cada postback)."""
        assert self.page is not None, "Llamar a conectar() primero."
        frame = self.page.frame("frameWorkingArea")
        if frame is None:
            raise RuntimeError("Se perdió frameWorkingArea.")
        return frame

    def navegar_a(self, texto_nodo: str, url_contiene: str | None = None) -> Frame:
        """Activa un nodo del menú lateral (TreeView) buscándolo por texto.

        No se clickea el nodo (congela la página): se usa TreeView_SelectNode + el
        __doPostBack del href. No se usan IDs (TreeView1tNNN), cambian por sucursal.
        Si se pasa `url_contiene`, espera a que frameWorkingArea cargue esa página.
        """
        assert self.page is not None, "Llamar a conectar() primero."
        log.info("Navegando a '%s'...", texto_nodo)
        self.page.wait_for_timeout(2000)

        frame_menu = self.page.frame("frameMenu")
        if frame_menu is None:
            raise RuntimeError("No se encontró frameMenu.")
        frame_menu.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)

        frame_menu.evaluate(r"""
        (objetivo) => {
            objetivo = objetivo.trim().toLowerCase();
            const candidatos = document.querySelectorAll('a[id^="TreeView1t"]');
            let el = null;
            for (const a of candidatos) {
                const txt = (a.innerText || a.textContent || '').trim().toLowerCase();
                if (txt === objetivo) { el = a; break; }
            }
            if (!el) {
                const disponibles = Array.from(candidatos)
                    .map(a => `${a.id}="${(a.innerText||a.textContent||'').trim()}"`)
                    .join(' | ');
                throw new Error(
                    `No encontré el nodo "${objetivo}" en el TreeView. ` +
                    'Nodos disponibles en frameMenu: ' + (disponibles || '(ninguno)')
                );
            }

            if (typeof TreeView_SelectNode === 'function' &&
                typeof TreeView1_Data !== 'undefined') {
                TreeView_SelectNode(TreeView1_Data, el, el.id);
            }

            const href = el.getAttribute('href');
            if (href && href.indexOf('__doPostBack') >= 0) {
                eval(href.replace('javascript:', ''));
            } else if (href) {
                const target = el.getAttribute('target') || 'frameWorkingArea';
                const f = window.parent.frames[target] || window.parent.frames['frameWorkingArea'];
                if (f) f.location.href = href;
            }
        }
        """, texto_nodo)

        self.page.wait_for_timeout(2000)
        for _ in range(15):
            frame = self.page.frame("frameWorkingArea")
            if frame is not None and (url_contiene is None or url_contiene in (frame.url or "")):
                frame.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
                log.info("frameWorkingArea: %s", frame.url)
                return frame
            self.page.wait_for_timeout(1000)
        raise RuntimeError(f"frameWorkingArea no cargó '{url_contiene or texto_nodo}'.")
