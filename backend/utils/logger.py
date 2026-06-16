"""G-Mini Agent — Logger centralizado con loguru."""

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Timestamp fijo capturado al arrancar el proceso. Cada EJECUCION del backend
# genera sus propios archivos de log (fecha + hora), no uno por dia. Asi cada
# corrida queda aislada y es trivial encontrar el log exacto de un problema
# puntual. Formato: gmini_2026-06-09_17-34-02.log
RUN_STAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
MAIN_LOG = LOG_DIR / f"gmini_{RUN_STAMP}.log"
DETAILED_LOG = LOG_DIR / f"gmini_detailed_{RUN_STAMP}.log"

# Remover handler default
logger.remove()

# Consola con colores
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> — <level>{message}</level>",
    level="DEBUG",
    colorize=True,
)

# Archivo principal por ejecucion (DEBUG). Sin rotation por tiempo: filename
# unico por corrida. rotation por tamaño evita archivos gigantes en sesiones largas.
logger.add(
    MAIN_LOG,
    rotation="20 MB",
    retention="14 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} — {message}",
    level="DEBUG",
    encoding="utf-8",
)

# Archivo ultra-detallado por ejecucion — TRACE level, todo el detalle posible
logger.add(
    DETAILED_LOG,
    rotation="50 MB",
    retention="7 days",
    compression="zip",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "pid={process} tid={thread} | "
        "{name}:{function}:{line} — {message}"
    ),
    level="TRACE",
    encoding="utf-8",
)

logger.info(f"Logger inicializado — run_stamp={RUN_STAMP} | main={MAIN_LOG.name} | detailed={DETAILED_LOG.name}")
