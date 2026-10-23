"""Cliente HTTP del CRM. Todas las conexiones son salientes (el bot nunca recibe nada)."""
import logging

import httpx

log = logging.getLogger(__name__)

API = "/api/v1/gbp-bot"


class CRMClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-Bot-Token": token},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def next_job(self) -> dict | None:
        """Pide el próximo presupuesto. None si no hay nada (204).

        OJO: un 200 marca el job "en_proceso" del lado del CRM (lo recicla a los 15 min
        si no reportamos resultado/error).
        """
        r = self._http.get(f"{API}/jobs/next")
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def post_resultado(self, job_id: int, numero_gbp: str, pdf: bytes) -> None:
        r = self._http.post(
            f"{API}/jobs/{job_id}/resultado",
            data={"numero_gbp": numero_gbp},
            files={"pdf": (f"presupuesto_{numero_gbp}.pdf", pdf, "application/pdf")},
        )
        r.raise_for_status()

    def post_error(self, job_id: int, mensaje: str) -> None:
        r = self._http.post(f"{API}/jobs/{job_id}/error", json={"mensaje": mensaje})
        r.raise_for_status()
