"""
logging_config.py
================================================
Configuración centralizada del logger del bot.

Uso:
    from modules.logging_config import setup_logging
    setup_logging()                      # nivel INFO por defecto
    setup_logging(level="DEBUG")         # con archivo + verbose

    # En cualquier módulo:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Operación ejecutada")
    logger.warning("Algo recuperable")
    logger.error("Algo grave")

Diseño:
    - INFO sale como mensaje pelado (preserva los emojis del bot).
    - WARNING/ERROR/CRITICAL llevan prefijo [LEVEL module] timestamp.
    - Si LOG_FILE está definido en env, también escribe a archivo (rotación 5MB x 5).
================================================
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


class TerseFormatter(logging.Formatter):
    """INFO sin prefijo (limpio); WARNING+ con prefijo legible."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno < logging.WARNING:
            return record.getMessage()
        return (
            f"[{record.levelname} {record.name}] "
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.getMessage()}"
        )


def setup_logging(level: str | int | None = None, log_file: str | None = None) -> None:
    """
    Configura el logger raíz.

    Args:
        level: nivel mínimo (str o int). Si None, lee LOG_LEVEL del env (default INFO).
        log_file: ruta al archivo. Si None, lee LOG_FILE del env. Si tampoco, sin archivo.
    """
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    if isinstance(level, str):
        level = getattr(logging, level, logging.INFO)

    if log_file is None:
        log_file = os.getenv("LOG_FILE", "").strip() or None

    root = logging.getLogger()
    root.setLevel(level)

    # Limpiar handlers previos para que llamadas idempotentes no dupliquen líneas.
    for h in list(root.handlers):
        root.removeHandler(h)

    # Stream handler (consola).
    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level)
    stream.setFormatter(TerseFormatter())
    root.addHandler(stream)

    # File handler con rotación (5 MB, 5 backups) — opcional.
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(file_handler)

    # Silenciar libs ruidosas.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
