# Instalación en Kubuntu

Pasos concretos para correr DNI Processor como servicio 24/7 en un servidor Kubuntu detrás de Tailscale.

## Convenciones de esta guía

- El proyecto vive en `/home/hernan/Documentos/Proyectos_Github/dni_processor`
- Corre como el usuario `hernan` (sin usuario dedicado)
- Si tu path o user son diferentes, ajustá los comandos y el unit file (`User=`, `WorkingDirectory=`, `ExecStart=`, `ReadWritePaths=`) antes de instalarlo

## 1. Prerequisitos del sistema

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
                    libgl1 libglib2.0-0
```

!!! note "`libgl1` y `libglib2.0-0`"
    OpenCV los carga dinámicamente para el procesamiento de imágenes. Sin ellos, `import cv2` falla con un error críptico de librería compartida faltante.

## 2. Clonar y armar el venv

```bash
cd /home/hernan/Documentos/Proyectos_Github
git clone <repo_url> dni_processor
cd dni_processor

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configurar el entorno

```bash
cp .env.example .env
nano .env
```

Para producción, verificar estos valores:

```bash
DNI_HOST=127.0.0.1       # NO cambiar — el servicio no debe ser accesible directo
DNI_PORT=8001             # Cambiar si choca con otro servicio
DNI_DEBUG=false           # Dejar en false
DNI_RATE_LIMIT_ENABLED=true
```

Ver [Configuración](configuracion.md) para la referencia completa.

## 4. Pre-descargar los modelos

```bash
# Con el venv activado
python scripts/preload_models.py
```

Salida esperada:

```
INFO  Detector de caras: ya en cache (o descargando...)
INFO  EasyOCR: ya en cache (o descargando ~500MB...)
INFO  Todos los modelos disponibles. Listo para deploy.
```

!!! warning "Si falla con HTTP 403"
    Algunos CDNs de EasyOCR son caprichosos. Intentá de nuevo en unos minutos, o descargá los modelos manualmente siguiendo el log de error.

## 5. Crear el directorio de datos

```bash
mkdir -p /home/hernan/Documentos/Proyectos_Github/dni_processor/data
```

## 6. Smoke test manual

```bash
python -m app.main
```

En otra terminal:

```bash
curl http://127.0.0.1:8001/api/v1/health
# {"status":"ok","version":"0.4.0","models":{"face":true,"ocr":true}}
```

`Ctrl+C` para parar antes de continuar.

## 7. Instalar el servicio systemd

Antes de copiar el unit, verificá que los paths en `deployment/dni_processor.service` coincidan con tu instalación. Las líneas críticas:

```ini
User=hernan
WorkingDirectory=/home/hernan/Documentos/Proyectos_Github/dni_processor
ExecStart=/home/hernan/Documentos/Proyectos_Github/dni_processor/.venv/bin/python -m app.main
ReadWritePaths=/home/hernan/Documentos/Proyectos_Github/dni_processor/data
ReadWritePaths=/home/hernan/.cache/dni_processor
ReadWritePaths=/home/hernan/.EasyOCR
```

Instalar:

```bash
sudo cp deployment/dni_processor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dni_processor.service
```

Verificar:

```bash
systemctl status dni_processor
# Debe decir: Active: active (running)

journalctl -u dni_processor -n 30
# Debe mostrar el banner de Uvicorn y "DNI Processor v0.4.0 arrancando..."
```

## 8. Configurar acceso por Tailscale

Ver [Acceso por Tailscale](tailscale.md).

## 9. Actualizar a una versión nueva

```bash
cd /home/hernan/Documentos/Proyectos_Github/dni_processor
sudo systemctl stop dni_processor

git pull
# o aplicar el ZIP de release manualmente

source .venv/bin/activate
pip install -r requirements.txt     # si cambiaron dependencias
python scripts/preload_models.py    # idempotente

# Si cambió el unit file:
sudo cp deployment/dni_processor.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl start dni_processor
systemctl status dni_processor
```

## Troubleshooting

### El servicio dice `failed`

```bash
journalctl -u dni_processor -n 50 --no-pager
```

| Síntoma | Causa probable |
|---|---|
| `ModuleNotFoundError` | El venv no está armado o `ExecStart=` apunta al python del sistema |
| `Address already in use` | Otro servicio en el puerto. Cambiar `DNI_PORT` en `.env` |
| `EROFS` o `PermissionError` en `data/` | `ReadWritePaths=` no incluye el data dir, o el dir tiene owner `root` |
| `Connection refused` en el curl | El servicio está bajado o bindea a otro host |

### `/api/v1/health` devuelve `"degraded"`

```bash
python scripts/preload_models.py
sudo systemctl restart dni_processor
```

### Los logs no aparecen en `journalctl -f`

El unit ya setea `PYTHONUNBUFFERED=1`. Si aún así no ves logs, hacé una request al health para forzar salida.

### El servicio responde en loopback pero no por Tailscale

Ver [Troubleshooting de Tailscale](tailscale.md#troubleshooting).
