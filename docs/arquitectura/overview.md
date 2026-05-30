# Visión general de la arquitectura

DNI Processor es un servicio web single-process construido sobre FastAPI. No tiene base de datos — el estado vive en archivos JSON en disco.

## Stack técnico

| Capa | Tecnología |
|---|---|
| Framework web | FastAPI + Uvicorn |
| Templates HTML | Jinja2 |
| Interactividad frontend | HTMX + JavaScript vanilla |
| Editor de recortes | Cropper.js |
| Reordenamiento de pares | SortableJS |
| Detección de caras | OpenCV DNN (ResNet-10 SSD) |
| OCR | EasyOCR |
| Generación PDF | FPDF2 |
| Validación y settings | Pydantic v2 + pydantic-settings |
| Rate limiting | slowapi |
| Deployment | systemd + Tailscale |

## Estructura de módulos

```
app/
├── main.py              # App factory, lifespan, health endpoint
├── config.py            # Settings (pydantic-settings, .env)
├── middleware.py         # SecurityHeadersMiddleware, RequestSizeLimitMiddleware
├── rate_limiter.py       # Singleton de slowapi.Limiter
│
├── core/                 # Lógica de dominio (sin dependencias de FastAPI)
│   ├── constants.py      # Dimensiones ID-1, thresholds, límites
│   ├── vision.py         # Detección de caras, recorte de imágenes, EXIF
│   ├── ocr.py            # OCR del número de DNI (EasyOCR)
│   ├── geometry.py       # Cálculo del bbox del DNI a partir del bbox de cara
│   ├── crop_adjustments.py  # Aplicar bbox + rotación al recorte final
│   ├── matcher.py        # Emparejamiento frente↔dorso por Levenshtein
│   ├── composer.py       # Layout del PDF A4 (FPDF2)
│   ├── sessions.py       # CRUD de sesiones en disco, cleanup
│   └── pipeline.py       # Orquestador del procesamiento de una imagen
│
├── schemas/              # Modelos Pydantic
│   ├── session.py        # Entidades de dominio (DetectedDNI, BoundingBox, ...)
│   ├── web.py            # Estado de sesión web (SessionState, CropState, ...)
│   └── api.py            # Request/response de la API REST
│
├── api/v1/               # Endpoints REST (JSON)
│   ├── routes_sessions.py
│   ├── routes_images.py
│   ├── routes_processing.py
│   └── routes_matching.py
│
└── web/                  # Endpoints HTML + assets
    ├── routes.py         # Páginas Jinja2 (upload, review, match, completed)
    ├── templates/        # HTML con HTMX
    └── static/           # CSS + JS
```

## Flujo de una request típica

```
Browser  →  Tailscale  →  127.0.0.1:8001
                               │
                    RequestSizeLimitMiddleware
                    SecurityHeadersMiddleware
                               │
                          FastAPI router
                               │
                    ┌──────────┴──────────┐
                  API JSON           Página HTML
               (routes_*.py)       (web/routes.py)
                    │                    │
              app/core/*           Jinja2 template
```

## Persistencia y estado

No hay base de datos. Cada sesión es un directorio en `data/sessions/<uuid>/`:

```
sessions/
└── <uuid>/
    ├── session.json          # Estado completo serializado
    ├── originals/            # Fotos subidas, normalizadas por EXIF
    ├── crops/
    │   ├── wide/             # Recortes amplios (auto-generados)
    │   └── final/            # Recortes finales confirmados por el usuario
    └── output.pdf            # PDF final (cuando se genera)
```

`session.json` se escribe atómicamente (write a temp + rename) para evitar corrupción ante interrupciones. Las sesiones se eliminan automáticamente después de 24 horas de inactividad.

## Topología de deployment

```
[Browser / Escriba]
        │
        │  HTTPS (cert Tailscale)
        ▼
[tailscaled en el servidor]
        │
        │  HTTP
        ▼
[127.0.0.1:8001 — Uvicorn]
        │
[FastAPI app]
```

`tailscale serve --bg --https=8443 http://127.0.0.1:8001` levanta el proxy HTTPS en el hostname Tailscale del servidor. El servicio en sí nunca escucha en una interfaz de red externa.
