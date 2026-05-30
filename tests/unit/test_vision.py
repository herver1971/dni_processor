"""
Tests unitarios del módulo de visión v0.2.1.

Cubren:
- Carga con EXIF respetado
- Aplicación de CLAHE
- Recorte con padding (preservación de inclinación)
- Guardado de recortes

Los tests de detección facial real con caras pre-entrenadas están en
tests/integration/ y se marcan como `real_data` (requieren imágenes
reales, no se ejecutan en CI).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageOps

from app.core.vision import (
    apply_clahe,
    crop_with_padding,
    load_image_exif_aware,
    save_crop,
)
from app.schemas.session import BoundingBox


pytestmark = pytest.mark.unit


class TestLoadImageExifAware:
    def test_load_jpeg_basico(self, synthetic_dni_image, tmp_image_dir, save_image):
        path = save_image(synthetic_dni_image, tmp_image_dir / "test.jpg")
        loaded = load_image_exif_aware(path)
        assert loaded.shape == synthetic_dni_image.shape
        assert loaded.dtype == np.uint8

    def test_load_png(self, synthetic_dni_image, tmp_image_dir, save_image):
        path = save_image(synthetic_dni_image, tmp_image_dir / "test.png")
        loaded = load_image_exif_aware(path)
        assert loaded.shape == synthetic_dni_image.shape

    def test_nonexistent_file_raises(self, tmp_image_dir):
        with pytest.raises(FileNotFoundError):
            load_image_exif_aware(tmp_image_dir / "no_existe.jpg")

    def test_invalid_image_raises(self, tmp_image_dir):
        bad = tmp_image_dir / "broken.jpg"
        bad.write_bytes(b"not an image")
        with pytest.raises(ValueError):
            load_image_exif_aware(bad)

    def test_exif_rotation_is_applied(self, tmp_image_dir):
        """
        Genera una imagen rectangular obvia (alto > ancho), le agrega EXIF
        orientation=6 (rotación 90° CW al mostrarla), y verifica que tras
        la carga las dimensiones quedan transpuestas (ancho > alto).
        """
        # Imagen 100x200 (alto > ancho)
        img = np.zeros((200, 100, 3), dtype=np.uint8)
        img[:, :, 0] = 255  # azul

        path = tmp_image_dir / "with_exif.jpg"
        # Usar PIL para escribir con EXIF orientation
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        # Guardar con orientation=6 (90° CW)
        exif = pil.getexif()
        exif[274] = 6  # Tag 274 = Orientation, valor 6 = 90° CW
        pil.save(path, exif=exif)

        loaded = load_image_exif_aware(path)
        # Después de aplicar la rotación EXIF, las dimensiones deben transponerse
        assert loaded.shape[0] == 100  # alto post-rotación = ancho original
        assert loaded.shape[1] == 200  # ancho post-rotación = alto original


class TestApplyClahe:
    def test_clahe_preserves_shape(self, synthetic_dni_image):
        result = apply_clahe(synthetic_dni_image)
        assert result.shape == synthetic_dni_image.shape
        assert result.dtype == np.uint8

    def test_clahe_modifies_low_contrast_image(self):
        """
        Una imagen con poco contraste debe cambiar perceptiblemente
        después de CLAHE.
        """
        # Imagen con valores muy comprimidos (poco contraste)
        img = np.full((200, 300, 3), 120, dtype=np.uint8)
        img[50:150, 100:200, :] = 130  # diferencia mínima

        enhanced = apply_clahe(img)
        # La desviación estándar del canal V (luminosidad) debe aumentar
        v_orig = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 2]
        v_enh = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV)[:, :, 2]
        assert v_enh.std() >= v_orig.std()


class TestCropWithPadding:
    def test_crop_includes_padding(self, synthetic_dni_image):
        # Bbox bien centrado para que entre todo el padding
        bbox = BoundingBox(x=500, y=300, width=200, height=126)
        crop = crop_with_padding(synthetic_dni_image, bbox, padding_px=20)
        assert crop.shape[1] == 200 + 2 * 20
        assert crop.shape[0] == 126 + 2 * 20

    def test_crop_clamps_to_image_bounds(self, synthetic_dni_image):
        bbox = BoundingBox(x=0, y=0, width=200, height=126)
        crop = crop_with_padding(synthetic_dni_image, bbox, padding_px=30)
        # Padding izquierdo y superior recortados a 0
        assert crop.shape[1] == 200 + 30
        assert crop.shape[0] == 126 + 30

    def test_crop_preserves_content(self, synthetic_dni_image):
        bbox = BoundingBox(x=400, y=300, width=200, height=126)
        crop = crop_with_padding(synthetic_dni_image, bbox, padding_px=0)
        original_region = synthetic_dni_image[300:426, 400:600]
        assert np.array_equal(crop, original_region)


class TestSaveCrop:
    def test_save_creates_file(self, synthetic_dni_image, tmp_path):
        output = tmp_path / "subdir" / "crop.jpg"
        save_crop(synthetic_dni_image, output)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_saved_file_is_readable(self, synthetic_dni_image, tmp_path):
        output = tmp_path / "crop.jpg"
        save_crop(synthetic_dni_image, output)
        loaded = load_image_exif_aware(output)
        assert loaded.shape == synthetic_dni_image.shape
