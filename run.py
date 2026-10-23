"""Entrypoint del bot.

    python run.py          # pollea cada POLL_INTERVAL_SECONDS
    python run.py --once   # un solo poll y sale
"""
import argparse
import logging
import sys

from bot.config import ROOT, load_config
from bot.runner import run


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "bot.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="un solo poll y salir")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(require_gbp=False)  # FASE 0: GBP todavía no se usa
    run(cfg, once=args.once)


if __name__ == "__main__":
    main()
