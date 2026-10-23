"""Loop principal: poll al CRM → (cargar en GBP) → callback.

FASE 0: sólo pollea e imprime el job. Al recibir uno, corta: ese job queda
"en_proceso" en el CRM y vuelve a "pendiente" solo a los 15 min.
"""
import json
import logging
import time

import httpx

from bot.config import Config
from bot.crm_client import CRMClient

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300


def run(cfg: Config, once: bool = False) -> None:
    crm = CRMClient(cfg.crm_base_url, cfg.bot_token)
    backoff = cfg.poll_interval
    try:
        while True:
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
                log.info("Job recibido:\n%s", json.dumps(job, indent=2, ensure_ascii=False))
                log.info("FASE 0: no se procesa. Queda 'en_proceso' en el CRM (se recicla en 15 min).")
                return

            log.info("Sin jobs pendientes.")
            if once:
                return
            time.sleep(backoff)
    finally:
        crm.close()
