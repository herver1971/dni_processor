# DNI Processor

Servicio auto-alojado para extraer, emparejar y organizar fotografías de DNIs
argentinos en un PDF A4 listo para impresión. Diseñado para uso notarial:
**preserva la integridad documental original** (sin warp ni transformaciones
de perspectiva).

> 🔒 **Privacidad:** Toda la información se procesa localmente. Las imágenes
> nunca salen del servidor. Pensado para deploy detrás de Tailscale.

---

## Estado actual

**Versión:** 0.1.0 — Fase 1 (MVP CLI)
**Próxima fase:** Calibración con imágenes reales

Ver [ROADMAP.md](ROADMAP.md) para el plan completo y [CHANGELOG.md](CHANGELOG.md)
para el historial de cambios.

---

## Quickstart (Fase 1 — CLI)

### 1. Requisitos

- Python 3.11+
- Kubuntu / Ubuntu (testeado) o cualquier Linux con OpenCV disponible
- ~1 GB de espacio en disco (incluye modelos de EasyOCR descargados en primer uso)

### 2. Instalación

```bash
git clone <repo>
cd dni_processor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Uso del CLI

```bash
python scripts/process_batch.py \
    --frentes /ruta/a/fotos/frentes \
    --dorsos  /ruta/a/fotos/dorsos \
    --output  /ruta/a/output.pdf
```

Opciones:

| Flag | Descripción |
|---|---|
| `--frentes` / `-f` | Carpeta con fotos de **frentes** de DNI |
| `--dorsos` / `-d` | Carpeta con fotos de **dorsos** de DNI |
| `--output` / `-o` | Path del PDF de salida |
| `--work-dir` / `-w` | Directorio para recortes intermedios (opcional) |
| `--verbose` / `-v` | Logging detallado (DEBUG) |
| `--quiet` / `-q` | Solo errores |

**Primer uso:** EasyOCR descarga los modelos del idioma español (~500 MB).
Tarda algunos minutos. En usos posteriores la inicialización es de ~3-5 s.

### 4. Estructura esperada de las carpetas

```
fotos/
├── frentes/                    # SOLO frentes de DNI
│   ├── IMG_001.jpg             # Puede contener varios DNIs por foto
│   ├── IMG_002.jpg
│   └── ...
└── dorsos/                     # SOLO dorsos de DNI
    ├── IMG_010.jpg
    └── ...
```

**Importante:** En Fase 1 una imagen no puede contener mezcla de frentes y
dorsos. Esto se eliminará en Fase 4 con clasificación automática.

---

## Estructura del proyecto

```
dni_processor/
├── app/
│   ├── core/                   # Pipeline de procesamiento
│   │   ├── constants.py        # Dimensiones ID-1, layout A4, thresholds
│   │   ├── vision.py           # Detección y recorte (OpenCV)
│   │   ├── ocr.py              # Extracción de número DNI (EasyOCR)
│   │   ├── matcher.py          # Emparejamiento frente↔dorso
│   │   ├── composer.py         # Generación PDF A4 (FPDF2)
│   │   └── pipeline.py         # Orquestador
│   ├── schemas/                # Modelos Pydantic
│   ├── api/                    # API REST (Fase 3+)
│   ├── web/                    # UI HTMX (Fase 3+)
│   └── main.py                 # Versión del paquete
├── scripts/
│   └── process_batch.py        # CLI Fase 1
├── tests/
│   ├── unit/                   # Tests unitarios (69 tests, <1s)
│   ├── integration/            # Tests de pipeline completo
│   └── fixtures/               # Imágenes sintéticas (las reales en .gitignore)
├── deployment/                 # systemd unit (Fase 6)
├── data/                       # Working dirs y BD (ignorados por git)
├── pyproject.toml
├── requirements.txt
├── .gitignore                  # Exhaustivo, foco en datos sensibles
├── ROADMAP.md
├── CHANGELOG.md
└── README.md
```

---

## Tests

```bash
# Todos los tests unitarios (rápidos, sin EasyOCR)
pytest tests/unit -v

# Solo tests marcados como unit
pytest -m unit

# Con cobertura
pytest --cov=app --cov-report=term-missing
```

**Estado:** 69 tests pasando.

---

## Principios de diseño

### 1. Integridad documental (no negociable)

El módulo de visión usa **únicamente bounding boxes rectos** (axis-aligned).
No se aplica:
- `cv2.warpPerspective`
- Deskew
- Rotación correctiva
- Rectificación trapezoidal

Si la foto del DNI está inclinada 15°, el recorte en el PDF estará inclinado
15° con fondo perimetral visible. Esto certifica visualmente que la imagen no
fue manipulada digitalmente, requisito para uso notarial.

### 2. OCR como medio, no como fin

El número de DNI extraído por OCR se usa **exclusivamente** para emparejar
frentes con dorsos. No se almacena, no se muestra al usuario, no se loguea
a nivel INFO. El PDF final no contiene texto agregado.

### 3. Matcheo conservador

Threshold Levenshtein de 2 + resolución determinística de conflictos.
Cuando hay duda, se genera huérfano explícito en lugar de match incorrecto.
En contexto notarial, un par mal emparejado es peor que pedirle al usuario
que lo resuelva manualmente.

### 4. Privacidad

- Procesamiento 100% local
- Acceso vía Tailscale (fase de deployment)
- `.gitignore` exhaustivo para imágenes, PDFs, bases de datos
- Logs DEBUG con números de DNI desactivados por defecto

---

## Roadmap resumido

- ✅ **v0.1.0** — MVP CLI funcional
- ⏳ **v0.2.0** — Calibración con imágenes reales
- ⏳ **v0.3.0** — Interfaz web (FastAPI + HTMX)
- ⏳ **v0.4.0** — Clasificación automática frente/dorso
- ⏳ **v0.5.0** — Integración con Escriba (API)
- ⏳ **v1.0.0** — Deployment producción + hardening

Detalle en [ROADMAP.md](ROADMAP.md).

---

## Licencia

Proprietary. Proyecto interno.
