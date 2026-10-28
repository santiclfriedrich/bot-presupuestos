"""Prueba de carga de un presupuesto HARDCODEADO (no pasa por el CRM).

Por defecto dry-run: carga todo, saca captura + resumen en logs/dryrun_<stamp>/
y cierra con "Salir" sin guardar. Con --guardar GUARDA en GBP de producción.

    .venv\\Scripts\\python scripts\\probar_presupuesto.py
    .venv\\Scripts\\python scripts\\probar_presupuesto.py --guardar
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import ROOT, load_config  # noqa: E402
from bot.gbp import GBPSession  # noqa: E402
from bot.presupuesto import CargadorPresupuesto  # noqa: E402

JOB = {
    "id": 0,
    "cliente_numero": "46",
    "entrega_por": "Retiro TML - Garin",
    "moneda": "USD",
    "documento": "Presupuesto USD",
    "fecha_entrega": "today",
    "items": [
        {"tipo": "normal", "sku": "VENKAN0017", "descripcion": "Ventilador", "cantidad": 2,
         "precio": 36, "moneda": "USD", "iva": None},
        {"tipo": "manual", "sku": None, "descripcion": "Flete (prueba bot)", "cantidad": 2,
         "precio": 5000, "moneda": "ARS", "iva": 21},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guardar", action="store_true",
                        help="GUARDA DE VERDAD en GBP (producción) y captura PDF + número")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    modo = "guardado" if args.guardar else "dryrun"
    carpeta = ROOT / "logs" / f"{modo}_{datetime.now():%Y%m%d_%H%M%S}"
    with GBPSession(load_config(), permitir_guardar=args.guardar) as gbp:
        res = CargadorPresupuesto(gbp, carpeta, dry_run=not args.guardar).cargar(JOB)
    if args.guardar:
        print(f"\nGUARDADO: {res['numero_gbp']} — PDF {len(res['pdf'])} bytes en {carpeta}")


if __name__ == "__main__":
    main()
