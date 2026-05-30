"""
Schemas Pydantic para la API REST (request/response).

Separados de los schemas de dominio (`session.py`) y de UI (`web.py`)
para mantener un contrato API explícito y versionable.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.session import BoundingBox, DNISide
from app.schemas.web import (
    CropStatus,
    ImageStatus,
    PairOrigin,
    SessionStatus,
)


# ============================================================
# Crear sesión
# ============================================================

class SessionCreateResponse(BaseModel):
    """Respuesta al crear una sesión."""

    session_id: str
    status: SessionStatus
    created_at: datetime


# ============================================================
# Estado de sesión (GET /sessions/{id})
# ============================================================

class ImageInfo(BaseModel):
    image_id: str
    original_filename: str
    declared_side: DNISide
    status: ImageStatus
    detection_strategy: str | None = None
    crop_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None


class CropInfo(BaseModel):
    crop_id: str
    source_image_id: str
    side: DNISide
    status: CropStatus
    suggested_bbox: BoundingBox | None = None
    final_bbox: BoundingBox | None = None
    rotation_degrees: int = 0
    has_wide_crop: bool
    has_final_crop: bool
    dni_number: str | None = None


class PairInfo(BaseModel):
    """Información de un par para la pantalla de matcheo."""

    pair_id: str
    frente_crop_id: str
    dorso_crop_id: str
    position: int
    origin: PairOrigin
    match_distance: int = 0


class SessionStateResponse(BaseModel):
    """Respuesta a GET /sessions/{id} — estado completo."""

    session_id: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    images: list[ImageInfo]
    crops: list[CropInfo]
    pairs: list[PairInfo] = Field(default_factory=list)
    detection_stats: dict[str, int] = Field(default_factory=dict)

    # Métricas derivadas
    n_pending_crops: int
    n_confirmed_crops: int
    n_images_failed_detection: int
    all_confirmed: bool
    can_generate_pdf: bool
    imbalance_message: str | None = None


# ============================================================
# Upload de imágenes (POST /sessions/{id}/images)
# ============================================================

class UploadedImageInfo(BaseModel):
    image_id: str
    original_filename: str
    declared_side: DNISide
    size_bytes: int


class UploadImagesResponse(BaseModel):
    session_id: str
    uploaded: list[UploadedImageInfo]
    skipped: list[dict] = Field(default_factory=list)


# ============================================================
# Procesar (POST /sessions/{id}/process)
# ============================================================

class ProcessRequest(BaseModel):
    """Body del POST /sessions/{id}/process. Vacío por ahora, queda
    para flags futuros (run_ocr, etc.)."""
    pass


class ProcessResponse(BaseModel):
    session_id: str
    status: SessionStatus
    n_crops_generated: int
    detection_stats: dict[str, int]


# ============================================================
# Confirmar recorte (POST /sessions/{id}/crops/{crop_id}/confirm)
# ============================================================

class ConfirmCropRequest(BaseModel):
    """
    Confirma el recorte final con bbox ajustado y rotación.

    Las coordenadas son RELATIVAS al recorte amplio (no a la imagen
    original).
    """

    final_bbox: BoundingBox
    rotation_degrees: int = 0


class ConfirmCropResponse(BaseModel):
    crop_id: str
    status: CropStatus
    final_crop_url: str


# ============================================================
# Crear recorte manual (POST /sessions/{id}/images/{image_id}/crops)
# ============================================================

class CreateManualCropRequest(BaseModel):
    """
    Crea un recorte manual sobre una imagen original (sin pasar por
    detección automática). Usado para dorsos y para frentes que fallaron
    en detección.

    Las coordenadas del bbox son RELATIVAS a la imagen normalizada
    (post-EXIF), que es la que el browser está mostrando.
    """

    bbox: BoundingBox
    side: DNISide
    rotation_degrees: int = 0


class CreateManualCropResponse(BaseModel):
    crop_id: str
    status: CropStatus
    final_crop_url: str


# ============================================================
# Matcheo (Sprint 3)
# ============================================================

class GenerateSuggestionsResponse(BaseModel):
    """
    Respuesta a POST /api/v1/sessions/{id}/match (sin body).

    El endpoint genera pares sugeridos usando OCR. Reemplaza los pares
    existentes si los hubiera. Devuelve la lista completa de pares.
    """

    session_id: str
    pairs: list[PairInfo]
    n_unpaired_frentes: int = 0
    n_unpaired_dorsos: int = 0


class PairAssignmentItem(BaseModel):
    """Una asignación de par dentro del request batch."""

    frente_crop_id: str
    dorso_crop_id: str
    position: int


class UpdatePairsRequest(BaseModel):
    """
    Reemplaza el conjunto completo de pares con la lista provista.

    Esta API es declarativa: el cliente envía el estado deseado de TODOS
    los pares, no incrementos. Esto simplifica la lógica de drag-and-drop
    en el browser (se manda el orden completo después de cualquier cambio).

    El backend valida:
    - Cada crop_id existe y está confirmado
    - Cada frente_crop_id apunta a un crop de side=FRENTE
    - Cada dorso_crop_id apunta a un crop de side=DORSO
    - Ningún crop aparece en más de un par
    - Las positions son únicas
    """

    pairs: list[PairAssignmentItem]


class UpdatePairsResponse(BaseModel):
    session_id: str
    pairs: list[PairInfo]


# ============================================================
# Generación de PDF (Sprint 3)
# ============================================================

class GeneratePdfResponse(BaseModel):
    session_id: str
    pdf_url: str
    n_pairs: int
    size_bytes: int


# ============================================================
# Reset (post-PDF: "empezar otro trámite")
# ============================================================

class ResetSessionResponse(BaseModel):
    """
    Respuesta a POST /api/v1/sessions/{id}/reset.

    La sesión actual queda descartada en disco. La UI debe redirigir al
    usuario a / para iniciar un trámite nuevo.
    """

    discarded_session_id: str
    redirect_to: str = "/"


# ============================================================
# Errores
# ============================================================

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
