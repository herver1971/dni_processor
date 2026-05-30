"""
Fixtures comunes para tests del DNI Processor (v0.2.1).

Genera imágenes sintéticas con "caras" rudimentarias para testear el
pipeline sin depender de imágenes reales (que no se pueden commitear
por privacidad notarial).

NOTA: las imágenes sintéticas usan formas oscuras sobre claras que NO
son detectables por el detector facial real (ResNet-10 SSD). Los tests
que requieren detección facial real están marcados como `real_data`
y se ejecutan localmente con imágenes reales (no en CI).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest


def _generate_synthetic_dni_image(
    width: int = 1200,
    height: int = 800,
    bg_color: tuple[int, int, int] = (40, 40, 40),
    dni_width_px: int = 600,
    dni_height_px: int = 378,
    position: tuple[int, int] | None = None,
) -> np.ndarray:
    """
    Genera una imagen con un "DNI" sintético sobre fondo oscuro.

    El "DNI" es un rectángulo claro con un cuadrado más oscuro a la
    izquierda simulando la posición de la cara. NO es un DNI real ni
    detectable por el detector facial — solo para testear lógica
    geométrica.
    """
    img = np.full((height, width, 3), bg_color, dtype=np.uint8)
    if position is None:
        cx, cy = width // 2, height // 2
    else:
        cx, cy = position

    x1, y1 = cx - dni_width_px // 2, cy - dni_height_px // 2
    x2, y2 = cx + dni_width_px // 2, cy + dni_height_px // 2

    # "DNI" — rectángulo claro
    cv2.rectangle(img, (x1, y1), (x2, y2), (220, 220, 220), -1)

    # "Cara" — cuadrado oscuro en la zona izquierda del "DNI"
    face_size = int(dni_height_px * 0.55)
    face_x = x1 + int(dni_width_px * 0.10)
    face_y = y1 + (dni_height_px - face_size) // 2
    cv2.rectangle(
        img, (face_x, face_y),
        (face_x + face_size, face_y + face_size),
        (80, 80, 80), -1,
    )
    return img


@pytest.fixture
def synthetic_dni_image() -> np.ndarray:
    return _generate_synthetic_dni_image()


@pytest.fixture
def tmp_image_dir(tmp_path: Path) -> Path:
    d = tmp_path / "images"
    d.mkdir()
    return d


@pytest.fixture
def save_image():
    def _save(image: np.ndarray, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), image)
        return path
    return _save
