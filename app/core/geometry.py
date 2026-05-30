"""
Módulo de Geometría — Cálculo del bbox del DNI a partir del bbox de la cara.

CONTEXTO:
El detector de caras nos da un bounding box de la cara del titular.
El DNI argentino tarjeta tiene la foto del titular siempre en una
posición fija (columna izquierda, aproximadamente centrada verticalmente).
Conocemos esa geometría, así que podemos calcular el bbox completo del
DNI extendiendo el bbox de la cara según ratios fijos.

VENTAJAS de este enfoque:
- Cero dependencias adicionales
- Determinístico y debuggeable
- No requiere otra red neuronal
- Funciona en CPU sin problemas
- Es ajustable empíricamente

LIMITACIONES:
- Asume DNI argentino tarjeta moderno con la foto en posición estándar
- Asume orientación normal (la cara está "parada"). Después del EXIF y
  los fallbacks de rotación del módulo `vision`, esto se cumple en
  prácticamente todos los casos.
- No detecta DNIs rotados de forma arbitraria (ej: 45°). Si la cara
  se detecta con esa inclinación, el bbox calculado va a quedar mal
  alineado. Mitigación: el recorte aún incluirá el DNI (con padding
  generoso), aunque también incluirá fondo extra. Esto es aceptable
  para integridad documental.

CALIBRACIÓN:
Los ratios iniciales están en `constants.py` y se calibran empíricamente
sobre el set real. Si la inspección visual muestra que los recortes
están sistemáticamente cortados o sobrados, ajustar los ratios y
re-ejecutar.
"""

from __future__ import annotations

import logging

from app.core.constants import (
    DNI_EXTEND_BOTTOM_RATIO,
    DNI_EXTEND_LEFT_RATIO,
    DNI_EXTEND_RIGHT_RATIO,
    DNI_EXTEND_TOP_RATIO,
)
from app.schemas.session import BoundingBox

logger = logging.getLogger(__name__)


def compute_dni_bbox_from_face(
    face_bbox: BoundingBox,
    image_width: int,
    image_height: int,
    extend_left: float = DNI_EXTEND_LEFT_RATIO,
    extend_right: float = DNI_EXTEND_RIGHT_RATIO,
    extend_top: float = DNI_EXTEND_TOP_RATIO,
    extend_bottom: float = DNI_EXTEND_BOTTOM_RATIO,
) -> BoundingBox:
    """
    Calcula el bbox del DNI completo a partir del bbox de la cara.

    Los ratios se aplican relativos al ANCHO de la cara (no al alto),
    porque el ancho es una métrica más estable: la altura del bbox de
    la cara varía más con el ángulo de la foto (más sombra/menos
    sombra debajo del mentón).

    El bbox resultante se trunca a los límites de la imagen — si la
    cara está cerca de un borde y el DNI se extendería fuera del frame,
    el bbox queda recortado. El recorte posterior con padding incluye
    el contexto disponible.

    Args:
        face_bbox: Bounding box de la cara detectada.
        image_width: Ancho de la imagen en píxeles.
        image_height: Alto de la imagen en píxeles.
        extend_left: Cuánto extender hacia la izquierda (× ancho_cara).
        extend_right: Cuánto extender hacia la derecha (× ancho_cara).
        extend_top: Cuánto extender hacia arriba (× ancho_cara).
        extend_bottom: Cuánto extender hacia abajo (× ancho_cara).

    Returns:
        BoundingBox del DNI completo, recortado a límites de la imagen.
    """
    face_w = face_bbox.width
    face_x = face_bbox.x
    face_y = face_bbox.y
    face_h = face_bbox.height

    # Extender desde los bordes de la cara
    dni_left = face_x - int(face_w * extend_left)
    dni_right = face_x + face_w + int(face_w * extend_right)
    dni_top = face_y - int(face_w * extend_top)
    dni_bottom = face_y + face_h + int(face_w * extend_bottom)

    # Truncar a límites de la imagen
    dni_left = max(0, dni_left)
    dni_right = min(image_width, dni_right)
    dni_top = max(0, dni_top)
    dni_bottom = min(image_height, dni_bottom)

    # Validar dimensiones positivas
    dni_w = dni_right - dni_left
    dni_h = dni_bottom - dni_top

    if dni_w <= 0 or dni_h <= 0:
        # Caso patológico: el cálculo dio un bbox vacío. Fallback al
        # propio bbox de la cara (preferible recortar solo la cara que
        # devolver un bbox inválido).
        logger.warning(
            f"Cálculo de bbox DNI dio dimensiones inválidas "
            f"({dni_w}x{dni_h}), usando bbox de la cara como fallback."
        )
        return face_bbox

    return BoundingBox(x=dni_left, y=dni_top, width=dni_w, height=dni_h)


def compute_suggested_bbox_within_crop(
    face_bbox: BoundingBox,
    wide_bbox: BoundingBox,
) -> BoundingBox:
    """
    Calcula dónde está el DNI sugerido DENTRO del recorte amplio.

    Devuelve coordenadas RELATIVAS al recorte amplio (no a la imagen
    fuente). Se usa para pre-cargar el rectángulo de ajuste en Cropper.js.

    Lógica:
    1. Calcula el bbox sugerido del DNI usando ratios SUGGESTED_BBOX_*
       (en coordenadas absolutas de la imagen fuente).
    2. Lo traduce a coordenadas relativas restando el origen del
       recorte amplio.
    3. Trunca a los límites del recorte amplio.

    Args:
        face_bbox: Bbox de la cara en coordenadas de la imagen fuente.
        wide_bbox: Bbox del recorte amplio en coordenadas de la imagen
                   fuente.

    Returns:
        BoundingBox en coordenadas relativas al recorte amplio.
    """
    from app.core.constants import (
        SUGGESTED_BBOX_BOTTOM_RATIO,
        SUGGESTED_BBOX_LEFT_RATIO,
        SUGGESTED_BBOX_RIGHT_RATIO,
        SUGGESTED_BBOX_TOP_RATIO,
    )

    face_w = face_bbox.width

    # 1. Bbox sugerido en coordenadas absolutas (imagen fuente)
    abs_left = face_bbox.x - int(face_w * SUGGESTED_BBOX_LEFT_RATIO)
    abs_right = face_bbox.x + face_w + int(face_w * SUGGESTED_BBOX_RIGHT_RATIO)
    abs_top = face_bbox.y - int(face_w * SUGGESTED_BBOX_TOP_RATIO)
    abs_bottom = face_bbox.y + face_bbox.height + int(face_w * SUGGESTED_BBOX_BOTTOM_RATIO)

    # 2. Traducir a coordenadas relativas al recorte amplio
    rel_left = abs_left - wide_bbox.x
    rel_right = abs_right - wide_bbox.x
    rel_top = abs_top - wide_bbox.y
    rel_bottom = abs_bottom - wide_bbox.y

    # 3. Truncar a límites del recorte amplio
    rel_left = max(0, rel_left)
    rel_right = min(wide_bbox.width, rel_right)
    rel_top = max(0, rel_top)
    rel_bottom = min(wide_bbox.height, rel_bottom)

    width = rel_right - rel_left
    height = rel_bottom - rel_top

    if width <= 0 or height <= 0:
        # Fallback: rectángulo centrado de tamaño razonable
        logger.warning(
            "Sugerido inválido — usando rectángulo centrado como fallback"
        )
        cx, cy = wide_bbox.width // 2, wide_bbox.height // 2
        w, h = wide_bbox.width // 2, wide_bbox.height // 2
        return BoundingBox(
            x=cx - w // 2, y=cy - h // 2, width=w, height=h,
        )

    return BoundingBox(x=rel_left, y=rel_top, width=width, height=height)
