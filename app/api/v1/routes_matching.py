"""
Endpoints de matcheo y generación de PDF (Sprint 3a).

POST   /api/v1/sessions/{id}/match                  → genera sugerencias por OCR
PUT    /api/v1/sessions/{id}/pairs                  → reemplaza pares con la lista del usuario
POST   /api/v1/sessions/{id}/generate-pdf           → produce el PDF final
GET    /api/v1/sessions/{id}/output.pdf             → descarga del PDF generado
POST   /api/v1/sessions/{id}/reset                  → descarta sesión post-PDF
                                                      ("empezar otro trámite")
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.core.composer import compose_pdf
from app.core.matcher import match_frentes_dorsos
from app.core.sessions import (
    SessionPaths,
    discard_session,
    load_session,
    save_session,
)
from app.rate_limiter import limiter
from app.schemas.api import (
    GeneratePdfResponse,
    GenerateSuggestionsResponse,
    PairInfo,
    ResetSessionResponse,
    UpdatePairsRequest,
    UpdatePairsResponse,
)
from app.schemas.session import BoundingBox as DomainBBox
from app.schemas.session import DetectedDNI, DNISide
from app.schemas.session import MatchedPair, UnpairedDNI
from app.schemas.web import (
    CropState,
    CropStatus,
    PairOrigin,
    PairState,
    SessionState,
    SessionStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["matching"])


# ============================================================
# Helpers
# ============================================================

def _crop_to_detected_dni(crop: CropState, paths: SessionPaths) -> DetectedDNI:
    """
    Convierte un CropState (web) a DetectedDNI (dominio).

    Necesario porque el módulo `matcher` opera sobre DetectedDNI, que es
    el modelo del dominio. La capa web tiene su propio modelo más rico
    con estado de UI.
    """
    return DetectedDNI(
        crop_id=crop.crop_id,
        source_image=paths.root / crop.wide_crop_path,
        bbox=crop.final_bbox or DomainBBox(x=0, y=0, width=1, height=1),
        crop_path=paths.root / (crop.final_crop_path or crop.wide_crop_path),
        side=crop.side,
        dni_number=crop.dni_number,
        ocr_confidence=crop.ocr_confidence,
    )


def _pair_to_info(pair: PairState):
    from app.schemas.api import PairInfo
    return PairInfo(
        pair_id=pair.pair_id,
        frente_crop_id=pair.frente_crop_id,
        dorso_crop_id=pair.dorso_crop_id,
        position=pair.position,
        origin=pair.origin,
        match_distance=pair.match_distance,
    )


# ============================================================
# POST /sessions/{id}/match — Genera sugerencias por OCR
# ============================================================

@router.post(
    "/{session_id}/match",
    response_model=GenerateSuggestionsResponse,
)
@limiter.limit("10/minute")
def generate_suggestions(request: Request, session_id: str) -> GenerateSuggestionsResponse:
    """
    Genera sugerencias de pares usando OCR.

    Reemplaza los pares existentes (si los hubiera) por las sugerencias
    del matcher. Frentes y dorsos que el matcher no pueda emparejar
    quedan sin par y deben ser resueltos por el usuario en la UI.

    El estado de la sesión pasa a MATCHING.
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    paths = SessionPaths(session_id)

    # Trabajamos solo con crops CONFIRMADOS
    frentes_confirmed = state.confirmed_frentes
    dorsos_confirmed = state.confirmed_dorsos

    if not frentes_confirmed and not dorsos_confirmed:
        raise HTTPException(
            status_code=400,
            detail="No hay crops confirmados. Confirmá los recortes antes de matchear.",
        )

    # Convertir a DetectedDNI para invocar el matcher
    frentes_domain = [_crop_to_detected_dni(c, paths) for c in frentes_confirmed]
    dorsos_domain = [_crop_to_detected_dni(c, paths) for c in dorsos_confirmed]

    pairs_result, unpaired_f, unpaired_d = match_frentes_dorsos(
        frentes_domain, dorsos_domain,
    )

    # Reemplazar pares del estado
    state.pairs.clear()
    for position, mp in enumerate(pairs_result):
        pair_id = str(uuid.uuid4())
        origin = PairOrigin.OCR_EXACT if mp.is_exact_match else PairOrigin.OCR_APPROXIMATE
        state.pairs[pair_id] = PairState(
            pair_id=pair_id,
            frente_crop_id=mp.frente.crop_id,
            dorso_crop_id=mp.dorso.crop_id,
            position=position,
            origin=origin,
            match_distance=mp.match_distance,
        )

    # Crear pares "manuales" provisorios para los huérfanos que se puedan
    # combinar 1-a-1. El usuario los corrige luego con drag-and-drop.
    # Esto evita el caso "tengo 3 frentes huérfanos y 3 dorsos huérfanos
    # pero la UI no me deja crear pares para emparejarlos".
    n_orphan_pairs = min(len(unpaired_f), len(unpaired_d))
    next_position = len(state.pairs)
    for i in range(n_orphan_pairs):
        pair_id = str(uuid.uuid4())
        state.pairs[pair_id] = PairState(
            pair_id=pair_id,
            frente_crop_id=unpaired_f[i].detected.crop_id,
            dorso_crop_id=unpaired_d[i].detected.crop_id,
            position=next_position + i,
            origin=PairOrigin.MANUAL,
            match_distance=0,
        )

    # Los que realmente quedan sin par (asimetría: más frentes que dorsos
    # o viceversa) son los que se reportan al usuario
    remaining_unpaired_f = len(unpaired_f) - n_orphan_pairs
    remaining_unpaired_d = len(unpaired_d) - n_orphan_pairs

    state.status = SessionStatus.MATCHING
    save_session(state, paths)

    logger.info(
        f"Sesión {session_id}: {len(pairs_result)} pares por OCR + "
        f"{n_orphan_pairs} pares provisorios = {len(state.pairs)} totales "
        f"(huérfanos restantes: {remaining_unpaired_f}F + {remaining_unpaired_d}D)"
    )

    sorted_pairs = sorted(state.pairs.values(), key=lambda p: p.position)
    return GenerateSuggestionsResponse(
        session_id=session_id,
        pairs=[_pair_to_info(p) for p in sorted_pairs],
        n_unpaired_frentes=remaining_unpaired_f,
        n_unpaired_dorsos=remaining_unpaired_d,
    )


# ============================================================
# PUT /sessions/{id}/pairs — Reemplaza pares con la lista del usuario
# ============================================================

@router.put(
    "/{session_id}/pairs",
    response_model=UpdatePairsResponse,
)
@limiter.limit("60/minute")
def update_pairs(
    request: Request,
    session_id: str,
    payload: UpdatePairsRequest,
) -> UpdatePairsResponse:
    """
    Reemplaza el conjunto completo de pares con la lista del request.

    API DECLARATIVA: el cliente manda el estado deseado de TODOS los
    pares (frente↔dorso + position), no incrementos. Esto simplifica
    el drag-and-drop en el browser: tras cualquier cambio se manda la
    lista completa.

    Validaciones:
    - Cada crop_id existe y está confirmado
    - Sides correctos (frente vs dorso)
    - Sin duplicación de crops entre pares
    - Positions únicas
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    paths = SessionPaths(session_id)

    # Validar cada item del request
    seen_frentes: set[str] = set()
    seen_dorsos: set[str] = set()
    seen_positions: set[int] = set()

    for item in payload.pairs:
        # Existe?
        if item.frente_crop_id not in state.crops:
            raise HTTPException(
                status_code=400,
                detail=f"Frente {item.frente_crop_id} no existe en la sesión",
            )
        if item.dorso_crop_id not in state.crops:
            raise HTTPException(
                status_code=400,
                detail=f"Dorso {item.dorso_crop_id} no existe en la sesión",
            )
        frente_crop = state.crops[item.frente_crop_id]
        dorso_crop = state.crops[item.dorso_crop_id]

        # Confirmado?
        if frente_crop.status != CropStatus.CONFIRMED:
            raise HTTPException(
                status_code=400,
                detail=f"Crop {item.frente_crop_id} no está confirmado",
            )
        if dorso_crop.status != CropStatus.CONFIRMED:
            raise HTTPException(
                status_code=400,
                detail=f"Crop {item.dorso_crop_id} no está confirmado",
            )

        # Lado correcto?
        if frente_crop.side != DNISide.FRENTE:
            raise HTTPException(
                status_code=400,
                detail=f"Crop {item.frente_crop_id} no es un frente (side={frente_crop.side})",
            )
        if dorso_crop.side != DNISide.DORSO:
            raise HTTPException(
                status_code=400,
                detail=f"Crop {item.dorso_crop_id} no es un dorso (side={dorso_crop.side})",
            )

        # Duplicaciones
        if item.frente_crop_id in seen_frentes:
            raise HTTPException(
                status_code=400,
                detail=f"Frente {item.frente_crop_id} aparece en múltiples pares",
            )
        if item.dorso_crop_id in seen_dorsos:
            raise HTTPException(
                status_code=400,
                detail=f"Dorso {item.dorso_crop_id} aparece en múltiples pares",
            )
        if item.position in seen_positions:
            raise HTTPException(
                status_code=400,
                detail=f"Posición {item.position} duplicada",
            )

        seen_frentes.add(item.frente_crop_id)
        seen_dorsos.add(item.dorso_crop_id)
        seen_positions.add(item.position)

    # Preservar el "origin" de pares pre-existentes cuando coinciden
    old_origin_by_crops: dict[tuple[str, str], tuple[PairOrigin, int]] = {}
    for old_pair in state.pairs.values():
        key = (old_pair.frente_crop_id, old_pair.dorso_crop_id)
        old_origin_by_crops[key] = (old_pair.origin, old_pair.match_distance)

    # Reemplazar
    state.pairs.clear()
    for item in payload.pairs:
        pair_id = str(uuid.uuid4())
        key = (item.frente_crop_id, item.dorso_crop_id)
        if key in old_origin_by_crops:
            origin, distance = old_origin_by_crops[key]
        else:
            origin, distance = PairOrigin.MANUAL, 0
        state.pairs[pair_id] = PairState(
            pair_id=pair_id,
            frente_crop_id=item.frente_crop_id,
            dorso_crop_id=item.dorso_crop_id,
            position=item.position,
            origin=origin,
            match_distance=distance,
        )

    save_session(state, paths)

    sorted_pairs = sorted(state.pairs.values(), key=lambda p: p.position)
    return UpdatePairsResponse(
        session_id=session_id,
        pairs=[_pair_to_info(p) for p in sorted_pairs],
    )


# ============================================================
# POST /sessions/{id}/generate-pdf
# ============================================================

@router.post(
    "/{session_id}/generate-pdf",
    response_model=GeneratePdfResponse,
)
@limiter.limit("30/minute")
def generate_pdf(request: Request, session_id: str) -> GeneratePdfResponse:
    """
    Genera el PDF final A4 con los pares en su orden actual.

    Pre-condición: `state.can_generate_pdf` debe ser True. Si no, devuelve
    400 con `imbalance_message` (ej: "Faltan 2 dorsos, volvé a revisar").

    El PDF se guarda en el working dir como `output.pdf`. La sesión
    pasa a status COMPLETED, pero el usuario aún puede regenerarlo si
    cambia algo (mientras el TTL no haya expirado).
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    if not state.can_generate_pdf:
        raise HTTPException(
            status_code=400,
            detail=state.imbalance_message or "La sesión no está lista para generar el PDF",
        )

    paths = SessionPaths(session_id)

    # Construir los MatchedPair que espera el composer, ordenados por position
    sorted_pairs = sorted(state.pairs.values(), key=lambda p: p.position)
    matched: list[MatchedPair] = []
    for p in sorted_pairs:
        frente_crop = state.crops[p.frente_crop_id]
        dorso_crop = state.crops[p.dorso_crop_id]
        matched.append(MatchedPair(
            frente=_crop_to_detected_dni(frente_crop, paths),
            dorso=_crop_to_detected_dni(dorso_crop, paths),
            match_distance=p.match_distance,
            is_exact_match=(p.match_distance == 0),
        ))

    # No hay huérfanos: validamos en can_generate_pdf que no haya
    compose_pdf(
        pairs=matched,
        unpaired_frentes=[],
        unpaired_dorsos=[],
        output_path=paths.output_pdf,
    )

    state.status = SessionStatus.COMPLETED
    save_session(state, paths)

    size_bytes = paths.output_pdf.stat().st_size
    logger.info(
        f"Sesión {session_id}: PDF generado con {len(matched)} pares, {size_bytes} bytes"
    )

    return GeneratePdfResponse(
        session_id=session_id,
        pdf_url=f"/api/v1/sessions/{session_id}/output.pdf",
        n_pairs=len(matched),
        size_bytes=size_bytes,
    )


# ============================================================
# GET /sessions/{id}/output.pdf — Descarga
# ============================================================

@router.get("/{session_id}/output.pdf")
def download_pdf(
    session_id: str,
    download: bool = False,
) -> FileResponse:
    """
    Sirve el PDF generado.

    Por default usa `Content-Disposition: inline` para que se pueda
    embeber en un iframe (la pantalla `/completed` lo previsualiza).

    Con `?download=1` cambia a `attachment` y fuerza descarga con
    nombre sugerido. Esto se usa para el botón "Descargar PDF".
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    paths = SessionPaths(session_id)
    if not paths.output_pdf.exists():
        raise HTTPException(
            status_code=404,
            detail="PDF no generado todavía. Llamá a POST /generate-pdf primero.",
        )

    suggested_filename = f"dni_processor_{session_id[:8]}.pdf"

    if download:
        # Forzar descarga con nombre sugerido
        return FileResponse(
            paths.output_pdf,
            media_type="application/pdf",
            filename=suggested_filename,
        )

    # Inline: permite embeber en iframe sin disparar descarga
    return FileResponse(
        paths.output_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{suggested_filename}"',
        },
    )


# ============================================================
# POST /sessions/{id}/reset — Descartar sesión post-PDF
# ============================================================

@router.post(
    "/{session_id}/reset",
    response_model=ResetSessionResponse,
)
@limiter.limit("60/minute")
def reset_session(request: Request, session_id: str) -> ResetSessionResponse:
    """
    "Empezar otro trámite" — descarta la sesión actual y prepara para
    redirección. Equivalente funcional a DELETE /sessions/{id}, pero
    devuelve el redirect_to que la UI debe usar.
    """
    if not discard_session(session_id):
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return ResetSessionResponse(
        discarded_session_id=session_id,
        redirect_to="/",
    )
