# Bot de presupuestos CRM → GBP

Bot que carga en GBP (GlobalBluePoint) los presupuestos encolados por el CRM y le
devuelve el PDF + N° de presupuesto. Corre en Windows, headful, y sólo hace
conexiones salientes (pollea al CRM cada ~20s).

## Instalar

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # y completar ERP_*, BOT_TOKEN
```

## Correr

```powershell
python run.py --once   # un poll y sale
python run.py          # loop cada POLL_INTERVAL_SECONDS
```

Logs en `logs/bot.log`.

## Estructura

- `bot/config.py` — lee el `.env`
- `bot/crm_client.py` — `next_job()`, `post_resultado()`, `post_error()`
- `bot/gbp.py` — sesión de GBP: login, perfil/sucursal, logout seguro, navegación por el menú
- `bot/runner.py` — loop: poll → cargar → callback

> Estado: FASE 0 — sólo pollea e imprime el job. Si hay uno, el CRM lo marca
> `en_proceso` y lo recicla a `pendiente` a los 15 min.

## Reglas del ERP

- Salir siempre con "Salir" (`#lnkLogout`), nunca cerrando el navegador: si no, GBP queda bloqueado ~5 min.
- Todo pasa en un popup; los frames (`frameMenu`, `frameWorkingArea`) se reemplazan en cada postback → re-obtenerlos.
- Menú: activar nodos por texto vía `TreeView_SelectNode` + `__doPostBack`, nunca click directo ni IDs hardcodeados.
- Si el login no llega a `wfmMain`, probablemente GBP pidió cambio de contraseña → actualizar `ERP_PASSWORD`.
- Un solo job a la vez (comparten la sesión de GBP).
