# Quickstart

Guía de instalación y primer uso para desarrollo local. Para el deploy en producción en Kubuntu, ver [Instalación en Kubuntu](../deployment/instalacion.md).

## Prerequisitos

- Python 3.11 o superior
- Linux (testeado en Kubuntu/Ubuntu). En Windows o macOS el servicio no está probado.
- ~1.5 GB de espacio libre en disco (incluye los modelos de EasyOCR, ~500 MB, y el detector de caras, ~10 MB)
- Conexión a internet para la primera descarga de modelos (después funciona offline)

## Instalación

```bash
git clone <repo_url> dni_processor
cd dni_processor

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

## Pre-descargar los modelos

La primera vez que se usa, EasyOCR y el detector de caras necesitan descargar sus modelos. Es preferible hacerlo una vez de forma controlada en lugar de que suceda en la primera request:

```bash
python scripts/preload_models.py
```

Salida esperada:

```
INFO  Detector de caras: OK
INFO  EasyOCR: OK
INFO  Todos los modelos disponibles. Listo para deploy.
```

!!! note "Primera descarga"
    EasyOCR descarga ~500 MB de modelos de idioma. Dependiendo de la conexión puede tardar 1-5 minutos. Las ejecuciones siguientes verifican el cache y terminan en milisegundos.

## Configuración

Copiá el archivo de ejemplo y ajustá según sea necesario:

```bash
cp .env.example .env
```

Para desarrollo local los defaults funcionan sin cambios. Ver [Configuración](../deployment/configuracion.md) para la referencia completa de variables.

## Iniciar el servidor

```bash
python -m app.main
```

El servidor arranca en `http://127.0.0.1:8001`. Abrí esa URL en el browser y vas a ver la pantalla de upload.

## Verificar que funciona

```bash
curl http://127.0.0.1:8001/api/v1/health
```

Respuesta esperada:

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

Si `status` es `"degraded"`, significa que falta algún modelo en cache. Re-corré `python scripts/preload_models.py`.

## Correr los tests

```bash
pytest -q
# → 168 passed, 1 warning
```

Los tests no requieren los modelos descargados — usan mocks y datos sintéticos.
