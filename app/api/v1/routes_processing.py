"""
Endpoints de procesamiento y manejo de crops.

POST   /api/v1/sessions/{id}/process                                → dispara detección automática
POST   /api/v1/sessions/{id}/crops/{crop_id}/confirm                → confirma recorte ajustado
POST   /api/v1/sessions/{id}/images/{image_id}/crops                → crea recorte manual
DELETE /api/v1/sessions/{id}/crops/{crop_id}                        → descarta un crop
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.core.crop_adjustments import apply_final_crop
from app.core.ocr import extract_dni_number
from app.core.sessions import (
    SessionPaths,
    add_crop_to_session,
    load_session,
    save_session,
)
from app.core.vision import (
    extract_frentes_from_image,
    get_face_net,
)
from app.rate_limiter import limiter
from app.schemas.api import (
    ConfirmCropRequest,
    ConfirmCropResponse,
    CreateManualCropRequest,
    CreateManualCropResponse,
    ProcessResponse,
)
from app.schemas.session import DNISide
from app.schemas.web import (
    CropState,
    CropStatus,
    ImageStatus,
    SessionStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["processing"])


# ============================================================
# Helpers
# ============================================================

def _run_ocr_background(
    session_id: str,
    crop_id: str,
    final_path: "Path",
) -> None:
    """
    Tarea de fondo: ejecuta OCR sobre un crop y persiste el resultado.

    Se invoca via FastAPI BackgroundTasks, después de que la respuesta
    HTTP fue enviada al cliente. La UI ve el crop como confirmado al
    instante; el dni_number aparece cuando el cliente vuelve a leer
    el estado (próximo GET /sessions/{id} o refresh).

    Si OCR falla (modelo no descargado, imagen corrupta, etc.) el crop
    queda con dni_number=None y la sesión sigue siendo válida.

    Race condition mínima: si el usuario modifica el crop entre la
    confirmación y el OCR (ej. lo descarta), el OCR escribe sobre un
    estado obsoleto. Para mitigarlo, el helper recarga el estado al
    leer Y al escribir, y abortar si el crop ya no existe.
    """
    try:
        number, confidence = extract_dni_number(final_path)
    except Exception as e:
        logger.warning(
            f"OCR background falló sobre crop {crop_id[:8]}: "
            f"{type(e).__name__}: {e}"
        )
        return

    # Re-cargar la sesión para escribir el resultado de forma atómica
    state = load_session(session_id)
    if state is None:
        logger.info(f"OCR background: sesión {session_id[:8]} ya no existe")
        return
    if crop_id not in state.crops:
        logger.info(f"OCR background: crop {crop_id[:8]} fue removido")
        return

    crop = state.crops[crop_id]
    crop.dni_number = number
    crop.ocr_confidence = float(confidence)
    paths = SessionPaths(session_id)
    save_session(state, paths)

    if number:
        logger.info(
            f"OCR background: crop {crop_id[:8]} ({crop.side.value}) → "
            f"número={number}, confianza={confidence:.2f}"
        )
    else:
        logger.info(
            f"OCR background: crop {crop_id[:8]} ({crop.side.value}) → "
            f"sin lectura plausible"
        )


def _schedule_ocr(
    background_tasks: BackgroundTasks,
    session_id: str,
    crop_id: str,
    final_path,
) -> None:
    """Encola la tarea de OCR. Se ejecuta DESPUÉS de devolver la respuesta."""
    background_tasks.add_task(
        _run_ocr_background,
        session_id=session_id,
        crop_id=crop_id,
        final_path=final_path,
    )


# ============================================================
# POST /sessions/{id}/process
# ============================================================

@router.post("/{session_id}/process", response_model=ProcessResponse)
@limiter.limit("10/minute")
def process_session(request: Request, session_id: str) -> ProcessResponse:
    """
    Dispara la detección automática sobre todas las imágenes de FRENTES
    pendientes de la sesión.

    Los dorsos no se procesan automáticamente — quedan para recorte
    manual desde la UI.

    Este endpoint es síncrono: bloquea hasta que termina. Para sesiones
    típicas (2-15 DNIs) eso es ~5-10 segundos, aceptable.
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    paths = SessionPaths(session_id)
    state.status = SessionStatus.PROCESSING
    save_session(state, paths)

    # Pre-cargar el detector
    net = get_face_net()
    n_crops_generated = 0
    detection_stats: dict[str, int] = dict(state.detection_stats)

    # Procesar SOLO frentes con status UPLOADED
    frente_images = [
        img for img in state.images.values()
        if img.declared_side == DNISide.FRENTE and img.status == ImageStatus.UPLOADED
    ]

    for img in frente_images:
        # Marcar como en proceso
        img.status = ImageStatus.PROCESSING
        save_session(state, paths)

        try:
            source_path = paths.root / img.normalized_path
            dnis, strategy = extract_frentes_from_image(
                source_path, paths.wide_crops_dir, net=net,
            )
            img.detection_strategy = strategy
            detection_stats[strategy] = detection_stats.get(strategy, 0) + 1

            if not dnis:
                img.status = ImageStatus.FAILED_DETECTION
                img.error_message = "No se detectó ninguna cara"
                logger.info(
                    f"Sesión {session_id}: {img.original_filename} sin detección, "
                    f"queda para recorte manual"
                )
                continue

            # Crear CropState por cada DNI detectado
            for dni in dnis:
                crop_state = CropState(
                    crop_id=dni.crop_id,
                    source_image_id=img.image_id,
                    side=DNISide.FRENTE,
                    status=CropStatus.PENDING,
                    wide_crop_path=str(dni.crop_path.relative_to(paths.root)),
                    suggested_bbox=dni.suggested_bbox_in_crop,
                )
                add_crop_to_session(state, crop_state)
                n_crops_generated += 1

            img.status = ImageStatus.DETECTED

        except Exception as e:
            logger.error(f"Error procesando {img.original_filename}: {e}")
            img.status = ImageStatus.ERROR
            img.error_message = f"{type(e).__name__}: {e}"

    state.detection_stats = detection_stats
    state.status = SessionStatus.REVIEW
    save_session(state, paths)

    logger.info(
        f"Sesión {session_id} procesada: "
        f"{n_crops_generated} crops generados, estrategias={detection_stats}"
    )
    return ProcessResponse(
        session_id=session_id,
        status=state.status,
        n_crops_generated=n_crops_generated,
        detection_stats=detection_stats,
    )


# ============================================================
# POST /sessions/{id}/crops/{crop_id}/confirm
# ============================================================

@router.post(
    "/{session_id}/crops/{crop_id}/confirm",
    response_model=ConfirmCropResponse,
)
@limiter.limit("60/minute")
def confirm_crop(
    request: Request,
    session_id: str,
    crop_id: str,
    payload: ConfirmCropRequest,
    background_tasks: BackgroundTasks,
) -> ConfirmCropResponse:
    """
    Confirma un crop con el bbox final ajustado por el usuario.

    Aplica el bbox + rotación sobre el recorte amplio y guarda el
    recorte final.
    """
    state = load_session(session_id)
    if state is None or crop_id not in state.crops:
        raise HTTPException(status_code=404, detail="Crop no encontrado")

    crop = state.crops[crop_id]
    paths = SessionPaths(session_id)
    wide_path = paths.root / crop.wide_crop_path

    if not wide_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Recorte amplio no encontrado en disco",
        )

    final_path = paths.final_crop_for(crop_id)

    try:
        apply_final_crop(
            wide_crop_path=wide_path,
            bbox=payload.final_bbox,
            rotation_degrees=payload.rotation_degrees,
            output_path=final_path,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error al confirmar crop {crop_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Actualizar estado
    crop.final_bbox = payload.final_bbox
    crop.rotation_degrees = payload.rotation_degrees
    crop.final_crop_path = str(final_path.relative_to(paths.root))
    crop.status = CropStatus.CONFIRMED

    # Si todos los crops están confirmados, cambiar status de sesión
    if state.all_crops_confirmed:
        state.status = SessionStatus.READY_FOR_MATCH

    save_session(state, paths)

    # Encolar OCR para después de responder (no bloquea la respuesta)
    _schedule_ocr(background_tasks, session_id, crop_id, final_path)

    final_url = f"/api/v1/sessions/{session_id}/crops/{crop_id}/final"
    return ConfirmCropResponse(
        crop_id=crop_id,
        status=crop.status,
        final_crop_url=final_url,
    )


# ============================================================
# POST /sessions/{id}/images/{image_id}/crops (recorte manual)
# ============================================================

@router.post(
    "/{session_id}/images/{image_id}/crops",
    response_model=CreateManualCropResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("60/minute")
def create_manual_crop(
    request: Request,
    session_id: str,
    image_id: str,
    payload: CreateManualCropRequest,
    background_tasks: BackgroundTasks,
) -> CreateManualCropResponse:
    """
    Crea un recorte manual sobre la imagen normalizada.

    Usado para:
    - Dorsos (siempre)
    - Frentes cuya detección automática falló
    - Casos donde el usuario quiere agregar más recortes a una imagen
      con múltiples DNIs

    El bbox del request está en coordenadas de la imagen normalizada
    (post-EXIF), que es la que el browser está mostrando.
    """
    state = load_session(session_id)
    if state is None or image_id not in state.images:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    img = state.images[image_id]
    paths = SessionPaths(session_id)
    source_path = paths.root / img.normalized_path

    if not source_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Imagen original no encontrada en disco",
        )

    # Crear crop_id nuevo
    crop_id = str(uuid.uuid4())
    final_path = paths.final_crop_for(crop_id)

    try:
        apply_final_crop(
            wide_crop_path=source_path,
            bbox=payload.bbox,
            rotation_degrees=payload.rotation_degrees,
            output_path=final_path,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error al crear crop manual: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Registrar el crop. No tiene wide_crop_path (vino de la imagen
    # normalizada directamente), así que el campo apunta a la imagen
    # original normalizada por consistencia.
    crop_state = CropState(
        crop_id=crop_id,
        source_image_id=image_id,
        side=payload.side,
        status=CropStatus.CONFIRMED,
        wide_crop_path=img.normalized_path,  # Apunta a la imagen original
        suggested_bbox=None,                  # Recorte manual, sin sugerencia
        final_bbox=payload.bbox,
        final_crop_path=str(final_path.relative_to(paths.root)),
        rotation_degrees=payload.rotation_degrees,
    )
    add_crop_to_session(state, crop_state)

    # Si la imagen estaba en FAILED_DETECTION y se agregó al menos un
    # crop manual, marcarla como DETECTED (manualmente)
    if img.status == ImageStatus.FAILED_DETECTION:
        img.status = ImageStatus.DETECTED

    if state.all_crops_confirmed:
        state.status = SessionStatus.READY_FOR_MATCH

    save_session(state, paths)

    # Encolar OCR para después de responder
    _schedule_ocr(background_tasks, session_id, crop_id, final_path)

    return CreateManualCropResponse(
        crop_id=crop_id,
        status=CropStatus.CONFIRMED,
        final_crop_url=f"/api/v1/sessions/{session_id}/crops/{crop_id}/final",
    )


# ============================================================
# DELETE /sessions/{id}/crops/{crop_id}
# ============================================================

@router.delete(
    "/{session_id}/crops/{crop_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@limiter.limit("60/minute")
def discard_crop(request: Request, session_id: str, crop_id: str) -> None:
    """
    Descarta un crop. Usado cuando el detector creó un crop falso
    (ej: detectó una cara que no era de DNI) y el usuario quiere
    eliminarlo en vez de confirmarlo.
    """
    state = load_session(session_id)
    if state is None or crop_id not in state.crops:
        raise HTTPException(status_code=404, detail="Crop no encontrado")

    crop = state.crops[crop_id]
    crop.status = CropStatus.DISCARDED
    paths = SessionPaths(session_id)

    # Remover de la lista de la imagen
    if crop.source_image_id in state.images:
        img = state.images[crop.source_image_id]
        if crop_id in img.crop_ids:
            img.crop_ids.remove(crop_id)

    save_session(state, paths)
