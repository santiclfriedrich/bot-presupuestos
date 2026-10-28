# Bot de presupuestos CRM → GBP

Carga en GBP (GlobalBluePoint) los presupuestos que encola el CRM y le devuelve
el PDF y el número de cada uno. Corre en esta PC con Windows, con el navegador
visible, y sólo hace conexiones salientes: cada 20 s le pregunta al CRM si hay
trabajo.

```
CRM (Railway) ──GET /jobs/next──▶ bot ──Playwright──▶ GBP "Generar Nuevo Pedido"
      ▲                                                       │
      └──── POST /jobs/{id}/resultado (número + PDF) ◀────────┘   (o /error)
```

Cada job trae como máximo 20 ítems y genera un presupuesto en GBP. El CRM parte
los Excel grandes; el formato está en [docs/spec_excel_crm.md](docs/spec_excel_crm.md).

## Operación diaria

El bot arranca solo al iniciar sesión en Windows (tarea programada "Bot
Presupuestos GBP") y sólo toma jobs entre `HORA_INICIO` y `HORA_FIN`.

| Qué | Cómo |
|---|---|
| Ver qué está haciendo | `logs\bot.log` (rota todos los días, guarda 30) |
| Ver si está corriendo | `Get-ScheduledTask "Bot Presupuestos GBP"` → `Running` |
| Detenerlo | `Stop-ScheduledTask "Bot Presupuestos GBP"` |
| Arrancarlo sin reiniciar la PC | `Start-ScheduledTask "Bot Presupuestos GBP"` |
| Correrlo a mano (con la tarea detenida) | `.venv\Scripts\python run.py --once` |
| Probar sin guardar | `.venv\Scripts\python run.py --once --max-jobs 1 --modo dryrun` |

Puede correr una sola instancia a la vez (`logs\bot.lock`). **No uses la cuenta
"Bot Presupuesto" de GBP a mano mientras el bot trabaja**: GBP no permite dos
sesiones del mismo usuario.

### Evidencia que deja cada job
- `logs\jobs\<fecha>\job_<id>.png` / `.json`: el pedido completo antes de guardar.
- `logs\jobs\<fecha>\job_<id>_ERROR.png`: la pantalla en el momento del error.
- `logs\jobs\<fecha>\job_<id>_guardado.*`: el PDF y el diagnóstico de después de guardar.
- `logs\guardados.jsonl` + `logs\pdfs\`: registro de lo guardado. Si el CRM
  reenvía un job ya guardado, el bot reenvía el resultado **sin cargarlo de nuevo**.

## Qué hace con cada job

1. Selecciona la sucursal del job (por defecto `GBP_SUCURSAL`).
2. Abre "Generar Nuevo Pedido" desde el menú; GBP lo abre en una ventana nueva.
3. Carga el cliente (con Enter) y verifica que GBP lo haya encontrado. Después
   elige el documento (`Presupuesto U$D` / `Presupuesto ARS`), "Entregar Por" y
   la fecha de entrega desde el calendario, con "Today".
4. Por cada ítem abre "Añadir Ítem" desde cero:
   - **normal**: SKU + Enter y verifica que GBP haya traído la descripción.
   - **manual**: descripción + IVA (21 / 10,5).

   Después carga la cantidad, la **moneda de la línea** y recién entonces el
   **precio neto**, y toca Aceptar.
5. Valida contra el pie de la pantalla: "Ingresos" = cantidad de líneas y
   "Cantidad" = suma de unidades. Tilda "Imprimir al Guardar".
6. Guarda **una sola vez**. GBP cierra la ventana del pedido y abre el PDF en una
   pestaña nueva. El bot lo baja con la sesión y lee el número del PDF
   (`#1-598177`).
7. Ante cualquier problema cancela, sale con "Salir" y reporta `/error` con un
   mensaje claro. Si el problema fue **después** de guardar, el mensaje dice
   "GUARDADO en GBP… verificar antes de reintentar".

## Instalación desde cero

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env      # completar ERP_* (cuenta "Bot Presupuesto") y BOT_TOKEN
powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea.ps1
```

## Configuración (`.env`)

| Variable | Default | |
|---|---|---|
| `ERP_URL`, `ERP_USER`, `ERP_PASSWORD` | — | Cuenta dedicada "Bot Presupuesto" |
| `CRM_BASE_URL`, `BOT_TOKEN` (o `GBP_BOT_TOKEN`) | — | Cola del CRM |
| `GBP_SUCURSAL` | — | Sucursal cuando el job no trae una (`1` = 01.ARG. COLOR) |
| `DRY_RUN` | `true` | `true` = carga todo pero no guarda |
| `HORA_INICIO` / `HORA_FIN` | `08:00` / `19:00` | En esta PC: `18:50`, porque se apaga cerca de las 19 |
| `POLL_INTERVAL_SECONDS` | `20` | |

## Herramientas de desarrollo

- `scripts\explorar_pedido.py`: recorre la pantalla de pedidos y guarda los
  campos y capturas, **sin guardar**.
- `scripts\probar_presupuesto.py [--guardar]`: carga un presupuesto armado en el
  código, sin pasar por el CRM.

## Particularidades de GBP (aprendidas a los golpes)

- Salir siempre con **"Salir"** / `#lnkLogout`, nunca con la X: si no, GBP
  bloquea el usuario unos minutos. El bot cierra cada ventana con "Salir"
  aunque haya un error, y cancela antes los modales y carteles que tapan el botón.
- Los nodos del menú traen la URL en `data-href` y, a veces, la abren en una
  ventana nueva por medio de una página `.htm` intermedia que se cierra sola.
- El modal de ítems es un iframe (`wfmSaleOrderDetail.aspx`), y la ventana
  principal tiene sus propios `#wucItemSelection_*` (la barra EAN): hay que
  ubicar siempre el iframe del modal.
- En el modal de ítems, el tipo (Normal / Manual) se elige **antes** de tipear:
  después GBP no deja cambiarlo.
- GBP muestra carteles propios (`WucMsgBox`, por ejemplo "El cliente está siendo
  usado por…") que no son diálogos del navegador. El bot los lee, toca Ok y aborta.
- Mientras se ve "Procesando, espere por favor…", la grilla no está actualizada.
- La grilla muestra unas 10 filas: no sirve para validar descripciones.
- Después de guardar, GBP puede abrir ventanas de proceso (`...CloseWindow=1`).
  No se cierran a mano: el bot espera a que se cierren solas.
- GBP usa coma decimal y guarda el precio **con IVA** redondeado a 2 decimales;
  el neto que muestra puede diferir en menos de un centavo del que se cargó.
