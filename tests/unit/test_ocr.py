"""
Tests unitarios del módulo OCR (app.core.ocr).

NOTA: EasyOCR es muy pesado de instanciar (~500MB de modelos descargados).
Estos tests cubren ÚNICAMENTE las funciones puras del módulo (normalización
de número, patrón regex, heurísticas de plausibilidad). La función
`extract_dni_number` que invoca EasyOCR se testea en integración con
imágenes reales en Fase 2.
"""

from __future__ import annotations

import pytest

from app.core.ocr import (
    DNI_PATTERN,
    _is_plausible_dni,
    _normalize_dni_number,
)


pytestmark = pytest.mark.unit


# ============================================================
# Normalización
# ============================================================

class TestNormalizeDniNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("12345678", "12345678"),
            ("12.345.678", "12345678"),
            ("12,345,678", "12345678"),
            ("12 345 678", "12345678"),
            ("DNI 12.345.678", "12345678"),
            ("", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_dni_number(raw) == expected


# ============================================================
# Patrón regex
# ============================================================

class TestDniPattern:
    def test_matches_plain_8_digits(self):
        m = DNI_PATTERN.search("Documento 12345678 emitido")
        assert m is not None
        assert m.group(1) == "12345678"

    def test_matches_with_dots(self):
        m = DNI_PATTERN.search("Documento 12.345.678 emitido")
        assert m is not None
        # El group conserva los puntos; la normalización los limpia después.
        assert "12" in m.group(1)

    def test_matches_with_commas(self):
        m = DNI_PATTERN.search("Documento 12,345,678 emitido")
        assert m is not None

    def test_matches_7_digits_with_separator(self):
        # DNI de 7 dígitos (más antiguos): "1234567" no matchea sin separador,
        # pero "1.234.567" sí.
        m = DNI_PATTERN.search("DNI 1.234.567")
        assert m is not None


# ============================================================
# Plausibilidad
# ============================================================

class TestIsPlausibleDni:
    @pytest.mark.parametrize(
        "number,expected",
        [
            ("12345678", True),    # 8 dígitos, válido
            ("1234567", True),     # 7 dígitos, válido
            ("123456", False),     # menos de 7 dígitos
            ("123456789", False),  # más de 8 dígitos
            ("11111111", False),   # todos iguales (trivial)
            ("00000000", False),   # todos ceros
            ("", False),           # vacío
        ],
    )
    def test_plausibility(self, number, expected):
        assert _is_plausible_dni(number) is expected
