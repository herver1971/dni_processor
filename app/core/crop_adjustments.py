"""
Ajustes finos sobre recortes — aplica el bbox y la rotación que el
usuario confirmó en la UI.

Funciones principales:
- `apply_final_crop()`: dado un recorte amplio + bbox interno + rotación,
  produce el recorte final.

PRESERVACIÓN DE INTEGRIDAD DOCUMENTAL:
- La rotación solo puede ser de 0, 90, 180 o 270 grados (múltiplos de 90).
  No se permiten rotaciones arbitrarias que requieran interpolación
  (eso sería deformar la imagen).
- El recorte sigue siendo axis-aligned (rectangular puro).
- Sin warp, sin deskew, sin perspective transform.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2

from app.core.vision import crop_with_padding, load_image_exif_aware, save_crop
from app.schemas.session import BoundingBox

logger = logging.getLogger(__name__)


VALID_ROTATIONS = (0, 90, 180, 270)


def apply_final_crop(
    wide_crop_path: Path,
    bbox: BoundingBox,
    rotation_degrees: int,
    output_path: Path,
    padding_px: int = 0,
) -> None:
    """
    Genera el recorte final aplicando rotación + bbox.

    ORDEN DE OPERACIONES (importante):
    1. Primero rotamos la imagen completa al ángulo indicado.
    2. Después aplicamos el bbox sobre la imagen ya rotada.

    Esto coincide con el comportamiento de Cropper.js en el frontend:
    cuando el usuario rota la imagen visualmente, `getData()` devuelve
    coordenadas en el espacio de la imagen ROTADA, no de la original.
    Si recortáramos primero y rotáramos después, el resultado quedaría
    desalineado.

    Args:
        wide_crop_path: Path al recorte amplio fuente (o imagen completa
                        normalizada en el caso de recortes manuales).
        bbox: Bbox del DNI en coords de la imagen YA ROTADA.
        rotation_degrees: Rotación a aplicar (0, 90, 180, 270).
        output_path: Path donde guardar el recorte final.
        padding_px: Padding adicional alrededor del bbox.

    Raises:
        ValueError: si rotation_degrees no es múltiplo de 90.
        FileNotFoundError: si wide_crop_path no existe.
    """
    if rotation_degrees not in VALID_ROTATIONS:
        raise ValueError(
            f"Rotación {rotation_degrees}° no permitida. "
            f"Solo se acepta: {VALID_ROTATIONS}"
        )

    # 1. Cargar la imagen original
    image = load_image_exif_aware(wide_crop_path)

    # 2. PRIMERO: rotar (si corresponde)
    # Cropper.js rota la imagen visualmente en sentido horario al hacer
    # cropper.rotate(90). Replicamos esa orientación server-side.
    if rotation_degrees == 90:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif rotation_degrees == 180:
        image = cv2.rotate(image, cv2.ROTATE_180)
    elif rotation_degrees == 270:
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    # rotation_degrees == 0 → sin rotación

    # 3. DESPUÉS: aplicar el bbox sobre la imagen ya rotada
    cropped = crop_with_padding(image, bbox, padding_px=padding_px)

    save_crop(cropped, output_path)
    logger.debug(
        f"Recorte final guardado: {output_path.name} "
        f"(rot={rotation_degrees}°, bbox={bbox.width}x{bbox.height})"
    )


def normalize_rotation(degrees: int) -> int:
    """Normaliza una rotación arbitraria a 0/90/180/270."""
    normalized = degrees % 360
    # Mapear el valor más cercano
    if normalized < 45 or normalized >= 315:
        return 0
    if 45 <= normalized < 135:
        return 90
    if 135 <= normalized < 225:
        return 180
    return 270
