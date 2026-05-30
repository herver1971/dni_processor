# Guía de Calibración Local — Fase 2

> 🔒 **Toda la calibración se ejecuta en tu Kubuntu. Las imágenes nunca
> salen de tu máquina. El reporte que compartas con Anthropic contiene
> únicamente métricas agregadas y, opcionalmente, IDs opacos para que
> vos puedas identificar localmente las imágenes problemáticas.**

---

## Objetivo de Fase 2

Validar que el pipeline funciona correctamente con tus fotos reales y
ajustar los parámetros de visión y OCR para maximizar la tasa de detección
y matcheo sin introducir falsos positivos.

**Métricas objetivo (mínimos aceptables):**

| Métrica | Mínimo |
|---|---|
| Tasa de detección | ≥ 90% |
| Tasa de OCR sobre detectados | ≥ 85% |
| Tasa de matcheo correcto | ≥ 85% |
| Falsos positivos de matcheo | 0 |
| Tiempo medio por imagen | ≤ 3s |

---

## Paso 0 — Preparar el set de prueba

1. Tomá entre **20 y 30 fotos** representativas de tu flujo real:
   - Variedad de iluminación (luz natural, artificial, mezclada)
   - Variedad de fondos (mesa, mostrador, papel)
   - Variedad de ángulos (algunas rectas, algunas inclinadas)
   - Si solés fotografiar múltiples DNIs por foto, incluí algunos casos
   - Algunos casos "difíciles" intencionalmente (reflejos, sombras, foco no perfecto)

2. Organizá las fotos en dos carpetas en tu máquina:

   ```
   ~/dni_calibracion/
   ├── frentes/
   │   ├── IMG_0001.jpg
   │   └── ...
   └── dorsos/
       ├── IMG_0010.jpg
       └── ...
   ```

3. Confirmá que `tests/fixtures/images/` esté **excluido por el `.gitignore`**
   (ya está, pero verificá con `git status` que no aparezcan tus fotos).

---

## Paso 1 — Sweep de parámetros de detección

El sweep barre combinaciones de parámetros de OpenCV sin usar OCR (es
rápido, ~5-10 minutos para 30 imágenes × 64 combinaciones).

```bash
cd dni_processor
source .venv/bin/activate

python scripts/calibrate.py sweep \
    --frentes ~/dni_calibracion/frentes \
    --dorsos  ~/dni_calibracion/dorsos \
    --output  ~/dni_calibracion/sweep.csv
```

### Qué hace
- Para cada combinación de `canny_low × canny_high × aspect_tolerance × min_area`,
  corre la detección sobre todas las imágenes.
- Cuenta cuántas imágenes tuvieron al menos un DNI detectado.
- Cuenta cuántos DNIs totales se detectaron.
- Mide el tiempo.

### Qué buscamos
- **Detección 100%** (ninguna imagen sin DNI detectado).
- **DNIs/imagen ≈ 1** (idealmente; si una foto tenía 2 DNIs reales, esperamos 2).
- **DNIs/imagen > 1 sospechoso** → puede indicar falsos positivos (bordes de
  mesa, sombras, etc.). En ese caso conviene endurecer `min_area_ratio` o
  reducir `aspect_tolerance`.

### Output
- CSV con todas las combinaciones y métricas
- Tabla de top 10 en consola
- **Compartible con Anthropic** (no contiene nombres ni datos)

### Cómo iterar
Si el top 10 muestra detección < 100% con todas las combinaciones, ampliá
el rango de búsqueda:

```bash
python scripts/calibrate.py sweep \
    --frentes ~/dni_calibracion/frentes \
    --dorsos  ~/dni_calibracion/dorsos \
    --output  ~/dni_calibracion/sweep_amplio.csv \
    --canny-low-values     "20,40,60,80,100" \
    --canny-high-values    "80,120,160,200,250,300" \
    --aspect-tolerance-values "0.10,0.15,0.20,0.25,0.30" \
    --min-area-values      "0.003,0.005,0.01,0.02"
```

---

## Paso 2 — Evaluación con OCR

Una vez identificada una combinación promisoria del sweep, corré la
evaluación completa con OCR habilitado:

```bash
python scripts/calibrate.py eval \
    --frentes ~/dni_calibracion/frentes \
    --dorsos  ~/dni_calibracion/dorsos \
    --output  ~/dni_calibracion/eval_v1 \
    --canny-low  50 \
    --canny-high 150 \
    --aspect-tolerance 0.15 \
    --min-area 0.01 \
    --match-max-distance 2
```

### Qué hace
- Detección con los parámetros indicados
- OCR sobre cada recorte (descarga modelos en el primer run, ~500MB)
- Matcheo Levenshtein
- Genera PDF de preview (eliminar con `--skip-pdf` si no querés)
- Produce dos reportes:
  - `eval_v1.txt` — Reporte legible para humanos
  - `eval_v1.json` — Estructurado, para análisis programático

### Qué buscamos en el reporte

```
## Detección
  Frentes detectados: 30 (100.0%)   ← debe ser ≥ 90%
  Dorsos detectados:  29 (96.7%)    ← debe ser ≥ 90%

## OCR
  Frentes con número leído: 27 (90.0%)  ← debe ser ≥ 85%
  Dorsos con número leído:  26 (89.7%)  ← debe ser ≥ 85%
  Confianza P50: 0.872                  ← P50 ≥ 0.7 es saludable
  Confianza P90: 0.954                  ← P90 ≥ 0.9 es excelente

## Matcheo
  Pares: 25
  Huérfanos frentes: 5
  Huérfanos dorsos: 4
  Tasa de matcheo: 86.2%               ← debe ser ≥ 85%

## Tiempos (segundos)
  detection_avg_per_image: 0.241
  ocr_avg_per_crop: 1.834
  pipeline_total: 67.3
```

### Imágenes problemáticas

El reporte incluye una sección final con identificadores **opacos** de
las imágenes que fallaron:

```
## Imágenes problemáticas
  [detection] img_a3f2b1c8: no_dni_detected
  [ocr] img_d9e7f4a2: no_number_extracted
  [ocr] img_b1c2d3e4: no_number_extracted
```

Para identificar qué imagen es cada hash, corré:

```bash
python -c "
import hashlib
from pathlib import Path
for d in [Path('~/dni_calibracion/frentes').expanduser(),
          Path('~/dni_calibracion/dorsos').expanduser()]:
    for p in d.iterdir():
        size = p.stat().st_size
        h = hashlib.sha1(f'{p.name}:{size}'.encode()).hexdigest()[:8]
        print(f'img_{h}  ->  {p.name}')
"
```

**No compartas la salida de este comando con Anthropic** — incluye nombres
de archivos que podrían contener información.

### Alternativa rápida (USO LOCAL ÚNICAMENTE)

Si querés que el reporte incluya nombres de archivo directamente:

```bash
python scripts/calibrate.py eval \
    --frentes ~/dni_calibracion/frentes \
    --dorsos  ~/dni_calibracion/dorsos \
    --output  ~/dni_calibracion/eval_v1_LOCAL \
    --include-filenames
```

**El reporte resultante NO debe compartirse.** Es solo para tu uso interno.

---

## Paso 3 — Qué compartir conmigo

Para que pueda ayudarte a ajustar parámetros y mejorar el pipeline,
mandame:

### ✅ Compartible con Anthropic
1. El **CSV del sweep completo** (`sweep.csv`)
2. El **JSON del eval** (`eval_v1.json`) — usá la versión SIN `--include-filenames`
3. Una **descripción cualitativa** de las imágenes problemáticas, por ejemplo:
   - "img_a3f2b1c8 era una foto con mucho reflejo del flash"
   - "img_d9e7f4a2 tenía el DNI inclinado ~25°"
   - "img_b1c2d3e4 tenía dos DNIs muy cerca uno del otro"

### ❌ NO compartir
- Las imágenes en sí
- Reportes generados con `--include-filenames`
- Output del comando de mapeo hash→nombre

---

## Paso 4 — Iteración

Sobre la base del reporte, vamos a:

1. Identificar la **clase** de fallo más común (¿problemas de detección o de OCR?)
2. Ajustar parámetros específicos:
   - **Bajo % detección** → ajustar Canny, padding, o agregar pre-procesamiento
     (CLAHE para mejorar contraste en fotos con baja iluminación)
   - **Bajo % OCR sobre detectados** → ajustar parámetros de EasyOCR
     (`contrast_ths`, `text_threshold`), considerar pre-procesamiento del recorte
   - **Bajo % matcheo** → revisar si es por OCR fallido (huérfanos sin número)
     o por números mal leídos (huérfanos con número pero sin par compatible)
3. Volver a correr el `eval` con los nuevos parámetros
4. Comparar reportes (versionalos: `eval_v1.json`, `eval_v2.json`, ...)
5. Repetir hasta cumplir los mínimos aceptables

---

## Cheatsheet

```bash
# 1. Sweep inicial (no usa OCR, rápido)
python scripts/calibrate.py sweep \
    -f ~/dni_calibracion/frentes \
    -d ~/dni_calibracion/dorsos \
    -o ~/dni_calibracion/sweep.csv

# 2. Eval con parámetros default
python scripts/calibrate.py eval \
    -f ~/dni_calibracion/frentes \
    -d ~/dni_calibracion/dorsos \
    -o ~/dni_calibracion/eval_default

# 3. Eval con parámetros específicos
python scripts/calibrate.py eval \
    -f ~/dni_calibracion/frentes \
    -d ~/dni_calibracion/dorsos \
    -o ~/dni_calibracion/eval_tuned \
    --canny-low 40 --canny-high 180 \
    --aspect-tolerance 0.20 \
    --min-area 0.008

# 4. Eval rápido sin PDF (solo métricas)
python scripts/calibrate.py eval ... --skip-pdf

# 5. Eval con nombres reales (USO LOCAL)
python scripts/calibrate.py eval ... --include-filenames
```
