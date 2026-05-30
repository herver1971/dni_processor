# Referencia de endpoints

Todos los endpoints REST viven bajo el prefijo `/api/v1/`. La documentación interactiva (Swagger UI) está disponible en `http://127.0.0.1:8001/docs` cuando el servicio está corriendo.

## Rate limits

Los endpoints "caros" tienen límites por IP para proteger contra macros accidentales. Con `DNI_RATE_LIMIT_ENABLED=true` (default en producción):

| Endpoint | Límite |
|---|---|
| `POST /sessions` | 30/min |
| `POST /sessions/{id}/images` | 60/min |
| `POST /sessions/{id}/process` | 10/min |
| `POST /sessions/{id}/match` | 10/min |
| `PUT /sessions/{id}/pairs` | 60/min |
| `POST /sessions/{id}/generate-pdf` | 30/min |
| `POST /sessions/{id}/reset` | 60/min |
| Confirmación, creación y descarte de crops | 60/min |
| GETs (imágenes, health, páginas web) | Sin límite |

## Health

### `GET /api/v1/health`

Verifica que el servicio está corriendo y reporta el estado del cache de modelos ML. Siempre devuelve HTTP 200 (ver [Decisiones de diseño](../arquitectura/decisiones.md#health-endpoint-devuelve-200-siempre-con-status-discriminado)).

**Respuesta:**

```json
{
  "status": "ok",
  "version": "0.4.0",
  "models": {
    "face": true,
    "ocr": true
  }
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `status` | `"ok"` \| `"degraded"` | `"degraded"` si falta algún modelo en cache |
| `version` | string | Versión del servicio |
| `models.face` | bool | `true` si el detector de caras está en `~/.cache/dni_processor/` |
| `models.ocr` | bool | `true` si los modelos de EasyOCR están en `~/.EasyOCR/model/` |

---

## Sesiones

### `POST /api/v1/sessions`

Crea una nueva sesión vacía.

**Rate limit:** 30/min

**Respuesta 201:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "created",
  "created_at": "2026-01-15T10:30:00Z"
}
```

---

### `GET /api/v1/sessions/{session_id}`

Devuelve el estado completo de la sesión: imágenes, crops, pares.

**Respuesta 200:** Ver schema `SessionStateResponse` en [Schemas](schemas.md).

**Errores:**

| Código | Condición |
|---|---|
| 404 | Sesión no encontrada o expirada (TTL 24h) |

---

### `DELETE /api/v1/sessions/{session_id}`

Descarta una sesión y elimina todos sus archivos en disco.

**Rate limit:** 60/min  
**Respuesta:** 204 No Content

---

## Imágenes

### `POST /api/v1/sessions/{session_id}/images`

Sube una o más fotos a la sesión.

**Rate limit:** 60/min  
**Content-Type:** `multipart/form-data`

**Campos:**

| Campo | Tipo | Descripción |
|---|---|---|
| `side` | `"frente"` \| `"dorso"` | Lado del DNI declarado por el usuario |
| `files` | archivo(s) | Una o más imágenes (JPEG, PNG, WebP, HEIC) |

**Validaciones:**
- Tamaño máximo por imagen: `MAX_IMAGE_SIZE_BYTES` (ver `constants.py`)
- Máximo de imágenes por sesión: `MAX_IMAGES_PER_SESSION`
- Extensiones permitidas: `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`

**Respuesta 200:**

```json
{
  "uploaded": [
    {
      "image_id": "...",
      "original_filename": "foto_01.jpg",
      "declared_side": "frente",
      "status": "uploaded"
    }
  ],
  "errors": []
}
```

---

### `GET /api/v1/sessions/{session_id}/images/{image_id}`

Sirve la imagen normalizada (post-EXIF) como archivo. Usado por el frontend para mostrar las imágenes en los editores Cropper.js.

**Respuesta:** `FileResponse` (JPEG)

---

### `GET /api/v1/sessions/{session_id}/crops/{crop_id}/wide`

Sirve el recorte amplio (pre-ajuste del usuario).

**Respuesta:** `FileResponse` (JPEG)

---

### `GET /api/v1/sessions/{session_id}/crops/{crop_id}/final`

Sirve el recorte final confirmado.

**Respuesta:** `FileResponse` (JPEG)

---

## Procesamiento

### `POST /api/v1/sessions/{session_id}/process`

Dispara la detección automática de frentes sobre todas las imágenes de frentes pendientes de la sesión.

**Rate limit:** 10/min

!!! note "Endpoint síncrono"
    Este endpoint bloquea hasta que termina. Para sesiones típicas (2-15 DNIs) eso son ~5-10 segundos. No hay polling — el frontend espera la respuesta.

**Respuesta 200:**

```json
{
  "processed_images": 3,
  "detected_crops": 5,
  "failed_images": 0,
  "session_status": "review"
}
```

---

### `POST /api/v1/sessions/{session_id}/crops/{crop_id}/confirm`

Confirma un crop con el bbox final ajustado por el usuario.

**Rate limit:** 60/min

**Body:**

```json
{
  "final_bbox": {"x": 10, "y": 15, "width": 320, "height": 200},
  "rotation_degrees": 0
}
```

`rotation_degrees` debe ser 0, 90, 180, o 270.

**Respuesta 200:**

```json
{
  "crop_id": "...",
  "status": "confirmed",
  "final_crop_path": "crops/final/<crop_id>.jpg"
}
```

---

### `POST /api/v1/sessions/{session_id}/images/{image_id}/crops`

Crea un recorte manual. Usado para dorsos y para frentes con detección fallida.

**Rate limit:** 60/min

**Body:**

```json
{
  "bbox": {"x": 50, "y": 80, "width": 400, "height": 250},
  "side": "dorso",
  "rotation_degrees": 0
}
```

**Respuesta 201:**

```json
{
  "crop_id": "...",
  "status": "confirmed"
}
```

---

### `DELETE /api/v1/sessions/{session_id}/crops/{crop_id}`

Descarta un crop (el usuario lo rechazó).

**Rate limit:** 60/min  
**Respuesta:** 204 No Content

---

## Matcheo y PDF

### `POST /api/v1/sessions/{session_id}/match`

Corre OCR sobre los recortes confirmados y genera sugerencias de pares frente↔dorso.

**Rate limit:** 10/min

**Respuesta 200:**

```json
{
  "pairs": [
    {
      "pair_id": "...",
      "frente_crop_id": "...",
      "dorso_crop_id": "...",
      "position": 0,
      "origin": "ocr_match",
      "match_distance": 1
    }
  ],
  "unmatched_frentes": [],
  "unmatched_dorsos": []
}
```

`origin` puede ser `"ocr_match"` (emparejado automáticamente) o `"manual"` (asignado por el usuario).

---

### `PUT /api/v1/sessions/{session_id}/pairs`

**API declarativa:** reemplaza el conjunto completo de pares con la lista enviada. El cliente manda el estado deseado de todos los pares, no incrementos.

**Rate limit:** 60/min

**Body:**

```json
{
  "pairs": [
    {
      "frente_crop_id": "...",
      "dorso_crop_id": "...",
      "position": 0
    }
  ]
}
```

Validaciones: cada `crop_id` debe existir y estar confirmado, sides correctos, sin duplicados, positions únicas.

---

### `POST /api/v1/sessions/{session_id}/generate-pdf`

Genera el PDF A4 con los pares confirmados.

**Rate limit:** 30/min

**Respuesta 200:**

```json
{
  "pdf_path": "output.pdf",
  "total_pairs": 4,
  "total_pages": 1
}
```

---

### `GET /api/v1/sessions/{session_id}/output.pdf`

Descarga el PDF generado.

**Respuesta:** `FileResponse` (PDF)

**Errores:**

| Código | Condición |
|---|---|
| 404 | PDF no generado aún o sesión no encontrada |

---

### `POST /api/v1/sessions/{session_id}/reset`

Descarta la sesión y devuelve la URL de redirección. Equivalente a `DELETE /sessions/{id}` pero diseñado para la acción "Empezar otro trámite" de la UI.

**Rate limit:** 60/min

**Respuesta 200:**

```json
{
  "discarded_session_id": "...",
  "redirect_to": "/"
}
```
