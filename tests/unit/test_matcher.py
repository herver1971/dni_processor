"""
Tests unitarios del módulo de matcheo (app.core.matcher).

Cubren:
- Matcheo exacto por número idéntico
- Matcheo tolerante (Levenshtein ≤ 2)
- Rechazo de matches con distancia > threshold
- Resolución de conflictos (mejor distancia gana)
- Manejo de DNIs sin número leído (None)
- Generación correcta de huérfanos
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.matcher import _compute_distance, match_frentes_dorsos
from app.schemas.session import BoundingBox, DetectedDNI, DNISide


pytestmark = pytest.mark.unit


def _make_dni(
    dni_number: str | None,
    side: DNISide,
    confidence: float = 0.9,
) -> DetectedDNI:
    """Helper para construir DetectedDNI mockeado."""
    return DetectedDNI(
        crop_id=str(uuid4()),
        source_image=Path("/fake/path/img.jpg"),
        bbox=BoundingBox(x=0, y=0, width=400, height=252),
        crop_path=Path("/fake/path/crop.jpg"),
        side=side,
        side_confidence=1.0,
        dni_number=dni_number,
        ocr_confidence=confidence,
    )


# ============================================================
# _compute_distance
# ============================================================

class TestComputeDistance:
    def test_identical_strings(self):
        assert _compute_distance("12345678", "12345678") == 0

    def test_one_substitution(self):
        assert _compute_distance("12345678", "12345679") == 1

    def test_one_deletion(self):
        assert _compute_distance("12345678", "1234568") == 1

    def test_none_inputs(self):
        assert _compute_distance(None, "12345678") is None
        assert _compute_distance("12345678", None) is None
        assert _compute_distance(None, None) is None


# ============================================================
# match_frentes_dorsos — casos base
# ============================================================

class TestMatchingExact:
    def test_single_exact_match(self):
        frentes = [_make_dni("12345678", DNISide.FRENTE)]
        dorsos = [_make_dni("12345678", DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert len(orphan_f) == 0
        assert len(orphan_d) == 0
        assert pairs[0].is_exact_match is True
        assert pairs[0].match_distance == 0

    def test_multiple_exact_matches(self):
        frentes = [
            _make_dni("11111111", DNISide.FRENTE),
            _make_dni("22222222", DNISide.FRENTE),
            _make_dni("33333333", DNISide.FRENTE),
        ]
        dorsos = [
            _make_dni("33333333", DNISide.DORSO),
            _make_dni("11111111", DNISide.DORSO),
            _make_dni("22222222", DNISide.DORSO),
        ]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 3
        assert all(p.is_exact_match for p in pairs)
        assert len(orphan_f) == 0
        assert len(orphan_d) == 0


# ============================================================
# match_frentes_dorsos — tolerancia
# ============================================================

class TestMatchingTolerance:
    def test_one_char_difference_matches(self):
        """Distancia 1: aceptable."""
        frentes = [_make_dni("12345678", DNISide.FRENTE)]
        dorsos = [_make_dni("12345679", DNISide.DORSO)]
        pairs, _, _ = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert pairs[0].match_distance == 1
        assert pairs[0].is_exact_match is False

    def test_two_char_difference_matches(self):
        """Distancia 2: aún aceptable (límite del threshold)."""
        frentes = [_make_dni("12345678", DNISide.FRENTE)]
        dorsos = [_make_dni("12345699", DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert pairs[0].match_distance == 2

    def test_three_char_difference_does_not_match(self):
        """Distancia 3: por encima del threshold → huérfanos."""
        frentes = [_make_dni("12345678", DNISide.FRENTE)]
        dorsos = [_make_dni("12999999", DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 0
        assert len(orphan_f) == 1
        assert len(orphan_d) == 1


# ============================================================
# match_frentes_dorsos — conflictos
# ============================================================

class TestMatchingConflictResolution:
    def test_best_distance_wins(self):
        """
        Si dos frentes podrían matchear con el mismo dorso,
        gana el de menor distancia.
        """
        frente_perfect = _make_dni("12345678", DNISide.FRENTE)
        frente_close = _make_dni("12345670", DNISide.FRENTE)  # distancia 1
        dorso = _make_dni("12345678", DNISide.DORSO)

        pairs, orphan_f, orphan_d = match_frentes_dorsos(
            [frente_perfect, frente_close], [dorso]
        )
        assert len(pairs) == 1
        assert pairs[0].frente.crop_id == frente_perfect.crop_id
        assert pairs[0].match_distance == 0
        assert len(orphan_f) == 1
        assert orphan_f[0].detected.crop_id == frente_close.crop_id

    def test_no_double_assignment(self):
        """Un mismo dorso no puede aparecer en dos pares."""
        frentes = [
            _make_dni("12345678", DNISide.FRENTE),
            _make_dni("12345678", DNISide.FRENTE),  # duplicado
        ]
        dorsos = [_make_dni("12345678", DNISide.DORSO)]
        pairs, orphan_f, _ = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert len(orphan_f) == 1


# ============================================================
# match_frentes_dorsos — casos sin número OCR
# ============================================================

class TestMatchingNoOcr:
    def test_frente_without_number_becomes_orphan(self):
        frentes = [_make_dni(None, DNISide.FRENTE)]
        dorsos = [_make_dni("12345678", DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 0
        assert len(orphan_f) == 1
        assert "No se extrajo número" in orphan_f[0].reason
        assert len(orphan_d) == 1

    def test_both_without_numbers(self):
        frentes = [_make_dni(None, DNISide.FRENTE)]
        dorsos = [_make_dni(None, DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 0
        assert len(orphan_f) == 1
        assert len(orphan_d) == 1

    def test_empty_inputs(self):
        pairs, orphan_f, orphan_d = match_frentes_dorsos([], [])
        assert pairs == []
        assert orphan_f == []
        assert orphan_d == []


# ============================================================
# Casos asimétricos (más frentes que dorsos o viceversa)
# ============================================================

class TestMatchingAsymmetric:
    def test_more_frentes_than_dorsos(self):
        frentes = [
            _make_dni("11111111", DNISide.FRENTE),
            _make_dni("22222222", DNISide.FRENTE),
            _make_dni("33333333", DNISide.FRENTE),
        ]
        dorsos = [_make_dni("22222222", DNISide.DORSO)]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert len(orphan_f) == 2
        assert len(orphan_d) == 0

    def test_more_dorsos_than_frentes(self):
        frentes = [_make_dni("22222222", DNISide.FRENTE)]
        dorsos = [
            _make_dni("11111111", DNISide.DORSO),
            _make_dni("22222222", DNISide.DORSO),
            _make_dni("33333333", DNISide.DORSO),
        ]
        pairs, orphan_f, orphan_d = match_frentes_dorsos(frentes, dorsos)
        assert len(pairs) == 1
        assert len(orphan_f) == 0
        assert len(orphan_d) == 2
