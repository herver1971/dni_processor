# Decisiones de diseño

Registro de las decisiones arquitectónicas que dieron forma al sistema. Cada entrada documenta el contexto, las opciones evaluadas, y la razón de la elección.

## Sin base de datos

**Decisión:** El estado de las sesiones vive en JSON en disco, no en SQLite ni ninguna otra DB.

**Razones:**
- Las sesiones son efímeras (TTL 24h) — no hay valor en la persistencia a largo plazo.
- Simplifica el deployment: sin migraciones, sin Alembic, sin schema que mantener.
- Facilita el backup: un `rsync` del directorio `data/` es suficiente.
- Evita acoplamiento entre el servicio y una instancia de DB que habría que gestionar.

**Trade-offs aceptados:** Sin DB no hay transacciones atómicas multi-entidad. La escritura atómica del `session.json` (write + rename) provee garantías suficientes para el caso de uso single-user.

---

## Integridad documental sobre comodidad

**Decisión:** No se aplica ninguna transformación de perspectiva ni deskew. Solo recortes rectangulares axis-aligned y rotaciones múltiplo de 90°.

**Razones:**
- En contexto notarial, el PDF debe ser evidencia de que la imagen no fue manipulada.
- Un DNI inclinado en la foto original aparece inclinado en el PDF, con fondo visible — eso certifica que no fue "enderezado" digitalmente.
- `warpPerspective` y similares alteran los píxeles de forma no reversible.

**Consecuencia para el usuario:** Si el DNI está muy inclinado, el recorte tiene más fondo alrededor. La recomendación es fotografiar con la cámara perpendicular al plano del DNI, pero no es un requisito técnico.

---

## OCR como sugerencia, matcheo conservador

**Decisión:** El número de DNI extraído por OCR se usa solo para pre-sugerir pares. El threshold de Levenshtein es 2 (permisivo con un carácter mal leído). Cuando hay duda, se genera huérfano explícito.

**Razones:**
- Un par incorrecto (frente de un titular con dorso de otro) es mucho peor que un huérfano que el usuario resuelve manualmente.
- El OCR sobre fotos de celular tiene error rate no despreciable en caracteres individuales.
- El usuario siempre puede corregir el matcheo arrastrando en la UI.

**Alternativa descartada:** Confiar en el orden de subida para emparejar. Se descartó porque el usuario puede subir en orden diferente al físico, y no hay forma de detectar esa situación automáticamente.

---

## Detección por caras, no por bordes

**Decisión:** El sistema detecta frentes buscando la cara del titular (ResNet-10 SSD), no intentando detectar el rectángulo del DNI por sus bordes.

**Razones:**
- La detección de bordes del DNI con Canny + contornos fue descartada en v0.2.0 con 23% de tasa de detección en el set real — insuficiente para uso en producción.
- Las caras son mucho más robustas de detectar que un rectángulo con reflejo, fondo variable, o ángulo no perfecto.
- Modelos pre-entrenados de detección facial tienen excelente performance en CPU.

**Limitación:** Sólo detecta frentes. Los dorsos siempre se recortan manualmente — `pyzbar` para barcode tuvo 5.6% de tasa en pruebas reales, igualmente descartado.

---

## Stack frontend minimal (HTMX, sin framework)

**Decisión:** UI con HTMX + JavaScript vanilla, sin React/Vue/Svelte.

**Razones:**
- El servicio es single-user y no tiene flujos complejos de estado en el cliente.
- HTMX permite interactividad tipo SPA (recarga parcial de HTML) con muy poco JS.
- El proyecto ya tiene Python/FastAPI como dependencia central — no agregar un toolchain de Node.js para algo que no lo necesita.
- Cropper.js y SortableJS cubren los dos casos donde JS real es necesario (editor de recortes y drag-and-drop).

---

## Singleton del Limiter separado de `main.py`

**Decisión:** El `Limiter` de slowapi vive en `app/rate_limiter.py`, no en `app/main.py`.

**Razones:**
- Los routers necesitan importar el limiter para decorar sus endpoints.
- Si el limiter estuviera en `main.py`, los routers importarían `main.py`, y `main.py` importa los routers → ciclo de imports.
- El módulo separado corta el ciclo: routers importan `rate_limiter.py`, `main.py` importa ambos.

---

## `data-debug` en lugar de `window.DNI_DEBUG`

**Decisión:** La flag de debug del frontend se lee de `document.documentElement.dataset.debug`, no de una variable global inyectada con `<script>`.

**Razones:**
- La CSP de Sprint 4a no permite `'unsafe-inline'` en `script-src`.
- Un `<script>window.DNI_DEBUG = true;</script>` inline violaría esa política.
- El atributo `data-debug` en el `<html>` es CSS/HTML puro, no requiere ninguna directiva CSP adicional.

---

## Preload de modelos fuera del startup del servidor

**Decisión:** La pre-descarga de modelos ML se hace con `scripts/preload_models.py`, no como hook del `lifespan` de FastAPI.

**Razones:**
- EasyOCR tarda 30-60 segundos en descargar e inicializar sus modelos.
- Un hook de startup que tardara 60s haría fallar el health check de systemd (timeout por defecto 30s) y dejaría el servicio en estado `failed` al primer boot.
- El preload es idempotente: si los archivos ya están, termina en milisegundos. Se puede ejecutar en cada redeploy sin costo.
- En runtime los modelos se cargan lazy en la primera request que los necesita (~5s con archivos en disco), lo cual es aceptable.

---

## Health endpoint devuelve 200 siempre, con `status` discriminado

**Decisión:** `/api/v1/health` devuelve HTTP 200 aunque los modelos no estén en cache (`status: "degraded"`), en lugar de 503.

**Razones:**
- `"degraded"` significa que el servicio puede recibir y servir requests (la web UI funciona), pero el procesamiento ML puede tardar más en la primera invocación.
- Un 503 haría que Tailscale y monitoreo externo marquen el servicio como down, cuando está reachable y funcionando parcialmente.
- La distinción semántica `"ok"` / `"degraded"` en el body es suficiente para el monitoreo programático.
