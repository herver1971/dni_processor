"""
Endpoints de gestión de sesiones.

POST   /api/v1/sessions              → crear nueva sesión
GET    /api/v1/sessions/{id}         → estado actual
DELETE /api/v1/sessions/{id}         → descartar sesión (cleanup manual)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.core.sessions import (
    create_session,
    discard_session,
    load_session,
)
from app.rate_limiter import limiter
from app.schemas.api import (
    CropInfo,
    ImageInfo,
    PairInfo,
    SessionCreateResponse,
    SessionStateResponse,
)
from app.schemas.web import CropState, ImageState, PairState, SessionState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _image_to_info(img: ImageState) -> ImageInfo:
    return ImageInfo(
        image_id=img.image_id,
        original_filename=img.original_filename,
        declared_side=img.declared_side,
        status=img.status,
        detection_strategy=img.detection_strategy,
        crop_ids=img.crop_ids,
        error_message=img.error_message,
    )


def _crop_to_info(crop: CropState) -> CropInfo:
    return CropInfo(
        crop_id=crop.crop_id,
        source_image_id=crop.source_image_id,
        side=crop.side,
        status=crop.status,
        suggested_bbox=crop.suggested_bbox,
        final_bbox=crop.final_bbox,
        rotation_degrees=crop.rotation_degrees,
        has_wide_crop=bool(crop.wide_crop_path),
        has_final_crop=bool(crop.final_crop_path),
        dni_number=crop.dni_number,
    )


def _pair_to_info(pair: PairState) -> PairInfo:
    return PairInfo(
        pair_id=pair.pair_id,
        frente_crop_id=pair.frente_crop_id,
        dorso_crop_id=pair.dorso_crop_id,
        position=pair.position,
        origin=pair.origin,
        match_distance=pair.match_distance,
    )


def state_to_response(state: SessionState) -> SessionStateResponse:
    """Convierte SessionState (dominio) a SessionStateResponse (API)."""
    sorted_pairs = sorted(state.pairs.values(), key=lambda p: p.position)
    return SessionStateResponse(
        session_id=state.session_id,
        status=state.status,
        created_at=state.created_at,
        updated_at=state.updated_at,
        images=[_image_to_info(img) for img in state.images.values()],
        crops=[_crop_to_info(c) for c in state.crops.values()],
        pairs=[_pair_to_info(p) for p in sorted_pairs],
        detection_stats=state.detection_stats,
        n_pending_crops=len(state.pending_crops),
        n_confirmed_crops=len(state.confirmed_crops),
        n_images_failed_detection=len(state.images_failed_detection),
        all_confirmed=state.all_crops_confirmed,
        can_generate_pdf=state.can_generate_pdf,
        imbalance_message=state.imbalance_message,
    )


@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")
def create_new_session(request: Request) -> SessionCreateResponse:
    """Crea una nueva sesión vacía y devuelve su ID."""
    state, _paths = create_session()
    return SessionCreateResponse(
        session_id=state.session_id,
        status=state.status,
        created_at=state.created_at,
    )


@router.get("/{session_id}", response_model=SessionStateResponse)
def get_session(session_id: str) -> SessionStateResponse:
    """Devuelve el estado completo de una sesión."""
    state = load_session(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada o expirada",
        )
    return state_to_response(state)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
def delete_session(request: Request, session_id: str) -> None:
    """Descarta una sesión (botón Recomenzar del usuario)."""
    if not discard_session(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada",
        )
