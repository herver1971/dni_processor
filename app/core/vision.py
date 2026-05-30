"""
Módulo de Visión por Computadora — Detección de DNIs mediante caras.

PIVOTE ARQUITECTÓNICO (v0.2.1): este módulo abandona completamente la
estrategia anterior de Canny + contornos (que daba 23% de matcheo) y
adopta un enfoque basado en detección facial pre-entrenada.

PRINCIPIO RECTOR: Integridad documental. Igual que antes, se realiza
ÚNICAMENTE recorte rectangular alineado al eje. No se aplica warp,
deskew, ni rectificación trapezoidal. Si el DNI está torcido en la
foto, se preserva torcido en el recorte.

PIPELINE NUEVO:
1. Carga de imagen RESPETANDO EXIF (vía Pillow) → BGR para OpenCV
2. Detección de caras con OpenCV DNN (ResNet-10 SSD pre-entrenado)
3. Si la detección falla, cascada de fallbacks:
   a. Aplicar CLAHE (mejora contraste local) y reintentar
   b. Rotar 90° CCW y reintentar (rescata fotos sin EXIF correcto)
   c. Rotar 90° CW y reintentar
   d. Rotar 180° y reintentar
4. Por cada cara detectada, calcular el bbox del DNI completo
   usando geometría conocida del DNI argentino
5. Recortar con padding perimetral preservando inclinación original

Validado empíricamente en set real: 100% de detección sobre 18 imágenes
(14 con la imagen original, 4 con fallback de rotación).
"""

from __future__ import annotations

import logging
import urllib.request
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# Registro de HEIF/HEIC para Pillow (fotos de iPhone)
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from app.core.constants import (
    BBOX_PADDING_PX,
    CLAHE_CLIP_LIMIT,
    CLAHE_TILE_GRID_SIZE,
    DNI_EXTEND_BOTTOM_RATIO,
    DNI_EXTEND_LEFT_RATIO,
    DNI_EXTEND_RIGHT_RATIO,
    DNI_EXTEND_TOP_RATIO,
    FACE_CONFIDENCE_THRESHOLD,
    FACE_MODEL_INPUT_SIZE,
    NMS_THRESHOLD,
)
from app.schemas.session import BoundingBox, DetectedDNI, DNISide

logger = logging.getLogger(__name__)


# ============================================================
# Modelo de caras pre-entrenado (descarga lazy)
# ============================================================

FACE_PROTO_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/"
    "face_detector/deploy.prototxt"
)
FACE_WEIGHTS_URL = (
    "https://raw.githubusercontent.com/opencv/opencv_3rdparty/"
    "dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
)

# Path por defecto del cache. Configurable vía Settings (Fase 3).
DEFAULT_MODEL_CACHE = Path.home() / ".cache" / "dni_processor"

# Singleton del net cargado en memoria (evita recarga en cada llamada)
_face_net: "cv2.dnn.Net | None" = None


def _ensure_face_model(cache_dir: Path | None = None) -> tuple[Path, Path]:
    """
    Asegura que el modelo de caras esté descargado en cache.

    Returns:
        Tupla (path_prototxt, path_weights).
    """
    if cache_dir is None:
        cache_dir = DEFAULT_MODEL_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)

    proto_path = cache_dir / "deploy.prototxt"
    weights_path = cache_dir / "res10_300x300_ssd_iter_140000.caffemodel"

    if not proto_path.exists():
        logger.info("Descargando prototxt del detector de caras...")
        urllib.request.urlretrieve(FACE_PROTO_URL, proto_path)
    if not weights_path.exists():
        logger.info("Descargando weights del detector de caras (~10MB)...")
        urllib.request.urlretrieve(FACE_WEIGHTS_URL, weights_path)

    return proto_path, weights_path


def get_face_net(cache_dir: Path | None = None) -> "cv2.dnn.Net":
    """
    Devuelve la instancia singleton del detector de caras.

    El modelo se descarga (si hace falta) y se carga en memoria en la
    primera llamada. Llamadas posteriores reutilizan la instancia.
    """
    global _face_net
    if _face_net is None:
        proto, weights = _ensure_face_model(cache_dir)
        _face_net = cv2.dnn.readNetFromCaffe(str(proto), str(weights))
        logger.info("Detector de caras cargado en memoria.")
    return _face_net


def is_face_model_cached(cache_dir: Path | None = None) -> bool:
    """
    Indica si los archivos del detector de caras están presentes en
    cache, sin intentar descargarlos ni cargar el modelo en memoria.

    Usado por el health endpoint y por scripts de pre-deployment para
    verificar el estado del cache sin side-effects.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_MODEL_CACHE
    proto = cache_dir / "deploy.prototxt"
    weights = cache_dir / "res10_300x300_ssd_iter_140000.caffemodel"
    return proto.exists() and weights.exists()


# ============================================================
# Carga de imágenes con EXIF respetado
# ============================================================

def load_image_exif_aware(image_path: Path) -> np.ndarray:
    """
    Carga una imagen RESPETANDO la rotación indicada en EXIF.

    Las cámaras de celular suelen guardar las fotos con la orientación
    física del sensor y agregar un metadato EXIF que indica cómo deben
    mostrarse. OpenCV ignora EXIF por defecto, lo que causa que vea
    imágenes "acostadas" cuando el usuario las ve "paradas".

    Esta función usa Pillow para leer EXIF y `ImageOps.exif_transpose()`
    para aplicar físicamente la rotación al array de píxeles. Luego
    convierte a BGR para uso con OpenCV.

    Soporta JPG, PNG, WebP, HEIC (vía pillow-heif).

    Args:
        image_path: Path al archivo de imagen.

    Returns:
        Array numpy BGR de forma (H, W, 3).

    Raises:
        FileNotFoundError: si el archivo no existe.
        ValueError: si el archivo no puede ser decodificado.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Imagen no encontrada: {image_path}")

    try:
        pil_image = Image.open(image_path)
        # Aplicar rotación EXIF físicamente
        pil_image = ImageOps.exif_transpose(pil_image)
        # Asegurar RGB (HEIC puede venir en otros modos)
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        # Convertir a BGR para OpenCV
        rgb_array = np.array(pil_image)
        bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
        return bgr_array
    except Exception as e:
        raise ValueError(f"No se pudo decodificar la imagen {image_path}: {e}") from e


# ============================================================
# Preprocesamiento CLAHE (fallback de mejora de contraste)
# ============================================================

def apply_clahe(image_bgr: np.ndarray) -> np.ndarray:
    """
    Aplica CLAHE (Contrast Limited Adaptive Histogram Equalization)
    sobre el canal V del espacio HSV.

    El canal V representa luminosidad: ecualizar solo V mejora contraste
    sin alterar colores percibidos. CLAHE adaptativo evita saturar
    regiones globalmente, útil para fotos con iluminación irregular.
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILE_GRID_SIZE)
    v_eq = clahe.apply(v)
    hsv_eq = cv2.merge([h, s, v_eq])
    return cv2.cvtColor(hsv_eq, cv2.COLOR_HSV2BGR)


# ============================================================
# Detección de caras (una sola pasada)
# ============================================================

def _detect_faces_single_pass(
    image: np.ndarray,
    net: "cv2.dnn.Net",
    confidence_threshold: float = FACE_CONFIDENCE_THRESHOLD,
) -> list[tuple[BoundingBox, float]]:
    """
    Una sola pasada del detector de caras sobre una imagen.

    Returns:
        Lista de tuplas (bbox_cara, confianza).
    """
    h, w = image.shape[:2]

    # El modelo SSD-ResNet espera 300x300 BGR con mean subtraction
    blob = cv2.dnn.blobFromImage(
        cv2.resize(image, FACE_MODEL_INPUT_SIZE),
        1.0,
        FACE_MODEL_INPUT_SIZE,
        (104.0, 177.0, 123.0),
    )
    net.setInput(blob)
    detections = net.forward()

    raw_faces: list[tuple[int, int, int, int, float]] = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < confidence_threshold:
            continue
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            raw_faces.append((x1, y1, x2, y2, conf))

    if not raw_faces:
        return []

    # Non-Maximum Suppression para eliminar duplicados
    if len(raw_faces) > 1:
        boxes = [(f[0], f[1], f[2] - f[0], f[3] - f[1]) for f in raw_faces]
        confs = [f[4] for f in raw_faces]
        indices = cv2.dnn.NMSBoxes(boxes, confs, confidence_threshold, NMS_THRESHOLD)
        if len(indices) > 0:
            indices = indices.flatten() if hasattr(indices, "flatten") else indices
            raw_faces = [raw_faces[i] for i in indices]

    return [
        (
            BoundingBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
            conf,
        )
        for x1, y1, x2, y2, conf in raw_faces
    ]


# ============================================================
# Detección con cascada de fallbacks
# ============================================================

class DetectionResult:
    """Resultado de la detección de caras en una imagen, con metadata."""

    def __init__(
        self,
        image: np.ndarray,
        faces: list[tuple[BoundingBox, float]],
        strategy: str,
    ):
        self.image = image  # Imagen post-procesamiento (puede estar rotada/CLAHE)
        self.faces = faces
        self.strategy = strategy  # "original" | "clahe" | "rotated_90_ccw" | etc.

    @property
    def has_detections(self) -> bool:
        return len(self.faces) > 0


def detect_faces_with_fallbacks(
    image: np.ndarray,
    net: "cv2.dnn.Net | None" = None,
    confidence_threshold: float = FACE_CONFIDENCE_THRESHOLD,
    enable_clahe: bool = True,
) -> DetectionResult:
    """
    Detecta caras con estrategia en cascada.

    Orden de intentos:
    1. Imagen original (con EXIF ya aplicado al cargar)
    2. CLAHE (si enable_clahe)
    3. Rotación 90° CCW
    4. Rotación 90° CW
    5. Rotación 180°

    Devuelve el primer resultado que detecte al menos una cara, junto
    con la imagen procesada correspondiente (puede ser rotada respecto
    a la entrada) y el nombre de la estrategia que tuvo éxito.

    El llamador debe usar `result.image` para todos los procesamientos
    posteriores (recortes, etc.) porque las coordenadas de los bboxes
    están referidas a esa imagen, no a la original.
    """
    if net is None:
        net = get_face_net()

    # Intento 1: original
    faces = _detect_faces_single_pass(image, net, confidence_threshold)
    if faces:
        return DetectionResult(image, faces, "original")

    # Intento 2: CLAHE
    if enable_clahe:
        enhanced = apply_clahe(image)
        faces = _detect_faces_single_pass(enhanced, net, confidence_threshold)
        if faces:
            return DetectionResult(enhanced, faces, "clahe")

    # Intento 3: rotación 90° CCW
    rot_ccw = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    faces = _detect_faces_single_pass(rot_ccw, net, confidence_threshold)
    if faces:
        return DetectionResult(rot_ccw, faces, "rotated_90_ccw")

    # Intento 4: rotación 90° CW
    rot_cw = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    faces = _detect_faces_single_pass(rot_cw, net, confidence_threshold)
    if faces:
        return DetectionResult(rot_cw, faces, "rotated_90_cw")

    # Intento 5: rotación 180°
    rot_180 = cv2.rotate(image, cv2.ROTATE_180)
    faces = _detect_faces_single_pass(rot_180, net, confidence_threshold)
    if faces:
        return DetectionResult(rot_180, faces, "rotated_180")

    return DetectionResult(image, [], "none")


# ============================================================
# Recorte con padding (preservación de inclinación)
# ============================================================

def crop_with_padding(
    image: np.ndarray,
    bbox: BoundingBox,
    padding_px: int = BBOX_PADDING_PX,
) -> np.ndarray:
    """
    Extrae un recorte rectangular alineado al eje con padding perimetral.

    Esta función NO realiza warp, deskew, ni transformación de perspectiva.
    Si el DNI está inclinado en la imagen, el recorte resultante mantendrá
    esa inclinación, con fondo perimetral visible. Requisito de
    integridad documental para uso notarial.

    Si el padding excede los bordes de la imagen, se trunca a los límites
    válidos (no se rellena con padding artificial — eso alteraría la
    prueba).
    """
    image_h, image_w = image.shape[:2]
    x1 = max(0, bbox.x - padding_px)
    y1 = max(0, bbox.y - padding_px)
    x2 = min(image_w, bbox.x + bbox.width + padding_px)
    y2 = min(image_h, bbox.y + bbox.height + padding_px)
    return image[y1:y2, x1:x2].copy()


def save_crop(crop: np.ndarray, output_path: Path, quality: int = 95) -> None:
    """Guarda un recorte como JPEG calidad 95."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(
        ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not success:
        raise IOError(f"Falló la codificación JPEG: {output_path}")
    encoded.tofile(str(output_path))


# ============================================================
# Función de alto nivel: procesar imagen completa
# ============================================================

def extract_frentes_from_image(
    image_path: Path,
    output_dir: Path,
    net: "cv2.dnn.Net | None" = None,
) -> tuple[list[DetectedDNI], str]:
    """
    Procesa una imagen de FRENTES y extrae los DNIs detectados.

    Esta es la función principal del módulo de visión para frentes.
    La detección se hace por caras + cálculo geométrico del bbox del DNI
    (delegado al módulo `geometry`).

    A partir de v0.3.0a, devuelve DOS bboxes por cada cara detectada:
    1. Un bbox AMPLIO (con ratios DNI_EXTEND_*) que se usa como recorte
       generoso garantizando que el DNI completo quede dentro.
    2. Un bbox SUGERIDO (con ratios SUGGESTED_BBOX_*) que indica DÓNDE
       dentro del recorte amplio se estima que está el DNI real.
       Este bbox se expresa en coordenadas RELATIVAS al recorte amplio
       y se usa para pre-cargar el rectángulo de ajuste en Cropper.js.

    Args:
        image_path: Path a la imagen fuente.
        output_dir: Directorio donde guardar los recortes.
        net: Detector de caras pre-cargado (opcional, usa singleton si no).

    Returns:
        Tupla (lista_de_DNIs, estrategia_usada).
        Cada DetectedDNI tiene `bbox` = recorte amplio (en coords de la
        imagen post-rotación) y `suggested_bbox_in_crop` = sugerencia
        para Cropper.js (en coords del recorte amplio).
    """
    from app.core.geometry import (
        compute_dni_bbox_from_face,
        compute_suggested_bbox_within_crop,
    )

    logger.info(f"Procesando imagen de frentes: {image_path.name}")

    image = load_image_exif_aware(image_path)
    result = detect_faces_with_fallbacks(image, net=net)

    if not result.has_detections:
        logger.warning(f"No se detectó cara en: {image_path.name}")
        return [], result.strategy

    logger.info(
        f"Detectadas {len(result.faces)} cara(s) en {image_path.name} "
        f"(estrategia: {result.strategy})"
    )

    detected: list[DetectedDNI] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    img_h, img_w = result.image.shape[:2]

    for face_bbox, face_conf in result.faces:
        # 1. Bbox AMPLIO: el recorte generoso que se le muestra al usuario
        wide_bbox = compute_dni_bbox_from_face(face_bbox, img_w, img_h)

        # 2. Bbox SUGERIDO: dónde está el DNI dentro del recorte amplio,
        # expresado en coordenadas RELATIVAS al recorte amplio
        suggested_bbox_in_crop = compute_suggested_bbox_within_crop(
            face_bbox, wide_bbox,
        )

        # Recortar la imagen amplia con padding
        crop = crop_with_padding(result.image, wide_bbox)
        crop_id = str(uuid.uuid4())
        crop_path = output_dir / f"{crop_id}.jpg"
        save_crop(crop, crop_path)

        detected.append(
            DetectedDNI(
                crop_id=crop_id,
                source_image=image_path,
                bbox=wide_bbox,
                crop_path=crop_path,
                side=DNISide.FRENTE,
                side_confidence=float(face_conf),
                suggested_bbox_in_crop=suggested_bbox_in_crop,
            )
        )

    return detected, result.strategy
