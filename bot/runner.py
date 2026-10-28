"""Loop principal: poll al CRM → cargar en GBP → callback (/resultado o /error).

- Sólo toma jobs dentro del horario (HORA_INICIO–HORA_FIN).
- Abre UNA sesión de GBP y procesa todos los jobs que haya en cola; cuando la
  cola se vacía (o termina el horario) hace logout y vuelve a pollear.
- Idempotencia: cada guardado exitoso queda en logs/guardados.jsonl. Si el CRM
  vuelve a mandar un job ya guardado (p. ej. falló el /resultado y lo recicló),
  se reenvía el resultado SIN volver a cargarlo en GBP.
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from bot.config import ROOT, Config
from bot.crm_client import CRMClient
from bot.gbp import GBPSession
from bot.presupuesto import CargadorPresupuesto, ErrorCarga

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300
REGISTRO = ROOT / "logs" / "guardados.jsonl"


def en_horario(cfg: Config) -> bool:
    return cfg.hora_inicio <= datetime.now().time() < cfg.hora_fin


# -- Registro local de guardados (idempotencia) ------------------------------

def _ya_guardado(job_id: int) -> dict | None:
    if not REGISTRO.exists():
        return None
    for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
        try:
            reg = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if reg.get("job_id") == job_id:
            return reg
    return None


def _registrar_guardado(job_id: int, numero: str, pdf: bytes) -> Path:
    carpeta = ROOT / "logs" / "pdfs"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta_pdf = carpeta / f"job_{job_id}.pdf"
    ruta_pdf.write_bytes(pdf)
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"job_id": job_id, "numero_gbp": numero, "pdf": str(ruta_pdf),
                            "fecha": datetime.now().isoformat(timespec="seconds")}) + "\n")
    return ruta_pdf


# -- Callbacks al CRM ---------------------------------------------------------

def _con_reintentos(accion, descripcion: str, intentos: int = 4) -> bool:
    for n in range(1, intentos + 1):
        try:
            accion()
            return True
        except Exception as e:
            log.warning("%s falló (intento %d/%d): %s", descripcion, n, intentos, e)
            time.sleep(5 * n)
    log.error("%s: no se pudo avisar al CRM.", descripcion)
    return False


def _reportar_resultado(crm: CRMClient, job_id: int, numero: str, pdf: bytes) -> None:
    _con_reintentos(lambda: crm.post_resultado(job_id, numero, pdf), f"/resultado job {job_id}")


def _reportar_error(crm: CRMClient, job_id: int, mensaje: str) -> None:
    log.error("Job %s → error: %s", job_id, mensaje)
    _con_reintentos(lambda: crm.post_error(job_id, mensaje[:1000]), f"/error job {job_id}")


# -- Procesamiento ------------------------------------------------------------

def procesar_job(cfg: Config, gbp: GBPSession, crm: CRMClient, job: dict) -> None:
    job_id = job["id"]
    log.info("== Job %s: cliente %s, %d ítems%s", job_id, job.get("cliente_numero"),
             len(job.get("items") or []),
             f" (parte {job['parte']}/{job['total_partes']})" if job.get("parte") else "")

    previo = _ya_guardado(job_id)
    if previo:
        log.warning("Job %s ya estaba guardado en GBP (%s): reenvío el resultado sin recargar.",
                    job_id, previo["numero_gbp"])
        _reportar_resultado(crm, job_id, previo["numero_gbp"], Path(previo["pdf"]).read_bytes())
        return

    carpeta = ROOT / "logs" / "jobs" / datetime.now().strftime("%Y%m%d")
    cargador = CargadorPresupuesto(gbp, carpeta, dry_run=cfg.dry_run)
    try:
        gbp.asegurar_sucursal(str(job.get("sucursal") or cfg.gbp_sucursal or "1"))
        res = cargador.cargar(job)
        if cfg.dry_run:
            _reportar_error(crm, job_id,
                            f"DRY-RUN: cargado en GBP SIN guardar ({len(res['items'])} ítems, "
                            f"total {res['total']}). Captura en la PC del bot: {res['captura']}")
            return
        _registrar_guardado(job_id, res["numero_gbp"], res["pdf"])
        log.info("Job %s guardado como %s.", job_id, res["numero_gbp"])
        _reportar_resultado(crm, job_id, res["numero_gbp"], res["pdf"])
    except ErrorCarga as e:
        _reportar_error(crm, job_id, str(e))
    except Exception as e:
        log.exception("Error inesperado en job %s", job_id)
        mensaje = f"Error inesperado del bot: {e}"
        if cargador.guardado:
            mensaje = ("ATENCIÓN: el presupuesto pudo haber quedado GUARDADO en GBP; "
                       "verificar antes de reintentar. " + mensaje)
        _reportar_error(crm, job_id, mensaje)
    finally:
        gbp.cerrar_ventanas()


def procesar_cola(cfg: Config, crm: CRMClient, primer_job: dict, max_jobs: int | None = None) -> int:
    """Abre una sesión de GBP y procesa jobs mientras haya en cola, estemos en horario
    y no se llegue a max_jobs. Devuelve cuántos jobs procesó."""
    job = primer_job
    pendiente = True  # el job actual todavía no fue reportado al CRM
    procesados = 0
    try:
        with GBPSession(cfg, permitir_guardar=not cfg.dry_run) as gbp:
            while job is not None:
                procesar_job(cfg, gbp, crm, job)
                procesados += 1
                pendiente = False
                if not en_horario(cfg) or (max_jobs is not None and procesados >= max_jobs):
                    break
                try:
                    job = crm.next_job()
                    pendiente = job is not None
                except httpx.HTTPError as e:
                    log.warning("No se pudo pedir el próximo job: %s", e)
                    job = None
    except Exception as e:
        # Falló la sesión (login, navegador caído, logout): el job tomado vuelve como error
        log.exception("Falló la sesión de GBP")
        if pendiente and job is not None and not _ya_guardado(job["id"]):
            _reportar_error(crm, job["id"], f"No se pudo operar GBP: {e}")
    return procesados


def run(cfg: Config, once: bool = False, max_jobs: int | None = None) -> None:
    log.info("Bot de presupuestos iniciado (dry_run=%s, horario %s–%s).",
             cfg.dry_run, cfg.hora_inicio.strftime("%H:%M"), cfg.hora_fin.strftime("%H:%M"))
    crm = CRMClient(cfg.crm_base_url, cfg.bot_token)
    backoff = cfg.poll_interval
    try:
        while True:
            if not en_horario(cfg):
                log.info("Fuera de horario.")
                if once:
                    return
                time.sleep(60)
                continue

            try:
                job = crm.next_job()
                backoff = cfg.poll_interval
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise RuntimeError("El CRM rechazó el BOT_TOKEN (401/403). Revisá el .env.") from e
                log.warning("CRM respondió %s: %s", e.response.status_code, e.response.text[:300])
                job = None
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            except httpx.TransportError as e:
                log.warning("No se pudo contactar al CRM (%s). Reintento en %ss", e, backoff)
                job = None
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

            if job is not None:
                procesar_cola(cfg, crm, job, max_jobs)
                if once:
                    return
            elif once:
                log.info("Sin jobs pendientes.")
                return
            time.sleep(backoff)
    finally:
        crm.close()
