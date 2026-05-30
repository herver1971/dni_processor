# Changelog — DNI Processor

Todas las modificaciones notables del proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
el proyecto adhiere a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.4.0] — 2026-05-30

**Sprint 4b — Deployment a producción.**

Segunda y última sub-entrega de Sprint 4. Con esto el servicio queda
listo para correr 24/7 en el servidor Kubuntu detrás de Tailscale,
junto con Escriba.

**168 tests pasando** (163 anteriores + 5 nuevos del health endpoint
enriquecido). Sin warnings nuevos.

El bump a `0.4.0` (minor, no patch) refleja el cambio funcional:
v0.3.x era "funcional, corrible localmente"; v0.4.0 es "corriendo como
servicio gestionado". El bump a `1.0.0` queda reservado para cuando
cierre Fase 5 (integración con Escriba).

### Motivación

Hasta v0.3.2 la app levantaba con `python -m app.main` pero requería
una shell abierta, dependía de que el usuario tuviera todo configurado
en `~/.cache`, y el primer request real esperaba 30-60s mientras
EasyOCR descargaba modelos. Sprint 4b cubre lo que falta para que el
servicio se comporte como un servicio: arranca al boot, sobrevive a
crashes, logs en journald, modelos pre-descargados, deployment
documentado paso a paso.

### Decisiones técnicas relevantes

#### Deployment como `hernan` en `/home/hernan/dni_processor`

Discutimos dos opciones: (A) `/opt/dni_processor` + usuario dedicado
`dni_processor`, o (B) `/home/hernan/...` corriendo como `hernan`.
La (A) es más higiénica desde el lado de privilegios (separación si
algún día hay una vulnerabilidad en una dep) y es la convención
"clásica" para servicios self-hosted. La (B) facilita backups con
rsync e iteración con `git pull` sin sudo.

Decisión: **(B) por ahora**. Single-user, LAN, backup simple. Si en
algún momento el servicio se expone más allá del tailnet o lo toca
otra persona, migrar a (A) es media hora de trabajo (mover archivos,
ajustar `User=`/`Group=`/`ReadWritePaths=` del unit). Está documentado
en el README_DEPLOY para no perder el hilo.

#### Health endpoint enriquecido — `200` con `status` discriminado

Hasta v0.3.2 el endpoint devolvía `{"status": "ok", "version": ...}`
literal. Ahora reporta presencia de modelos en cache sin instanciarlos:

```json
{
  "status": "ok",
  "version": "0.4.0",
  "models": { "face": true, "ocr": true }
}
```

Cuando falta al menos un modelo, `status` pasa a `"degraded"` pero el
código sigue siendo **200, no 503**. Razonamiento: el servicio es
capaz de aceptar requests (los uploads y la web UI funcionan); lo que
no está es el procesamiento. Devolver 503 haría que Tailscale o
monitoreo externo marquen el servicio como down, cuando en realidad
está reachable. La distinción entre `"ok"` y `"degraded"` es lo que
discrimina si hay que correr el preload script.

Notable: el endpoint **NO instancia los modelos**, sólo chequea
archivos en disco (`Path.exists()` sobre el detector de caras,
`glob("*.pth")` sobre el dir de EasyOCR). Esto se testea explícitamente
con `mock.patch` que falla si alguien accidentalmente llama a
`get_face_net()` o `get_reader()` desde el health.

#### Pre-descarga de modelos: script de deploy, no hook de startup

Discutimos correr el preload como hook del `lifespan` de FastAPI al
arrancar el servicio. Lo descarté: cargar EasyOCR demora 30-60s y
rompe el ready de systemd y de los health checks de Tailscale.
Mejor: deploy-time pre-descarga vía `scripts/preload_models.py`,
runtime carga lazy en la primera request (que ahora es ~5-10s con
archivos en disco, no 30-60s).

El script es **idempotente**: si los archivos ya están en cache, sale
en milisegundos sin descargar nada. Eso lo hace seguro de correr en
cada redeploy. Devuelve exit code `1` si algo falló, lo cual permite
encadenarlo con `&&` en scripts de deploy.

Limitación documentada: EasyOCR maneja su propio cache en
`~/.EasyOCR/model/`, no configurable desde nuestros Settings. Lo
único que podemos hacer es disparar `easyocr.Reader(['es'], ...)` y
dejar que la lib se baje los archivos donde quiera. El detector de
caras sí respeta `Settings.model_cache_dir`.

#### Hardening del unit systemd — directivas baratas, alto valor

El unit incluye un bloque de hardening del proceso (NoNewPrivileges,
ProtectSystem=strict, ProtectHome=read-only, PrivateTmp,
PrivateDevices, RestrictAddressFamilies, etc.). Cero costo de runtime,
y si un día hay un RCE en alguna dep (Pillow, OpenCV, FastAPI...),
el blast radius queda contenido al data dir del proyecto y los caches
de modelos.

`ReadWritePaths=` declara explícitamente sólo `/home/hernan/dni_processor/data`,
`~/.cache/dni_processor` y `~/.EasyOCR`. Cualquier intento del proceso
de escribir en otro lado falla con `EROFS`.

#### `.env` leído por pydantic-settings, no `EnvironmentFile=` del unit

El unit NO declara `EnvironmentFile=/path/to/.env` porque
pydantic-settings ya lee `.env` del WorkingDirectory automáticamente.
Hacerlo dos veces sería redundante y crea dos lugares para tener
discrepancias. La única env var que setea el unit es
`PYTHONUNBUFFERED=1` para que journald reciba los logs en tiempo real.

#### Restart con `StartLimitBurst`

`Restart=on-failure` solo no es suficiente: si el servicio falla por
una razón persistente (config rota, .env malformado), systemd lo
restartearía en loop indefinido. Agregamos `StartLimitBurst=5` y
`StartLimitIntervalSec=300`: si crashea 5 veces en 5 minutos, systemd
se rinde y deja el servicio en estado `failed` — y eso te avisa por
`systemctl status` que hay algo serio que arreglar.

### Added

#### `app/main.py` — health endpoint enriquecido

`/api/v1/health` ahora devuelve `{status, version, models: {face, ocr}}`.
`status` es `"ok"` cuando ambos modelos están cacheados, `"degraded"`
cuando falta al menos uno. Siempre 200.

#### `app/core/vision.py::is_face_model_cached(cache_dir)`

Devuelve `bool`: chequea presencia de `deploy.prototxt` y
`res10_300x300_ssd_iter_140000.caffemodel` en `cache_dir` (default:
`Settings.model_cache_dir`). No instancia ni descarga.

#### `app/core/ocr.py::is_ocr_model_cached()`

Devuelve `bool`: chequea que `~/.EasyOCR/model/` existe y tiene al menos
dos archivos `.pth` (detector CRAFT + al menos un modelo de idioma).
Robusto a cambios de naming entre versiones de EasyOCR; puede dar
false-negative en cambios drásticos del layout, nunca false-positive.

También se exporta la constante `EASYOCR_MODEL_DIR` para que el
preload script la use sin duplicar.

#### `scripts/preload_models.py` (nuevo)

Script standalone que pre-descarga ambos modelos. Idempotente. Se
corre una vez durante el deploy:

```bash
python scripts/preload_models.py
```

Reporta progreso por logging, devuelve exit `0` si todo OK, `1` si
algo falló.

#### `.env.example` (nuevo en raíz)

Plantilla con todas las env vars documentadas y sus defaults. Se
copia a `.env` en producción.

#### `deployment/dni_processor.service` (nuevo)

systemd unit. Corre como `hernan`, WorkingDirectory en
`/home/hernan/dni_processor`, `ExecStart` apunta al python del venv.
Incluye hardening del proceso (NoNewPrivileges, ProtectSystem,
PrivateTmp, etc.). Restart con `StartLimitBurst=5`/`StartLimitIntervalSec=300`.

#### `deployment/README_DEPLOY.md` (nuevo)

Guía paso a paso de instalación en Kubuntu. Cubre prerequisitos,
clone, venv, .env, preload, smoke test manual, instalación del unit,
acceso vía Tailscale, operación diaria, actualización a versiones
nuevas, backup con rsync, y troubleshooting.

#### Tests del health enriquecido (`tests/integration/test_security.py`)

5 tests nuevos en `TestEnrichedHealth`:
- Estructura de la respuesta (`status`, `version`, `models.face`, `models.ocr`).
- `status="ok"` cuando ambos modelos están cacheados (con mocks).
- `status="degraded"` cuando falta face.
- `status="degraded"` cuando falta ocr.
- El endpoint **no instancia los modelos** (verificado con `mock.patch`
  sobre `get_face_net` y `get_reader` — si alguien accidentalmente los
  llama desde el health, el test falla).

### Changed

- **`app/main.py`**:
  - Bump `__version__` a `"0.4.0"`.
  - Health endpoint expandido (ver arriba).
  - Imports de `is_face_model_cached` y `is_ocr_model_cached`.

- **`tests/integration/test_api.py::TestHealth::test_health_endpoint`**:
  - Ajustado para aceptar tanto `"ok"` como `"degraded"` en `status`
    (el entorno de tests no tiene los modelos preloadeados).
  - Agregadas aserciones sobre la estructura `models: {face, ocr}`.
  - Los tests específicos de `"ok"` vs `"degraded"` viven en
    `test_security.py` con mocks de los chequeos.

### Cómo verificar

```bash
# Tests
pytest -q
# → 168 passed, 1 warning

# Sanity: levanta v0.4.0
python -c "from app.main import __version__; print(__version__)"
# → 0.4.0

# Health enriquecido (en una shell donde corre el servicio)
curl http://127.0.0.1:8001/api/v1/health
# → {"status":"ok","version":"0.4.0","models":{"face":true,"ocr":true}}
# Si "status":"degraded", correr scripts/preload_models.py

# Preload (idempotente)
python scripts/preload_models.py
# → "Todos los modelos disponibles. Listo para deploy." (exit 0)

# Deploy del unit (después de seguir README_DEPLOY)
sudo cp deployment/dni_processor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dni_processor.service
systemctl status dni_processor
journalctl -u dni_processor -f
```

### Lo que NO entra en este sprint

Diferido a sprints posteriores:
- **Tailscale ACL específica para integración con Escriba** — cuando
  exista la integración (Fase 5), recién ahí tiene sentido restringir
  qué tags del tailnet pueden llamar al servicio.
- **Audit log persistente** — descartado: si aparece requisito, Escriba
  puede registrar las llamadas desde su lado.
- **Migración a `/opt/dni_processor` + user dedicado** — viable cuando
  Hernán decida formalizar la separación; hoy es overkill para LAN
  single-user.
- **Logs JSON estructurados** — texto plano se lee mucho mejor con
  `journalctl`; JSON tiene sentido sólo si en el futuro se manda a
  un agregador externo.

---

## [0.3.2] — 2026-05-30

**Sprint 4a — Hardening de la aplicación.**

Primera de dos sub-entregas de Sprint 4 (4a hardening, 4b deployment).
El objetivo de 4a es dejar la app endurecida a nivel código antes de
empaquetarla para systemd. Todo lo que toca este sprint vive dentro de
`app/`, es testeable con pytest, y no requiere infra externa.

**163 tests pasando** (150 anteriores + 13 nuevos de seguridad). 97 warnings
de `datetime.utcnow` deprecated eliminados.

### Motivación

El servicio corre detrás de Tailscale en una LAN domiciliaria — el modelo
de amenaza no incluye atacantes externos. Pero hardening en profundidad
es barato y atrapa casos accidentales: clicks erróneos que disparan
macros, configuraciones de fonts que filtran información a terceros,
uploads gigantes que tumban el proceso. La política es "defense in
depth": cada capa es independiente y suma.

### Decisiones técnicas relevantes

#### CSP estricta sin `'unsafe-inline'` en script-src

El frontend carga assets externos de cinco orígenes (Google Fonts,
cdnjs para Cropper, unpkg para HTMX, jsdelivr para SortableJS, más
`/static/*`). NO hay `<script>` inline ni handlers `onclick`, lo cual
permitió mantener `script-src` estricto. `style-src` sí necesita
`'unsafe-inline'` porque varios templates tienen `style="..."` inline
para tweaks puntuales — migrarlos a clases está fuera de scope y el
riesgo de XSS por CSS es ínfimo.

Esto obligó a cambiar el patrón con el que se inyecta la flag DEBUG
del frontend (ver "DEBUG configurable" más abajo).

#### Tamaño máximo de request: `Content-Length` middleware, no uvicorn

Inicialmente discutimos "agregar `limit_max_request_size` a uvicorn.run".
Eso no existe en uvicorn: no tiene parámetro nativo para limitar el
body de request. La alternativa correcta es un middleware ASGI que
verifica `Content-Length` y rechaza con 413 antes de bufferear el body.

Limitaciones documentadas en el código y los tests:
- Sólo protege contra clientes que mandan Content-Length honestamente.
  Un cliente malicioso con `Transfer-Encoding: chunked` sin Content-Length
  pasa este filtro.
- La defensa real contra uploads abusivos sigue siendo la validación
  app-level en `routes_images.py` (MAX_IMAGE_SIZE_BYTES por imagen,
  MAX_SESSION_SIZE_BYTES por sesión, que ya existían desde Sprint 2).
- Este middleware existe para fail-fast en el caso típico de "subí
  accidentalmente un blob de 5GB" sin bufferearlo en memoria.

El límite global se calcula como `MAX_SESSION_SIZE_BYTES × 1.10` para
tolerar el overhead del multipart envelope sin bloquear uploads válidos.

#### DEBUG configurable via `data-debug` (no `window.DNI_DEBUG`)

El parche de diagnóstico de v0.3.1b.2 dejó `const DEBUG = true` hardcodeado
en `match.js`. La forma natural de hacerlo configurable era inyectar
`<script>window.DNI_DEBUG = ...</script>` en `base.html`, pero eso
requiere `'unsafe-inline'` en `script-src` — incompatible con la decisión
de CSP estricta.

Solución: `data-debug` como atributo del `<html>`, leído por JS via
`document.documentElement.dataset.debug === 'true'`. Cero scripts inline,
CSP limpia, y deja la puerta abierta a usar la misma flag desde
`review.js` / `upload.js` en el futuro sin tocar el HTML otra vez.

Controlado por `DNI_DEBUG` env var → `Settings.debug` → atributo en
template. Default es `false`: en producción el atributo no se emite
(ni siquiera como `data-debug="false"`).

#### Rate limiter desactivable por setting

slowapi keyea por IP (`get_remote_address`). En producción detrás de
Tailscale cada device del tailnet tiene IP única (100.x.y.z), así que
discrimina bien entre clientes.

Para tests, en lugar de mockear el limiter (frágil, ata cada test a
detalles internos de slowapi), se agregó el setting `rate_limit_enabled`
con default `True`. Los fixtures `isolated_sessions_dir` lo overridean
a `False` via env var. Es opt-out informado, no opt-in: los tests
nuevos que necesitan verificar el 429 hacen un override explícito al
inverso (`isolated_app_with_rate_limit`).

Como el `Limiter` es singleton y mantiene contadores in-memory entre
instancias de la app, los tests que ejercitan el rate limit llaman
`limiter.reset()` al setup y teardown.

#### Límites por endpoint

Single-user tras Tailscale: los números son barrera contra macros
accidentales, no contra atacantes. La calibración apunta a "ningún
humano va a alcanzar esto pegando manualmente, pero un loop infinito
mal escrito sí":

| Endpoint | Límite |
|---|---|
| `POST /sessions` | 30/min |
| `POST /sessions/{id}/images` | 60/min |
| `POST /sessions/{id}/process` | 10/min (caro: OCR + detección facial) |
| `POST /sessions/{id}/match` (sugerencias OCR) | 10/min |
| `PUT /sessions/{id}/pairs` | 60/min |
| `POST /sessions/{id}/generate-pdf` | 30/min |
| `POST /sessions/{id}/reset` | 60/min |
| `POST /crops/{id}/confirm`, `POST /images/{id}/crops`, DELETEs | 60/min |
| `/api/v1/health`, GETs de assets (imágenes, crops), web pages | sin límite |

#### Rename de parámetros de body: `request` → `payload`

slowapi requiere que los endpoints tengan un parámetro llamado `request`
de tipo `fastapi.Request` para extraer la IP. Cuatro endpoints ya tenían
un parámetro llamado `request` que era el body Pydantic
(`ConfirmCropRequest`, `CreateManualCropRequest`, `UpdatePairsRequest`).
Para no perder claridad ni romper la API HTTP (los nombres de parámetro
de FastAPI son internos al handler — los clientes mandan JSON), se
renombró el body a `payload` en cada uno.

### Added

#### `app/middleware.py` (nuevo)

- **`SecurityHeadersMiddleware`**: agrega CSP, X-Content-Type-Options,
  X-Frame-Options, Referrer-Policy y Permissions-Policy a TODA respuesta
  (incluyendo `/static/*`). NO setea HSTS porque Tailscale termina TLS
  y la app bindea a HTTP en 127.0.0.1.
- **`RequestSizeLimitMiddleware`**: rechaza con 413 cualquier request
  cuyo `Content-Length` exceda `MAX_SESSION_SIZE_BYTES × 1.10`, antes
  de bufferear el body.
- **`CSP_HEADER_VALUE`**: constante construida desde un dict de
  directivas (legible, diffable, exportable para tests).

#### `app/rate_limiter.py` (nuevo)

- **`limiter`**: instancia singleton de `slowapi.Limiter` keyeada por
  IP remota. Importable desde todos los routers sin crear ciclos con
  `app.main`.
- **`refresh_limiter_enabled()`**: sincroniza `limiter.enabled` con
  `Settings.rate_limit_enabled` cuando arranca la app o cuando el
  setting cambia (tests).

#### Settings nuevos (`app/config.py`)

- **`debug: bool = False`** — controla `data-debug` en el `<html>`.
- **`rate_limit_enabled: bool = True`** — habilita / deshabilita slowapi.

#### `tests/integration/test_security.py` (nuevo)

13 tests cubriendo:
- Presencia de cada header de seguridad en respuestas HTML y JSON.
- Directivas críticas de CSP (`default-src 'self'`, `frame-ancestors 'none'`,
  `base-uri 'self'`, ausencia de `'unsafe-inline'` en `script-src`).
- CDNs requeridos presentes en CSP.
- Sanity del header servido vs el armado por el módulo.
- Ausencia de HSTS (decisión explícita).
- 413 cuando `Content-Length` excede el límite.
- 201 cuando el request es de tamaño normal (no regresión).
- GETs sin Content-Length pasan limpios.
- 429 después del threshold con rate limit habilitado.
- `/api/v1/health` nunca rate-limited.
- GETs de estado de sesión no rate-limited.
- `data-debug` ausente cuando `DNI_DEBUG=false` (default).
- `data-debug="true"` presente cuando `DNI_DEBUG=true`.

### Changed

- **`app/main.py`**:
  - Bump `__version__` a `"0.3.2"`.
  - Registra `SecurityHeadersMiddleware` y `RequestSizeLimitMiddleware`.
  - Registra `limiter` en `app.state` y el handler de `RateLimitExceeded`.
  - Llama `refresh_limiter_enabled()` al iniciar.
- **`app/api/v1/routes_sessions.py`**:
  - `create_new_session`: rate limit `30/minute`, parámetro `request: Request`.
  - `delete_session`: rate limit `60/minute`, parámetro `request: Request`.
- **`app/api/v1/routes_images.py`**:
  - `upload_images`: rate limit `60/minute`, parámetro `request: Request`.
- **`app/api/v1/routes_processing.py`**:
  - `process_session`: rate limit `10/minute`, parámetro `request: Request`.
  - `confirm_crop`: rate limit `60/minute`, body renombrado a `payload`.
  - `create_manual_crop`: rate limit `60/minute`, body renombrado a `payload`.
  - `discard_crop`: rate limit `60/minute`, parámetro `request: Request`.
- **`app/api/v1/routes_matching.py`**:
  - `generate_suggestions`: rate limit `10/minute`, parámetro `request: Request`.
  - `update_pairs`: rate limit `60/minute`, body renombrado a `payload`.
  - `generate_pdf`: rate limit `30/minute`, parámetro `request: Request`.
  - `reset_session`: rate limit `60/minute`, parámetro `request: Request`.
- **`app/web/routes.py`**:
  - `_base_ctx()` helper inyecta `debug` y `version` al contexto de los
    templates de página completa (no aplica a partials, que no incluyen
    `<html>`).
- **`app/web/templates/base.html`**:
  - Atributo `data-debug="true"` en `<html>` cuando `debug` es truthy
    en el contexto del template. Si es falsy, NO se emite el atributo.
- **`app/web/static/js/match.js`**:
  - `DEBUG` se lee de `document.documentElement.dataset.debug === 'true'`
    en lugar de constante hardcodeada.
- **`tests/integration/*.py`** (5 archivos):
  - Cada fixture `isolated_sessions_dir` ahora setea
    `DNI_RATE_LIMIT_ENABLED=false` para que múltiples invocaciones de
    los mismos endpoints en un test no gatillen 429 espurio.

### Fixed

- **97 warnings de `datetime.utcnow()` deprecated eliminados.** Las dos
  ocurrencias en `app/schemas/web.py` (los `default_factory` de
  `created_at` y `updated_at` de `SessionState`) se reemplazaron por
  `lambda: datetime.now(timezone.utc)`. Como Pydantic invoca esos
  factories cada vez que construye un `SessionState`, eso era la fuente
  de la totalidad del ruido. Tras el fix, el único warning que queda
  en el output de pytest es la deprecación interna de Starlette sobre
  `httpx` en su TestClient — no es nuestro código.

### Added (dependency)

- **`slowapi>=0.1.9,<1.0`** agregado a `requirements.txt`.

### Cómo verificar

```bash
# Tests
pytest -q
# → 163 passed, 1 warning (el de starlette)

# Sanity: la app levanta y reporta versión
python -c "from app.main import app, __version__; print(__version__)"
# → 0.3.2

# Headers en la home
curl -i http://127.0.0.1:8001/ | head -20
# Debería traer:
#   Content-Security-Policy: default-src 'self'; ...
#   X-Content-Type-Options: nosniff
#   X-Frame-Options: DENY
#   Referrer-Policy: strict-origin-when-cross-origin
#   Permissions-Policy: camera=(), microphone=(), ...

# DEBUG OFF (default)
curl -s http://127.0.0.1:8001/ | grep -c "data-debug"
# → 0

# DEBUG ON
DNI_DEBUG=true python -m app.main &
curl -s http://127.0.0.1:8001/ | grep 'data-debug'
# → <html lang="es" data-debug="true">

# Rate limit (con DNI_RATE_LIMIT_ENABLED=true, que es el default)
for i in $(seq 1 35); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8001/api/v1/sessions
done
# → 201 × 30, luego 429 × 5
```

### Lo que NO entra en este sprint

Reservado para 4b (deployment, próxima entrega):
- `dni_processor.service` (systemd unit).
- `scripts/preload_models.py` (pre-descarga de modelos EasyOCR + caras).
- `.env.example`.
- `deployment/README_DEPLOY.md`.
- Health endpoint enriquecido (chequea presencia de modelos en cache).

Diferido a sprint posterior (cuando exista la integración Escriba):
- Tailscale ACL específica.
- Audit log persistente.

---

## [0.3.1b.4] — 2026-05-29

**Patch sobre v0.3.1b.3 — Preview del PDF en /completed.**

Patch chico que arregla el comportamiento del endpoint del PDF para
que el iframe del preview pueda mostrarlo, sin perder la capacidad de
descarga explícita.

**150 tests pasando** (148 anteriores + 2 nuevos del comportamiento
del endpoint).

### Causa raíz

`FastAPI.FileResponse(filename="...")` agrega automáticamente el
header `Content-Disposition: attachment`. Este header le dice al
browser "este recurso es para descargar". Cuando el iframe de
`completed.html` intentaba cargar el PDF, el browser respetaba el
header y disparaba la descarga automática del archivo en lugar de
mostrarlo. Efecto secundario: el iframe quedaba en blanco y el PDF
se descargaba al cargar la pantalla.

### Fixed

#### Endpoint del PDF ahora soporta dos modos

`GET /api/v1/sessions/{id}/output.pdf` ahora distingue entre:

- **Sin query param** → `Content-Disposition: inline` (default).
  El browser muestra el PDF en lugar de descargarlo. Es lo que usa
  el iframe del preview.

- **`?download=1`** → `Content-Disposition: attachment` con nombre
  sugerido `dni_processor_<sid>.pdf`. El browser descarga. Es lo que
  usa el botón "Descargar PDF" del `/completed`.

### Changed

- `app/api/v1/routes_matching.py::download_pdf`:
  - Nuevo parámetro `download: bool = False`
  - Cuando es `True`: `FileResponse(filename=...)` (attachment)
  - Cuando es `False`: header `Content-Disposition: inline` explícito
- `app/web/templates/completed.html`:
  - Botón "Descargar PDF" apunta a `/output.pdf?download=1`
  - Iframe sigue apuntando a `/output.pdf` (sin query) → inline
  - Removido atributo `download` del `<a>` (innecesario con
    `?download=1` que ya fuerza descarga server-side)

### Added

#### Tests específicos del comportamiento del endpoint

- `test_pdf_default_is_inline_for_iframe_embed`: verifica que sin
  query params, el header es `inline`. Sin esto, el iframe no carga.
- `test_pdf_download_query_forces_attachment`: verifica que con
  `?download=1`, el header es `attachment` con el nombre sugerido
  `dni_processor_<sid>.pdf`.

### Cómo verificar

Después de actualizar, generá un PDF y entrá a `/completed`:

- El iframe debería mostrar el PDF embebido (con el visor del browser
  ajustando al ancho por `#view=FitH`)
- El browser NO debería descargar el PDF automáticamente al entrar
- El botón "Descargar PDF" debería disparar la descarga al hacer
  click, con el nombre `dni_processor_<sid>.pdf`

---

## [0.3.1b.3] — 2026-05-29

**Patch sobre v0.3.1b.2 — Tres fixes complementarios.**

Tres cosas que se acumularon del feedback de prueba real:

1. **DnD seguía sin droppar**. Los logs confirmaron que `onAdd` jamás
   se disparaba cuando el slot destino tenía un dorso ocupándolo.
2. **Lentitud generalizada** desde que se introdujo OCR sincrónico.
   Cada confirmación bloqueaba ~2-5 segundos por imagen.
3. **Caso con muchos huérfanos sin forma de emparejarlos**: si OCR
   solo matchea 2 de 5, quedan 3 frentes huérfanos y 3 dorsos
   huérfanos, pero la UI no permite crear nuevos pares para
   combinarlos.

**148 tests pasando** (sin cambios en count — adaptados al nuevo
comportamiento de `generate_suggestions`).

### Fixed

#### DnD acepta drops sobre slots ocupados

`emptyInsertThreshold: 8` y `swapThreshold: 0.65` en la configuración
de SortableJS hacen que el drop se acepte aunque el slot target ya
tenga un elemento ocupándolo. Sin estos parámetros, SortableJS rechaza
silenciosamente el drop si interpreta que no hay "espacio" donde
insertar.

- `emptyInsertThreshold: 8`: 8 píxeles de buffer alrededor del
  contenedor activan la detección de drop aunque tenga elementos.
- `swapThreshold: 0.65`: cuando el cursor cubre 65% del área del
  elemento existente, se considera swap válido.

Aplicado a slots de dorso y al container de huérfanos.

#### OCR en background — performance restaurada

`extract_dni_number()` ahora se ejecuta vía FastAPI `BackgroundTasks`,
DESPUÉS de devolver la respuesta HTTP. El usuario ve el crop
confirmado al instante; el `dni_number` aparece en el siguiente GET
del estado (típicamente en menos de un segundo si OCR es exitoso).

**Antes** (v0.3.1b.1): cada `POST /confirm` o `POST /crops` bloqueaba
2-5 segundos hasta que OCR terminaba. Total para 10 crops: ~30-50s.

**Ahora**: cada confirmación retorna en ~100ms. OCR corre en paralelo
para todos los crops, completándose en background mientras el usuario
sigue trabajando.

Implementación:

- Helper renombrado a `_run_ocr_background(session_id, crop_id,
  final_path)` que recarga el estado de sesión para escribir el
  resultado de forma atómica. Si el crop fue eliminado entre la
  confirmación y la ejecución del OCR, la tarea aborta limpiamente.
- Nuevo helper `_schedule_ocr(background_tasks, ...)` que encola la
  ejecución vía `background_tasks.add_task()`.
- `confirm_crop` y `create_manual_crop` ahora aceptan
  `background_tasks: BackgroundTasks` como parámetro inyectado por
  FastAPI.

#### Pares provisorios para huérfanos combinables

`generate_suggestions` ahora hace un paso adicional después de
ejecutar el matcher:

1. Matcher OCR produce N pares de alta confianza
2. Si quedan **frentes huérfanos AND dorsos huérfanos**, se crean
   pares manuales 1-a-1 (asignación arbitraria: frente[0] con
   dorso[0], etc.)
3. Estos pares "provisorios" tienen `origin=MANUAL` y el usuario los
   corrige luego con drag-and-drop si la asignación arbitraria no
   coincide con la realidad
4. Solo aparecen como huérfanos los que están en exceso por
   asimetría (más frentes que dorsos o viceversa)

Antes del fix: 5 frentes + 5 dorsos con OCR que solo matchea 2 →
2 pares + 3F y 3D huérfanos sin forma de emparejarlos.

Después del fix: 5 frentes + 5 dorsos con OCR que solo matchea 2 →
2 pares OCR + 3 pares MANUAL provisorios = 5 pares listos, usuario
corrige con drag-and-drop.

### Changed

- `app/api/v1/routes_processing.py`:
  - Import de `BackgroundTasks` desde FastAPI
  - Reemplazado `_run_ocr_on_crop(crop, path)` (sincrónico) por
    `_run_ocr_background(session_id, crop_id, path)` + helper
    `_schedule_ocr(background_tasks, ...)`
  - `confirm_crop` y `create_manual_crop` reciben `background_tasks`
    como dependencia inyectada por FastAPI
  - OCR encolado como background task en lugar de invocado en línea
- `app/api/v1/routes_matching.py::generate_suggestions`:
  - Después del matcher OCR, crea pares manuales provisorios para
    combinar huérfanos 1-a-1
  - `n_unpaired_frentes` y `n_unpaired_dorsos` en la respuesta ahora
    reflejan los huérfanos REALES (por asimetría), no los que se
    combinarán en pares provisorios
- `app/web/static/js/match.js`:
  - `emptyInsertThreshold: 8` y `swapThreshold: 0.65` en cada slot
  - `emptyInsertThreshold: 8` en el container de huérfanos
  - Logs y `draggable` explícito mantenidos de v0.3.1b.2

### Decisiones técnicas registradas

- **Race condition en OCR background**: si el usuario descarta un
  crop entre la confirmación y la ejecución del OCR, el OCR
  encuentra que el crop ya no existe en el estado y aborta sin
  escribir nada. Loguea informativamente. La sesión queda consistente.

- **OCR en `BackgroundTasks` vs Celery/RQ**: BackgroundTasks de
  FastAPI corre en el mismo proceso que el servidor. Es suficiente
  para uso single-user (vos sos el único cliente). Si en el futuro
  hay múltiples notarios usando la misma instancia simultáneamente,
  habría que migrar a una cola distribuida real. Para ahora,
  BackgroundTasks es el balance correcto entre simplicidad y
  performance.

- **Pares provisorios usan asignación arbitraria por orden**:
  no intentamos ser inteligentes (ej. matchear por similitud de
  imagen). Simplemente asignamos en el orden en que aparecen los
  huérfanos. El usuario corrige rápidamente con drag-and-drop.
  Mejor un punto de partida arbitrario que un usuario bloqueado
  sin forma de emparejar.

### Cómo probarlo

```bash
unzip dni_processor_v0.3.1b.3.zip
cd dni_processor_v0.3.1b.3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q   # 148 passed
python -m app.main
```

Validación esperada:

1. **Confirmar un crop debe ser instantáneo** (no esperar a OCR)
2. **Generar sugerencias siempre crea N=min(F,D) pares** — nunca te
   deja con huérfanos combinables sin solución
3. **Arrastrar un dorso sobre un slot ocupado** debe disparar el
   swap (los logs `[match]` en la consola confirman si funciona)
4. **El número OCR aparece después de unos segundos** en cada crop,
   automáticamente cuando refrescás la página de matcheo (o cuando
   navegás a `/match` después de confirmar todos los crops)

### Conocido — pendiente

- **DEBUG=true sigue activo** en match.js. En Sprint 4 (hardening)
  lo apago por default.
- **El OCR background no notifica al frontend cuando termina**.
  Si OCR es muy lento, el usuario puede llegar a la pantalla de
  matcheo antes de que esté listo. La página actual no auto-refresca
  cuando el OCR termina; el usuario tiene que recargar. En la
  práctica el OCR es lo suficientemente rápido para que esto no se
  note (~1-2s por crop), pero si en el futuro hay sesiones grandes
  podría agregarse polling o SSE.

---

## [0.3.1b.2] — 2026-05-29

**Patch sobre v0.3.1b.1 — Drag-and-drop con `draggable` explícito + diagnóstico.**

Patch que arregla el comportamiento de SortableJS donde el dorso se
arrastraba visualmente pero al soltar volvía a su lugar original sin
disparar el `onAdd`. Causa probable: cuando una tarjeta está dentro de
un slot, SortableJS no identificaba claramente cuál era el "ítem"
arrastrable (default `>*` puede no matchear según el orden de los
hijos).

**148 tests pasando** (sin cambios en suite — fix puramente cliente).

### Fixed

#### `draggable` explícito en cada Sortable.create()

En `match.js`, cada llamada a `Sortable.create()` ahora especifica
inequívocamente qué elemento es el ítem arrastrable:

- `#pair-list`: `draggable: '.pair-row'`
- Cada `.dorso-slot`: `draggable: '.pair-card--dorso'`
- `#orphan-dorsos`: `draggable: '.orphan-card--draggable'`

Esto le dice a SortableJS exactamente qué buscar al iniciar un drag y
qué considerar válido como destino. Sin esto, el comportamiento
default (`>*`, cualquier hijo directo) puede fallar silenciosamente si
hay variaciones de estructura entre orígenes y destinos.

### Added

#### Logs explícitos en consola para diagnóstico futuro

El JS ahora loguea cada evento del DnD con prefijo `[match]`:

- `[match] Inicializando match screen, sessionId = ...`
- `[match] SortableJS disponible: true`
- `[match] Inicializando 2 dorso-slots como Sortable`
- `[match] Slot[0] onStart: agarrando dorso a3b1c...`
- `[match] Slot[1] onAdd: recibió a3b1c...`
- `[match] handleSwapOrMove: { from: ..., to: ..., item: ... }`
- `[match] Target es slot: true — elementos sobrantes: 1`
- `[match] Haciendo swap visual: moviendo d8e2f... al source`
- `[match] Enviando PUT /pairs con 2 pares`
- `[match] Respuesta PUT /pairs: 200`
- `[match] PUT /pairs OK, 2 pares persistidos`

Si vuelve a haber un problema con el DnD, abriendo la consola del
browser (F12) durante el arrastre se ve en qué punto exacto se corta
el flujo:

- Si no aparece `onStart` → SortableJS no reconoce el ítem como
  arrastrable
- Si aparece `onStart` pero no `onAdd` → el slot target no acepta el
  drop (sospechar `group` o `put: false`)
- Si aparece `onAdd` pero no `PUT /pairs` → falla en collectPairsFromDom
  o sendPairsUpdate
- Si `PUT /pairs` devuelve status ≠ 200 → el backend rechaza el payload
  (ver el texto del error)

### Changed

- `app/web/static/js/match.js`:
  - `DEBUG = true` con función `log()` que prefija todos los mensajes
  - `draggable` explícito en todas las llamadas a Sortable.create()
  - `onUpdate` y `onAdd` capturados igual en slots para que el swap
    funcione tanto en intercambio entre slots como en reordenamiento
  - `handleSwapOrMove` renombrada (era `handleSwap`) para reflejar
    que ahora maneja ambos casos
  - Logs detallados en `collectPairsFromDom`, `sendPairsUpdate`,
    `handleSwapOrMove`, `initPairListSortable`, `initDorsoSlotsSortable`

### Decisiones técnicas

- **DEBUG=true por default**: en una versión de pruebas/uso interno,
  preferimos verbosidad. En Sprint 4 (hardening) lo bajamos a `false`
  o lo controlamos via flag. Por ahora la cantidad de logs no
  satura la consola (ocurren solo durante interacciones de drag).

- **No removí los handlers anteriores**: si por algún motivo el fix
  no funciona y aparece un error nuevo, los logs nos van a decir
  exactamente dónde mirar.

### Cómo probarlo

```bash
unzip dni_processor_v0.3.1b.2.zip
cd dni_processor_v0.3.1b.2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q   # 148 passed
python -m app.main
```

**Abrí la consola del browser (F12 → Console)** antes de empezar a
arrastrar. Vas a ver los logs `[match]` aparecer en cada paso.
Si el drop funciona, vas a ver la secuencia completa hasta
`PUT /pairs OK`. Si falla, el último log antes del corte indica
dónde está el problema.

---

## [0.3.1b.1] — 2026-05-29

**Patch sobre Sprint 3b — Bug del DnD + OCR no se ejecutaba + UX mejoras.**

Patch que arregla tres problemas reales detectados en uso:

1. **OCR nunca se ejecutaba en el pipeline.** Los crops se confirmaban
   pero `extract_dni_number()` no se invocaba en `routes_processing.py`.
   Resultado: todos los `dni_number` quedaban en `None` y el matcher
   no tenía números para emparejar — todos los pares aparecían como
   `manual`.
2. **Drag-and-drop de dorsos no funcionaba.** El DnD nativo HTML5
   estaba en conflicto con SortableJS observando el contenedor padre
   (`#pair-list`). El usuario veía la "mano cerrada" arrastrando pero
   al soltar el evento `drop` no se disparaba sobre los slots target.
3. **Miniaturas demasiado pequeñas para verificación visual** de
   números de DNI cuando OCR fallaba.

**148 tests pasando** (146 de v0.3.1b + 2 nuevos del pipeline OCR).

### Fixed

#### Bug crítico: OCR no se ejecutaba

`app/api/v1/routes_processing.py` se modificó para que `extract_dni_number()`
se invoque después de cada `apply_final_crop()`. Tanto en:
- `confirm_crop` (frentes auto-detectados confirmados por el usuario)
- `create_manual_crop` (dorsos y frentes-fallidos marcados a mano)

La invocación está envuelta en un helper `_run_ocr_on_crop()` que:
- Captura excepciones (si EasyOCR no está disponible o falla, el crop
  sigue siendo válido con `dni_number=None`)
- Loguea explícitamente el resultado para diagnóstico en producción:
  - `OCR sobre crop <id> (frente): número=12345678, confianza=0.95`
  - `OCR sobre crop <id> (dorso): sin lectura plausible`
  - `OCR falló sobre crop <id>: ModuleNotFoundError: ...`

Esto te permite verificar en `journalctl` (o donde sea que captures
stderr) si el OCR efectivamente corre y qué lee.

#### Bug crítico: DnD de dorsos no funcionaba

**Causa raíz**: el HTML5 DnD nativo y SortableJS observando el mismo
subárbol del DOM compiten por los eventos. Cuando el usuario empezaba
a arrastrar un `<img>` de dorso, SortableJS interceptaba el evento
antes de que llegara al handler nativo. Resultado: el browser mostraba
el cursor "agarrando" pero el evento `drop` jamás se disparaba.

**Fix**: unificar todo bajo SortableJS con groups.

- Cada slot de dorso ahora es un contenedor `.dorso-slot` que es a
  su vez una mini-lista SortableJS de un solo elemento.
- Todos los `.dorso-slot` comparten `group: "dorsos"` con `pull: true,
  put: true`.
- El container de dorsos huérfanos (`#orphan-dorsos`) está en el mismo
  group.
- Cuando un dorso entra a un slot ocupado, `handleSwap()` mueve el
  dorso desplazado al slot de origen → swap visual instantáneo.
- Una sola librería = un solo modelo mental, sin conflictos.

#### Tamaño de miniaturas insuficiente

- `.pair-card__image`: 140×88 → **220×138 px** (+57%)
- `.orphan-card`: 160 → **240 px** de ancho
- `.orphan-card__image`: 88 → **138 px** de alto
- `.pair-card` min-height: 96 → 148 px (para acomodar el thumb mayor)

### Added

#### Lightbox para inspección detallada

Click en cualquier thumbnail (frente, dorso, huérfano) abre una vista
ampliada centrada en pantalla. Funcionalidades:
- Imagen al tamaño natural, hasta 92vw × 78vh
- Si hay número OCR, se muestra grande debajo con label
  "leído por OCR — verificá"
- Cierre con: click en backdrop, botón ×, o tecla Escape
- Cursor `zoom-in` sobre los thumbnails sugiere visualmente la acción

Implementación pasiva intencional: el lightbox solo muestra, no
permite drag-and-drop desde ahí. Es para verificar números cuando
OCR no funcionó.

#### Hint visual en la pantalla de matcheo

Mensaje destacado al inicio de la lista de pares:
> Arrastrá el handle ⋮⋮ a la izquierda de una fila para reordenar
> verticalmente. Arrastrá una tarjeta de dorso hacia otro par para
> hacer swap. Click en cualquier miniatura para ver más grande.

Aclara las dos interacciones disponibles + la nueva función de
lightbox.

#### Indicador visual de slot vacío

Si por algún motivo un slot de dorso queda vacío durante una
operación de DnD (transitorio), se muestra con borde dashed amarillo
y fondo amarillo claro para que el usuario vea que necesita asignar
algo.

### Changed

- `app/api/v1/routes_processing.py`:
  - Nuevo helper `_run_ocr_on_crop(crop, final_path)` con manejo robusto
    de errores y logging detallado
  - Import de `extract_dni_number` desde `app.core.ocr`
  - Invocación de OCR en `confirm_crop` y `create_manual_crop` tras
    `apply_final_crop`
- `app/web/static/js/match.js`:
  - Reescrito completo. Eliminado todo el código de DnD nativo HTML5.
  - Nueva inicialización con SortableJS en dos modos:
    `initPairListSortable` (reorder vertical) y
    `initDorsoSlotsSortable` (swap entre slots y huérfanos)
  - Nueva función `handleSwap(evt)` que detecta cuando un slot recibe
    un elemento estando ocupado y mueve el desplazado al origen
  - Nuevo lightbox con `openLightbox(src, dniNumber)` y
    `closeLightbox()`
  - Cleanup de event listeners: click en imagen abre lightbox; click
    en botones con `data-action` ejecuta acción correspondiente
- `app/web/templates/partials/match_content.html`:
  - Nueva estructura: cada dorso vive dentro de un envoltorio
    `.dorso-slot`. La tarjeta del dorso (`.pair-card--dorso`) es la
    que SortableJS mueve, el slot es el contenedor.
  - Eliminados atributos `draggable="true"` (HTML5 DnD) en imágenes
    de dorso y orphan-cards
  - Agregado `data-dni-number` en cards para que el JS pueda mostrar
    el número en el lightbox
  - Nuevo `match-hint` con las instrucciones de uso
  - Las dorso-cards huérfanas usan ahora clase `.orphan-card--draggable`
    en lugar de atributo HTML5 `draggable="true"`
- `app/web/static/css/main.css`:
  - Tamaños de imagen actualizados (220×138 cards, 240px orphans)
  - Nuevo selector `.dorso-slot` con `min-height: 148px` y borde
    dashed amarillo cuando está vacío
  - Nuevos estilos para `.match-hint` y `.orphans-column__hint`
  - Cursor `zoom-in` sobre todas las imágenes clickables
  - Nuevo bloque completo de estilos del lightbox (~75 líneas)
  - Eliminados estilos obsoletos del DnD nativo (`.drop-target-active`,
    selectores `[draggable="true"]`)

### Added — Tests

`tests/integration/test_ocr_pipeline.py` — 2 tests nuevos:

- `test_ocr_runs_on_manual_crop`: mockea `extract_dni_number` para
  devolver `("12345678", 0.95)` y verifica que tras crear un recorte
  manual, el crop tiene `dni_number == "12345678"` en su estado.
- `test_ocr_failure_does_not_break_crop_creation`: simula que el OCR
  tira RuntimeError y verifica que el endpoint igual devuelve 201,
  con `dni_number=None` en el estado.

Los tests usan `monkeypatch.setattr` para patchear
`app.api.v1.routes_processing.extract_dni_number` (donde se importa,
no donde se define). Esto permite correr toda la suite sin requerir
EasyOCR instalado.

### Decisiones técnicas registradas

- **Logging explícito de OCR**: aunque el OCR ya logueaba al cargar el
  modelo, no logueaba qué leyó. Ahora cada invocación deja registro,
  con el número leído (cuando existe) o "sin lectura plausible". En
  producción esto te permite diagnosticar rápidamente si: (a) EasyOCR
  no está descargando el modelo, (b) descargó el modelo pero no
  encuentra texto en las imágenes (problema de calidad), o (c) lee
  pero los números no pasan los filtros de plausibilidad.

- **Lightbox pasivo**: no permite DnD desde la vista ampliada, solo
  inspección. La complejidad adicional de soportar DnD en el lightbox
  (con su propio set de slots target) era 4-5× el código del lightbox
  pasivo, sin valor real (el usuario abre, lee, cierra, arrastra en
  la vista normal).

- **`.dorso-slot` como contenedor**: SortableJS necesita que cada
  "lista" sea un elemento DOM identificable. No se puede aplicar
  Sortable directamente a la tarjeta del dorso (sería arrastrarse
  a sí misma). Por eso introdujimos el `.dorso-slot` como envoltorio
  vacío que solo existe para SortableJS.

- **Swap manual en `handleSwap()` en lugar de plugin swap de
  SortableJS**: el plugin oficial de swap de SortableJS hace swap
  entre listas con múltiples elementos, lo cual es overkill para
  nuestro caso (cada slot tiene exactamente 0 o 1 elemento). La
  implementación manual es más clara y específica.

### Cómo verificar el OCR

Después de actualizar, **levantá el servidor con `--log-level=INFO`** y
hacé un trámite completo. Los logs deberían mostrar (suponiendo que
EasyOCR tiene los modelos descargados):

```
[INFO] app.core.ocr: Inicializando EasyOCR (primera carga, puede tardar)...
[INFO] app.core.ocr: EasyOCR inicializado.
[INFO] app.api.v1.routes_processing: OCR sobre crop a3b1c... (frente): número=32450789, confianza=0.92
[INFO] app.api.v1.routes_processing: OCR sobre crop f8e2d... (dorso): sin lectura plausible
```

Si ves "sin lectura plausible" en todos los crops a pesar de que las
imágenes son claras, los recortes pueden estar demasiado chicos para
que EasyOCR lea bien (el cropper amplio de 220×138 px se redimensiona
internamente). En ese caso, la próxima iteración podría aumentar la
resolución del recorte final, o usar el `wide_crop` (el más grande)
para OCR mientras el `final_crop` queda como visualización.

### Conocido — sigue pendiente

- **Sin animación al hacer swap**: cuando dos dorsos se intercambian,
  el movimiento es instantáneo. SortableJS anima los reorders pero el
  swap manual (`appendChild` en `handleSwap()`) es directo. Si en uso
  real se siente abrupto, agregar transitions CSS.

- **Touch/mobile sigue sin soportar**: SortableJS sí maneja touch,
  pero la pantalla no está pensada para mobile. Sigue siendo
  desktop-only como confirmaste en Sprint 2.

- **Si EasyOCR demora mucho la primera vez**: la descarga de modelos
  bloquea el primer request que invoque OCR. En producción esto
  significa que el primer recorte que confirmes después de instalar
  o reiniciar el servicio va a tardar ~30-60 segundos extra. Si esto
  causa problemas, en Sprint 4 (hardening) preinicializamos EasyOCR
  en el arranque del servicio.

---

## [0.3.1b] — 2026-05-29

**Sprint 3b — UI de matcheo + pantalla post-PDF.**

Cierra el ciclo completo del flujo: el usuario puede subir fotos →
revisar/ajustar recortes → emparejar frentes con dorsos → generar el PDF
→ descargarlo → iniciar un nuevo trámite. **Todo desde el browser, sin
necesidad de `curl` ni Swagger UI.**

**146 tests pasando** (134 del Sprint 3a + 12 nuevos de UI).

### Added

#### Rutas web nuevas (`app/web/routes.py`)

- `GET /sessions/{id}/match` — pantalla principal de matcheo
- `GET /sessions/{id}/match/partial` — partial HTMX refrescable
- `GET /sessions/{id}/completed` — pantalla post-PDF con descarga +
  opción de "Empezar otro trámite"

#### Templates Jinja2

**`match.html`** — pantalla de matcheo:
- Header con título "Emparejar frentes y dorsos" + botón "← Volver
  a revisar"
- Container HTMX que se refresca tras cada cambio de pares
- Carga SortableJS desde CDN (`sortablejs@1.15.2`, ~10KB)
- Carga `/static/js/match.js`

**`partials/match_content.html`** — contenido refrescable:
- Stats bar con: frentes / dorsos / pares / huérfanos
- Sección "Pares emparejados" con una `pair-row` por par:
  - Handle ⋮⋮ a la izquierda para drag de reordenamiento
  - Número de posición (#1, #2, ...)
  - Card del frente (izquierda) con thumbnail + número OCR si existe
    + label "leído por OCR — verificá"
  - Badge de origen del par (OCR exacto / OCR aprox. / manual) con
    colores distintivos: verde / amarillo / gris
  - Card del dorso (derecha) con thumbnail draggable + número OCR
- Sección "Sin emparejar" con dos columnas: frentes huérfanos
  (no draggables — solo visualización) y dorsos huérfanos (draggable
  hacia los slots de dorso de los pares)
- Footer-bar con botón "Generar PDF" — habilitado cuando
  `can_generate_pdf=True`, deshabilitado con tooltip mostrando
  `imbalance_message` cuando no

**`completed.html`** — pantalla post-PDF:
- Hero con badge "✓ PDF generado" + título + resumen ("N pares")
- Dos botones grandes: "Descargar PDF" (link directo a la API) +
  "Empezar otro trámite" (descarta sesión y redirige a `/`)
- Iframe con previsualización del PDF embebido (con `#view=FitH`
  para que cargue ajustando al ancho)

#### CSS (`app/web/static/css/main.css`)

Bloque "Pantalla de matcheo" (~150 líneas):
- `.pair-list`, `.pair-row` (grid de 5 columnas: handle, #pos, frente,
  conector+badge, dorso)
- Estados `sortable-ghost` y `sortable-chosen` para drag visual
- `.pair-card` con variantes `--frente` (borde ocre) y `--dorso`
  (borde gris). Estado `.drop-target-active` (borde dashed ocre +
  fondo)
- `.pair-card__image` (140×88px), `.pair-card__meta` y bloques de DNI
  con label monoespaciada
- `.pair-row__badge` con variantes `--ocr_exact` (verde),
  `--ocr_approximate` (amarillo), `--manual` (gris)
- `.orphans-grid` (2 columnas), `.orphans-column`, `.orphan-card`
  (160px de ancho), estado `.dragging`

Bloque "Pantalla completed":
- `.completed-hero`, `.completed-hero__badge`
- `.completed-actions` (botones grandes centrados)
- `.completed-preview` con `.completed-preview__frame` (iframe 100% ×
  600px) y `.completed-preview__note`

#### JavaScript

**`app/web/static/js/match.js`** — el script más complejo del proyecto:
- Auto-trigger de `POST /api/v1/sessions/{id}/match` al cargar la
  página SI no hay pares todavía (genera sugerencias por OCR)
- Inicialización de **SortableJS** sobre `#pair-list` con
  `handle: '.pair-row__handle'`. Tras cada `onEnd`, llama
  `sendPairsUpdate()` que recolecta el orden actual del DOM y manda
  `PUT /pairs` con la lista completa
- **Drag-and-drop nativo HTML5** para mover dorsos entre pares:
  - Sources: `<img>` de dorso dentro de pares + `.orphan-card` de
    dorsos huérfanos
  - Targets: `.pair-card--dorso.dorso-drop-target` (cada slot de dorso)
  - Handler de drop hace SWAP visual de las imágenes en el DOM y
    luego `sendPairsUpdate()`
- Handler de "Generar PDF" que llama `POST /generate-pdf` y al
  recibir 200 redirige a `/completed`
- Re-inicialización de Sortable y DnD después de cada `htmx:afterSwap`

**`app/web/static/js/completed.js`**:
- Handler de "Empezar otro trámite": pide confirmación, llama
  `POST /reset` y redirige según `redirect_to` de la respuesta

### Changed

- `app/web/templates/partials/review_content.html`:
  - El botón "Continuar al matcheo" ahora es un `<a>` real con
    `href="/sessions/{id}/match"` cuando `everything_ready=True`.
    Antes era un botón deshabilitado con tooltip "Disponible en
    Sprint 3".
- `app/web/static/js/review.js`:
  - Removido el handler `continue-to-match` que solo mostraba toast
    "Sprint 3 — próximamente"

### Tests

**`tests/integration/test_match_web.py`** — 12 nuevos tests:

`TestMatchPage` (4):
- Página renderiza sin pares (empty-state + botón generar sugerencias)
- Página renderiza con pares (pair-rows + badges + botón habilitado)
- Botón Generar PDF queda disabled si hay imbalance (3F vs 1D)
- 404 para sesión inexistente

`TestMatchPartial` (2):
- Partial renderiza sin layout (`<html>` ausente, `stats-bar`
  presente)
- 404 para sesión inexistente

`TestCompletedPage` (3):
- Renderiza con preview + botones después de generar PDF
- Renderiza incluso si el PDF no existe todavía (iframe vacío pero
  resto OK)
- 404 para sesión inexistente

`TestReviewLinksToMatch` (1):
- El botón "Continuar al matcheo" del review apunta correctamente a
  `/sessions/{id}/match`

`TestMatchJsServed` (2):
- `match.js` y `completed.js` se sirven desde `/static/js/` con tipo
  MIME correcto y contenido reconocible

### Decisiones técnicas registradas

- **SortableJS para reordenamiento vertical, drag-and-drop nativo
  HTML5 para movimiento de dorsos entre pares**. Dos mecanismos
  distintos porque atacan problemas distintos: SortableJS maneja
  swaps de filas completas con animaciones; el DnD nativo es más
  flexible para "tomar un dorso, soltar en otro slot, hacer swap".
  Mezclar ambos en una sola lib (ej. solo SortableJS con groups)
  resultaba en interferencias entre los dos modos de interacción.

- **API declarativa: cada cambio de DnD manda la lista completa
  de pares**. El frontend hace el swap visual del DOM, recolecta el
  estado actual (orden + asignación de dorsos), y manda `PUT /pairs`
  con todo. Esto evita que el frontend tenga que llevar contabilidad
  precisa de qué cambió. El backend valida y respeta lo que recibe.

- **Auto-trigger de sugerencias OCR al entrar a /match**. Si no hay
  pares todavía, el JS dispara `POST /match` automáticamente. Si el
  usuario ya estuvo y volvió, no se re-genera (porque ya hay pares).
  Esto evita que el usuario tenga que apretar un botón "Generar
  sugerencias" en el caso común (primera visita).

- **Iframe para preview del PDF en /completed**. Más simple que un
  visor JS dedicado, funciona en todos los browsers modernos. El
  parámetro `#view=FitH` indica al PDF viewer del browser que ajuste
  al ancho. Si por algún motivo el iframe no carga (ej. el browser
  bloqueó el plugin), el botón "Descargar PDF" sigue funcionando como
  fallback.

- **Confirm() del botón "Empezar otro trámite"**. Acción destructiva
  (borra todo el working dir de la sesión). El `confirm()` nativo del
  browser es suficiente — un modal personalizado sería overkill para
  un caso de uso poco frecuente.

### Conocido — pendiente

- **Touch / mobile**: el drag-and-drop nativo HTML5 no funciona en
  touch. SortableJS sí lo soporta pero el DnD de dorsos no. Sigue
  siendo válido: confirmaste desktop-only en Sprint 2.
- **Sin animación de swap**: cuando se mueve un dorso entre pares, el
  swap es instantáneo. SortableJS tiene animaciones suaves para el
  reorder vertical, pero el DnD nativo no.
- **No hay "deshacer"** en la pantalla de matcheo. Si el usuario
  arrastra mal un dorso, tiene que volver a arrastrarlo a su lugar.
  En la práctica no es problema porque el badge de origen le muestra
  cuál era la sugerencia original ("OCR exacto").

### Cómo probarlo

```bash
unzip dni_processor_v0.3.1b.zip
cd dni_processor_v0.3.1b
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q   # 146 passed
python -m app.main
```

Flujo completo desde el browser:
1. Abrí `http://127.0.0.1:8001/`
2. Subí fotos de frentes y dorsos
3. Ajustá los recortes en `/review`
4. Click en "Continuar al matcheo →"
5. (Automáticamente se generan sugerencias OCR)
6. Reordená pares con el handle ⋮⋮ y/o reasigná dorsos arrastrándolos
7. Click en "Generar PDF →"
8. En `/completed`: descargá o empezá otro trámite

### Próximos pasos

- **Sprint 4** (v0.3.2): hardening + deployment
  - systemd unit
  - Pre-descarga del modelo de caras
  - Rate limiting (slowapi)
  - Security headers
  - Tailscale ACL para integración con Escriba
  - Migrar warnings de `datetime.utcnow()` deprecated

---

## [0.3.1a] — 2026-05-29

**Sprint 3a — Backend de matcheo + generación del PDF final.**

Entrega que cierra el ciclo backend completo del flujo: upload → revisión
→ **matcheo → PDF → reset**. Sin UI todavía (eso es Sprint 3b). Los 5
endpoints nuevos son validables vía `curl` o cualquier cliente HTTP.

**134 tests pasando** (112 del Sprint 2b + 22 nuevos del backend de matcheo).

### Decisiones de producto cerradas en este sprint

1. **Huérfanos bloquean la generación del PDF**. Si hay frentes sin
   dorsos (o viceversa), el sistema NO genera el PDF. El usuario debe
   volver a `/review` para corregir la asimetría estructural — el
   matcheo no permite descarte de crops (eso era error de subida).
2. **Orden manual con drag-and-drop**. El backend acepta una lista de
   pares `[(frente_id, dorso_id, position), ...]` y respeta el orden
   visual decidido por el usuario.
3. **OCR visible solo en preview**. La API expone `dni_number` en los
   `CropInfo` de la respuesta del estado, para que el frontend lo
   muestre como sugerencia verificable. El composer del PDF lo ignora
   (sigue siendo puramente visual).
4. **Edición vuelve a `/review`**. No hay endpoint de "ajustar crop
   desde matcheo". Si algo está mal, se vuelve a la pantalla anterior.
5. **Post-PDF**: la sesión queda en COMPLETED durante 24h (TTL). Hay
   endpoint `POST /reset` para descartarla manualmente y arrancar
   trámite nuevo.

### Added

#### Schemas de dominio (`app/schemas/web.py`)

- `PairOrigin` enum: `OCR_EXACT` / `OCR_APPROXIMATE` / `MANUAL`.
  Marca cómo se generó cada par para mostrarlo en el preview.
- `PairState` modelo: `pair_id`, `frente_crop_id`, `dorso_crop_id`,
  `position` (0-based para el PDF), `origin`, `match_distance`
  (Levenshtein entre números OCR).
- `SessionStatus.MATCHING`: nuevo estado intermedio entre
  `READY_FOR_MATCH` y `COMPLETED`.
- `SessionState.pairs: dict[str, PairState]` — almacenamiento por
  pair_id.
- 4 properties nuevas en `SessionState`:
  - `confirmed_frentes` / `confirmed_dorsos`: filtros por side+status
  - `can_generate_pdf`: True si hay pares, mismos N frentes/dorsos, y
    todos los crops confirmados están en algún par
  - `imbalance_message`: explicación legible cuando
    `can_generate_pdf=False` (incluye plurales correctos en español)

#### Schemas de API (`app/schemas/api.py`)

- `PairInfo`: representación de un par en la respuesta
- `GenerateSuggestionsResponse` (POST /match): pares generados +
  contadores de huérfanos
- `PairAssignmentItem` + `UpdatePairsRequest`: input declarativo del
  drag-and-drop (lista completa, no incrementos)
- `UpdatePairsResponse`: pares resultantes ordenados por position
- `GeneratePdfResponse`: URL del PDF + tamaño en bytes
- `ResetSessionResponse`: incluye `redirect_to="/"` para que la UI sepa
  a dónde ir

`SessionStateResponse` extendido con: `pairs`, `can_generate_pdf`,
`imbalance_message`.

#### Router de matcheo (`app/api/v1/routes_matching.py`)

5 endpoints nuevos:

**`POST /api/v1/sessions/{id}/match`** — Genera sugerencias por OCR
- Invoca `match_frentes_dorsos()` del módulo `matcher`
- Reemplaza pares existentes con las sugerencias
- Devuelve los pares + contadores de huérfanos
- Cambia status a MATCHING
- 400 si no hay crops confirmados

**`PUT /api/v1/sessions/{id}/pairs`** — API declarativa de pares
- Recibe la lista completa de pares con sus positions
- Valida exhaustivamente:
  - Cada crop existe y está confirmado
  - Sides correctos (frente vs dorso)
  - Sin duplicación de crops entre pares
  - Positions únicas
- Preserva `origin` de pares pre-existentes cuando coinciden
- Reemplaza el estado completo de pares en una operación atómica

**`POST /api/v1/sessions/{id}/generate-pdf`** — Produce el PDF final
- Pre-valida `can_generate_pdf`. Si False, devuelve 400 con
  `imbalance_message` legible.
- Construye los `MatchedPair` ordenados por `position`
- Invoca `compose_pdf()` con `unpaired_frentes=[]` y
  `unpaired_dorsos=[]` (la validación garantiza que no hay)
- Guarda en `<working_dir>/output.pdf`
- Cambia status a COMPLETED

**`GET /api/v1/sessions/{id}/output.pdf`** — Descarga
- `FileResponse` con MIME `application/pdf`
- Nombre sugerido: `dni_processor_<sid_short>.pdf`
- 404 si todavía no se generó

**`POST /api/v1/sessions/{id}/reset`** — Empezar otro trámite
- Descarta la sesión (equivalente a DELETE)
- Devuelve `redirect_to="/"` para que la UI redirija

#### Tests de integración (`tests/integration/test_matching.py`)

**22 tests nuevos** cubriendo:

- `TestGenerateSuggestions` (4 tests): match con/sin crops, status
  cambia a MATCHING, 404 para sesión inexistente
- `TestUpdatePairs` (7 tests):
  - Setear pares manualmente
  - Pares ordenados por position en la respuesta
  - Rechazar wrong side (frente+frente)
  - Rechazar duplicación de frente
  - Rechazar position duplicada
  - Rechazar crop_id inexistente
  - Lista vacía limpia los pares
- `TestCanGeneratePdf` (3 tests): sin pares, asimetría
  (3 frentes vs 1 dorso), balanced+paired
- `TestGeneratePdf` (5 tests): error sin pares, generate exitoso,
  status=COMPLETED, descarga del PDF (verifica magic bytes `%PDF`),
  404 sin generar previamente
- `TestReset` (2 tests): reset exitoso, 404 para inexistente
- `TestFullLifecycle` (1 test): create → upload → crops → pairs →
  generate → download → reset → 404 final

### Changed

- `app/api/v1/routes_sessions.py::state_to_response`:
  - Incluye `pairs` ordenados por position
  - Incluye `can_generate_pdf` y `imbalance_message`
- `app/main.py`: registra el router de matcheo

### Decisiones técnicas registradas

- **API declarativa para `PUT /pairs`**, no incrementos.
  El browser tras un drag-and-drop manda el estado completo de pares.
  Justificación: simplifica enormemente la lógica del frontend (un
  reorder = un PUT con la lista nueva, sin lógica de "mover de N a M"),
  y el cliente de validación server-side es el mismo siempre. El
  trade-off es payload más grande, pero para 2-15 pares es trivial.

- **`can_generate_pdf` valida en propiedad, no en endpoint**.
  La verificación es una property de `SessionState`, así está
  disponible en la respuesta del estado para que la UI muestre el botón
  habilitado o no SIN tener que hacer una llamada adicional.

- **`origin` se preserva en updates de pares**.
  Si el usuario reordena pares pero mantiene los mismos pares, el
  `PairOrigin.OCR_EXACT` no se pierde (lo conservamos por la clave
  `(frente_id, dorso_id)`). Esto permite al frontend mostrar "match por
  OCR" incluso después de que el usuario haya reordenado.

- **No hay endpoint DELETE por par individual.**
  Para deshacer un par, se manda PUT /pairs con la lista nueva sin
  ese par. Es coherente con la API declarativa.

- **`compose_pdf` se invoca con listas vacías de huérfanos**.
  La validación en `can_generate_pdf` garantiza que llegamos al
  composer sin huérfanos. La firma de `compose_pdf` igual los acepta
  como parámetros (compatibilidad), pero las listas siempre van
  vacías.

### Cómo validar la API antes de Sprint 3b

```bash
unzip dni_processor_v0.3.1a.zip
cd dni_processor_v0.3.1a
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q   # 134 passed
python -m app.main
```

Flujo completo con `curl`:

```bash
# 1. Crear sesión
SID=$(curl -sX POST http://localhost:8001/api/v1/sessions | jq -r .session_id)

# 2. Subir frentes y dorsos
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/images" \
  -F "side=frente" -F "files=@frente1.jpg" -F "files=@frente2.jpg"
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/images" \
  -F "side=dorso" -F "files=@dorso1.jpg" -F "files=@dorso2.jpg"

# 3. Procesar frentes
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/process"

# 4. (En cada crop pendiente, hacer POST a /confirm con el bbox final)

# 5. Generar sugerencias por OCR
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/match" | jq

# 6. Confirmar/reordenar pares (declarativo)
curl -X PUT "http://localhost:8001/api/v1/sessions/$SID/pairs" \
  -H "Content-Type: application/json" \
  -d '{"pairs": [
        {"frente_crop_id": "F1", "dorso_crop_id": "D1", "position": 0},
        {"frente_crop_id": "F2", "dorso_crop_id": "D2", "position": 1}
      ]}'

# 7. Ver estado (can_generate_pdf debería ser True)
curl "http://localhost:8001/api/v1/sessions/$SID" | jq .can_generate_pdf

# 8. Generar PDF
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/generate-pdf" | jq

# 9. Descargar
curl "http://localhost:8001/api/v1/sessions/$SID/output.pdf" -o output.pdf

# 10. Reset (empezar otro trámite)
curl -X POST "http://localhost:8001/api/v1/sessions/$SID/reset" | jq
```

### Próximos pasos

- **Sprint 3b** (v0.3.1b): UI de matcheo + post-PDF
  - Nueva ruta web `GET /sessions/{id}/match`
  - Layout dos columnas (frentes / dorsos) con drag-and-drop
  - Pre-carga automática de sugerencias OCR al entrar
  - Badge de origen del par (OCR exacto / OCR aproximado / manual)
  - Display del número de OCR debajo de cada card (con label
    "leído por OCR — verificá")
  - Botón "Volver a revisar" → `/review`
  - Botón "Generar PDF" (deshabilitado con tooltip mostrando
    `imbalance_message` si aplica)
  - Pantalla post-PDF: preview embebido + botón "Descargar PDF" +
    botón "Empezar otro trámite" (llama `/reset` y redirige a `/`)

- **Sprint 4** (v0.3.2): hardening + deployment
  - systemd unit
  - Pre-descarga del modelo de caras en deployment
  - Rate limiting (slowapi)
  - Security headers
  - Tailscale ACL para integración con Escriba
  - Migración de warnings de `datetime.utcnow()` deprecated

---

## [0.3.0b.2] — 2026-05-29

**Patch sobre v0.3.0b.1 — Footer de estado y cierre del flujo de revisión.**

Patch que arregla el último UX gap detectado: el usuario no tenía cómo
saber cuándo "terminaba" con las imágenes manuales. La sección "Marcar
manualmente" no tenía estado de cierre, y el botón "Continuar al matcheo"
en el footer no daba feedback sobre qué faltaba específicamente.

**Decisión de diseño**: optamos por el modelo "cada recorte agregado es
definitivo" (Modelo A propuesto), donde:
- Cada click en "Agregar recorte" confirma ese rectángulo al instante
- Si el usuario se equivoca, descarta el thumbnail con el botón ✕
- El sistema se considera "listo" cuando cada imagen manual tiene al
  menos un recorte confirmado, sin requerir un botón explícito por card

**112 tests pasando** (sin cambios en suite — el cambio es puramente
template/CSS).

### Added

#### Footer con estado del flujo

El footer del partial ahora muestra el estado real del trabajo
pendiente, calculado a partir de:
- `n_pending_auto`: frentes auto-detectados aún sin confirmar
- `n_images_unmarked`: imágenes manuales sin NINGÚN recorte confirmado

**Estados visibles:**

- **Sin recortes todavía**: badge "Pendiente" + texto "Sin recortes
  todavía". Botón deshabilitado.
- **Faltan recortes manuales**: "Falta marcar al menos un recorte en N
  imágenes". Plurales correctos con tildes (1 imagen / N imágenes,
  1 frente / N frentes).
- **Faltan confirmar frentes**: "Falta confirmar N frentes detectados
  automáticamente"
- **Casos mixtos**: "Falta confirmar N frentes y marcar M imágenes"
- **Todo listo**: badge verde "✓ Todo listo" + texto "N recortes
  confirmados — pasá al matcheo cuando quieras". Botón habilitado
  (con tooltip "Disponible en Sprint 3" porque aún no implementado).

### Fixed

#### Bug: scope de variables en loops anidados de Jinja2

La lógica para detectar "imágenes sin marcas" usaba `{% set
img_has_marks = true %}` dentro de un loop anidado, pero las
asignaciones en `{% set %}` no escapan al scope del loop exterior en
Jinja2. Resultado: la variable siempre quedaba en `false`, y el badge
"Todo listo" nunca se mostraba aunque todas las imágenes tuvieran
recortes.

**Fix**: usar `{% set ns = namespace(has_marks=false) %}` que sí
permite mutación cross-scope. Patrón documentado en
[Jinja2 templates - Assignments](https://jinja.palletsprojects.com/en/stable/templates/#assignments).

#### Bug: `selectattr` con acceso a `.value` de enum

`{{ crops | selectattr('status.value', 'equalto', 'confirmed') }}` no
funciona porque selectattr no soporta acceso a atributos anidados con
notación de punto. Reemplazado por loop manual con namespace.

### Changed

- **`app/web/templates/partials/review_content.html`**:
  - Nuevo bloque `images_without_marks` que detecta imágenes manuales
    sin recortes
  - Nuevo cálculo `n_confirmed_total` con namespace
  - Nuevo flag `everything_ready` que combina ambos contadores
  - Footer reescrito con dos estados visuales claros
  - Plurales correctos con tildes en español
- **`app/web/static/css/main.css`**:
  - Nuevos estilos `.footer-bar`, `.footer-bar__status`,
    `.footer-bar__status-badge`, `.footer-bar__status-badge--ready`,
    `.footer-bar__status-badge--waiting`, `.footer-bar__status-text`

### Decisiones técnicas registradas

- **No agregamos botón "Terminar imagen" por card**. Esa fue una de
  las opciones discutidas. Lo descartamos porque introduciría estado
  adicional sin valor real: si una imagen tiene 1 recorte y el usuario
  no agrega más, semánticamente ya terminó con ella. El indicador
  visual (borde verde + badge) + el contador del footer ya transmiten
  el progreso. Si en uso real aparece confusión, lo reevaluamos.

- **Botón "Continuar al matcheo" sigue deshabilitado incluso cuando
  todo está listo**. Esto es porque la pantalla de matcheo es Sprint 3.
  El tooltip lo explica. Cuando se implemente Sprint 3, el `disabled`
  desaparece y el botón redirige a la URL correspondiente.

---

## [0.3.0b.1] — 2026-05-29

**Patch sobre Sprint 2b — UX fixes en el flujo de recorte manual.**

Patch que arregla dos gaps de UX detectados al usar la versión 0.3.0b en
producción local:

1. **Feedback visual ausente al marcar recortes manuales** sobre dorsos
   (e imágenes que fallaron detección). El backend recibía y guardaba
   correctamente, pero el usuario no tenía forma de saber qué imágenes ya
   estaban marcadas y cuáles seguían pendientes.

2. **Rotación 90° sin feedback visual**. El botón disparaba la rotación
   server-side al confirmar, pero la imagen en pantalla no cambiaba al
   hacer click — el usuario no podía verificar la rotación antes de
   confirmar.

**112 tests pasando** (92 de v0.3.0b + 20 nuevos de orden rotate-then-crop).

### Fixed

#### Feedback visual para recortes manuales

- Cada card de imagen pendiente ahora muestra una **mini-galería** con
  los recortes ya hechos sobre esa imagen específica. Cada thumbnail
  tiene un botón ✕ para descartar individualmente.
- Las cards con al menos un recorte marcado se diferencian visualmente
  de las "vírgenes" (clase `crop-card--has-marks`): borde verde
  izquierdo + fondo levemente teñido.
- El badge de estado cambia: "sin marcar" (warning) → "✓ N marcado/s"
  (success).
- El botón principal cambia de label: "Marcar recorte" (primer marcado)
  → "Agregar otro recorte" (siguientes).
- La sección "Confirmados" del final ahora **excluye** los recortes
  manuales (que ya aparecen en su card de imagen) — evita duplicación.

#### Rotación con feedback visual

- El click en "Rotar 90°" ahora invoca `cropper.rotate(90)` que rota
  la imagen visualmente en el browser. El comentario anterior que
  decía "no rotamos la preview" era una mala decisión que arrastrábamos.
- El botón muestra el estado de rotación acumulada:
  `Rotar 90°` (default) → `↻ 90°` → `↻ 180°` → `↻ 270°` → `Rotar 90°`
- El botón con rotación activa tiene estilo distintivo (acento ocre,
  clase `button--rotate-active`).
- El estado de rotación se limpia al refresh de HTMX porque los
  croppers se re-inicializan desde cero (consistencia con el DOM
  recargado).

### Changed

#### Orden de operaciones server-side: rotate-then-crop

Antes el flujo era: cargar imagen → recortar con bbox → rotar el recorte.
Esto funcionaba cuando la rotación era solo conceptual. Pero ahora que
Cropper.js rota la imagen visualmente, `cropper.getData()` devuelve
coordenadas en el espacio de la imagen YA ROTADA. Si el backend recortara
primero y rotara después, el resultado quedaría desalineado.

**Nuevo orden** en `apply_final_crop()`:
1. Cargar imagen original
2. PRIMERO: rotar la imagen completa al ángulo indicado
3. DESPUÉS: aplicar el bbox sobre la imagen ya rotada

Esto coincide exactamente con el comportamiento del frontend.

#### Otros

- `app/web/static/css/main.css`:
  - Nuevos estilos `.crop-card--has-marks` (borde verde izquierdo)
  - Nuevos estilos `.crop-card__marks`, `.crop-card__marks-label`,
    `.crop-card__marks-grid` para la mini-galería
  - Nuevos estilos `.mark-thumb`, `.mark-thumb__delete`
  - Nuevo estilo `.button--rotate-active`
- `app/web/static/js/review.js`:
  - `handleRotate()` reescrita: invoca `cropper.rotate(90)`,
    actualiza el botón
  - Nueva función `updateRotateButton()` para sincronizar label/estilo
  - `destroyAllCroppers()` ahora limpia el Map de rotations
- `app/web/templates/partials/review_content.html`:
  - Mini-galería de marks renderizada cuando `has_crops`
  - Clase `crop-card--has-marks` aplicada condicionalmente
  - Sección "Confirmados" excluye crops cuya source_image está en
    `images_needing_manual` para evitar duplicación

### Added

#### Tests

`tests/unit/test_crop_adjustments.py` — 20 nuevos tests:
- `TestRotationOrder` (4 tests): valida el orden rotate-then-crop con
  una imagen de prueba con franjas de color rojo/azul:
  - Sin rotación, bbox sobre la franja roja → resultado rojo
  - Rotación 90°, bbox sobre el top → resultado rojo (franja izquierda
    pasó a ser top)
  - Rotación 180°, bbox sobre el lado derecho → resultado rojo
  - Rotación inválida (45°) → ValueError
- `TestNormalizeRotation` (16 tests parametrizados): snap de ángulos
  arbitrarios al múltiplo de 90 más cercano (incluyendo negativos y
  > 360)

### Conocido — sigue pendiente

- Polling de progreso durante procesamiento (sigue siendo overlay
  simple).
- Animación cuando un crop pasa de "pending" a "confirmed" (lo dejaste
  como feature posterior).
- Las warnings de `datetime.utcnow()` deprecated (Pydantic interno, no
  bloquea — para limpiar en sprint de hardening).

---

## [0.3.0b] — 2026-05-29

**Sprint 2b — Interfaz web completa con Cropper.js + HTMX.**

Cierra el alcance del Sprint 2 con la capa de UI sobre el backend de
Sprint 2a. El flujo completo upload → revisión asistida ya es operable
desde el browser, sin necesidad de `curl`. Faltan solo las pantallas de
matcheo y generación final del PDF (Sprint 3).

**92 tests pasando** (87 del Sprint 2a + 10 nuevos de la capa web).

### Decisión estética registrada

El proyecto adopta una identidad visual **editorial / utilitaria sobria**:

- Tipografía con personalidad: Fraunces (display italic) + IBM Plex Sans
  (body) + IBM Plex Mono (datos técnicos)
- Paleta cálida con acento ocre (#b8531a) en vez de los celestes/azules
  típicos de software corporativo
- Layout limpio, generoso en espacio negativo
- Sin emojis, sin íconos genéricos: detalles tipográficos y separadores
  como vocabulario visual
- Tono profesional acorde a contexto notarial

### Added

#### Router web (`app/web/routes.py`)
- `GET /` — pantalla de upload (home)
- `GET /sessions/{id}/review` — pantalla de revisión asistida
- `GET /sessions/{id}/review/partial` — partial HTML para HTMX swaps
- Configuración de Jinja2 con `finalize` que serializa enums a su `.value`
  automáticamente al renderizar (evita ver "DNISide.DORSO" en lugar de
  "dorso" en el HTML resultante)

#### Templates Jinja2

**`base.html`** — layout compartido:
- Carga de fuentes Google (Fraunces + IBM Plex Sans + IBM Plex Mono)
- Carga de Cropper.js 1.6.1 vía CDN
- Carga de HTMX 1.9.10 vía CDN
- Header con monograma "DNI" + tagline "organizador notarial · auto-alojado"
- Footer con versión

**`upload.html`** — pantalla de upload:
- Dos zonas drag-and-drop separadas (Frentes / Dorsos)
- Contador de archivos por zona
- Botón "Procesar" deshabilitado hasta tener al menos un archivo
- Overlay de progreso durante upload + procesamiento

**`review.html`** — pantalla de revisión:
- Header con botón "Recomenzar" (descarta sesión completa)
- Contenedor refrescable vía HTMX (`hx-trigger="refreshReview from:body"`)
- Incluye partial de contenido

**`partials/review_content.html`** — contenido refrescable:
- Stats bar con conteo agregado (imágenes, recortes, confirmados,
  pendientes, sin detectar)
- Sección "Frentes detectados" con Cropper.js sobre cada wide_crop,
  rectángulo pre-cargado en `suggested_bbox` (calibrado por el usuario
  en Sprint 1)
- Sección "Marcar manualmente" con Cropper.js sobre imagen normalizada,
  para dorsos y frentes que fallaron detección. Permite múltiples
  recortes por imagen (botón "Agregar recorte")
- Sección "Confirmados" con preview del recorte final
- Footer con botón "Continuar al matcheo" (deshabilitado hasta Sprint 3)

#### CSS (`app/web/static/css/main.css`)
- ~480 líneas de CSS custom, sin frameworks
- Variables CSS organizadas por categoría (paleta, tipografía, spacing, layout)
- Componentes: masthead, page, drop-zone, button, crop-card, stats-bar,
  toast, overlay, processing spinner
- Override sutil de Cropper.js para alinearlo a la paleta (acentos ocre)
- Estados de crop-card: pending (amarillo cálido) / confirmed (verde sobrio)

#### JavaScript

**`app/web/static/js/upload.js`** — pantalla de upload:
- Validación client-side de tipos MIME y tamaños (15MB por archivo)
- Drag-and-drop con feedback visual (clase `--dragover`)
- Submit orquesta el flujo completo: crear sesión → subir frentes →
  subir dorsos → disparar `/process` → redirigir a `/review`
- Overlay de progreso con mensajes intermedios
- Toasts efímeros para errores
- Subida en batch (un POST con múltiples files por side, no uno por uno)

**`app/web/static/js/review.js`** — pantalla de revisión:
- Inicialización de Cropper.js en dos modos:
  - `auto-crop`: rectángulo pre-cargado en `suggested_bbox` (acción:
    confirmar con bbox ajustado)
  - `manual-crop`: rectángulo dibujado desde cero por el usuario
    (acción: agregar como nuevo crop)
- Event delegation para todas las acciones (confirmar, descartar,
  rotar, agregar manual, descartar sesión)
- Rotación acumulada en estado local del cliente (0/90/180/270);
  se aplica server-side al confirmar
- Destrucción y re-inicialización de croppers después de cada swap HTMX
  (evita memory leaks)
- Listener de `htmx:afterSwap` que re-inicializa croppers en el
  contenido refrescado

### Changed

- `app/main.py`:
  - Monta el router web
  - Monta `/static` con `StaticFiles` apuntando a `app/web/static/`
- `requirements.txt`:
  - Agregado `jinja2>=3.1,<4.0` para templates HTML
- `app/web/templates/partials/review_content.html`:
  - Comparaciones de enum reemplazadas por `.value` explícito en todos
    los `selectattr` y `if` para evitar bugs cuando se compara enum vs
    string literal

### Tests

**`tests/integration/test_web.py`** — 10 tests nuevos:
- Home renderiza con drop-zones para frentes y dorsos
- Static files (CSS, upload.js, review.js) se sirven correctamente
- Review renderiza con session_id inyectado como data attribute
- Review devuelve 404 para sesión inexistente
- Partial NO incluye `<html>` (es solo content, no layout)
- Partial renderiza correctamente con imágenes subidas
- Flujo end-to-end desde browser muestra dorsos en sección "Marcar manualmente"

### Bug fixes en este sprint

- **Comparaciones de enum en Jinja**: el template original usaba
  `selectattr('side', 'equalto', 'frente')` que fallaba porque
  `side` es `DNISide.FRENTE` (enum) y `'frente'` es string. Resuelto
  comparando `.value` explícitamente.
- **Serialización de enum a HTML attribute**: `data-side="{{ side }}"`
  producía `data-side="DNISide.DORSO"`. Resuelto con `templates.env.finalize`
  que convierte automáticamente Enum → .value al renderizar.
- **datetime cleanup**: comparación entre datetime aware/naive que
  rompía el background task de cleanup. Resuelto normalizando ambos
  a tz-aware al comparar.
- **TemplateResponse signature**: Starlette nuevo requiere `request`
  como primer parámetro positional, no en el context dict. Actualizado
  en los tres endpoints web.

### Cómo probarlo

```bash
unzip dni_processor_v0.3.0b.zip
cd dni_processor_v0.3.0b
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q  # 92 passed

# Levantar el servidor
python -m app.main
# Browser → http://127.0.0.1:8001/
```

Para uso vía Tailscale, exponer en `0.0.0.0`:

```bash
DNI_HOST=0.0.0.0 python -m app.main
```

### Próximos pasos

- **Sprint 3** (v0.3.1): pantalla de matcheo + generación final del PDF
  - Endpoint `POST /api/v1/sessions/{id}/match` (sugerencias por OCR)
  - Endpoint `POST /api/v1/sessions/{id}/generate-pdf`
  - Pantalla de matcheo con drag-and-drop
  - Botón final de descarga
- **Sprint 4** (v0.3.2): hardening + deployment
  - systemd unit
  - Pre-descarga del modelo de caras en deployment
  - Rate limiting (slowapi)
  - Security headers
  - Tailscale ACL para integración con Escriba

### Conocido — pendiente de mejora

- Las comparaciones `.value` en Jinja son verbosas. Si se ven más
  patrones similares en Sprint 3, evaluar custom Jinja test que
  abstraiga la comparación.
- El polling de progreso durante procesamiento es básico (solo overlay).
  Si en producción se ven sesiones que tardan >30s, agregar SSE o
  long-polling con percentages.
- Cropper.js no es responsive en touch (no tiene sentido para este
  sprint porque confirmaste solo desktop).

---

## [0.3.0a] — 2026-05-29

**Sprint 2a — Backend FastAPI con flujo de recorte asistido.**

Entrega que cubre TODA la API REST del flujo asistido (upload, procesamiento,
recortes manuales y automáticos, confirmación). Sin UI HTMX todavía: eso es
Sprint 2b. La API ya está validable vía `curl` o cualquier cliente HTTP.

**82 tests pasando** (64 unit + 18 integration de API).

### Decisión clave: ratios amplios + bbox sugerido

Tras la validación visual de Sprint 1 que mostró recortes "algunos sobrados
otros faltantes" con DNIs idénticos, se confirmó que el bbox del detector
facial es inherentemente variable. La decisión:

- Los ratios `DNI_EXTEND_*` ahora son **deliberadamente amplios** (left=1.5,
  right=8.0, top=2.0, bottom=2.0) para garantizar que el DNI completo quede
  siempre dentro del recorte.
- Se agregaron nuevos ratios `SUGGESTED_BBOX_*` (left=0.6, right=5.5, top=1.3,
  bottom=1.1) que indican **dónde está el DNI dentro del recorte amplio**
  según las mediciones que el usuario hizo sobre DNIs reales.
- El usuario verá el recorte amplio con un rectángulo de ajuste pre-cargado
  (Sprint 2b) en la posición sugerida, y podrá arrastrar para corregirlo.

### Added

#### Configuración (`app/config.py`)
- `Settings` clase basada en Pydantic Settings
- Variables soportadas: `DNI_HOST`, `DNI_PORT`, `DNI_DATA_DIR`,
  `DNI_SESSIONS_DIR`, `DNI_MODEL_CACHE_DIR`, `DNI_LOG_LEVEL`, `DNI_RUN_OCR`
- Singleton lazy con `get_settings()` y `reset_settings()` (este último
  para tests)
- Soporta `.env` file via Pydantic Settings

#### Gestión de sesiones (`app/core/sessions.py`)
- `create_session()` — crea sesión con UUID y working directory completo
- `load_session()` — carga estado desde `session.json` con manejo robusto
  de errores
- `save_session()` — persistencia atómica (escribe a `.tmp` y renombra)
- `update_session()` — load + modify + save en una operación
- `cleanup_expired_sessions()` — borra sesiones con `updated_at` más
  antiguo que TTL (default 24h, configurable via `SESSION_TTL_HOURS`)
- `discard_session()` — borrado manual (botón "Recomenzar")
- `SessionPaths` — helper para resolver paths del working dir
  (originals, wide_crops, final_crops, output.pdf)

Estructura por sesión:
```
sessions/<uuid>/
├── session.json
├── originals/<image_id>.jpg
├── crops/wide/<crop_id>.jpg
├── crops/final/<crop_id>.jpg
└── output.pdf  (al completar matcheo, Sprint 3)
```

#### Schemas de UI (`app/schemas/web.py`)
- `SessionStatus` enum: created / uploading / processing / review /
  ready_for_match / completed / failed
- `CropStatus` enum: pending / confirmed / discarded
- `ImageStatus` enum: uploaded / processing / detected /
  failed_detection / error
- `CropState` — estado de cada recorte (suggested_bbox, final_bbox,
  rotation_degrees, paths)
- `ImageState` — estado de cada imagen subida (detection_strategy,
  crop_ids, error_message)
- `SessionState` — estado completo serializable con properties derivadas
  (`all_crops_confirmed`, `pending_crops`, `images_failed_detection`)

#### Schemas de API REST (`app/schemas/api.py`)
- Request/response separados de los modelos de dominio
- `SessionCreateResponse`, `SessionStateResponse` con métricas derivadas
- `UploadImagesResponse` con uploaded[] y skipped[]
- `ProcessResponse`, `ConfirmCropRequest/Response`,
  `CreateManualCropRequest/Response`

#### Routers FastAPI

**`app/api/v1/routes_sessions.py`** — Gestión de sesiones:
- `POST /api/v1/sessions` — crear sesión
- `GET /api/v1/sessions/{id}` — estado completo
- `DELETE /api/v1/sessions/{id}` — descartar

**`app/api/v1/routes_images.py`** — Upload y servido de archivos:
- `POST /api/v1/sessions/{id}/images` — upload multipart con normalización
  EXIF automática (Pillow + ImageOps.exif_transpose → re-encode JPEG)
- `GET /api/v1/sessions/{id}/images/{image_id}` — sirve imagen normalizada
- `GET /api/v1/sessions/{id}/crops/{crop_id}/wide` — sirve recorte amplio
- `GET /api/v1/sessions/{id}/crops/{crop_id}/final` — sirve recorte final
- Validación path traversal con `_safe_path_under()` (resuelve paths y
  verifica que estén dentro del working dir)

**`app/api/v1/routes_processing.py`** — Procesamiento y crops:
- `POST /api/v1/sessions/{id}/process` — dispara detección automática
  sobre frentes UPLOADED. Los dorsos NO se procesan automáticamente.
  Síncrono (~5-10s para sesiones típicas de 2-15 DNIs).
- `POST /api/v1/sessions/{id}/crops/{crop_id}/confirm` — recibe bbox
  ajustado + rotación, genera recorte final
- `POST /api/v1/sessions/{id}/images/{image_id}/crops` — crea recorte
  manual sobre imagen normalizada (para dorsos y frentes fallidos)
- `DELETE /api/v1/sessions/{id}/crops/{crop_id}` — descarta crop

#### Ajustes finos sobre recortes (`app/core/crop_adjustments.py`)
- `apply_final_crop()` — aplica bbox + rotación al recorte amplio
- Validación estricta: solo rotaciones de 0/90/180/270 grados.
  Cualquier otro valor lanza ValueError (preservación de integridad
  documental — sin interpolación).
- `normalize_rotation()` — utility para snappear ángulos arbitrarios al
  valor permitido más cercano

#### App principal (`app/main.py`)
- Factory `create_app()` reusable por tests
- Lifespan hook que arranca background task de cleanup periódico
- Endpoint `/api/v1/health` para health checks
- `/docs` y `/redoc` automáticos vía FastAPI

#### Geometría extendida (`app/core/geometry.py`)
- `compute_suggested_bbox_within_crop()` — calcula coordenadas del bbox
  sugerido en el espacio del recorte amplio (coords relativas para
  Cropper.js)
- Fallback robusto si el cálculo da un bbox vacío (centro del recorte)

#### Tests de integración (`tests/integration/test_api.py`)
- **18 tests** cubriendo:
  - Health endpoint
  - CRUD de sesiones (incluyendo 404s)
  - Upload de imágenes (single, multiple, validación de side,
    extensiones inválidas, sesión inexistente)
  - Servido de archivos
  - Recortes manuales (incluyendo múltiples por imagen)
  - Validación de rotaciones (rechaza 45°, acepta 0/90/180/270)
  - Lifecycle completo end-to-end con manual crops only
- Fixture `isolated_sessions_dir` aísla cada test en su propio tmpdir
- Fixture `sample_image_bytes` genera JPGs válidos sin DNI real
- No requieren descarga del modelo de caras (los tests del modelo real
  se ejecutan localmente con detect_frentes.py)

### Changed

- `app/core/constants.py`:
  - Ratios `DNI_EXTEND_*` reescritos como "amplios" (1.5 / 8.0 / 2.0 / 2.0)
  - Nuevos `SUGGESTED_BBOX_*` con valores medidos por el usuario sobre
    DNIs reales
  - Agregadas constantes de sesión: `SESSION_TTL_HOURS = 24`,
    `CLEANUP_INTERVAL_MINUTES = 60`
- `app/core/vision.py::extract_frentes_from_image`:
  - Ahora calcula y devuelve TANTO el bbox amplio como el `suggested_bbox_in_crop`
  - El `DetectedDNI` resultante tiene ambos campos poblados
- `app/schemas/session.py::DetectedDNI`:
  - Nuevo campo `suggested_bbox_in_crop: BoundingBox | None`
- `app/main.py`:
  - Pasa de ser un módulo-versión a ser el entry point FastAPI completo
- `requirements.txt`:
  - Agregado FastAPI, uvicorn, python-multipart, httpx (tests)

### Decisiones técnicas registradas

- **Normalización EXIF al subir**, no en cada lectura. Decisión validada
  por usuario en conversación previa. Garantiza que browser y backend
  vean exactamente la misma orientación. Se pierde el archivo original
  (que tenía EXIF), pero no necesitamos preservarlo: el archivo
  normalizado es la versión canónica para todo el flujo.

- **Procesamiento síncrono** en `POST /process`. Para sesiones típicas
  (2-15 DNIs) tarda 5-10 segundos, aceptable bloqueando. Si en el
  futuro hacen falta sesiones más grandes, migrar a background task
  con polling.

- **Path traversal validation** con `_safe_path_under()` en todos los
  endpoints que sirven archivos. Validación defensiva aunque los
  parámetros vengan de path params (no de query strings).

- **Persistencia atómica** con write-to-tmp + rename. Evita corrupción
  del JSON si el proceso muere a la mitad de una escritura. Pattern
  común en sistemas que manejan estado sin base de datos.

- **Sin DB todavía**. Confirma decisión del roadmap: el estado vive en
  disco como JSON con TTL de 24h. Si en el futuro necesitamos
  auditoría notarial persistente, se agrega SQLite + Alembic.

### Próximos pasos

- **Sprint 2b**: Templates Jinja2 + Cropper.js + integración HTMX
  - Pantalla de upload con dos zonas drag-and-drop
  - Pantalla de procesamiento con polling de progreso
  - Pantalla de revisión con Cropper.js sobre cada recorte
  - Botones de rotación 90° por crop
  - Soporte para múltiples recortes en una imagen (modo "agregar otro")
- **Sprint 3**: Matcheo asistido + generación final del PDF
  - Pantalla de matcheo con drag-and-drop sobre pares pre-sugeridos
    por OCR
  - Generación del PDF con el composer existente
  - Endpoint `POST /api/v1/sessions/{id}/match` y `POST /generate-pdf`

### Cómo correr el backend

```bash
unzip dni_processor_v0.3.0a.zip
cd dni_processor_v0.3.0a
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q  # debería dar 82 passed
python -m app.main  # arranca el servidor en 127.0.0.1:8001
# Browser: http://127.0.0.1:8001/docs para Swagger UI
```

---

## [0.2.1] — 2026-05-28

**Sprint 1 de Fase 2.1 — Pivote arquitectónico: detección por caras.**

Esta versión refleja un cambio profundo de estrategia tras las pruebas
empíricas con el set real del usuario. El pipeline anterior basado en
Canny + contornos daba 23% de matcheo final y el sweep de parámetros
confirmó que era estructuralmente irrecuperable (todas las combinaciones
techaban en 53.8% de detección). Se reemplaza completamente por un
pipeline basado en detección facial pre-entrenada.

Validado empíricamente: **100% de detección sobre el set real de 18
imágenes de frentes** (14 con imagen original post-EXIF, 4 con fallback
de rotación).

### Added

#### Nuevo módulo `app/core/geometry.py`
Cálculo determinístico del bbox del DNI completo a partir del bbox de
la cara detectada. Usa ratios fijos calibrados sobre el DNI argentino
moderno (posición conocida de la foto del titular en la columna
izquierda). Trunca a bordes de imagen y tiene fallback al bbox de la
cara en casos patológicos. 8 tests unitarios cubren todos los casos
límite.

#### Nuevo script `scripts/detect_frentes.py`
CLI de Sprint 1 que produce los recortes de DNIs detectados para
inspección visual local. Permite al usuario validar que el cálculo
geométrico está bien calibrado antes de avanzar a la UI web. Genera:
- Recortes con naming inspeccionable (`img_<hash>_face<N>.jpg`)
- Reporte JSON compartible con IDs opacos, métricas agregadas y
  distribución de estrategias usadas (`original` / `clahe` /
  `rotated_*`)
- Output en consola con tabla resumen y guía de pasos siguientes

#### Constantes nuevas en `app/core/constants.py`
- `FACE_CONFIDENCE_THRESHOLD = 0.3` (más permisivo que el default 0.5
  para recuperar caras chicas dentro del frame)
- `NMS_THRESHOLD = 0.3` para deduplicación de detecciones
- `FACE_MODEL_INPUT_SIZE = (300, 300)` para el modelo ResNet-10 SSD
- `CLAHE_CLIP_LIMIT = 3.0` y `CLAHE_TILE_GRID_SIZE = (8, 8)` para
  preprocesamiento de contraste
- `DNI_EXTEND_LEFT_RATIO`, `RIGHT_RATIO`, `TOP_RATIO`, `BOTTOM_RATIO`
  para el cálculo geométrico del bbox del DNI desde la cara
- `BBOX_PADDING_PX = 30` (subió de 20px en v0.2.0 para preservar más
  fondo perimetral — requisito de integridad documental)

#### Tests
- `tests/unit/test_geometry.py` — 8 tests cubriendo cálculo típico,
  truncamientos en bordes, ratios custom, casos patológicos y
  validación de outputs
- `tests/unit/test_vision.py` — reescrito completo con tests para:
  - Carga con EXIF respetado (incluyendo test que escribe EXIF
    orientation=6 y verifica que las dimensiones se transponen)
  - CLAHE preserva forma y modifica contraste
  - Recorte con padding (preservación de contenido, clamping en bordes)
- 64 tests totales pasando en 0.84s

### Changed

#### Módulo `app/core/vision.py` reescrito completo
- Eliminadas todas las funciones de detección por contornos
  (`detect_dni_bboxes`, `_find_candidate_contours`,
  `_is_valid_dni_contour`, `_deduplicate_bboxes`, `_iou`).
- Nueva función `load_image_exif_aware()` que usa Pillow +
  `ImageOps.exif_transpose()` para rotar físicamente la imagen
  según metadata EXIF antes de pasarla a OpenCV. Resuelve el caso
  documentado del Redmi Note 8 Pro con "Rotada 90° antihorario".
- Nueva función `apply_clahe()` para mejorar contraste local sobre
  el canal V de HSV.
- Nueva función `detect_faces_with_fallbacks()` que implementa la
  cascada: original → CLAHE → 90° CCW → 90° CW → 180°. Devuelve un
  `DetectionResult` con la imagen procesada, las caras detectadas,
  y la estrategia que rescató.
- Nueva función `extract_frentes_from_image()` como entrada de alto
  nivel del pipeline de frentes.
- Singleton del net de OpenCV con descarga lazy del modelo
  (`get_face_net()`).

#### Módulo `app/core/pipeline.py` reescrito
- `process_frente_images()` — detección automática + OCR opcional
- `process_dorso_crops()` — procesa recortes de dorsos ya producidos
  (en Sprint 2 esto vendrá de la UI de recorte manual; los dorsos
  ya no se detectan automáticamente porque pyzbar dio 5.6% en
  pruebas empíricas, irrecuperable)
- `process_batch_assisted()` — orquestador completo modo CLI
- Estadísticas de estrategias usadas en el output

#### Versionado
- `app/main.py` — bump a `0.2.1`

### Removed

- `scripts/process_batch.py` — el CLI de v0.1.0 ya no aplica al nuevo
  flujo asistido
- `scripts/calibrate.py` — el script de calibración Fase 2 quedó
  obsoleto tras el pivote (sus métricas de Canny no aplican)
- `docs/CALIBRACION.md` — reemplazado por nueva documentación al
  cierre del Sprint 1

### Decisiones técnicas registradas

- **Detección por caras + geometría fija** en lugar de:
  - Modelo de objetos genérico (YOLO): más pesado, sin garantía de
    mejora respecto al detector facial maduro de OpenCV
  - Fine-tuning con set propio: requería anotación manual del usuario
  - Recorte 100% manual: descartado tras conseguir 100% automático en
    el probe v2

- **EXIF respetado al cargar, siempre**. No hay flag para desactivarlo.
  Razón: ningún caso de uso requiere ver la imagen "como la guardó el
  sensor"; el detector trabaja siempre mejor con la imagen en
  orientación visual correcta.

- **Cascada de fallbacks como parte del pipeline normal, no opcional**.
  Razón: el probe v2 mostró que 4/18 imágenes (22%) sin EXIF correcto
  se rescataron por rotación; eliminar el fallback bajaría la
  detección a 78%.

- **OCR pasa a ser sugerencia, no decisión**. El matcheo final se
  confirma en la UI por el usuario (Sprint 3). En este sprint el OCR
  sigue corriendo pero el contrato del pipeline ya distingue
  "matcheo automático sugerido" de "matcheo confirmado".

- **Dorsos no se detectan automáticamente.** El usuario recortará
  manualmente en la UI (Sprint 2). Razón: pyzbar dio 5.6% en pruebas.

### Próximos pasos

- **Validación local Sprint 1**: usuario corre `detect_frentes.py`
  con su set real y revisa visualmente los recortes. Si los ratios
  geométricos producen recortes cortados o muy sobrados, ajustar
  `DNI_EXTEND_*_RATIO` en `constants.py` y re-ejecutar.
- **Sprint 2**: capa web con FastAPI + Jinja2 + HTMX + Cropper.js.
  UI de upload, procesamiento async, pantalla de revisión con
  editor de recorte para dorsos e imágenes fallidas.
- **Sprint 3**: pantalla de matcheo asistido (drag-and-drop sobre
  pares pre-sugeridos por OCR) y generación final del PDF.

---

## [0.2.0-dev] — 2026-05-27

**Fase 2 en curso — Herramientas de calibración local.**

Esta versión agrega el instrumental necesario para calibrar el pipeline
contra imágenes reales **sin que esas imágenes salgan de la máquina del
usuario**. Decisión motivada por requisitos de privacidad notarial: las
fotos de DNIs reales no deben transitar por servicios externos.

### Added

#### Script `scripts/calibrate.py`

Nuevo CLI con dos subcomandos para evaluación local del pipeline.

- **`sweep`** — Barrido de parámetros de detección.
  - Recorre combinaciones cartesianas de `canny_low × canny_high ×
    aspect_tolerance × min_area_ratio`.
  - Por cada combinación, mide tasa de detección, DNIs detectados totales
    y tiempo.
  - Salida: CSV agregado + tabla de top 10 en consola.
  - **No usa OCR** — rápido (~5-10 min para 30 imágenes × 64 combos).
  - Monkeypatcha temporalmente las constantes del módulo `vision` para
    cada combinación sin alterar el código.
  - Salida diseñada para ser compartible: no contiene nombres de archivo
    ni números de DNI.

- **`eval`** — Evaluación completa del pipeline con parámetros indicados.
  - Corre detección + OCR + matcheo + (opcional) PDF.
  - Produce reporte dual: `.txt` legible + `.json` estructurado.
  - Métricas medidas:
    - Tasa de detección (frentes y dorsos por separado)
    - Tasa de OCR sobre detectados
    - Percentiles P50/P90 de confianza de OCR
    - Tasa de matcheo
    - Tiempos por etapa (detección, OCR, matcheo, PDF)
    - Lista de imágenes problemáticas con identificadores opacos
  - Flag `--skip-pdf` para evaluaciones rápidas solo con métricas.
  - Flag `--include-filenames` para incluir nombres reales en el reporte
    (uso local únicamente — el reporte resultante NO debe compartirse).

#### Privacidad por diseño

- **Identificadores opacos por defecto.** Las imágenes problemáticas se
  reportan con hash SHA1 truncado del nombre + tamaño (`img_a3f2b1c8`),
  no con su nombre original. El usuario puede mapear hashes a nombres
  localmente con un snippet incluido en la documentación.
- **Sin logs de números OCR.** El script no escribe números de DNI en
  archivos ni en consola. Solo cuenta cuántos números pudo extraer.
- **Reportes claramente etiquetados.** Cuando se usa `--include-filenames`,
  un warning explícito en consola indica que el reporte no debe compartirse.
- **Limpieza automática.** Los recortes intermedios se eliminan al
  terminar la evaluación.

#### Documentación

- **`docs/CALIBRACION.md`** — Guía paso a paso de uso del script:
  - Preparación del set de prueba (recomendación: 20-30 fotos)
  - Cómo correr el sweep e interpretar resultados
  - Cómo correr el eval e interpretar el reporte
  - Qué compartir con Anthropic (CSV, JSON sin filenames)
  - Qué NO compartir (imágenes, reportes con filenames)
  - Cheatsheet de comandos
  - Métricas objetivo de Fase 2

### Changed

- `app/main.py` — Bump de versión a `0.2.0-dev` (la versión `0.2.0`
  estable se libera al cierre de Fase 2, con parámetros calibrados).

### Próximos pasos

- Usuario corre `sweep` y `eval` localmente con su set real.
- Comparte JSON del eval (sin filenames) + descripción cualitativa de las
  imágenes problemáticas identificadas por sus hashes.
- Iteración: ajuste de parámetros en `constants.py` y, si hace falta,
  pre-procesamiento adicional (CLAHE para baja iluminación, ajuste de
  parámetros de EasyOCR).
- Cierre con `v0.2.0` cuando se cumplan los mínimos: detección ≥90%,
  OCR ≥85%, matcheo ≥85%, 0 falsos positivos, ≤3s por imagen.

---

## [0.1.0] — 2026-05-27

**Fase 1 completa — MVP CLI de procesamiento funcional.**

Primera versión funcional del proyecto. Pipeline completo de procesamiento
de DNIs ejecutable por línea de comandos, sin interfaz web. Establece la
arquitectura base sobre la que se construirán las fases siguientes.

### Added

#### Estructura del proyecto
- `pyproject.toml` con metadata, configuración de pytest, ruff y setuptools.
- `requirements.txt` con dependencias congeladas para Fase 1 (FastAPI excluido
  hasta Fase 3 para mantener el MVP liviano).
- `.gitignore` exhaustivo, con foco especial en **datos sensibles**:
  - Imágenes reales de DNI bajo `tests/fixtures/images/real/` (no commitear)
  - Todos los `*.pdf` generados (pueden contener datos personales)
  - Working directories de procesamiento (`data/work/`, `data/sessions/`)
  - Bases SQLite (`data/*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`)
  - Modelos de EasyOCR descargados (~500MB)
  - Archivos `.env` y configuración local
  - Logs y deliverables ZIP

#### Módulos `app/core/`
- **`constants.py`** — Constantes globales del proyecto:
  - Dimensiones físicas estándar ID-1 (`DNI_WIDTH_MM=85.60`, `DNI_HEIGHT_MM=53.98`)
  - Aspect ratio ID-1 con tolerancia ±15% para detección robusta
  - Layout A4 con cálculo de márgenes, gaps y `PAIRS_PER_PAGE=4`
  - Parámetros de Canny y blur gaussiano (valores iniciales, se calibran en Fase 2)
  - Padding perimetral de bbox (`BBOX_PADDING_PX=20`) — crítico para
    preservar fondo y demostrar integridad documental
  - Tolerancia Levenshtein de matcheo (`MATCH_MAX_DISTANCE=2`)
  - Validaciones de input (extensiones permitidas, tamaños máximos)

- **`vision.py`** — Detección y recorte de DNIs:
  - `load_image()` — Carga con soporte JPG, PNG, WebP y HEIC (vía `pillow-heif`)
    para fotos de iPhone. Soporta paths con caracteres no-ASCII (`np.fromfile`).
  - `detect_dni_bboxes()` — Pipeline Canny + cierre morfológico + filtrado por
    aspect ratio ID-1 ±15% y por área (`MIN_CONTOUR_AREA_RATIO=0.01`,
    `MAX_CONTOUR_AREA_RATIO=0.95`).
  - `_deduplicate_bboxes()` + `_iou()` — Eliminación de bboxes solapados
    (IoU > 0.5), conserva siempre el de mayor área.
  - `crop_with_padding()` — Recorte rectangular alineado con padding
    perimetral. **No realiza warp ni transformación de perspectiva.**
    Si el padding excede los bordes de la imagen, se trunca a límites
    válidos (sin rellenar artificialmente, lo cual alteraría la prueba).
  - `save_crop()` — Guardado JPEG calidad 95 con soporte de paths no-ASCII.
  - `extract_dnis_from_image()` — Función de alto nivel que orquesta
    detección, recorte y guardado, devolviendo `list[DetectedDNI]`.

- **`ocr.py`** — Extracción de número de DNI:
  - `get_reader()` — Singleton lazy del Reader de EasyOCR (modelo español,
    sin GPU). Evita recarga de modelos (~500MB) en cada llamada.
  - `DNI_PATTERN` — Regex que matchea números de DNI argentino con o sin
    separadores (puntos, comas).
  - `_normalize_dni_number()` — Limpia separadores, devuelve solo dígitos.
  - `_is_plausible_dni()` — Heurística de filtrado de falsos positivos:
    7-8 dígitos, no todos iguales (descarta lecturas triviales).
  - `extract_dni_number()` — Aplica OCR con `allowlist` restringido a
    dígitos y separadores, devuelve `(número, confianza)`.

- **`matcher.py`** — Emparejamiento frente↔dorso:
  - Algoritmo greedy por distancia Levenshtein ascendente.
  - Resolución automática de conflictos: si dos frentes podrían matchear con
    el mismo dorso, gana el de menor distancia.
  - Política conservadora: **nunca se empareja por orden de subida ni
    heurísticas alternativas**. Si OCR falla en un DNI, el ítem va a
    huérfanos para resolución manual. Falsos positivos en matcheo notarial
    son inaceptables.
  - Threshold configurable vía `MATCH_MAX_DISTANCE` (default 2).
  - Mensajes de huérfano descriptivos (sin número leído vs. sin par
    compatible).

- **`composer.py`** — Generación del PDF A4:
  - `_compute_pair_positions()` — Cálculo de coordenadas en mm para los 4
    pares por hoja, centrados horizontal y verticalmente.
  - `compose_pdf()` — Genera el PDF completo:
    - Sección 1: pares matcheados (4 por hoja, frentes columna izquierda,
      dorsos columna derecha, tamaño físico real 85.6×53.98 mm).
    - Sección 2: páginas adicionales con huérfanos, frentes en su columna
      habitual y dorsos en la suya, sin emparejamiento visual.
    - Sin texto, sin numeración, sin etiquetas (decisión del usuario:
      "Sin texto agregado").
  - Crea automáticamente el directorio padre del output si no existe.

- **`pipeline.py`** — Orquestador del flujo completo:
  - `process_batch()` — Función principal que une vision + ocr + matcher +
    composer en un único llamado.
  - `_list_images_in_dir()` — Listado no recursivo con filtrado por extensión.
  - `_process_side()` — Procesa un lote de frentes o dorsos, manejando
    excepciones por imagen sin abortar el batch completo (los errores se
    reportan como `UnprocessedImage`).
  - Devuelve `ProcessingResult` con todas las estadísticas y referencias
    para uso downstream (CLI ahora, web API en Fase 3).

#### Schemas (Pydantic v2)
- **`app/schemas/session.py`**:
  - `DNISide` (enum) — `FRENTE`, `DORSO`, `UNKNOWN`.
  - `BoundingBox` (frozen) — Coordenadas de píxel con `.area` y
    `.aspect_ratio` (normalizado a orientación horizontal).
  - `DetectedDNI` — DNI detectado con metadata: crop_id, fuente, bbox,
    path del recorte, lado, número OCR, confianzas.
  - `MatchedPair` — Par frente+dorso con distancia y flag `is_exact_match`.
  - `UnpairedDNI` — Huérfano con `reason` descriptivo.
  - `UnprocessedImage` — Imagen sin DNIs detectados.
  - `ProcessingResult` — Resultado completo con properties `.total_pairs`,
    `.total_orphans`, `.total_unprocessed`, `.total_images_input`.

#### CLI
- **`scripts/process_batch.py`** — Entry point Fase 1 usando Typer + Rich:
  - Argumentos: `--frentes`, `--dorsos`, `--output`, `--work-dir`,
    `--verbose`, `--quiet`.
  - Validación automática de existencia de carpetas (Typer).
  - Output formateado con tabla Rich, listado de huérfanos y no procesadas.
  - Códigos de salida: `0` éxito, `1` validación, `2` error inesperado.

#### Testing
- **`tests/conftest.py`** — Fixtures comunes con generador de DNIs sintéticos
  (rectángulos de aspect ratio ID-1 sobre fondo de alto contraste). Permite
  testear el pipeline sin imágenes reales (que no se pueden commitear por
  privacidad notarial).
- **`tests/unit/test_vision.py`** — 24 tests cubriendo:
  - Carga JPG/PNG, manejo de archivo inexistente y archivo corrupto.
  - Detección de uno, múltiples y cero DNIs.
  - Detección con inclinación leve (5°).
  - Validación de filtros (área, aspect ratio).
  - IoU y deduplicación.
  - Recorte con padding y clamping en bordes.
  - Preservación exacta de contenido sin padding.
- **`tests/unit/test_matcher.py`** — 19 tests cubriendo:
  - Match exacto y match con tolerancia 1 y 2.
  - Rechazo con distancia 3 (sobre threshold).
  - Resolución de conflictos (mejor distancia gana).
  - Casos asimétricos (más frentes que dorsos y viceversa).
  - DNIs sin número OCR (None) → huérfanos.
  - Inputs vacíos.
- **`tests/unit/test_composer.py`** — 11 tests cubriendo:
  - Cálculo de posiciones dentro de bounds A4.
  - Frente siempre a la izquierda del dorso.
  - Orden top-to-bottom de filas.
  - Generación de PDF con 1 par, página completa, múltiples páginas.
  - PDF con solo huérfanos.
  - PDF con pares + huérfanos.
  - Creación automática de directorio padre.
- **`tests/unit/test_ocr.py`** — 15 tests cubriendo solo las funciones puras
  (regex, normalización, plausibilidad). `extract_dni_number()` se testea
  con imágenes reales en Fase 2 para evitar la carga de EasyOCR en CI.

**Resultado de la batería:** 69 tests / 69 pasando en 0.54s.

#### Documentación
- `ROADMAP.md` — Plan completo de fases con decisiones congeladas,
  riesgos, criterios de aceptación y cronograma.
- `README.md` — Quickstart, estructura del proyecto, uso del CLI.
- `CHANGELOG.md` — Este documento.

### Decisiones técnicas registradas

- **Sin transformación de perspectiva.** El módulo de visión usa
  exclusivamente `cv2.boundingRect()` para extraer bounding boxes alineados
  al eje. Si el DNI fue fotografiado torcido, se mantiene torcido en el PDF
  con fondo perimetral visible. Esto es un **requisito legal** para uso
  notarial: certifica que la foto no fue manipulada digitalmente.

- **OCR como criterio de matcheo, no como dato persistido.** Los números
  de DNI extraídos se usan únicamente para emparejar frentes con dorsos.
  No se almacenan, no se muestran al usuario, no se loguean a nivel INFO.
  Aparecen en logs DEBUG para troubleshooting.

- **Política conservadora de matcheo.** En contexto notarial, preferimos
  huérfanos explícitos antes que falsos positivos. El threshold de
  Levenshtein 2 + resolución determinística de conflictos garantiza que
  no se emparejen DNIs distintos por accidente.

- **Carga lazy de EasyOCR.** El singleton del Reader se inicializa solo
  en el primer llamado a `extract_dni_number()`. Esto mantiene la importación
  del paquete rápida (<0.5s) y permite que los tests del resto del pipeline
  corran sin la carga de los modelos.

### Out of scope para v0.1.0
- Interfaz web (Fase 3, v0.3.0)
- Clasificación automática frente/dorso (Fase 4, v0.4.0)
- Integración con Escriba (Fase 5, v0.5.0)
- systemd unit y deployment (Fase 6, v1.0.0)
- Persistencia con SQLite + Alembic (postergada hasta validar necesidad)

### Próximos pasos
- Fase 2 — Calibración con set de imágenes reales provisto por usuario.
  Tests de integración con OCR habilitado, medición de tasas de detección
  y matcheo, ajuste de parámetros de Canny y EasyOCR.
