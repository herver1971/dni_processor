# ROADMAP — DNI Processor

**Versión:** 0.1.0 (planificación)
**Autor:** Hernan
**Fecha:** 2026-05-27
**Estado:** Fase 0 — Diseño congelado, pendiente inicio de implementación

---

## 1. Visión del Producto

Aplicación web auto-alojada en Linux (Kubuntu) que automatiza la organización de fotografías de DNIs argentinos (tarjeta) en un PDF tamaño A4 listo para impresión, conservando la integridad documental original (sin transformaciones de perspectiva).

**Caso de uso primario:** Escribanía. Procesamiento de lotes de fotos de DNIs tomadas durante trámites, organización en hoja única para anexar a expediente físico.

**Principio rector — Integridad probatoria:** El sistema realiza únicamente recorte rectangular (bounding box recto). Si el documento fue fotografiado torcido, se preserva torcido en el PDF, con fondo perimetral visible, demostrando que la imagen no fue manipulada digitalmente.

---

## 2. Decisiones Arquitectónicas Congeladas

### 2.1. Stack Técnico

| Capa | Tecnología | Justificación |
|---|---|---|
| Lenguaje | Python 3.11+ | Madurez en visión y OCR |
| Web framework | FastAPI | Coherencia con Escriba, async nativo |
| Templates | Jinja2 + HTMX | Mismo patrón que Escriba, sin SPA |
| Visión | OpenCV (`opencv-python-headless`) | Sin overhead de GUI en servidor |
| OCR | EasyOCR | Mejor performance que Tesseract en fotos reales con reflejos; modelo español |
| PDF | FPDF2 | Liviano, control directo sobre coordenadas mm |
| Persistencia | SQLite + Alembic | Solo si se confirma necesidad de auditoría (ver §2.5) |
| Validación | Pydantic v2 | Estándar FastAPI |
| Tests | pytest + pytest-asyncio | Coherencia con Escriba |
| Deployment | systemd unit + uvicorn | Idéntico a Escriba |
| Acceso remoto | Tailscale | Infraestructura existente |

### 2.2. Topología — Servicio Independiente con API

Decisión: opción **(a)** del diseño previo.

```
┌──────────────────────────────────────────┐
│  Escribanía / Cliente (Browser)          │
└──────────────────────────────────────────┘
        │ (Tailscale)
        ▼
┌──────────────────┐      ┌──────────────────┐
│  Escriba         │      │  DNI Processor   │
│  (puerto 8000)   │─────►│  (puerto 8001)   │
│                  │ HTTP │                  │
│  Notarial Mgmt   │      │  OpenCV+EasyOCR  │
└──────────────────┘      └──────────────────┘
        │                          │
        ▼                          ▼
  escriba.db                  /var/lib/dni_processor/
                              (work dir, modelos OCR)
```

**Beneficios:**
- Escriba no carga OpenCV (~200MB) ni modelos EasyOCR (~500MB) en su proceso
- Reinicios independientes
- Procesamiento CPU-intensivo aislado de la app principal
- Reutilizable por otros sistemas en el futuro

### 2.3. Modos de Operación

El servicio expone **dos modos** sobre el mismo backend:

1. **Modo Standalone (Web UI):** Usuario accede vía browser a `http://dni-processor.tailnet:8001/`. Sube imágenes, revisa matcheos, descarga PDF localmente.
2. **Modo Integrado (API):** Escriba invoca `POST /api/v1/process` pasando `operacion_id` y `output_path`. El PDF se escribe directamente en la carpeta de la operación en el filesystem compartido.

### 2.4. Pipeline de Procesamiento (Flujo de Datos)

```
[Imágenes subidas]
        │
        ▼
┌─────────────────────────────┐
│ 1. Detección y Segmentación │  OpenCV: Canny + contornos + filtrado
│    (1 imagen → N recortes)  │  por aspect ratio ID-1 (1.586 ±15%)
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ 2. Clasificación (Fase 2)   │  MRZ + keywords OCR
│    Frente / Dorso / Duda    │  Confianza <80% → revisión manual
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ 3. Extracción de Identidad  │  EasyOCR: número DNI (frente y dorso)
│    OCR en segundo plano     │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ 4. Matcheo                  │  Levenshtein ≤2 entre nº de frente y dorso
│    Pares + Huérfanos        │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ 5. UI de Revisión Manual    │  Usuario corrige matcheos erróneos,
│    (drag + drop)            │  recorta/rota imágenes no procesadas
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ 6. Composición PDF          │  FPDF2: 4 pares por A4
│    Frentes izq. / Dorsos der│  Tamaño real 85.6×53.98mm
└─────────────────────────────┘
```

### 2.5. Persistencia y Auditoría — Decisión

**MVP (Fase 1-3):** Stateless. Archivos en `/var/lib/dni_processor/work/<session_uuid>/` con cleanup automático a las 24h. Sin base de datos.

**Fase 5 (opcional, evaluable):** Si se requiere trazabilidad notarial completa, agregar SQLite + Alembic con tabla `procesamiento_log` (uuid, timestamp, n_imagenes, n_pares, operacion_id, output_path, hash_pdf). Decisión postergada hasta validar necesidad real en uso.

### 2.6. Estructura de Carpetas

```
dni_processor/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, lifespan, mount routers
│   ├── config.py                  # Pydantic Settings (env vars)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── vision.py              # OpenCV: detect_dni_contours, crop_bbox
│   │   ├── ocr.py                 # EasyOCR wrapper, extract_dni_number
│   │   ├── classifier.py          # Frente/Dorso (Fase 2)
│   │   ├── matcher.py             # Levenshtein, pares y huérfanos
│   │   └── composer.py            # FPDF2, layout A4
│   ├── api/
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── routes_process.py  # POST /api/v1/process (integración)
│   │   │   └── routes_session.py  # GET/POST /api/v1/sessions/* (UI)
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes.py              # Rutas HTML (HTMX)
│   │   ├── templates/
│   │   │   ├── base.html
│   │   │   ├── upload.html
│   │   │   ├── review.html        # Matcheos + manual
│   │   │   └── partials/
│   │   │       ├── card_pair.html
│   │   │       ├── card_orphan.html
│   │   │       └── card_unprocessed.html
│   │   └── static/
│   │       ├── css/
│   │       └── js/htmx.min.js
│   └── schemas/
│       ├── __init__.py
│       ├── session.py             # Pydantic: Session, Crop, Pair, etc.
│       └── api.py                 # Request/Response models
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── images/                # Set de prueba real (Fase 2)
│   ├── unit/
│   │   ├── test_vision.py
│   │   ├── test_ocr.py
│   │   ├── test_matcher.py
│   │   └── test_composer.py
│   └── integration/
│       ├── test_api_process.py
│       └── test_web_flow.py
├── scripts/
│   ├── process_batch.py           # CLI Fase 1 (sin web)
│   └── download_easyocr_models.py # Pre-descarga de modelos
├── deployment/
│   ├── dni_processor.service      # systemd unit
│   └── README_DEPLOY.md
├── data/                          # Solo si se activa Fase 5 (SQLite)
├── pyproject.toml
├── requirements.txt
├── README.md
├── CHANGELOG.md
└── ROADMAP.md                     # Este documento
```

### 2.7. Convenciones de Versionado y Documentación

Hereda del estándar Escriba:
- Versión en `app/main.py` (constante `__version__`)
- CHANGELOG.md actualizado en cada feature/fix con rationale, archivos modificados, clases CSS si aplica
- ROADMAP.md actualizado al cerrar cada fase
- Commits convencionales: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`
- ZIPs de deliverable excluyen `data/`, `__pycache__/`, modelos descargados, working dirs

---

## 3. Plan de Fases

### Fase 0 — Diseño y Decisiones Congeladas ✅

**Estado:** Completo (este documento)

**Entregable:** ROADMAP.md, decisiones de stack, estructura de carpetas, contratos de API.

---

### Fase 1 — MVP CLI de Procesamiento

**Objetivo:** Pipeline funcional vía línea de comandos, sin web. Permite iterar rápido sobre algoritmos de visión sin overhead de servidor.

**Scope:**
- Script `scripts/process_batch.py` que recibe dos carpetas (`--frentes` y `--dorsos`) y un output path, produce PDF.
- Implementación de `app/core/vision.py`: detección y recorte por bounding box.
- Implementación de `app/core/ocr.py`: extracción de número de DNI.
- Implementación de `app/core/matcher.py`: pares y huérfanos.
- Implementación de `app/core/composer.py`: layout A4 4 pares por hoja.

**Criterios de aceptación:**
- Procesa una carpeta de ≥10 imágenes frente + ≥10 dorso y genera PDF en <60s
- Detecta correctamente ≥85% de DNIs en imágenes con fondo de contraste razonable
- Los recortes preservan inclinación original y muestran fondo perimetral
- Matchea correctamente ≥80% de los pares en imágenes legibles
- Genera huérfanos explícitos para casos no matcheables (no falsos positivos)

**Out of scope:** Web UI, clasificación automática frente/dorso, integración con Escriba.

**Versión al cierre:** v0.1.0

---

### Fase 2 — Calibración de Visión y OCR

**Objetivo:** Tuning sobre datos reales. Esta es la fase de mayor riesgo técnico.

**Scope:**
- Hernan provee set de ~20-30 imágenes representativas (variedad de fondos, iluminación, ángulos).
- Banco de pruebas reproducible: `tests/integration/test_real_dataset.py`.
- Métricas medibles: tasa de detección, tasa de matcheo correcto, falsos positivos, tiempo por imagen.
- Ajuste de parámetros: thresholds de Canny, tolerancia de aspect ratio, parámetros de EasyOCR (allowlist de caracteres, contrast_ths, text_threshold).
- Implementación de fallbacks: si la detección falla con parámetros default, retry con conjuntos alternativos.

**Criterios de aceptación:**
- Tasa de detección ≥90% sobre el set real
- Tasa de matcheo correcto ≥85% sobre el set real
- Falsos positivos de matcheo = 0 (preferimos huérfano antes que error)
- Tiempo de procesamiento ≤3s por imagen en hardware del servidor de Escriba

**Riesgo identificado:** Si la calidad fotográfica es muy variable, la fase puede extenderse. Plan B: documentar guías de fotografía (distancia, iluminación, fondo) para el usuario.

**Versión al cierre:** v0.2.0

---

### Fase 3 — Capa Web (FastAPI + HTMX)

**Objetivo:** UI funcional, modo Standalone completo.

**Scope:**

*Endpoints HTTP:*
- `GET /` — Página de upload (drag-and-drop)
- `POST /sessions` — Crea sesión, recibe imágenes (multipart). MVP: dos campos separados `frentes[]` y `dorsos[]`.
- `GET /sessions/{uuid}/review` — Pantalla de revisión: pares detectados, huérfanos, no procesados.
- `POST /sessions/{uuid}/pairs` — Corrección manual de matcheos (HTMX, drag & drop).
- `POST /sessions/{uuid}/crops/{crop_id}/edit` — Recorte/rotación manual de imagen no procesada.
- `POST /sessions/{uuid}/generate` — Dispara generación de PDF.
- `GET /sessions/{uuid}/download` — Descarga PDF generado.

*UI:*
- Una sola página de upload con dos zonas de drop (Frentes / Dorsos) — MVP
- Pantalla de revisión con grid de pares detectados, sección de huérfanos arrastrables, sección de no procesados con editor inline (rotate, crop manual)
- Indicador de progreso durante procesamiento (HTMX SSE o polling)

*Validaciones:*
- MIME types permitidos: `image/jpeg`, `image/png`, `image/webp`, `image/heic`
- Tamaño máximo por imagen: 15MB
- Tamaño máximo por sesión: 200MB
- Cantidad máxima de imágenes por sesión: 100

**Criterios de aceptación:**
- Flujo completo upload → review → corrección → PDF en <2 minutos para 20 imágenes
- HTMX maneja correcciones sin recargar página
- Errores de validación reportados con mensaje claro al usuario

**Versión al cierre:** v0.3.0

---

### Fase 4 — Clasificación Automática Frente/Dorso

**Objetivo:** Eliminar la división en dos zonas de drop. Una sola zona, sistema clasifica.

**Scope:**
- Implementación de `app/core/classifier.py`:
  - Detección de MRZ: búsqueda de patrones `<<<<<<` en el OCR del recorte → alta confianza dorso
  - Keywords frente: "APELLIDO", "NOMBRES", "SEXO", "NACIONALIDAD"
  - Keywords dorso: "DONANTE", "ESPECIMEN", "TRÁMITE", "EJEMPLAR"
  - Score combinado, threshold de confianza 80%
- Cambios en UI: zona de drop única, sin distinción
- Casos de baja confianza van al panel "no procesados" para asignación manual

**Criterios de aceptación:**
- Tasa de clasificación correcta ≥95% sobre el set de prueba
- Errores de clasificación (frente como dorso o viceversa) <2%
- Ítems dudosos correctamente enviados a revisión manual

**Versión al cierre:** v0.4.0

---

### Fase 5 — Integración con Escriba

**Objetivo:** Modo Integrado funcional. Escriba invoca el servicio desde una operación.

**Scope:**

*API contract:*

```
POST /api/v1/process
Content-Type: multipart/form-data

Fields:
  - operacion_id: str (referencia a Escriba)
  - output_path: str (ruta absoluta donde escribir el PDF)
  - frentes[]: file[] (opcional si se usa clasificación automática)
  - dorsos[]: file[]
  - images[]: file[] (alternativa con clasificación automática)
  - callback_url: str (opcional, webhook al completar)

Response 202 Accepted:
{
  "session_id": "uuid",
  "status": "processing",
  "review_url": "http://dni-processor:8001/sessions/{uuid}/review"
}

Response 200 OK (procesamiento sincrónico si no requiere revisión):
{
  "session_id": "uuid",
  "status": "completed",
  "output_path": "/path/al/pdf",
  "stats": {
    "n_imagenes_input": 24,
    "n_pares": 12,
    "n_huerfanos": 0,
    "n_no_procesados": 0
  }
}
```

*Cambios en Escriba (sprint separado):*
- Botón "Procesar DNIs" en vista de operación
- Llamada HTTP al servicio
- Si requiere revisión, abre URL en nueva pestaña; si no, recibe PDF en carpeta de operación
- Almacenamiento del PDF resultante asociado a la operación

**Criterios de aceptación:**
- Escriba puede disparar procesamiento sin que el usuario abandone su flujo
- PDF queda guardado en la carpeta correcta de la operación
- Errores del servicio se propagan a Escriba con mensaje útil

**Versión al cierre:** v0.5.0

---

### Fase 6 — Deployment, Hardening y Documentación

**Objetivo:** Producción.

Esta fase se dividió en dos sub-entregas:

- **Sprint 4a (v0.3.2) — Hardening de aplicación.** ✅ COMPLETADO
- **Sprint 4b (v0.4.0) — Deployment a producción.** ✅ COMPLETADO

El bump a `v1.0.0` queda reservado para cuando exista la integración
con Escriba (Fase 5) cerrada.

**Scope:**

*Deployment (4b, completado):*
- [x] systemd unit `dni_processor.service` con hardening del proceso
- [x] Configuración Tailscale (DNS interno del tailnet, documentada en README_DEPLOY)
- [x] Pre-descarga de modelos EasyOCR vía `scripts/preload_models.py`
- [x] `.env.example` con todas las env vars documentadas
- [x] Variables de entorno via `.env` (Pydantic Settings)
- [x] Health endpoint enriquecido (chequea presencia de modelos en cache)

*Hardening (4a, completado):*
- [x] Rate limiting (slowapi) sobre endpoints "caros", configurable
- [x] Security headers: CSP estricta, X-Content-Type-Options,
      X-Frame-Options, Referrer-Policy, Permissions-Policy
- [x] Límite de tamaño via middleware ASGI sobre `Content-Length`
- [x] Cleanup automático de working dirs (background task, ya existía)
- [x] DEBUG configurable por env var (no más constante hardcodeada)
- [x] `datetime.utcnow()` deprecated reemplazado por timezone-aware
- [x] Validación de extensión de archivos (ya existía)
- [ ] Validación de MIME via magic bytes (diferido — el frontend ya
      filtra por extensión y el servicio es single-user tras Tailscale)
- [ ] Sanitización de nombres de archivo de upload (revisar — los nombres
      se renombran a UUID al guardar, así que el riesgo es bajo)
- [ ] Si se conecta a Escriba: validación de origen via Tailscale ACL
      (diferido a sprint posterior cuando se defina la integración)

*Tests (4a + 4b, completado):*
- [x] Tests del middleware (headers, CSP, request size, rate limit, DEBUG)
- [x] Tests del health endpoint enriquecido (con mocks para discriminar
      "ok" vs "degraded" sin depender de modelos en cache)
- [ ] Cobertura objetivo: ≥70% sobre `app/core/` (verificar)
- [x] Tests E2E del flujo web con httpx + datos sintéticos (ya existían)
- [ ] Tests del contrato API con Escriba (cuando exista)

*Documentación (4b, completado):*
- [ ] README.md con quickstart actualizado para v0.4.0 (pendiente menor)
- [x] `deployment/README_DEPLOY.md` con pasos de instalación en Kubuntu
- [x] Documentación de API en `/docs` (FastAPI auto-genera con OpenAPI)
- [ ] Guía de fotografía para usuarios (si Fase 2 lo recomienda)

**Criterios de aceptación de Fase 6:**
- [x] Hardening verificable con `pytest -q tests/integration/test_security.py`
- [x] Servicio arranca con `systemctl start dni_processor`
- [x] Accesible vía Tailscale hostname desde otra máquina del tailnet
- [x] Sobrevive a reinicio del servidor (systemd `enable` + restart on-failure)
- [x] Logs útiles en `journalctl -u dni_processor`

**Versiones:**
- v0.3.2 — Sprint 4a (hardening de app) ✅
- v0.4.0 — Sprint 4b (servicio corriendo 24/7 en producción) ✅
- v1.0.0 — Reservado para cuando Fase 5 (integración Escriba) cierre

---

## 4. Riesgos Identificados

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| OCR no lee número de DNI en alto % de fotos | Media | Alto | Fase 2 dedicada a calibración. Plan B: guía de fotografía. |
| Detección de contornos falla con fondos arbitrarios | Media | Alto | Multi-estrategia (Canny + saturación + ML opcional). Fallback a recorte manual. |
| EasyOCR muy lento en servidor sin GPU | Media | Medio | Procesamiento en background, UI con feedback. Si crítico: evaluar PaddleOCR o Tesseract con preprocesamiento. |
| Tamaño de modelos OCR (~500MB) | Baja | Bajo | Pre-descarga en deployment, no en runtime |
| Conflicto de puerto con otros servicios | Baja | Bajo | Puerto 8001 configurable vía env var |
| Imágenes HEIC de iPhones no soportadas por OpenCV | Media | Medio | Usar Pillow + pillow-heif para decode previo |

---

## 5. Stack de Testing

```
tests/
├── unit/              # Pure functions, no I/O
│   ├── test_vision.py     # Sobre imágenes fixture
│   ├── test_ocr.py        # Sobre crops fixture
│   ├── test_matcher.py    # Lógica Levenshtein, casos huérfanos
│   └── test_composer.py   # Layout PDF, validación con pdfplumber
├── integration/       # Pipeline completo
│   ├── test_full_pipeline.py
│   ├── test_real_dataset.py  # Set provisto por Hernan
│   ├── test_api_process.py
│   └── test_web_flow.py
└── fixtures/
    ├── images/synthetic/  # Generadas para CI
    ├── images/real/       # Subset anonimizado del set real
    └── pdf_expected/      # Snapshots para regression
```

**CI:** Aunque sea uso monousuario, los tests corren localmente con `pytest -v` antes de cada bump de versión. No se sube fixture con DNIs reales al repo (excluir en .gitignore).

---

## 6. Dependencias Iniciales (requirements.txt)

```
fastapi>=0.110
uvicorn[standard]>=0.27
jinja2>=3.1
python-multipart>=0.0.9
pydantic>=2.5
pydantic-settings>=2.1
opencv-python-headless>=4.9
easyocr>=1.7
numpy>=1.26
pillow>=10.2
pillow-heif>=0.15
fpdf2>=2.7
python-Levenshtein>=0.25
slowapi>=0.1.9
httpx>=0.27          # Tests
pytest>=8.0
pytest-asyncio>=0.23
```

Pin de versiones exactas en el `requirements.txt` final tras Fase 1.

---

## 7. Cronograma Estimado

| Fase | Estimación | Notas |
|---|---|---|
| Fase 0 | ✅ Completa | |
| Fase 1 (MVP CLI) | 3-5 días | Foco en vision + composer |
| Fase 2 (Calibración) | 2-4 días | Depende de set real |
| Fase 3 (Web UI) | 4-6 días | HTMX, UI de revisión es lo más complejo |
| Fase 4 (Clasificación) | 2-3 días | Sobre base ya funcional |
| Fase 5 (Integración Escriba) | 2-3 días | + sprint en Escriba para botón |
| Fase 6 (Deployment) | 1-2 días | Aplicando hardening conocido |

**Total estimado:** 14-23 días de trabajo efectivo, distribuibles según disponibilidad.

---

## 8. Próximos Pasos Inmediatos

1. Confirmar este ROADMAP
2. Inicializar repositorio `dni_processor/` con la estructura definida en §2.6
3. Crear `pyproject.toml` y `requirements.txt` iniciales
4. Comenzar Fase 1 — implementación de `app/core/vision.py` con tests unitarios
5. Al cierre de Fase 1: Hernan provee set de imágenes reales para Fase 2

---

**Fin del documento.**
