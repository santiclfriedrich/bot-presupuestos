"""Lee la configuración del .env (en la raíz del repo)."""
import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta {name} en el .env ({ROOT / '.env'})")
    return value


@dataclass(frozen=True)
class Config:
    crm_base_url: str
    bot_token: str
    gbp_url: str
    gbp_user: str
    gbp_pass: str
    gbp_perfil: str      # vacío = no tocar el perfil
    gbp_sucursal: str    # value de #ddlBranch; vacío = no tocar
    gbp_timeout: int
    poll_interval: int
    headless: bool
    dry_run: bool        # True: carga todo, saca captura y NO guarda
    hora_inicio: time    # fuera de este horario no se toman jobs
    hora_fin: time


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def _hora(name: str, default: str) -> time:
    return datetime.strptime(os.getenv(name, default).strip(), "%H:%M").time()


def load_config(require_gbp: bool = True) -> Config:
    """require_gbp=False permite correr sólo el polling sin credenciales de GBP."""
    get_gbp = _required if require_gbp else (lambda n: os.getenv(n, "").strip())
    return Config(
        crm_base_url=_required("CRM_BASE_URL").rstrip("/"),
        bot_token=os.getenv("BOT_TOKEN", "").strip() or _required("GBP_BOT_TOKEN"),
        gbp_url=get_gbp("ERP_URL"),
        gbp_user=get_gbp("ERP_USER"),
        gbp_pass=get_gbp("ERP_PASSWORD"),
        gbp_perfil=os.getenv("GBP_PERFIL", "").strip(),
        gbp_sucursal=os.getenv("GBP_SUCURSAL", "").strip(),
        gbp_timeout=int(os.getenv("GBP_TIMEOUT_SECONDS", "30")),
        poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "20")),
        headless=_flag("HEADLESS", "false"),
        dry_run=_flag("DRY_RUN", "true"),
        hora_inicio=_hora("HORA_INICIO", "08:00"),
        hora_fin=_hora("HORA_FIN", "19:00"),
    )
