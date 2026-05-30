# Schemas Pydantic

Los schemas están organizados en tres módulos con responsabilidades distintas.

## `app/schemas/session.py` — Entidades de dominio

Modelos que representan las entidades que fluyen por el pipeline de procesamiento. No tienen dependencias de FastAPI ni de la capa web.

### `DNISide`

```python
class DNISide(str, Enum):
    FRENTE = "frente"
    DORSO  = "dorso"
    UNKNOWN = "unknown"
```

### `BoundingBox`

Bounding box rectangular en coordenadas de píxel. Es inmutable (`frozen=True`).

| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| `x` | `int` | ≥ 0 | Coordenada x del vértice superior izquierdo |
| `y` | `int` | ≥ 0 | Coordenada y del vértice superior izquierdo |
| `width` | `int` | > 0 | Ancho en píxeles |
| `height` | `int` | > 0 | Alto en píxeles |

Propiedades computadas: `area`, `aspect_ratio` (siempre ancho/alto en orientación horizontal, normalizado).

### `DetectedDNI`

Un DNI detectado y recortado de una imagen fuente. Resultado de la fase de visión por computadora.

| Campo | Tipo | Descripción |
|---|---|---|
| `crop_id` | `str` | UUID del recorte |
| `source_image` | `Path` | Imagen fuente |
| `bbox` | `BoundingBox` | Bounding box en la imagen fuente |
| `crop_path` | `Path` | Path al archivo del recorte guardado |
| `side` | `DNISide` | Frente / Dorso / Desconocido |
| `side_confidence` | `float` | Confianza de la clasificación (0-1) |
| `dni_number` | `str \| None` | Número extraído por OCR (solo dígitos) |
| `ocr_confidence` | `float` | Confianza del OCR (0-1) |

### `MatchedPair`

Par frente+dorso emparejado para el PDF.

| Campo | Tipo | Descripción |
|---|---|---|
| `frente` | `DetectedDNI` | Frente del par |
| `dorso` | `DetectedDNI` | Dorso del par |
| `match_distance` | `int` | Distancia Levenshtein entre números de DNI |
| `position` | `int` | Orden en el PDF (0-indexed) |

### `UnpairedDNI`

DNI sin par asignado (huérfano). Aparece al final del PDF con la celda del par vacía.

### `ProcessingResult`

Resultado completo del pipeline de procesamiento de un lote.

| Campo | Tipo | Descripción |
|---|---|---|
| `matched_pairs` | `list[MatchedPair]` | Pares emparejados |
| `unmatched_frentes` | `list[UnpairedDNI]` | Frentes sin dorso |
| `unmatched_dorsos` | `list[UnpairedDNI]` | Dorsos sin frente |
| `unprocessed_images` | `list[UnprocessedImage]` | Imágenes donde falló la detección |

---

## `app/schemas/web.py` — Estado de sesión web

Extienden los schemas de dominio con estado de la UI: qué está pendiente de revisión, qué está confirmado, etc.

### `SessionStatus`

```
CREATED → UPLOADING → PROCESSING → REVIEW → READY_FOR_MATCH → MATCHING → COMPLETED
                                                                         ↕
                                                                       FAILED
```

### `CropStatus`

| Valor | Descripción |
|---|---|
| `"pending"` | Pendiente de revisión por el usuario |
| `"confirmed"` | Confirmado (con o sin ajustes del bbox) |
| `"discarded"` | Descartado por el usuario |

### `ImageStatus`

| Valor | Descripción |
|---|---|
| `"uploaded"` | Recibida, no procesada aún |
| `"processing"` | En proceso de detección |
| `"detected"` | Detección automática exitosa |
| `"failed_detection"` | Falló — requiere recorte manual |
| `"error"` | Error en carga o procesamiento |

### `CropState`

Estado de un recorte individual dentro de la sesión.

| Campo | Tipo | Descripción |
|---|---|---|
| `crop_id` | `str` | UUID del recorte |
| `source_image_id` | `str` | ID de la imagen fuente en la sesión |
| `side` | `DNISide` | Frente o dorso |
| `status` | `CropStatus` | Estado actual |
| `wide_crop_path` | `str` | Path al recorte amplio (relativo al working dir) |
| `suggested_bbox` | `BoundingBox \| None` | Bbox sugerido por el detector (None si fue manual) |
| `final_bbox` | `BoundingBox \| None` | Bbox confirmado por el usuario |
| `rotation_degrees` | `int` | Rotación aplicada (0, 90, 180, 270) |
| `final_crop_path` | `str \| None` | Path al recorte final (una vez confirmado) |
| `dni_number` | `str \| None` | Número OCR para matcheo |

### `SessionState`

Estado completo de una sesión. Serializado a `session.json`.

| Campo | Tipo | Descripción |
|---|---|---|
| `session_id` | `str` | UUID de la sesión |
| `status` | `SessionStatus` | Estado del ciclo de vida |
| `created_at` | `datetime` | Timestamp de creación (timezone-aware, UTC) |
| `updated_at` | `datetime` | Timestamp de última modificación |
| `images` | `dict[str, ImageState]` | Imágenes indexadas por `image_id` |
| `crops` | `dict[str, CropState]` | Recortes indexados por `crop_id` |
| `pairs` | `dict[str, PairState]` | Pares indexados por `pair_id` |

---

## `app/schemas/api.py` — Contrato REST

Schemas de request/response para la API. Son la única interfaz pública documentada — los schemas de dominio y web son internos.

Los schemas de respuesta (`*Response`) extienden o proyectan los schemas de estado web en un formato apropiado para el cliente JSON. Los schemas de request (`*Request`) validan el body de los POSTs.

Ver los ejemplos de respuesta en [Referencia de endpoints](api.md) para la forma concreta de cada objeto.
