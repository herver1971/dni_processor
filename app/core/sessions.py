"""
Gestión de sesiones de procesamiento.

Cada sesión vive en un directorio bajo `sessions_dir`:

    sessions/
    └── <session_uuid>/
        ├── session.json              # Estado serializado
        ├── originals/                # Imágenes subidas, normalizadas EXIF
        │   ├── <image_id>.jpg
        │   └── ...
        ├── crops/
        │   ├── wide/                 # Recortes amplios (auto)
        │   │   └── <crop_id>.jpg
        │   └── final/                # Recortes finales confirmados
        │       └── <crop_id>.jpg
        └── output.pdf                # PDF final (al completar)

CONTRATO:
- Toda escritura al estado se hace via `update_session()` que persiste
  atómicamente (escribe a temp y rename).
- Las lecturas son baratas (deserialización JSON, sin lock para MVP
  monousuario).
- Cleanup periódico borra sesiones con `updated_at` más viejo que TTL.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.core.constants import SESSION_TTL_HOURS
from app.schemas.web import (
    CropState,
    CropStatus,
    ImageState,
    ImageStatus,
    SessionState,
    SessionStatus,
)

logger = logging.getLogger(__name__)


# ============================================================
# Resolución de paths
# ============================================================

class SessionPaths:
    """Helper para resolver paths dentro del working dir de una sesión."""

    def __init__(self, session_id: str, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = get_settings().get_sessions_dir()
        self.session_id = session_id
        self.root = base_dir / session_id

    @property
    def session_json(self) -> Path:
        return self.root / "session.json"

    @property
    def originals_dir(self) -> Path:
        return self.root / "originals"

    @property
    def wide_crops_dir(self) -> Path:
        return self.root / "crops" / "wide"

    @property
    def final_crops_dir(self) -> Path:
        return self.root / "crops" / "final"

    @property
    def output_pdf(self) -> Path:
        return self.root / "output.pdf"

    def original_for(self, image_id: str, ext: str = ".jpg") -> Path:
        return self.originals_dir / f"{image_id}{ext}"

    def wide_crop_for(self, crop_id: str) -> Path:
        return self.wide_crops_dir / f"{crop_id}.jpg"

    def final_crop_for(self, crop_id: str) -> Path:
        return self.final_crops_dir / f"{crop_id}.jpg"


# ============================================================
# Creación
# ============================================================

def create_session(base_dir: Path | None = None) -> tuple[SessionState, SessionPaths]:
    """
    Crea una nueva sesión con directorio working.

    Returns:
        Tupla (estado_inicial, paths_helper).
    """
    session_id = str(uuid.uuid4())
    paths = SessionPaths(session_id, base_dir)

    # Crear estructura de directorios
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.originals_dir.mkdir(parents=True, exist_ok=True)
    paths.wide_crops_dir.mkdir(parents=True, exist_ok=True)
    paths.final_crops_dir.mkdir(parents=True, exist_ok=True)

    state = SessionState(session_id=session_id)
    save_session(state, paths)
    logger.info(f"Sesión creada: {session_id}")
    return state, paths


# ============================================================
# Persistencia
# ============================================================

def save_session(state: SessionState, paths: SessionPaths) -> None:
    """
    Persiste el estado de la sesión a disco de forma atómica.

    Escribe a un archivo temp y luego rename — evita corrupción si el
    proceso muere a la mitad de la escritura.
    """
    state.touch()
    tmp_path = paths.session_json.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2, default=str)
    tmp_path.replace(paths.session_json)


def load_session(session_id: str, base_dir: Path | None = None) -> SessionState | None:
    """
    Carga el estado de una sesión desde disco.

    Returns:
        SessionState si la sesión existe, None en caso contrario.
    """
    paths = SessionPaths(session_id, base_dir)
    if not paths.session_json.exists():
        return None
    try:
        with paths.session_json.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return SessionState.model_validate(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error cargando sesión {session_id}: {e}")
        return None


def update_session(
    session_id: str,
    update_fn,
    base_dir: Path | None = None,
) -> SessionState | None:
    """
    Carga, modifica y persiste una sesión en una sola operación.

    Args:
        session_id: ID de la sesión.
        update_fn: Función que recibe SessionState y lo modifica in-place
                   (puede devolver None) o devuelve un nuevo SessionState.
        base_dir: Directorio base (default: get_settings().get_sessions_dir()).

    Returns:
        Estado actualizado, o None si la sesión no existe.
    """
    state = load_session(session_id, base_dir)
    if state is None:
        return None
    paths = SessionPaths(session_id, base_dir)

    result = update_fn(state)
    if isinstance(result, SessionState):
        state = result

    save_session(state, paths)
    return state


# ============================================================
# Helpers de mutación común
# ============================================================

def add_image_to_session(
    state: SessionState,
    image_id: str,
    original_filename: str,
    declared_side,  # DNISide, no importado para evitar ciclo
    normalized_path: str,
) -> ImageState:
    """Agrega una imagen al estado y devuelve el ImageState creado."""
    img = ImageState(
        image_id=image_id,
        original_filename=original_filename,
        normalized_path=normalized_path,
        declared_side=declared_side,
    )
    state.images[image_id] = img
    return img


def add_crop_to_session(
    state: SessionState,
    crop: CropState,
) -> None:
    """Agrega un crop al estado y vincula con su imagen fuente."""
    state.crops[crop.crop_id] = crop
    if crop.source_image_id in state.images:
        state.images[crop.source_image_id].crop_ids.append(crop.crop_id)


# ============================================================
# Cleanup
# ============================================================

def cleanup_expired_sessions(
    base_dir: Path | None = None,
    ttl_hours: int = SESSION_TTL_HOURS,
) -> int:
    """
    Elimina sesiones cuya `updated_at` es más antigua que `ttl_hours`.

    Returns:
        Cantidad de sesiones eliminadas.
    """
    if base_dir is None:
        base_dir = get_settings().get_sessions_dir()
    if not base_dir.exists():
        return 0

    threshold = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    deleted = 0

    for session_dir in base_dir.iterdir():
        if not session_dir.is_dir():
            continue

        json_path = session_dir / "session.json"
        if not json_path.exists():
            # Directorio huérfano sin session.json — borrar igual
            shutil.rmtree(session_dir, ignore_errors=True)
            deleted += 1
            continue

        try:
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            updated_at_str = data.get("updated_at")
            if not updated_at_str:
                continue
            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            # Asegurar que ambos datetimes son timezone-aware para comparar
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            expired = updated_at < threshold

            if expired:
                shutil.rmtree(session_dir, ignore_errors=True)
                logger.info(f"Cleanup: eliminada sesión expirada {session_dir.name}")
                deleted += 1
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(f"Cleanup: no se pudo evaluar {session_dir.name}: {e}")

    return deleted


# ============================================================
# Discard (manual)
# ============================================================

def discard_session(session_id: str, base_dir: Path | None = None) -> bool:
    """
    Elimina manualmente una sesión (botón "Recomenzar" del usuario).

    Returns:
        True si se eliminó, False si no existía.
    """
    paths = SessionPaths(session_id, base_dir)
    if not paths.root.exists():
        return False
    shutil.rmtree(paths.root, ignore_errors=True)
    logger.info(f"Sesión descartada: {session_id}")
    return True
