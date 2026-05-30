# Flujo completo de un trámite

Descripción paso a paso de lo que sucede en la UI, con el detalle técnico de lo que ocurre en el backend en cada momento.

## Pantalla 1 — Upload

**URL:** `/`

Subís dos lotes de fotos: frentes y dorsos por separado. Pueden ser varias fotos por lote, y cada foto puede contener más de un DNI.

**Qué hace el backend:**

1. Crea una sesión nueva (`POST /api/v1/sessions`) con un UUID único.
2. Por cada foto que subís (`POST /api/v1/sessions/{id}/images`):
   - La recibe, verifica extensión y tamaño (máximo 15 MB por foto).
   - Aplica rotación EXIF para que quede "parada" independientemente de cómo la tomó la cámara.
   - La guarda en `sessions/<id>/originals/` como JPEG de buena calidad.
3. Cuando terminás de subir, disparás el procesamiento (`POST /api/v1/sessions/{id}/process`):
   - Para cada foto de frentes, el detector facial (ResNet-10 SSD) busca caras.
   - Por cada cara encontrada, calcula el bounding box del DNI completo usando la geometría conocida del DNI argentino tarjeta.
   - Guarda un "recorte amplio" en `sessions/<id>/crops/wide/` con margen generoso.
   - Si no encuentra ninguna cara, la imagen queda marcada como `FAILED_DETECTION` para recorte manual.

!!! info "¿Por qué frentes y dorsos por separado?"
    Los dorsos no tienen cara — el detector facial no puede ubicarlos automáticamente. Por eso los dorsos siempre se recortan de forma manual en la pantalla siguiente.

## Pantalla 2 — Revisión de recortes

**URL:** `/sessions/{id}/review`

Para cada imagen procesada, ves el resultado de la detección. Podés ajustar el recorte, rotarlo, o descartarlo.

**Frentes detectados automáticamente:** Aparecen con el recorte amplio en un editor Cropper.js. El rectángulo de ajuste ya está pre-posicionado sobre el DNI. Si el encuadre no es perfecto, lo arrastras. Cuando confirmás (`POST /api/v1/sessions/{id}/crops/{crop_id}/confirm`), el backend aplica el bbox final y guarda el recorte definitivo en `sessions/<id>/crops/final/`.

**Frentes con detección fallida y todos los dorsos:** Aparecen como imagen completa en Cropper.js. Dibujás el rectángulo manualmente encima del DNI y confirmás (`POST /api/v1/sessions/{id}/images/{image_id}/crops`).

**Rotación:** En incrementos de 90°. No se permiten ángulos arbitrarios — eso requeriría interpolación y alteraría la imagen.

!!! warning "Integridad documental"
    Si el DNI está inclinado en la foto, el recorte quedará inclinado. Esto es intencional: certifica que la imagen no fue manipulada digitalmente.

Cuando todos los recortes están confirmados, aparece el botón "Continuar al matcheo".

## Pantalla 3 — Matcheo

**URL:** `/sessions/{id}/match`

Emparejás cada frente con su dorso correspondiente.

**Qué hace el backend al entrar:**

1. Corre OCR sobre los números de DNI de los recortes (`POST /api/v1/sessions/{id}/match`).
2. Usa distancia Levenshtein para sugerir los pares más probables (threshold ≤ 2 caracteres de diferencia).
3. Resuelve conflictos: si dos frentes apuntan al mismo dorso, gana el de menor distancia.

**En la UI:** Ves dos columnas — frentes a la izquierda, dorsos a la derecha. Los pares sugeridos ya están conectados. Podés reordenarlos arrastrando para cambiar el orden en el PDF final. Si hay huérfanos (sin par), aparecen al final.

!!! note "Matcheo conservador"
    Si el OCR no puede leer el número con suficiente confianza, el DNI queda como huérfano para que lo ubiques manualmente. En contexto notarial, un match incorrecto es peor que tener que resolverlo a mano.

Cuando estás conforme con los pares, generás el PDF (`POST /api/v1/sessions/{id}/generate-pdf`).

## Pantalla 4 — PDF generado

**URL:** `/sessions/{id}/completed`

El PDF está listo. Podés descargarlo. El layout es:

- Hoja A4 vertical
- 4 pares por hoja
- Frente en columna izquierda, dorso en columna derecha
- Cada DNI a escala real ID-1 (85.6 × 53.98 mm)
- Sin texto agregado, sin numeración, sin marcas de agua

Si hay más de 4 pares, el PDF tendrá varias hojas. Los huérfanos van al final, una imagen por celda.

Con el botón "Empezar otro trámite" la sesión se descarta (`POST /api/v1/sessions/{id}/reset`) y volvés a la pantalla de upload.

## Ciclo de vida de una sesión

```
CREATED → UPLOADING → PROCESSING → REVIEW → READY_FOR_MATCH → MATCHING → COMPLETED
                                                                         ↓
                                                                    (reset → CREATED)
```

Las sesiones tienen un TTL de 24 horas. Un proceso de cleanup en background las elimina automáticamente junto con todos sus archivos.
