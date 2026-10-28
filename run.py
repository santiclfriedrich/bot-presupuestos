"""Entrypoint del bot.

    python run.py                          # loop: pollea cada POLL_INTERVAL_SECONDS (8 a 19 h)
    python run.py --once                   # procesa lo que haya en cola y sale
    python run.py --once --max-jobs 1      # procesa UN solo job y sale
    python run.py --modo real              # guarda en GBP aunque el .env diga DRY_RUN=true
    python run.py --modo dryrun            # no guarda aunque el .env diga DRY_RUN=false
"""
import argparse
import dataclasses
import logging
import msvcrt
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from bot.config import ROOT, load_config
from bot.runner import run


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers = [TimedRotatingFileHandler(log_dir / "bot.log", when="midnight",
                                         backupCount=30, encoding="utf-8")]
    if sys.stdout is not None:  # con pythonw.exe (tarea programada) no hay consola
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def tomar_lock():
    """Una sola instancia del bot a la vez (comparten la cuenta de GBP).
    El lock lo libera Windows al terminar el proceso, aunque muera mal."""
    archivo = open(ROOT / "logs" / "bot.lock", "w")
    try:
        msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        logging.error("Ya hay otra instancia del bot corriendo. Salgo.")
        sys.exit(1)
    return archivo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="procesar lo que haya y salir")
    parser.add_argument("--max-jobs", type=int, default=None, help="máximo de jobs por sesión de GBP")
    parser.add_argument("--modo", choices=("dryrun", "real"), default=None,
                        help="pisa DRY_RUN del .env (dryrun = no guarda, real = guarda)")
    args = parser.parse_args()

    setup_logging()
    lock = tomar_lock()  # noqa: F841 (se mantiene abierto mientras viva el proceso)
    cfg = load_config()
    if args.modo:
        cfg = dataclasses.replace(cfg, dry_run=(args.modo == "dryrun"))
    if args.once:
        run(cfg, once=True, max_jobs=args.max_jobs)
        return
    # Modo servicio: si algo inesperado tira el loop, se registra y se reinicia.
    while True:
        try:
            run(cfg, max_jobs=args.max_jobs)
        except KeyboardInterrupt:
            logging.info("Bot detenido (Ctrl+C).")
            return
        except Exception:
            logging.exception("El loop del bot se cayó; reinicio en 60 s.")
            time.sleep(60)


if __name__ == "__main__":
    main()
