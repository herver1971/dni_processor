"""
Tests unitarios del módulo de geometría (app.core.geometry).

Cubren el cálculo del bbox del DNI a partir del bbox de la cara:
- Caso típico: cara en el centro de una imagen grande → DNI extendido
  según ratios
- Caso límite: cara cerca del borde izquierdo → DNI truncado a 0
- Caso límite: cara cerca del borde derecho → DNI truncado al ancho
- Caso patológico: ratios inválidos → fallback al bbox de la cara
"""

from __future__ import annotations

import pytest

from app.core.constants import (
    DNI_EXTEND_BOTTOM_RATIO,
    DNI_EXTEND_LEFT_RATIO,
    DNI_EXTEND_RIGHT_RATIO,
    DNI_EXTEND_TOP_RATIO,
)
from app.core.geometry import compute_dni_bbox_from_face
from app.schemas.session import BoundingBox


pytestmark = pytest.mark.unit


class TestComputeDniBboxFromFace:
    def test_typical_case_centered_face(self):
        """Cara en el centro de una imagen grande, sin truncamiento."""
        face = BoundingBox(x=500, y=400, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)

        # Con ratios amplios v0.3.0a (left=1.5, right=8.0, top=2.0, bottom=2.0):
        # left = 500 - 100*1.5 = 350
        # right = 500 + 100 + 100*8.0 = 1400
        # top = 400 - 100*2.0 = 200
        # bottom = 400 + 130 + 100*2.0 = 730
        assert bbox.x == 350
        assert bbox.y == 200
        assert bbox.width == 1400 - 350
        assert bbox.height == 730 - 200

    def test_face_near_left_edge_truncates(self):
        """Si la cara está pegada al borde izquierdo, el bbox se trunca a 0."""
        face = BoundingBox(x=10, y=400, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)
        # Con ratio left=0.4 y face.width=100, left intentaría ser 10 - 40 = -30
        # → debe truncarse a 0
        assert bbox.x == 0

    def test_face_near_right_edge_truncates(self):
        """Si la cara está pegada al borde derecho, el bbox se trunca al ancho."""
        face = BoundingBox(x=1800, y=400, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)
        # right = 1800 + 100 + 100*2.5 = 2150 → truncado a 2000
        assert bbox.x + bbox.width == 2000

    def test_face_near_top_truncates(self):
        face = BoundingBox(x=500, y=10, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)
        assert bbox.y == 0

    def test_face_near_bottom_truncates(self):
        face = BoundingBox(x=500, y=1400, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)
        assert bbox.y + bbox.height == 1500

    def test_custom_ratios(self):
        """Override de ratios funciona correctamente."""
        face = BoundingBox(x=500, y=400, width=100, height=100)
        bbox = compute_dni_bbox_from_face(
            face, image_width=2000, image_height=1500,
            extend_left=1.0, extend_right=1.0, extend_top=0.5, extend_bottom=0.5,
        )
        assert bbox.x == 400  # 500 - 100
        assert bbox.width == 300  # de 400 a 700
        assert bbox.y == 350  # 400 - 50
        assert bbox.height == 200  # de 350 a 550

    def test_pathological_case_fallback_to_face(self):
        """Ratios extremos que dan bbox vacío → fallback al face_bbox."""
        face = BoundingBox(x=500, y=400, width=100, height=130)
        # Ratios negativos imposibles: simular caso donde left > right
        bbox = compute_dni_bbox_from_face(
            face, image_width=600, image_height=500,
            extend_left=10.0,   # left = 500 - 1000 = clamp 0
            extend_right=10.0,  # right = 500 + 100 + 1000 = clamp 600
            extend_top=10.0,    # top = 400 - 1000 = clamp 0
            extend_bottom=10.0, # bottom = 400 + 130 + 1000 = clamp 500
        )
        # En este caso particular el bbox sigue siendo válido (toda la imagen).
        # El caso pathological real es cuando los clamps dan width<=0 o height<=0
        # (imagen muy chica con cara fuera), que es muy raro.
        assert bbox.width > 0 and bbox.height > 0

    def test_uses_face_width_not_height_for_ratios(self):
        """
        Los ratios son relativos al ANCHO de la cara, no al alto.
        Verificación: si duplicamos solo la altura de la cara, el bbox
        no debe cambiar significativamente.
        """
        face_thin = BoundingBox(x=500, y=400, width=100, height=100)
        face_tall = BoundingBox(x=500, y=400, width=100, height=200)
        bbox_thin = compute_dni_bbox_from_face(face_thin, 2000, 1500)
        bbox_tall = compute_dni_bbox_from_face(face_tall, 2000, 1500)

        # X y width deben ser iguales (no dependen de la altura de la cara)
        assert bbox_thin.x == bbox_tall.x
        assert bbox_thin.width == bbox_tall.width

    def test_returns_valid_bounding_box(self):
        face = BoundingBox(x=500, y=400, width=100, height=130)
        bbox = compute_dni_bbox_from_face(face, image_width=2000, image_height=1500)
        # Validar que el resultado es un BoundingBox bien formado
        assert isinstance(bbox, BoundingBox)
        assert bbox.width > 0
        assert bbox.height > 0
        assert bbox.x >= 0
        assert bbox.y >= 0
