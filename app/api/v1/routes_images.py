"""
Endpoints de imágenes y archivos.

POST   /api/v1/sessions/{id}/images                   → upload de imágenes
GET    /api/v1/sessions/{id}/images/{image_id}        → servir imagen normalizada
GET    /api/v1/sessions/{id}/crops/{crop_id}/wide     → servir recorte amplio
GET    /api/v1/sessions/{id}/crops/{crop_id}/final    → servir recorte final
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import cv2
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.core.constants import (
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_IMAGE_SIZE_BYTES,
    MAX_IMAGES_PER_SESSION,
)
from app.core.sessions import (
    SessionPaths,
    add_image_to_session,
    load_session,
    save_session,
)
from app.core.vision import load_image_exif_aware
from app.rate_limiter import limiter
from app.schemas.api import (
    UploadedImageInfo,
    UploadImagesResponse,
)
from app.schemas.session import DNISide
from app.schemas.web import ImageStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["images"])


def _validate_upload(file: UploadFile) -> tuple[bool, str | None]:
    """
    Valida un archivo subido.

    Returns:
        (ok, error_message). Si ok=True, error_message es None.
    """
    if not file.filename:
        return False, "Archivo sin nombre"

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return False, f"Extensión no permitida: {ext}"

    # FastAPI no nos da el size directo de forma confiable;
    # validamos al leer el contenido más abajo.
    return True, None


@router.post("/{session_id}/images", response_model=UploadImagesResponse)
@limiter.limit("60/minute")
async def upload_images(
    request: Request,
    session_id: str,
    side: str = Form(..., description="'frente' o 'dorso'"),
    files: list[UploadFile] = File(...),
) -> UploadImagesResponse:
    """
    Sube imágenes a una sesión.

    El parámetro `side` indica si las imágenes son frentes o dorsos.
    Las imágenes se NORMALIZAN al subir: se aplica la rotación EXIF
    físicamente y se guardan como JPEG estándar para que el browser
    y el backend vean exactamente la misma orientación.
    """
    # Validar sesión
    state = load_session(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada",
        )

    # Validar side
    try:
        declared_side = DNISide(side.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Side inválido: '{side}'. Usar 'frente' o 'dorso'.",
        )

    if declared_side == DNISide.UNKNOWN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side='unknown' no permitido al subir.",
        )

    # Validar cantidad máxima
    if len(state.images) + len(files) > MAX_IMAGES_PER_SESSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Excede el máximo de {MAX_IMAGES_PER_SESSION} imágenes por sesión",
        )

    paths = SessionPaths(session_id)
    uploaded: list[UploadedImageInfo] = []
    skipped: list[dict] = []

    for file in files:
        ok, err = _validate_upload(file)
        if not ok:
            skipped.append({"filename": file.filename, "reason": err})
            continue

        # Leer contenido
        content = await file.read()
        if len(content) > MAX_IMAGE_SIZE_BYTES:
            skipped.append({
                "filename": file.filename,
                "reason": f"Excede tamaño máximo ({MAX_IMAGE_SIZE_BYTES} bytes)",
            })
            continue

        # Guardar a un archivo temp con la extensión original
        image_id = str(uuid.uuid4())
        ext_original = Path(file.filename).suffix.lower()
        temp_path = paths.originals_dir / f"{image_id}_temp{ext_original}"
        temp_path.write_bytes(content)

        # Normalizar EXIF y reguardar como JPEG estándar
        try:
            normalized = load_image_exif_aware(temp_path)
            final_path = paths.original_for(image_id, ext=".jpg")
            # Guardar como JPEG calidad 95
            success, encoded = cv2.imencode(".jpg", normalized, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not success:
                raise IOError("No se pudo codificar como JPEG")
            encoded.tofile(str(final_path))
            temp_path.unlink()
        except Exception as e:
            skipped.append({
                "filename": file.filename,
                "reason": f"Falló normalización: {type(e).__name__}: {e}",
            })
            temp_path.unlink(missing_ok=True)
            continue

        # Registrar en el estado
        relative_path = str(final_path.relative_to(paths.root))
        add_image_to_session(
            state, image_id, file.filename or "unknown", declared_side, relative_path,
        )
        uploaded.append(UploadedImageInfo(
            image_id=image_id,
            original_filename=file.filename or "unknown",
            declared_side=declared_side,
            size_bytes=final_path.stat().st_size,
        ))

    save_session(state, paths)
    logger.info(
        f"Sesión {session_id}: subidos {len(uploaded)}/{len(files)} archivos como {declared_side}"
    )
    return UploadImagesResponse(
        session_id=session_id,
        uploaded=uploaded,
        skipped=skipped,
    )


# ============================================================
# Servido de archivos
# ============================================================

def _safe_path_under(root: Path, candidate: Path) -> Path:
    """
    Verifica que `candidate` esté dentro de `root` (evita path traversal).
    Devuelve el candidate resuelto si es seguro.
    """
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path inválido (fuera del working dir)",
        )
    return candidate_resolved


@router.get("/{session_id}/images/{image_id}")
def get_image(session_id: str, image_id: str) -> FileResponse:
    """Sirve la imagen normalizada de una sesión."""
    state = load_session(session_id)
    if state is None or image_id not in state.images:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    paths = SessionPaths(session_id)
    file_path = _safe_path_under(paths.root, paths.original_for(image_id))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    return FileResponse(file_path, media_type="image/jpeg")


@router.get("/{session_id}/crops/{crop_id}/wide")
def get_wide_crop(session_id: str, crop_id: str) -> FileResponse:
    """Sirve el recorte amplio de un crop (para el cropper)."""
    state = load_session(session_id)
    if state is None or crop_id not in state.crops:
        raise HTTPException(status_code=404, detail="Crop no encontrado")

    paths = SessionPaths(session_id)
    file_path = _safe_path_under(paths.root, paths.wide_crop_for(crop_id))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    return FileResponse(file_path, media_type="image/jpeg")


@router.get("/{session_id}/crops/{crop_id}/final")
def get_final_crop(session_id: str, crop_id: str) -> FileResponse:
    """Sirve el recorte final confirmado."""
    state = load_session(session_id)
    if state is None or crop_id not in state.crops:
        raise HTTPException(status_code=404, detail="Crop no encontrado")

    crop = state.crops[crop_id]
    if not crop.final_crop_path:
        raise HTTPException(status_code=404, detail="Crop aún no confirmado")

    paths = SessionPaths(session_id)
    file_path = _safe_path_under(paths.root, paths.final_crop_for(crop_id))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    return FileResponse(file_path, media_type="image/jpeg")
