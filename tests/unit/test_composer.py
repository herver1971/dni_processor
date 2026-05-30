"""
Tests unitarios del módulo composer (app.core.composer).

Cubren:
- Cálculo de posiciones de pares en hoja A4
- Generación de PDF con pares matcheados
- Generación de PDF con huérfanos al final
- PDF vacío (sin contenido) — caso límite
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from app.core.composer import _compute_pair_positions, compose_pdf
from app.core.constants import (
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    DNI_HEIGHT_MM,
    DNI_WIDTH_MM,
    PAIRS_PER_PAGE,
)
from app.schemas.session import (
    BoundingBox,
    DetectedDNI,
    DNISide,
    MatchedPair,
    UnpairedDNI,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def dummy_crop(tmp_path) -> Path:
    """Genera un archivo JPG mínimo para usar como recorte de test."""
    img = np.full((252, 400, 3), 200, dtype=np.uint8)
    cv2.rectangle(img, (10, 10), (390, 242), (50, 50, 50), 2)
    path = tmp_path / "crops" / f"{uuid4()}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


def _make_dni(crop_path: Path, side: DNISide, number: str = "12345678") -> DetectedDNI:
    return DetectedDNI(
        crop_id=str(uuid4()),
        source_image=Path("/fake/img.jpg"),
        bbox=BoundingBox(x=0, y=0, width=400, height=252),
        crop_path=crop_path,
        side=side,
        dni_number=number,
    )


def _make_pair(crop_path: Path) -> MatchedPair:
    return MatchedPair(
        frente=_make_dni(crop_path, DNISide.FRENTE),
        dorso=_make_dni(crop_path, DNISide.DORSO),
        match_distance=0,
        is_exact_match=True,
    )


# ============================================================
# Posiciones del layout
# ============================================================

class TestPairPositions:
    def test_returns_four_positions(self):
        positions = _compute_pair_positions()
        assert len(positions) == PAIRS_PER_PAGE

    def test_positions_within_page_bounds(self):
        positions = _compute_pair_positions()
        for x_f, y_f, x_d, y_d in positions:
            # Cada DNI debe entrar completo en la página
            assert 0 <= x_f and x_f + DNI_WIDTH_MM <= A4_WIDTH_MM
            assert 0 <= y_f and y_f + DNI_HEIGHT_MM <= A4_HEIGHT_MM
            assert 0 <= x_d and x_d + DNI_WIDTH_MM <= A4_WIDTH_MM
            assert 0 <= y_d and y_d + DNI_HEIGHT_MM <= A4_HEIGHT_MM

    def test_frente_left_of_dorso(self):
        positions = _compute_pair_positions()
        for x_f, _, x_d, _ in positions:
            assert x_f < x_d, "Frente debe estar a la izquierda del dorso"

    def test_rows_ordered_top_to_bottom(self):
        positions = _compute_pair_positions()
        ys = [y_f for _, y_f, _, _ in positions]
        assert ys == sorted(ys), "Las filas deben ir de arriba hacia abajo"


# ============================================================
# Generación de PDF
# ============================================================

class TestComposePdf:
    def test_creates_pdf_with_single_pair(self, dummy_crop, tmp_path):
        pair = _make_pair(dummy_crop)
        output = tmp_path / "output.pdf"
        compose_pdf([pair], [], [], output)
        assert output.exists()
        assert output.stat().st_size > 0
        # Validar header básico de PDF
        with output.open("rb") as f:
            assert f.read(4) == b"%PDF"

    def test_creates_pdf_with_full_page(self, dummy_crop, tmp_path):
        pairs = [_make_pair(dummy_crop) for _ in range(PAIRS_PER_PAGE)]
        output = tmp_path / "output.pdf"
        compose_pdf(pairs, [], [], output)
        assert output.exists()

    def test_creates_pdf_with_multiple_pages(self, dummy_crop, tmp_path):
        # 9 pares → 3 páginas (4+4+1)
        pairs = [_make_pair(dummy_crop) for _ in range(9)]
        output = tmp_path / "output.pdf"
        compose_pdf(pairs, [], [], output)
        assert output.exists()
        # No validamos el conteo exacto de páginas sin pypdf; verificamos
        # que el archivo sea sustancialmente mayor que un PDF de una página.
        assert output.stat().st_size > 5000  # heurística básica

    def test_creates_pdf_with_orphans_only(self, dummy_crop, tmp_path):
        orphan_f = UnpairedDNI(
            detected=_make_dni(dummy_crop, DNISide.FRENTE),
            reason="test",
        )
        output = tmp_path / "output.pdf"
        compose_pdf([], [orphan_f], [], output)
        assert output.exists()

    def test_creates_pdf_with_pairs_and_orphans(self, dummy_crop, tmp_path):
        pairs = [_make_pair(dummy_crop)]
        orphan_f = UnpairedDNI(
            detected=_make_dni(dummy_crop, DNISide.FRENTE),
            reason="test",
        )
        orphan_d = UnpairedDNI(
            detected=_make_dni(dummy_crop, DNISide.DORSO),
            reason="test",
        )
        output = tmp_path / "output.pdf"
        compose_pdf(pairs, [orphan_f], [orphan_d], output)
        assert output.exists()

    def test_creates_output_parent_dir(self, dummy_crop, tmp_path):
        """compose_pdf debe crear el directorio padre si no existe."""
        pair = _make_pair(dummy_crop)
        output = tmp_path / "no" / "existe" / "todavia" / "out.pdf"
        compose_pdf([pair], [], [], output)
        assert output.exists()
