"""
Tests del módulo crop_adjustments — específicamente el orden de operaciones
rotate-then-crop introducido en v0.3.0b.1.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core.crop_adjustments import apply_final_crop, normalize_rotation
from app.schemas.session import BoundingBox


pytestmark = pytest.mark.unit


@pytest.fixture
def landscape_image(tmp_path) -> Path:
    """
    Imagen landscape 800x400 con un patrón distintivo:
    - franja roja en el lado izquierdo (x < 200)
    - franja azul en el lado derecho (x > 600)
    Permite verificar que después de rotar, las franjas quedan donde se espera.
    """
    img = np.full((400, 800, 3), 128, dtype=np.uint8)
    img[:, :200] = [0, 0, 200]    # franja izquierda (rojo BGR)
    img[:, 600:] = [200, 0, 0]    # franja derecha (azul BGR)

    path = tmp_path / "landscape.jpg"
    cv2.imwrite(str(path), img)
    return path


class TestRotationOrder:
    """
    Valida que rotación se aplique ANTES del bbox.

    En la imagen landscape de 800x400 (W=800, H=400):
    - Sin rotar: la franja ROJA está a la izquierda (x<200).
    - Rotada 90° CW: queda 400x800 (W=400, H=800), la franja roja AHORA está
      ARRIBA (y<200).
    - Si el bbox en coords post-rotación es x=0, y=0, w=400, h=200, debe
      contener la franja ROJA.
    """

    def test_no_rotation_preserves_layout(self, landscape_image, tmp_path):
        # Bbox sobre la mitad izquierda → debe ser principalmente roja
        bbox = BoundingBox(x=0, y=0, width=200, height=400)
        out = tmp_path / "out.jpg"
        apply_final_crop(landscape_image, bbox, 0, out)

        result = cv2.imread(str(out))
        # Verificar que el componente R domina (BGR: índice 2)
        mean = result.mean(axis=(0, 1))
        # R debe ser > 100 (era 200 en la original, jpeg loss da ~150-200)
        assert mean[2] > 100, f"Esperaba franja roja, mean BGR = {mean}"

    def test_rotation_90_then_crop_top_gives_red_stripe(
        self, landscape_image, tmp_path,
    ):
        """
        Después de rotar 90° CW la imagen 800x400, queda 400x800.
        Bbox en la franja superior (y=0, h=200) debe contener la franja
        que ANTES de rotar era la izquierda (la roja).
        """
        # Bbox en el TOP de la imagen rotada (donde queda la franja roja)
        bbox = BoundingBox(x=0, y=0, width=400, height=200)
        out = tmp_path / "out_rotated.jpg"
        apply_final_crop(landscape_image, bbox, 90, out)

        result = cv2.imread(str(out))
        # El recorte debe tener dimensiones 200x400 (h x w post-bbox)
        assert result.shape[0] == 200
        assert result.shape[1] == 400
        # Y debe ser predominantemente rojo
        mean = result.mean(axis=(0, 1))
        assert mean[2] > 100, f"Esperaba franja roja, mean BGR = {mean}"

    def test_rotation_180_inverts_layout(self, landscape_image, tmp_path):
        """
        Rotar 180° → la franja roja (que estaba a la izquierda) queda a la
        derecha. Un bbox sobre el lado derecho (x>=600) post-rotación debe
        contener la franja roja.
        """
        bbox = BoundingBox(x=600, y=0, width=200, height=400)
        out = tmp_path / "out_180.jpg"
        apply_final_crop(landscape_image, bbox, 180, out)

        result = cv2.imread(str(out))
        mean = result.mean(axis=(0, 1))
        assert mean[2] > 100, f"Esperaba franja roja, mean BGR = {mean}"

    def test_invalid_rotation_rejected(self, landscape_image, tmp_path):
        bbox = BoundingBox(x=0, y=0, width=100, height=100)
        with pytest.raises(ValueError, match="no permitida"):
            apply_final_crop(landscape_image, bbox, 45, tmp_path / "x.jpg")


class TestNormalizeRotation:
    @pytest.mark.parametrize("degrees,expected", [
        (0, 0),
        (44, 0),
        (45, 90),
        (89, 90),
        (90, 90),
        (134, 90),
        (135, 180),
        (180, 180),
        (224, 180),
        (225, 270),
        (270, 270),
        (314, 270),
        (315, 0),
        (360, 0),
        (450, 90),  # > 360 también se normaliza
        (-45, 0),   # -45 % 360 = 315 → 0
    ])
    def test_normalize(self, degrees, expected):
        assert normalize_rotation(degrees) == expected
