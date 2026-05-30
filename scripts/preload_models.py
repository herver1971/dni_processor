#!/usr/bin/env python3
"""
Pre-descarga de modelos para deployment.

Se corre UNA vez durante el deploy, antes de habilitar el servicio
systemd. Garantiza que las primeras requests reales del usuario no
tengan que esperar 30-60s de descarga lazy de EasyOCR (~500MB) ni
~10MB del detector de caras.

Idempotente: si los archivos ya están en cache, no descarga nada.

Uso:
    # Desde la raíz del proyecto, con el venv activado:
    python scripts/preload_models.py

    # O sin activar venv:
    /home/hernan/dni_processor/.venv/bin/python \\
        /home/hernan/dni_processor/scripts/preload_models.py

Variables de entorno relevantes:
    DNI_MODEL_CACHE_DIR — Path del cache del detector de caras.
                          Default: ~/.cache/dni_processor

EasyOCR usa su propio cache (~/.EasyOCR/model), no configurable desde
nuestras Settings — es interno de la librería.

Exit codes:
    0 — todos los modelos disponibles al finalizar
    1 — al menos un modelo falló y no quedó disponible
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Permite que el script se ejecute desde cualquier directorio:
# el directorio padre de scripts/ es la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.ocr import (  # noqa: E402
    EASYOCR_MODEL_DIR,
    get_reader,
    is_ocr_model_cached,
)
from app.core.vision import (  # noqa: E402
    _ensure_face_model,
    is_face_model_cached,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("preload_models")


def preload_face_model(cache_dir: Path) -> bool:
    """
    Descarga el detector de caras a `cache_dir` si no está.

    Returns True si quedó disponible al finalizar, False si falló.
    """
    if is_face_model_cached(cache_dir):
        logger.info("Detector de caras: ya en cache (%s)", cache_dir)
        return True
    logger.info("Detector de caras: descargando a %s ...", cache_dir)
    try:
        proto, weights = _ensure_face_model(cache_dir)
        logger.info("Detector de caras: OK")
        logger.info("  prototxt: %s (%d bytes)", proto, proto.stat().st_size)
        logger.info("  weights:  %s (%d bytes)", weights, weights.stat().st_size)
        return True
    except Exception as e:
        logger.error("Detector de caras: falló (%s)", e)
        return False


def preload_ocr_model() -> bool:
    """
    Fuerza la instanciación de EasyOCR Reader, lo que dispara la
    descarga de los modelos (~500MB) a `~/.EasyOCR/model/`.

    Returns True si los modelos quedan disponibles al finalizar.
    """
    if is_ocr_model_cached():
        logger.info("EasyOCR: ya en cache (%s)", EASYOCR_MODEL_DIR)
        return True
    logger.info(
        "EasyOCR: descargando modelos (~500MB) a %s ...",
        EASYOCR_MODEL_DIR,
    )
    logger.info("Esto puede tardar 1-3 minutos según la conexión.")
    try:
        # `get_reader()` instancia EasyOCR, que descarga lazy.
        reader = get_reader()  # noqa: F841
        if is_ocr_model_cached():
            logger.info("EasyOCR: OK")
            files = sorted(EASYOCR_MODEL_DIR.glob("*.pth"))
            for f in files:
                size_mb = f.stat().st_size / (1024 * 1024)
                logger.info("  %s (%.1f MB)", f.name, size_mb)
            return True
        logger.error(
            "EasyOCR: Reader instanciado pero no se detectan archivos "
            ".pth en %s. Verificá manualmente.",
            EASYOCR_MODEL_DIR,
        )
        return False
    except Exception as e:
        logger.error("EasyOCR: falló (%s)", e)
        return False


def main() -> int:
    settings = get_settings()
    logger.info("DNI Processor — pre-descarga de modelos")
    logger.info("Cache del detector de caras: %s", settings.model_cache_dir)
    logger.info("Cache de EasyOCR: %s", EASYOCR_MODEL_DIR)
    logger.info("---")

    face_ok = preload_face_model(settings.model_cache_dir)
    ocr_ok = preload_ocr_model()

    logger.info("---")
    if face_ok and ocr_ok:
        logger.info("Todos los modelos disponibles. Listo para deploy.")
        return 0
    logger.error(
        "Al menos un modelo falló (face=%s, ocr=%s). Revisá los "
        "mensajes arriba.",
        face_ok,
        ocr_ok,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
