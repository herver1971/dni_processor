"""
Pipeline orquestador del procesamiento de DNIs (v0.2.1).

CAMBIOS RESPECTO A v0.2.0:

Esta versión refleja el pivote arquitectónico hacia flujo asistido:

- FRENTES: detección automática por caras (cascada con EXIF + CLAHE +
  rotaciones). Los que fallen se exponen al usuario para recorte manual.
- DORSOS: NO se intenta detección automática (pyzbar dio 5.6% en pruebas
  reales, no es recuperable). El usuario recorta los dorsos manualmente
  en la UI.
- OCR: se mantiene pero como SUGERENCIA para pre-emparejar pares.
  El usuario confirma o corrige el matcheo en la UI.

En Sprint 1 (este sprint), el pipeline expone una API programática que
la capa web (Sprint 2-3) va a invocar. No hay UI todavía: para validar
el backend se usa un script CLI que produce los recortes de frentes y
permite revisarlos visualmente.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import cv2
import numpy as np

from app.core.composer import compose_pdf
from app.core.constants import ALLOWED_IMAGE_EXTENSIONS
from app.core.matcher import match_frentes_dorsos
from app.core.ocr import extract_dni_number
from app.core.vision import (
    crop_with_padding,
    extract_frentes_from_image,
    get_face_net,
    load_image_exif_aware,
    save_crop,
)
from app.schemas.session import (
    BoundingBox,
    DetectedDNI,
    DNISide,
    ProcessingResult,
    UnprocessedImage,
)

logger = logging.getLogger(__name__)


def _list_images_in_dir(directory: Path) -> list[Path]:
    """Lista archivos de imagen válidos en un directorio (no recursivo)."""
    if not directory.exists() or not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
    )


# ============================================================
# Procesamiento de FRENTES (detección automática + OCR)
# ============================================================

def process_frente_images(
    frente_image_paths: list[Path],
    crops_dir: Path,
    run_ocr: bool = True,
) -> tuple[list[DetectedDNI], list[UnprocessedImage], dict[str, int]]:
    """
    Procesa un lote de imágenes de frentes con el detector facial.

    Args:
        frente_image_paths: Imágenes a procesar.
        crops_dir: Directorio donde guardar los recortes.
        run_ocr: Si True, ejecuta OCR sobre cada recorte para extraer
                 el número de DNI (usado como sugerencia de matcheo).

    Returns:
        Tupla (dnis_detectados, imágenes_no_procesadas, stats).
        `stats` es un dict con conteo por estrategia ("original", "clahe",
        "rotated_90_ccw", etc.) — útil para diagnóstico.
    """
    detected: list[DetectedDNI] = []
    unprocessed: list[UnprocessedImage] = []
    strategy_stats: dict[str, int] = {}

    # Pre-cargar el modelo de caras (singleton)
    net = get_face_net()

    for img_path in frente_image_paths:
        try:
            dnis, strategy = extract_frentes_from_image(
                img_path, crops_dir, net=net,
            )
            strategy_stats[strategy] = strategy_stats.get(strategy, 0) + 1
        except (FileNotFoundError, ValueError, IOError) as e:
            logger.error(f"Error procesando {img_path}: {e}")
            unprocessed.append(
                UnprocessedImage(source_image=img_path, reason=str(e))
            )
            continue

        if not dnis:
            unprocessed.append(
                UnprocessedImage(
                    source_image=img_path,
                    reason="No se detectó ninguna cara (requiere recorte manual)",
                )
            )
            continue

        if run_ocr:
            # OCR sobre cada recorte para sugerir matcheo
            for dni in dnis:
                number, conf = extract_dni_number(dni.crop_path)
                dni_updated = dni.model_copy(
                    update={"dni_number": number, "ocr_confidence": conf}
                )
                detected.append(dni_updated)
        else:
            detected.extend(dnis)

    return detected, unprocessed, strategy_stats


# ============================================================
# Procesamiento de DORSOS (sólo OCR, los recortes vienen del usuario)
# ============================================================

def process_dorso_crops(
    dorso_crop_paths: list[Path],
    run_ocr: bool = True,
) -> list[DetectedDNI]:
    """
    Procesa recortes de DORSOS ya producidos por el usuario.

    En este pipeline asistido, los dorsos NO se detectan automáticamente.
    El usuario marca el bbox de cada dorso en la UI (Sprint 2) y aquí
    sólo recibimos los recortes ya hechos para extraer su número de DNI
    via OCR.

    Args:
        dorso_crop_paths: Paths a los recortes de dorsos (ya recortados
                          por el usuario o por una rutina previa).
        run_ocr: Si True, ejecuta OCR sobre cada recorte.

    Returns:
        Lista de DetectedDNI representando los dorsos.
    """
    detected: list[DetectedDNI] = []
    for crop_path in dorso_crop_paths:
        # Cargar la imagen del recorte para obtener sus dimensiones
        # (necesarias para BoundingBox)
        image = load_image_exif_aware(crop_path)
        h, w = image.shape[:2]

        crop_id = str(uuid.uuid4())
        bbox = BoundingBox(x=0, y=0, width=w, height=h)

        dni = DetectedDNI(
            crop_id=crop_id,
            source_image=crop_path,  # El recorte ES su propia fuente
            bbox=bbox,
            crop_path=crop_path,
            side=DNISide.DORSO,
            side_confidence=1.0,  # Asignado manualmente por el usuario
        )

        if run_ocr:
            number, conf = extract_dni_number(crop_path)
            dni = dni.model_copy(
                update={"dni_number": number, "ocr_confidence": conf}
            )

        detected.append(dni)

    return detected


# ============================================================
# Orquestador completo (Sprint 1 — modo CLI)
# ============================================================

def process_batch_assisted(
    frentes_dir: Path,
    dorsos_dir: Path,
    output_pdf: Path,
    work_dir: Path | None = None,
    run_ocr: bool = True,
) -> ProcessingResult:
    """
    Procesamiento completo en modo Sprint 1 (sin UI todavía).

    En este modo, asumimos que el usuario ya separó las imágenes en dos
    carpetas (frentes y dorsos), y que los dorsos están "pre-recortados"
    (una imagen = un dorso ya recortado). Esto es una simplificación
    temporal para validar el backend sin UI.

    En Sprint 2-3 esta función será reemplazada por endpoints HTTP donde:
    - El usuario sube imágenes "como vienen"
    - El sistema detecta automáticamente los frentes (esta lógica)
    - Para dorsos y frentes fallidos, el usuario recorta manualmente
    - El sistema sugiere matcheo por OCR y el usuario confirma

    Args:
        frentes_dir: Carpeta con imágenes de frentes.
        dorsos_dir: Carpeta con recortes de dorsos (pre-recortados).
        output_pdf: Path del PDF de salida.
        work_dir: Directorio de trabajo (default: /tmp/dni_processor/<uuid>).
        run_ocr: Si True, intenta matcheo automático por OCR.

    Returns:
        ProcessingResult con todas las estadísticas.
    """
    session_id = str(uuid.uuid4())
    if work_dir is None:
        work_dir = Path(f"/tmp/dni_processor/{session_id}")
    crops_dir = work_dir / "crops"

    logger.info(f"=== Sesión {session_id} ===")
    logger.info(f"Frentes: {frentes_dir}")
    logger.info(f"Dorsos:  {dorsos_dir}")

    frente_images = _list_images_in_dir(frentes_dir)
    dorso_crops = _list_images_in_dir(dorsos_dir)

    if not frente_images:
        raise ValueError(f"No hay imágenes en {frentes_dir}")
    if not dorso_crops:
        raise ValueError(f"No hay recortes en {dorsos_dir}")

    # FRENTES: detección automática
    frentes_detected, frentes_unprocessed, strategy_stats = process_frente_images(
        frente_images, crops_dir / "frentes", run_ocr=run_ocr,
    )
    logger.info(f"Estrategias usadas: {strategy_stats}")

    # DORSOS: ya pre-recortados
    dorsos_detected = process_dorso_crops(dorso_crops, run_ocr=run_ocr)

    # MATCHEO (sugerencia automática, asume que el usuario confirmará luego)
    pairs, unpaired_frentes, unpaired_dorsos = match_frentes_dorsos(
        frentes_detected, dorsos_detected,
    )

    # COMPOSICIÓN PDF
    compose_pdf(pairs, unpaired_frentes, unpaired_dorsos, output_pdf)

    result = ProcessingResult(
        session_id=session_id,
        pairs=pairs,
        unpaired_frentes=unpaired_frentes,
        unpaired_dorsos=unpaired_dorsos,
        unprocessed_images=frentes_unprocessed,
        output_pdf_path=output_pdf,
    )

    logger.info(
        f"=== Sesión {session_id} completa: "
        f"pares={result.total_pairs}, "
        f"frentes huérfanos={len(unpaired_frentes)}, "
        f"dorsos huérfanos={len(unpaired_dorsos)}, "
        f"frentes no detectados={len(frentes_unprocessed)} ==="
    )
    return result
