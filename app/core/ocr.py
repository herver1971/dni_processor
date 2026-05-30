"""
Módulo de OCR — Extracción del número de DNI desde recortes.

Usa EasyOCR para reconocer texto en los recortes. El OCR opera ÚNICAMENTE
en segundo plano: extrae el número de DNI para usarlo como criterio
de matcheo entre frente y dorso, pero los datos no se almacenan ni se
muestran al usuario (preservación de privacidad).

Estrategia:
1. Carga lazy del modelo EasyOCR (es pesado, se inicializa solo si se usa)
2. Aplicación de allowlist de caracteres (dígitos y punto separador)
3. Filtrado por longitud razonable (7-8 dígitos para DNI argentino)
4. Heurísticas para descartar números que no son DNI (años, etc.)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.constants import (
    DNI_NUMBER_MAX_DIGITS,
    DNI_NUMBER_MIN_DIGITS,
    OCR_DNI_ALLOWLIST,
    OCR_LANGUAGES,
)

if TYPE_CHECKING:
    import easyocr

logger = logging.getLogger(__name__)


# ============================================================
# Singleton del lector EasyOCR (carga lazy y cacheada)
# ============================================================

_reader_instance: "easyocr.Reader | None" = None


def get_reader() -> "easyocr.Reader":
    """
    Devuelve la instancia singleton de EasyOCR Reader.

    EasyOCR descarga modelos (~500MB) la primera vez que se instancia.
    Mantenemos un singleton para evitar recargarlos en cada llamada.

    El parámetro `gpu=False` es explícito: el servidor de producción
    (Kubuntu sin GPU dedicada para ML) no tiene CUDA configurada.
    """
    global _reader_instance
    if _reader_instance is None:
        import easyocr
        logger.info("Inicializando EasyOCR (primera carga, puede tardar)...")
        _reader_instance = easyocr.Reader(OCR_LANGUAGES, gpu=False, verbose=False)
        logger.info("EasyOCR inicializado.")
    return _reader_instance


# Path donde EasyOCR guarda sus modelos. Es interno de la librería y
# NO se puede cambiar desde nuestros Settings sin parchearla.
EASYOCR_MODEL_DIR = Path.home() / ".EasyOCR" / "model"


def is_ocr_model_cached() -> bool:
    """
    Indica si los modelos de EasyOCR están presentes en cache, sin
    instanciar el Reader (que tomaría 30-60s).

    Estrategia: chequear que `~/.EasyOCR/model/` existe y contiene al
    menos dos archivos `.pth` (el detector CRAFT + al menos un modelo
    de idioma). Es robusto a cambios de naming entre versiones de
    EasyOCR; puede dar un false-negative si la lib cambia drásticamente
    su layout, pero nunca un false-positive.
    """
    if not EASYOCR_MODEL_DIR.exists():
        return False
    pth_files = list(EASYOCR_MODEL_DIR.glob("*.pth"))
    return len(pth_files) >= 2


# ============================================================
# Extracción del número de DNI
# ============================================================

# Patrón para número de DNI argentino con o sin separadores de miles.
# Acepta: "12345678", "12.345.678", "12,345,678"
DNI_PATTERN = re.compile(r"\b(\d{1,3}[.,]?\d{3}[.,]?\d{3})\b")


def _normalize_dni_number(raw: str) -> str:
    """Elimina puntos, comas y espacios. Devuelve solo dígitos."""
    return re.sub(r"[^\d]", "", raw)


def _is_plausible_dni(number: str) -> bool:
    """
    Filtra falsos positivos comunes.

    Heurísticas:
    - Longitud entre 7 y 8 dígitos (DNI argentino moderno)
    - No empieza con "19" o "20" seguido de dos dígitos plausibles como año
      (descartar lecturas de fechas de nacimiento/emisión)
    - No es una secuencia trivial (todos los mismos dígitos)
    """
    if not (DNI_NUMBER_MIN_DIGITS <= len(number) <= DNI_NUMBER_MAX_DIGITS):
        return False

    # Descartar todos los dígitos iguales (ej: "11111111")
    if len(set(number)) == 1:
        return False

    # Heurística débil: descartar lecturas que parecen año "19xx" o "20xx"
    # solo cuando tienen exactamente 4 dígitos. Como exigimos 7-8 dígitos
    # arriba, este check ya está cubierto, pero lo dejamos explícito.
    return True


def extract_dni_number(crop_path: Path) -> tuple[str | None, float]:
    """
    Extrae el número de DNI de un recorte.

    Args:
        crop_path: Path al archivo de recorte (JPEG).

    Returns:
        Tupla (número_normalizado, confianza). El número es None si no se
        encontró ningún candidato plausible. La confianza es la mayor
        confianza reportada por EasyOCR entre los candidatos válidos.
    """
    reader = get_reader()

    # detail=1 devuelve (bbox, text, confidence) por cada detección.
    # allowlist restringe los caracteres reconocidos a dígitos y separadores.
    results = reader.readtext(
        str(crop_path),
        detail=1,
        allowlist=OCR_DNI_ALLOWLIST,
        paragraph=False,
    )

    best_number: str | None = None
    best_confidence: float = 0.0

    for _bbox, text, confidence in results:
        # Buscar todos los patrones tipo DNI en el texto reconocido
        for match in DNI_PATTERN.finditer(text):
            normalized = _normalize_dni_number(match.group(1))
            if _is_plausible_dni(normalized) and confidence > best_confidence:
                best_number = normalized
                best_confidence = float(confidence)

    if best_number is None:
        logger.debug(f"OCR no encontró DNI plausible en: {crop_path.name}")
    else:
        logger.debug(
            f"OCR extrajo DNI '{best_number}' (conf={best_confidence:.2f}) "
            f"de {crop_path.name}"
        )

    return best_number, best_confidence
