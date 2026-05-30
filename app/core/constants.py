"""
Constantes globales del proyecto DNI Processor.

Las dimensiones físicas del DNI siguen el estándar ID-1 (ISO/IEC 7810):
85.60 mm × 53.98 mm, idéntico a tarjetas de crédito y al DNI argentino tarjeta.

NOTA (v0.2.1): se eliminaron las constantes de Canny (`CANNY_THRESHOLD_LOW/HIGH`,
`MIN_CONTOUR_AREA_RATIO`, etc.) porque el pipeline de detección por contornos
fue reemplazado por detección facial. Los parámetros del nuevo pipeline están
en la sección "DETECCIÓN FACIAL" más abajo.
"""

# ============================================================
# DIMENSIONES FÍSICAS — Estándar ID-1 (DNI argentino tarjeta)
# ============================================================

DNI_WIDTH_MM: float = 85.60
DNI_HEIGHT_MM: float = 53.98
DNI_ASPECT_RATIO: float = DNI_WIDTH_MM / DNI_HEIGHT_MM  # ≈ 1.5858

# ============================================================
# LAYOUT A4
# ============================================================

A4_WIDTH_MM: float = 210.0
A4_HEIGHT_MM: float = 297.0

PAGE_MARGIN_MM: float = 15.0
COLUMN_GAP_MM: float = 8.0
ROW_GAP_MM: float = 6.0
PAIRS_PER_PAGE: int = 4

# ============================================================
# DETECCIÓN FACIAL (OpenCV DNN — ResNet-10 SSD)
# ============================================================

# Threshold de confianza del detector. 0.3 es más permisivo que el default
# (0.5) y recupera caras chicas dentro del frame, que es el caso típico
# en fotos de DNI tomadas con celular desde distancia normal.
FACE_CONFIDENCE_THRESHOLD: float = 0.3

# Threshold para Non-Maximum Suppression — elimina detecciones duplicadas.
NMS_THRESHOLD: float = 0.3

# Modelo de caras pre-entrenado. URLs de descarga lazy en el módulo vision.
FACE_MODEL_INPUT_SIZE: tuple[int, int] = (300, 300)

# Parámetros de CLAHE para el fallback de mejora de contraste.
CLAHE_CLIP_LIMIT: float = 3.0
CLAHE_TILE_GRID_SIZE: tuple[int, int] = (8, 8)

# ============================================================
# GEOMETRÍA DEL DNI — Cálculo del bbox del DNI desde la cara
# ============================================================
#
# CONTEXTO (v0.3.0a): Tras el Sprint 1 confirmamos que el bbox devuelto
# por el detector facial es inherentemente variable (cara apretada vs
# amplia según iluminación y ángulo). Cualquier ratio fijo produce
# resultados dispares: algunos recortes sobrados, otros faltantes.
#
# DECISIÓN: en lugar de buscar ratios "perfectos", usamos ratios
# DELIBERADAMENTE AMPLIOS que garantizan incluir el DNI completo con
# margen seguro. Después, en la UI (Sprint 2b), el usuario ajusta el
# recorte fino con Cropper.js sobre este recorte amplio.
#
# Los ratios "sugeridos" más conservadores (medidos por el usuario sobre
# DNIs reales en Sprint 1) se exponen en SUGGESTED_BBOX_* y se usan
# para PRE-CARGAR el rectángulo de Cropper.js sobre la imagen amplia.

# --- RATIOS AMPLIOS (para el recorte que se le muestra al usuario) ---
# Estos valores generan un recorte con margen amplio que CASI siempre
# contiene el DNI completo, sin importar las variaciones del detector.
DNI_EXTEND_LEFT_RATIO: float = 1.50
DNI_EXTEND_RIGHT_RATIO: float = 8.00
DNI_EXTEND_TOP_RATIO: float = 2.00
DNI_EXTEND_BOTTOM_RATIO: float = 2.00

# --- RATIOS SUGERIDOS (para el rectángulo inicial de Cropper.js) ---
# Estos son los valores calibrados por el usuario en Sprint 1 sobre
# DNIs medidos. Definen DÓNDE está el DNI dentro del recorte amplio
# para pre-cargar el cropper. El usuario ajusta desde ahí.
SUGGESTED_BBOX_LEFT_RATIO: float = 0.60
SUGGESTED_BBOX_RIGHT_RATIO: float = 5.50
SUGGESTED_BBOX_TOP_RATIO: float = 1.30
SUGGESTED_BBOX_BOTTOM_RATIO: float = 1.10

# Padding perimetral adicional (en píxeles) después del cálculo geométrico.
# Garantiza que se preserve fondo perimetral incluso si los ratios son
# levemente conservadores. Requisito de integridad documental.
BBOX_PADDING_PX: int = 30

# ============================================================
# OCR (EasyOCR) — Sugerencia, no decisión
# ============================================================

OCR_LANGUAGES: list[str] = ["es"]
OCR_DNI_ALLOWLIST: str = "0123456789."
DNI_NUMBER_MIN_DIGITS: int = 7
DNI_NUMBER_MAX_DIGITS: int = 8

# ============================================================
# MATCHEO (asistido — OCR sugiere, usuario confirma)
# ============================================================

MATCH_MAX_DISTANCE: int = 2

# ============================================================
# VALIDACIÓN DE INPUT
# ============================================================

ALLOWED_IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
)

MAX_IMAGE_SIZE_BYTES: int = 15 * 1024 * 1024
MAX_SESSION_SIZE_BYTES: int = 200 * 1024 * 1024
MAX_IMAGES_PER_SESSION: int = 100

# ============================================================
# SESIONES Y STORAGE
# ============================================================

# Directorio raíz para sesiones de procesamiento. Cada sesión vive en
# un subdirectorio con UUID. Configurable vía env var DNI_SESSIONS_DIR.
DEFAULT_SESSIONS_DIR_NAME: str = "sessions"

# TTL de sesiones inactivas. Después de este tiempo, el cleanup
# automático borra el working dir completo.
SESSION_TTL_HOURS: int = 24

# Frecuencia del cleanup (background task)
CLEANUP_INTERVAL_MINUTES: int = 60
