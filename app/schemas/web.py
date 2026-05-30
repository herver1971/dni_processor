"""
Schemas de sesión web — modelos para el flujo asistido.

Estos schemas extienden los del dominio (`session.py`) con estado de UI:
cuál es el estado actual de la sesión, qué imágenes están pendientes de
revisión manual, qué recortes están confirmados, etc.

El estado de la sesión vive en disco como JSON (`session.json` en el
working dir), no en base de datos. Esto preserva la decisión del roadmap
de mantener el servicio stateless a nivel infra.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.session import BoundingBox, DNISide


class SessionStatus(str, Enum):
    """Estado del ciclo de vida de una sesión."""

    CREATED = "created"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    REVIEW = "review"           # Esperando confirmación del usuario sobre crops
    READY_FOR_MATCH = "ready_for_match"  # Todos los crops confirmados
    MATCHING = "matching"       # Usuario en la pantalla de matcheo
    COMPLETED = "completed"     # PDF generado
    FAILED = "failed"


class CropStatus(str, Enum):
    """Estado de un recorte individual."""

    PENDING = "pending"          # Pendiente de revisión por el usuario
    CONFIRMED = "confirmed"      # El usuario confirmó (con o sin ajustes)
    DISCARDED = "discarded"      # El usuario lo descartó


class ImageStatus(str, Enum):
    """Estado de una imagen subida."""

    UPLOADED = "uploaded"        # Recibida, todavía no procesada
    PROCESSING = "processing"    # En proceso de detección
    DETECTED = "detected"        # Detección automática exitosa
    FAILED_DETECTION = "failed_detection"  # Detección automática falló
                                           # → requiere recorte manual
    ERROR = "error"              # Error en carga o procesamiento


class CropState(BaseModel):
    """
    Estado de un recorte individual dentro de la sesión.

    Un recorte puede provenir de:
    - Detección automática (frente con cara reconocida)
    - Recorte manual (dorso siempre, frente cuando falló detección)

    El usuario puede ajustar el bbox interno antes de confirmar.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    crop_id: str
    source_image_id: str = Field(description="ID de la imagen fuente en la sesión")
    side: DNISide
    status: CropStatus = CropStatus.PENDING

    # Path al recorte amplio (relativo al working dir de la sesión)
    wide_crop_path: str

    # Bbox sugerido dentro del recorte amplio (None si fue recorte manual desde cero)
    suggested_bbox: BoundingBox | None = None

    # Bbox final confirmado por el usuario (None hasta que confirme)
    final_bbox: BoundingBox | None = None

    # Path al recorte final ajustado (None hasta que el usuario confirme)
    final_crop_path: str | None = None

    # Rotación aplicada al recorte final (en grados, múltiplos de 90)
    rotation_degrees: int = 0

    # OCR ejecutado sobre el recorte final
    dni_number: str | None = None
    ocr_confidence: float = 0.0


class PairOrigin(str, Enum):
    """Cómo se generó el par."""

    OCR_EXACT = "ocr_exact"           # Match exacto por OCR (distancia 0)
    OCR_APPROXIMATE = "ocr_approximate"  # Match por OCR con distancia <= threshold
    MANUAL = "manual"                  # Match hecho a mano por el usuario


class PairState(BaseModel):
    """
    Un par frente↔dorso confirmado en la sesión.

    Los pares se generan inicialmente por OCR (cuando es posible) y se
    confirman/reordenan/modifican por el usuario en la pantalla de matcheo.

    El campo `position` determina el orden en el PDF final.
    """

    pair_id: str
    frente_crop_id: str
    dorso_crop_id: str
    position: int = Field(description="Posición 0-based en el PDF final")
    origin: PairOrigin = PairOrigin.MANUAL
    match_distance: int = 0  # Levenshtein entre números OCR (0 si exacto)


class ImageState(BaseModel):
    """Estado de una imagen subida a la sesión."""

    image_id: str
    original_filename: str = Field(description="Nombre original del archivo subido")
    normalized_path: str = Field(description="Path a la imagen post-EXIF (relativo al working dir)")
    declared_side: DNISide = Field(description="Frente o dorso, según en qué zona la subió el usuario")
    status: ImageStatus = ImageStatus.UPLOADED

    # Estrategia que rescató la detección (vacío hasta procesar)
    detection_strategy: str | None = None

    # IDs de los crops generados a partir de esta imagen
    # (varios si hubo múltiples caras detectadas o múltiples recortes manuales)
    crop_ids: list[str] = Field(default_factory=list)

    # Mensaje de error si status == ERROR o FAILED_DETECTION
    error_message: str | None = None


class SessionState(BaseModel):
    """
    Estado completo de una sesión de procesamiento.

    Se serializa como JSON en disk (`session.json` dentro del working dir).
    """

    session_id: str
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # Imágenes subidas (clave: image_id)
    images: dict[str, ImageState] = Field(default_factory=dict)

    # Recortes generados (clave: crop_id)
    crops: dict[str, CropState] = Field(default_factory=dict)

    # Pares emparejados (clave: pair_id). Vacío hasta que el usuario llegue
    # a la pantalla de matcheo y se ejecute el endpoint de sugerencias.
    pairs: dict[str, PairState] = Field(default_factory=dict)

    # Estadísticas de procesamiento
    detection_stats: dict[str, int] = Field(
        default_factory=dict,
        description="Conteo por estrategia (original / clahe / rotated_* / none)"
    )

    def touch(self) -> None:
        """Actualiza updated_at al momento actual."""
        self.updated_at = datetime.now(timezone.utc)

    @property
    def all_crops_confirmed(self) -> bool:
        """True si todos los crops están confirmados o descartados."""
        if not self.crops:
            return False
        return all(
            c.status in (CropStatus.CONFIRMED, CropStatus.DISCARDED)
            for c in self.crops.values()
        )

    @property
    def pending_crops(self) -> list[CropState]:
        return [c for c in self.crops.values() if c.status == CropStatus.PENDING]

    @property
    def confirmed_crops(self) -> list[CropState]:
        return [c for c in self.crops.values() if c.status == CropStatus.CONFIRMED]

    @property
    def images_failed_detection(self) -> list[ImageState]:
        return [
            img for img in self.images.values()
            if img.status == ImageStatus.FAILED_DETECTION
        ]

    @property
    def confirmed_frentes(self) -> list[CropState]:
        """Crops confirmados de tipo FRENTE."""
        return [
            c for c in self.crops.values()
            if c.status == CropStatus.CONFIRMED and c.side == DNISide.FRENTE
        ]

    @property
    def confirmed_dorsos(self) -> list[CropState]:
        """Crops confirmados de tipo DORSO."""
        return [
            c for c in self.crops.values()
            if c.status == CropStatus.CONFIRMED and c.side == DNISide.DORSO
        ]

    @property
    def can_generate_pdf(self) -> bool:
        """
        Indica si la sesión está lista para generar el PDF.

        Condiciones:
        - Hay al menos un par
        - Cantidad de frentes confirmados == cantidad de dorsos confirmados
          (no hay huérfanos estructurales)
        - Todos los frentes y dorsos están emparejados (presentes en algún par)
        """
        if not self.pairs:
            return False
        n_frentes = len(self.confirmed_frentes)
        n_dorsos = len(self.confirmed_dorsos)
        if n_frentes != n_dorsos:
            return False
        # Validar que todos los crops confirmados estén en algún par
        paired_crops: set[str] = set()
        for p in self.pairs.values():
            paired_crops.add(p.frente_crop_id)
            paired_crops.add(p.dorso_crop_id)
        all_confirmed = self.confirmed_frentes + self.confirmed_dorsos
        return all(c.crop_id in paired_crops for c in all_confirmed)

    @property
    def imbalance_message(self) -> str | None:
        """
        Mensaje describiendo por qué can_generate_pdf es False.
        None si todo está OK.
        """
        if self.can_generate_pdf:
            return None
        n_frentes = len(self.confirmed_frentes)
        n_dorsos = len(self.confirmed_dorsos)
        if n_frentes == 0 and n_dorsos == 0:
            return "No hay recortes confirmados todavía"
        if n_frentes > n_dorsos:
            diff = n_frentes - n_dorsos
            return (
                f"Faltan {diff} dorso{'s' if diff != 1 else ''}: "
                f"{n_frentes} frentes vs {n_dorsos} dorsos. "
                f"Volvé a revisar para corregir."
            )
        if n_dorsos > n_frentes:
            diff = n_dorsos - n_frentes
            return (
                f"Faltan {diff} frente{'s' if diff != 1 else ''}: "
                f"{n_dorsos} dorsos vs {n_frentes} frentes. "
                f"Volvé a revisar para corregir."
            )
        # Mismas cantidades pero no todos están emparejados
        if not self.pairs:
            return "Falta generar el matcheo entre frentes y dorsos"
        return "Algunos recortes aún no están emparejados"
