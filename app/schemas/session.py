"""
Schemas Pydantic — modelos de dominio del DNI Processor.

Estos schemas representan las entidades que fluyen por el pipeline:
- DetectedDNI: un recorte de DNI detectado en una imagen
- DNISide: enum frente/dorso/desconocido
- MatchedPair: par frente+dorso emparejado
- UnpairedDNI: DNI huérfano (sin par)
- UnprocessedImage: imagen donde no se detectó ningún DNI
- ProcessingResult: resultado completo del pipeline
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, ConfigDict


class DNISide(str, Enum):
    """Lado del DNI: frente, dorso, o desconocido (clasificación pendiente)."""

    FRENTE = "frente"
    DORSO = "dorso"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    """Bounding box rectangular en coordenadas de píxel."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(ge=0, description="Coordenada x del vértice superior izquierdo")
    y: int = Field(ge=0, description="Coordenada y del vértice superior izquierdo")
    width: int = Field(gt=0, description="Ancho en píxeles")
    height: int = Field(gt=0, description="Alto en píxeles")

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        # Aspect ratio normalizado: siempre ancho/alto en orientación horizontal.
        return max(self.width, self.height) / min(self.width, self.height)


class DetectedDNI(BaseModel):
    """
    Un DNI detectado y recortado de una imagen fuente.

    Representa el resultado de la fase de visión por computadora:
    se ha identificado un contorno con aspect ratio compatible con ID-1
    y se ha extraído el recorte rectangular (bounding box) preservando
    la inclinación original.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    crop_id: str = Field(description="Identificador único del recorte (UUID)")
    source_image: Path = Field(description="Imagen fuente de donde se extrajo")
    bbox: BoundingBox = Field(description="Bounding box en la imagen fuente")
    crop_path: Path = Field(description="Path al archivo del recorte ya guardado")
    side: DNISide = Field(default=DNISide.UNKNOWN, description="Frente/Dorso/Desconocido")
    side_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confianza de la clasificación (0-1)"
    )
    dni_number: str | None = Field(
        default=None,
        description="Número de DNI extraído por OCR (sin separadores)"
    )
    ocr_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confianza del OCR sobre el número extraído"
    )
    # v0.3.0a: bbox sugerido dentro del recorte amplio para pre-cargar
    # el rectángulo de ajuste en Cropper.js. Coordenadas RELATIVAS al
    # `crop_path` (no a la imagen fuente). None cuando el recorte fue
    # hecho manualmente por el usuario.
    suggested_bbox_in_crop: BoundingBox | None = Field(
        default=None,
        description="Bbox sugerido dentro del recorte amplio (para Cropper.js)"
    )


class MatchedPair(BaseModel):
    """Par de DNI frente + dorso emparejados por número."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    frente: DetectedDNI
    dorso: DetectedDNI
    match_distance: int = Field(
        ge=0,
        description="Distancia Levenshtein entre número de frente y dorso"
    )
    is_exact_match: bool = Field(
        description="True si el match fue exacto (distancia 0)"
    )


class UnpairedDNI(BaseModel):
    """DNI detectado que no pudo emparejarse — requiere intervención manual."""

    detected: DetectedDNI
    reason: str = Field(description="Por qué no se pudo emparejar")


class UnprocessedImage(BaseModel):
    """Imagen donde no se detectó ningún DNI — requiere recorte manual."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_image: Path
    reason: str = Field(description="Por qué falló la detección")


class ProcessingResult(BaseModel):
    """Resultado completo del pipeline de procesamiento."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    pairs: list[MatchedPair] = Field(default_factory=list)
    unpaired_frentes: list[UnpairedDNI] = Field(default_factory=list)
    unpaired_dorsos: list[UnpairedDNI] = Field(default_factory=list)
    unprocessed_images: list[UnprocessedImage] = Field(default_factory=list)
    output_pdf_path: Path | None = None

    @property
    def total_images_input(self) -> int:
        # Las fuentes únicas: pares (cada DNI tiene una source) + huérfanos + no procesadas
        sources: set[Path] = set()
        for pair in self.pairs:
            sources.add(pair.frente.source_image)
            sources.add(pair.dorso.source_image)
        for orphan in self.unpaired_frentes + self.unpaired_dorsos:
            sources.add(orphan.detected.source_image)
        for unproc in self.unprocessed_images:
            sources.add(unproc.source_image)
        return len(sources)

    @property
    def total_pairs(self) -> int:
        return len(self.pairs)

    @property
    def total_orphans(self) -> int:
        return len(self.unpaired_frentes) + len(self.unpaired_dorsos)

    @property
    def total_unprocessed(self) -> int:
        return len(self.unprocessed_images)
