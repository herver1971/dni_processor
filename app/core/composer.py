"""
Módulo de Composición del PDF — Layout A4 con 4 pares por hoja.

Construye el PDF final usando FPDF2. Layout:
- Hoja A4 vertical (210 × 297 mm)
- 4 pares por hoja (frente + dorso)
- Frentes en columna izquierda, dorsos en columna derecha
- Cada DNI dibujado a tamaño físico real ID-1 (85.6 × 53.98 mm)
- Sin texto, sin etiquetas, sin numeración (decisión del usuario)

Cálculo del layout:
- Ancho útil: 210 - 2*15 = 180 mm
- Ancho de dos columnas + gap: 85.6 + 8 + 85.6 = 179.2 mm → entra
- Alto útil: 297 - 2*15 = 267 mm
- Alto de 4 filas + 3 gaps: 4*53.98 + 3*6 = 233.92 mm → entra cómodo

Los huérfanos (frentes sin par o dorsos sin par) se incluyen al final,
una imagen por celda, para que el usuario pueda revisarlos manualmente.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fpdf import FPDF

from app.core.constants import (
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    COLUMN_GAP_MM,
    DNI_HEIGHT_MM,
    DNI_WIDTH_MM,
    PAGE_MARGIN_MM,
    PAIRS_PER_PAGE,
    ROW_GAP_MM,
)
from app.schemas.session import MatchedPair, UnpairedDNI

logger = logging.getLogger(__name__)


def _compute_pair_positions() -> list[tuple[float, float, float, float]]:
    """
    Calcula las posiciones (x_frente, y_frente, x_dorso, y_dorso) en mm
    para los 4 pares de una hoja A4.

    Returns:
        Lista de 4 tuplas, una por par, con coordenadas absolutas en mm.
    """
    # Posición X de las dos columnas
    total_width = 2 * DNI_WIDTH_MM + COLUMN_GAP_MM
    x_start = (A4_WIDTH_MM - total_width) / 2  # centrado horizontal
    x_frente = x_start
    x_dorso = x_start + DNI_WIDTH_MM + COLUMN_GAP_MM

    # Posición Y de las 4 filas (centrado vertical del bloque)
    total_height = PAIRS_PER_PAGE * DNI_HEIGHT_MM + (PAIRS_PER_PAGE - 1) * ROW_GAP_MM
    y_start = (A4_HEIGHT_MM - total_height) / 2

    positions: list[tuple[float, float, float, float]] = []
    for i in range(PAIRS_PER_PAGE):
        y = y_start + i * (DNI_HEIGHT_MM + ROW_GAP_MM)
        positions.append((x_frente, y, x_dorso, y))
    return positions


def compose_pdf(
    pairs: list[MatchedPair],
    unpaired_frentes: list[UnpairedDNI],
    unpaired_dorsos: list[UnpairedDNI],
    output_path: Path,
) -> Path:
    """
    Genera el PDF final con todos los pares y los huérfanos.

    Estructura del PDF:
    1. Páginas con pares matcheados (4 por hoja)
    2. Si hay huérfanos: páginas adicionales con los huérfanos
       (frentes en columna izquierda, dorsos en columna derecha,
       sin par)

    Args:
        pairs: Pares matcheados.
        unpaired_frentes: Frentes huérfanos.
        unpaired_dorsos: Dorsos huérfanos.
        output_path: Path donde guardar el PDF.

    Returns:
        Path al PDF generado.
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM)

    positions = _compute_pair_positions()

    # ============================================================
    # SECCIÓN 1 — Pares matcheados
    # ============================================================
    for page_idx in range(0, len(pairs), PAIRS_PER_PAGE):
        pdf.add_page()
        page_pairs = pairs[page_idx:page_idx + PAIRS_PER_PAGE]

        for slot_idx, pair in enumerate(page_pairs):
            x_f, y_f, x_d, y_d = positions[slot_idx]
            # Imágenes a tamaño físico real
            pdf.image(
                str(pair.frente.crop_path),
                x=x_f, y=y_f,
                w=DNI_WIDTH_MM, h=DNI_HEIGHT_MM,
            )
            pdf.image(
                str(pair.dorso.crop_path),
                x=x_d, y=y_d,
                w=DNI_WIDTH_MM, h=DNI_HEIGHT_MM,
            )

    # ============================================================
    # SECCIÓN 2 — Huérfanos (si los hay)
    # ============================================================
    # Los huérfanos se ubican uno por celda (frentes a la izquierda,
    # dorsos a la derecha) en filas separadas. Si hay 3 frentes huérfanos
    # y 1 dorso huérfano, se renderizan en las primeras posiciones
    # disponibles sin intentar emparejarlos visualmente.
    if unpaired_frentes or unpaired_dorsos:
        _render_orphans_pages(pdf, unpaired_frentes, unpaired_dorsos, positions)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    logger.info(f"PDF generado: {output_path}")
    return output_path


def _render_orphans_pages(
    pdf: FPDF,
    unpaired_frentes: list[UnpairedDNI],
    unpaired_dorsos: list[UnpairedDNI],
    positions: list[tuple[float, float, float, float]],
) -> None:
    """
    Renderiza páginas adicionales con los huérfanos.

    Frentes huérfanos van en la columna izquierda (su posición habitual);
    dorsos huérfanos en la columna derecha. No se intenta emparejarlos
    visualmente — quedan en celdas separadas para indicar al usuario
    que requieren revisión manual.
    """
    # Iterators para ir consumiendo los huérfanos a medida que llenamos páginas
    frente_iter = iter(unpaired_frentes)
    dorso_iter = iter(unpaired_dorsos)

    current_frente: UnpairedDNI | None = next(frente_iter, None)
    current_dorso: UnpairedDNI | None = next(dorso_iter, None)

    while current_frente is not None or current_dorso is not None:
        pdf.add_page()
        for slot_idx in range(PAIRS_PER_PAGE):
            if current_frente is None and current_dorso is None:
                break
            x_f, y_f, x_d, y_d = positions[slot_idx]

            if current_frente is not None:
                pdf.image(
                    str(current_frente.detected.crop_path),
                    x=x_f, y=y_f,
                    w=DNI_WIDTH_MM, h=DNI_HEIGHT_MM,
                )
                current_frente = next(frente_iter, None)

            if current_dorso is not None:
                pdf.image(
                    str(current_dorso.detected.crop_path),
                    x=x_d, y=y_d,
                    w=DNI_WIDTH_MM, h=DNI_HEIGHT_MM,
                )
                current_dorso = next(dorso_iter, None)
