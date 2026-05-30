# Módulos internos

Referencia de los módulos de `app/core/`. Son la lógica de dominio pura — no dependen de FastAPI ni de la capa web, y son los únicos cubiertos por tests unitarios directos.

## `app/core/vision.py`

Detección de caras, carga de imágenes, y generación de recortes.

**Dependencias:** OpenCV DNN, Pillow, NumPy, `pillow-heif` (HEIC)

### Funciones públicas

#### `load_image_exif_aware(image_path)`

Carga una imagen respetando los metadatos EXIF de rotación. Usa Pillow para leer EXIF y aplica la rotación físicamente con `ImageOps.exif_transpose()` antes de convertir a BGR para OpenCV. Soporta JPEG, PNG, WebP, HEIC.

#### `get_face_net(cache_dir=None)`

Devuelve el singleton del detector de caras (OpenCV DNN, ResNet-10 SSD). Descarga el modelo en `cache_dir` (default: `~/.cache/dni_processor/`) si no está. La descarga es lazy — ocurre una sola vez. Usa `~10 MB` de disco y ~`80 MB` de RAM.

#### `is_face_model_cached(cache_dir=None)`

Verifica si los archivos del detector están en `cache_dir` sin instanciar el modelo. Rápido y sin side-effects — usado por el health endpoint.

#### `extract_frentes_from_image(image_path, crops_dir, net=None)`

Pipeline completo de detección sobre una imagen: carga EXIF, prueba la cascada de estrategias (original → CLAHE → rotaciones), por cada cara detectada calcula el bbox del DNI, guarda el recorte amplio.

Devuelve `(lista_de_DetectedDNI, estrategia_exitosa)`.

#### `crop_with_padding(image, bbox, padding_px, output_path)`

Recorta un área de `image` con padding adicional, clipeando al borde de la imagen. Guarda el resultado como JPEG.

#### `save_crop(image, output_path)`

Guarda un array NumPy BGR como JPEG en `output_path`.

---

## `app/core/geometry.py`

Cálculo del bounding box del DNI completo a partir del bounding box de la cara detectada.

#### `compute_dni_bbox_from_face(face_bbox, image_width, image_height, ...)`

Extiende `face_bbox` en las cuatro direcciones usando los ratios de `constants.py` para cubrir el DNI completo. Los ratios son: `DNI_EXTEND_LEFT_RATIO`, `DNI_EXTEND_RIGHT_RATIO`, `DNI_EXTEND_TOP_RATIO`, `DNI_EXTEND_BOTTOM_RATIO`.

El resultado se clipea a los bordes de la imagen. Retorna un `BoundingBox`.

---

## `app/core/ocr.py`

Extracción del número de DNI por OCR.

**Dependencias:** EasyOCR (~500 MB de modelos)

#### `get_reader()`

Singleton de `easyocr.Reader`. Descarga los modelos en `~/.EasyOCR/model/` en la primera llamada. Usa `gpu=False` explícitamente.

#### `is_ocr_model_cached()`

Verifica si hay al menos dos archivos `.pth` en `~/.EasyOCR/model/` (el detector CRAFT + al menos un modelo de idioma). Sin side-effects.

#### `extract_dni_number(crop_path)`

Corre EasyOCR sobre la imagen en `crop_path`. Limpia el resultado con regex para extraer el número de DNI (7-8 dígitos, elimina separadores). Retorna `(número_str | None, confianza_float)`.

**Constante exportada:** `EASYOCR_MODEL_DIR` — path del cache de modelos de EasyOCR. Usado por `scripts/preload_models.py`.

---

## `app/core/crop_adjustments.py`

Aplicación de bbox + rotación al recorte final.

#### `apply_final_crop(wide_crop_path, bbox, rotation_degrees, output_path, padding_px=0)`

Genera el recorte final en dos pasos: primero rota la imagen completa, luego recorta el bbox sobre la imagen ya rotada. Este orden coincide con el comportamiento de Cropper.js.

`rotation_degrees` debe ser 0, 90, 180, o 270. Cualquier otro valor genera `ValueError`.

---

## `app/core/matcher.py`

Emparejamiento de frentes con dorsos por distancia Levenshtein.

#### `match_frentes_dorsos(frentes, dorsos)`

Empareja listas de `DetectedDNI`. Retorna `(pares_matched, frentes_sin_par, dorsos_sin_par)`.

El algoritmo es greedy: construye todos los candidatos ordenados por distancia y asigna en orden sin backtracking. El threshold es `MATCH_MAX_DISTANCE` (= 2). Si un frente o dorso no tiene número OCR (`dni_number is None`), queda como huérfano automáticamente.

---

## `app/core/composer.py`

Generación del PDF A4.

#### `compose_pdf(pairs, output_path, unmatched_frentes=None, unmatched_dorsos=None)`

Construye el PDF con FPDF2. Layout: 4 pares por hoja, frentes en columna izquierda, dorsos en columna derecha, a escala real ID-1 (85.6 × 53.98 mm). Los huérfanos van en hojas adicionales con la celda del par en blanco.

#### `_compute_pair_positions()`

Función interna. Calcula las coordenadas absolutas en mm de los 4 pares de una hoja, centrando el bloque en el área útil (A4 menos márgenes de 15 mm).

---

## `app/core/sessions.py`

CRUD de sesiones en disco.

#### Clase `SessionPaths`

Helper para resolver todos los paths de una sesión dado su ID. Evita que cada módulo tenga que reconstruir la lógica de layout.

| Atributo/método | Descripción |
|---|---|
| `.root` | Directorio raíz de la sesión |
| `.state_file` | Path al `session.json` |
| `.originals_dir` | Directorio de imágenes normalizadas |
| `.wide_crops_dir` | Directorio de recortes amplios |
| `.final_crops_dir` | Directorio de recortes finales |
| `.final_crop_for(crop_id)` | Path para el recorte final de un crop específico |
| `.output_pdf` | Path del PDF generado |

#### Funciones

| Función | Descripción |
|---|---|
| `create_session(base_dir=None)` | Crea directorio + `session.json` vacío. Retorna `(SessionState, SessionPaths)`. |
| `load_session(session_id, base_dir=None)` | Deserializa `session.json`. Retorna `SessionState \| None`. |
| `save_session(state, paths)` | Escribe `session.json` atómicamente (write temp + rename). |
| `discard_session(session_id, base_dir=None)` | Elimina el directorio completo. Retorna `bool`. |
| `add_image_to_session(state, image_state)` | Agrega `ImageState` al dict de imágenes del estado. |
| `add_crop_to_session(state, crop_state)` | Agrega `CropState` al dict de crops del estado. |
| `cleanup_expired_sessions(sessions_dir, ttl_hours=24)` | Elimina sesiones con `updated_at` más viejo que `ttl_hours`. Retorna el count de sesiones eliminadas. |

---

## `app/core/pipeline.py`

Orquestador del procesamiento de lotes. Capa de alto nivel que llama a `vision`, `ocr`, `matcher`, y `composer`.

!!! info "Uso"
    Este módulo es el punto de entrada del CLI (Fase 1) y sigue siendo válido para procesamiento batch. En la capa web (Fase 3+), los endpoints llaman a los módulos de `core/` directamente en lugar de pasar por el orquestador, para tener control más granular del flujo y del estado de sesión.

#### `process_frente_images(frente_image_paths, crops_dir, run_ocr=True)`

Procesa un lote de imágenes de frentes. Retorna `(detected_dnis, unprocessed_images, strategy_stats)`. `strategy_stats` es un dict que contabiliza cuántas imágenes se detectaron con cada estrategia (útil para diagnóstico).

#### `process_dorso_crops(dorso_crop_paths, run_ocr=True)`

Aplica OCR a los recortes de dorsos ya provistos por el usuario.

#### `process_batch_assisted(frente_paths, dorso_paths, work_dir, run_ocr=True)`

Pipeline completo: frentes → matcheo → retorna `ProcessingResult`. El PDF lo genera el llamador con `compose_pdf()`.

---

## `app/core/constants.py`

Todas las constantes del proyecto. Sin lógica — solo valores nombrados.

| Grupo | Constantes |
|---|---|
| Dimensiones DNI | `DNI_WIDTH_MM`, `DNI_HEIGHT_MM`, `DNI_ASPECT_RATIO` |
| Layout A4 | `A4_WIDTH_MM`, `A4_HEIGHT_MM`, `PAGE_MARGIN_MM`, `COLUMN_GAP_MM`, `ROW_GAP_MM`, `PAIRS_PER_PAGE` |
| Detección facial | `FACE_CONFIDENCE_THRESHOLD`, `NMS_THRESHOLD`, `FACE_MODEL_INPUT_SIZE`, `CLAHE_CLIP_LIMIT`, `CLAHE_TILE_GRID_SIZE` |
| Geometría | `DNI_EXTEND_LEFT_RATIO`, `DNI_EXTEND_RIGHT_RATIO`, `DNI_EXTEND_TOP_RATIO`, `DNI_EXTEND_BOTTOM_RATIO`, `BBOX_PADDING_PX` |
| Matcheo | `MATCH_MAX_DISTANCE` |
| Sesiones | `SESSION_TTL_HOURS`, `CLEANUP_INTERVAL_MINUTES` |
| Uploads | `MAX_IMAGE_SIZE_BYTES`, `MAX_SESSION_SIZE_BYTES`, `MAX_IMAGES_PER_SESSION`, `ALLOWED_IMAGE_EXTENSIONS` |
