# Pipeline de procesamiento

Descripción técnica de cómo el sistema procesa una imagen desde la foto cruda hasta el recorte final.

## Etapa 1 — Normalización EXIF

**Módulo:** `app/core/vision.py` → `load_image_exif_aware()`

Las fotos tomadas con celular llevan metadatos EXIF que indican la orientación de la cámara al momento de la toma. OpenCV ignora esos metadatos y carga la imagen en la orientación raw del sensor, lo que resulta en imágenes "acostadas" cuando el usuario las ve "paradas".

El pipeline lee EXIF con Pillow (`ImageOps.exif_transpose()`) y aplica la rotación físicamente al array de píxeles antes de procesarlos. Esto asegura que la imagen que entra al detector de caras siempre tenga la orientación visual correcta.

Formatos soportados: JPEG, PNG, WebP, HEIC (fotos de iPhone, vía `pillow-heif`).

## Etapa 2 — Detección de caras (frentes)

**Módulo:** `app/core/vision.py` → `extract_frentes_from_image()`

El sistema usa OpenCV DNN con el modelo ResNet-10 SSD pre-entrenado para detectar caras. El modelo se descarga en `~/.cache/dni_processor/` al primer uso y queda cacheado.

### Threshold permisivo

El confidence threshold es **0.3** (más permisivo que el default de 0.5). El razonamiento: las fotos de DNI tomadas con celular en condiciones reales tienen caras pequeñas relativas al frame, a veces con algo de reflejo o sombra. Un threshold conservador rechaza demasiadas detecciones válidas. La UI de revisión sirve como red de seguridad — si el detector da un falso positivo, el usuario lo descarta.

### Cascada de fallbacks

Si la detección sobre la imagen original no encuentra ninguna cara, el sistema prueba cuatro estrategias en secuencia:

| Estrategia | Descripción |
|---|---|
| `original` | Imagen normalizada EXIF, sin modificación |
| `clahe` | CLAHE aplicado (mejora contraste local, recupera caras en imágenes oscuras) |
| `rotated_90_ccw` | Rotada 90° en sentido anti-horario |
| `rotated_90_cw` | Rotada 90° en sentido horario |
| `rotated_180` | Rotada 180° |

Los fallbacks de rotación rescatan fotos donde el EXIF estaba ausente o incorrecto. La estrategia que tuvo éxito se registra en el log para diagnóstico.

Validado en set real: **100% de detección sobre 18 imágenes** (14 con imagen original, 4 con fallback de rotación).

## Etapa 3 — Cálculo del bbox del DNI

**Módulo:** `app/core/geometry.py` → `compute_dni_bbox_from_face()`

El detector devuelve el bounding box de la **cara**. El sistema necesita el bounding box del **DNI completo**. La conversión usa la geometría conocida del DNI argentino tarjeta moderno:

- La foto del titular está siempre en la columna izquierda, aproximadamente centrada verticalmente.
- Conociendo esa posición relativa, podemos extender el bbox de la cara en las cuatro direcciones con ratios fijos para cubrir el DNI completo.

Los ratios se calibraron empíricamente sobre imágenes reales y viven en `app/core/constants.py` como `DNI_EXTEND_*_RATIO`. Si los recortes sistemáticamente quedan cortos o sobrados en alguna dirección, ajustar esas constantes es suficiente.

**Ventajas del enfoque geométrico** sobre intentar detectar el borde del DNI directamente: cero dependencias adicionales, determinístico, sin otra red neuronal, funciona en CPU.

**Limitaciones:** Asume DNI argentino tarjeta moderno con foto en posición estándar y orientación aproximadamente correcta (que se garantiza con la etapa 1 + fallbacks de rotación).

## Etapa 4 — Recorte amplio

**Módulo:** `app/core/vision.py` → `crop_with_padding()`

El sistema guarda un recorte "amplio" — el bbox del DNI más un padding generoso. Ese recorte amplio es lo que se muestra en el editor Cropper.js. El usuario ajusta el rectángulo interno dentro del recorte amplio, no sobre la imagen completa. Ventajas:

- La imagen en el editor es mucho más pequeña (menos trabajo para el browser).
- El usuario tiene contexto visual del contorno del DNI sin ver toda la foto.

## Etapa 5 — Ajuste del usuario y recorte final

**Módulo:** `app/core/crop_adjustments.py` → `apply_final_crop()`

Cuando el usuario confirma un recorte, el backend aplica en este orden:

1. **Rotación** (0°, 90°, 180°, o 270°) sobre el recorte amplio.
2. **Recorte** con el bbox que Cropper.js devolvió (coordenadas sobre la imagen ya rotada).

El orden importa: Cropper.js opera sobre la imagen visualmente rotada, así que las coordenadas del bbox corresponden al espacio de la imagen rotada, no de la original. Invertir el orden produce un recorte desalineado.

Solo se permiten rotaciones múltiplo de 90°. No se admiten rotaciones arbitrarias porque requieren interpolación bilineal o bicúbica, que altera los píxeles — incompatible con el principio de integridad documental.

## Etapa 6 — OCR del número de DNI

**Módulo:** `app/core/ocr.py` → `extract_dni_number()`

Se ejecuta sobre el recorte final. Usa EasyOCR con idioma español (`es`). El texto extraído se limpia con regex para quedarse solo con los dígitos que tengan el formato del número de DNI argentino (7-8 dígitos, puede tener puntos o espacios como separadores).

El número extraído se usa **exclusivamente** para el matcheo. No se almacena de forma persistente, no aparece en el PDF, no se loguea a nivel INFO.

## Etapa 7 — Matcheo frente↔dorso

**Módulo:** `app/core/matcher.py` → `match_frentes_dorsos()`

El algoritmo de matcheo por distancia Levenshtein:

1. Construir todos los pares candidatos (frente × dorso) con sus distancias.
2. Ordenar por distancia ascendente.
3. Asignar pares en orden, marcando frentes y dorsos como "usados".
4. Si la distancia supera `MATCH_MAX_DISTANCE` (= 2 caracteres), no se empareja.
5. Los no emparejados quedan como huérfanos.

El threshold de 2 tolera un OCR levemente impreciso (un dígito mal leído o un punto extra) sin generar falsos positivos.

## Etapa 8 — Generación del PDF

**Módulo:** `app/core/composer.py` → `compose_pdf()`

Layout A4 con FPDF2:

- **4 pares por hoja** (configurable en `PAIRS_PER_PAGE`)
- **Frente** en columna izquierda, **dorso** en columna derecha
- Dimensiones exactas: 85.6 × 53.98 mm por DNI (estándar ID-1)
- El bloque de 4 pares se centra en el área útil (A4 menos márgenes de 15 mm)
- Huérfanos al final, una imagen por celda, con la celda del par en blanco

No se agrega texto, numeración, ni ningún elemento que no estuviera en las fotos originales.
