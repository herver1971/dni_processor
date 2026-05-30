# Deployment — DNI Processor en Kubuntu

Pasos concretos para correr el servicio 24/7 en un servidor Kubuntu
detrás de Tailscale. Asume que ya tenés Tailscale instalado y el
servidor en tu tailnet (mismo escenario que Escriba).

Convenciones de este documento:

- El proyecto vive en **`/home/hernan/dni_processor`**.
- Corre como el usuario **`hernan`** (sin user dedicado).
- Si tu path o user son diferentes, ajustá los comandos y editá el
  unit file (`User=`, `WorkingDirectory=`, `ExecStart=`,
  `ReadWritePaths=`) antes de instalarlo.

---

## 1. Prerequisitos del sistema

Una sola vez, en cada server nuevo:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
                    libgl1 libglib2.0-0
```

- `python3.11`: la app necesita 3.11+. Si tu Kubuntu trae 3.12, está bien.
- `libgl1` + `libglib2.0-0`: OpenCV los carga dinámicamente para imágenes;
  sin ellos `import cv2` explota.

---

## 2. Clonar y armar el venv

```bash
cd /home/hernan
# Si el proyecto ya está clonado, hacé pull en su lugar.
git clone <repo_url> dni_processor
cd dni_processor

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Configurar el entorno

Copiá la plantilla y ajustá:

```bash
cp .env.example .env
# Editá .env con tu editor preferido.
nano .env
```

Los defaults son razonables para single-user en LAN. Lo que típicamente
querés revisar:

- `DNI_HOST` — dejar en `127.0.0.1`. Tailscale alcanza loopback sin problema.
- `DNI_PORT` — cambiar si choca con Escriba u otro servicio. Default `8001`.
- `DNI_DATA_DIR` — donde viven las sesiones. Default `./data`, que con
  la `WorkingDirectory` del unit queda en
  `/home/hernan/dni_processor/data`. Asegurate que exista y sea escribible:

```bash
mkdir -p /home/hernan/dni_processor/data
```

---

## 4. Pre-descargar los modelos

EasyOCR baja ~500MB la primera vez que se instancia, y el detector de
caras otros ~10MB. Si no los pre-bajamos, la primera request real del
usuario se queda esperando 30-60s mientras el servicio descarga todo.
Mejor hacerlo ahora, antes de habilitar el servicio:

```bash
# Con el venv activado:
python scripts/preload_models.py
```

El script es **idempotente**: si los archivos ya están en cache, no
descarga nada. Útil cuando rehacés deploys.

Salida esperada al final:

```
Todos los modelos disponibles. Listo para deploy.
```

Los archivos quedan en:
- Detector de caras: `~/.cache/dni_processor/` (configurable via `DNI_MODEL_CACHE_DIR`)
- EasyOCR: `~/.EasyOCR/model/` (no configurable, es interno de la lib)

---

## 5. Smoke test manual

Antes de meter systemd al medio, verificá que el servicio arranca y
responde:

```bash
# Con el venv activado, desde /home/hernan/dni_processor:
python -m app.main
```

En otra terminal:

```bash
curl http://127.0.0.1:8001/api/v1/health
# Esperado:
# {"status":"ok","version":"0.4.0","models":{"face":true,"ocr":true}}
```

Si `status` es `"degraded"`, alguno de los modelos no está en cache —
volvé al paso 4. Si curl falla, mirá el output del `python -m app.main`
para ver el error.

`Ctrl+C` para parar el servidor manual antes de seguir.

---

## 6. Instalar y arrancar el servicio

```bash
# Copiar el unit file. Si tu path/user son distintos, editá el archivo
# antes de copiarlo o después de copiarlo (con `sudoedit`).
sudo cp deployment/dni_processor.service /etc/systemd/system/

# Recargar systemd para que vea el unit nuevo.
sudo systemctl daemon-reload

# Habilitar arranque al boot.
sudo systemctl enable dni_processor.service

# Arrancar ya.
sudo systemctl start dni_processor.service
```

Verificación rápida:

```bash
systemctl status dni_processor
# Debería decir "active (running)" con el PID y unos pocos segundos
# de uptime.

curl http://127.0.0.1:8001/api/v1/health
# Mismo output que en el smoke test.

journalctl -u dni_processor -n 20
# Las últimas 20 líneas del log. Tenés que ver el banner de Uvicorn
# y el mensaje "DNI Processor v0.4.0 arrancando..."
```

---

## 7. Acceso desde otra máquina del tailnet

Desde una máquina con Tailscale activo en el mismo tailnet:

```bash
curl http://<hostname-del-server>:8001/api/v1/health
```

Donde `<hostname-del-server>` es el nombre Tailscale del server
(`tailscale status` lo muestra). No hace falta abrir puertos en el
firewall del server — Tailscale enruta el tráfico al loopback.

En un browser podés abrir `http://<hostname>:8001/` y debería verse la
pantalla de upload.

---

## 8. Operación diaria

| Acción | Comando |
|---|---|
| Ver estado | `systemctl status dni_processor` |
| Ver logs en vivo | `journalctl -u dni_processor -f` |
| Ver logs históricos | `journalctl -u dni_processor --since yesterday` |
| Reiniciar | `sudo systemctl restart dni_processor` |
| Parar (sin disable) | `sudo systemctl stop dni_processor` |
| Deshabilitar al boot | `sudo systemctl disable dni_processor` |

---

## 9. Actualizar a una versión nueva

```bash
cd /home/hernan/dni_processor
sudo systemctl stop dni_processor

# Aplicar la nueva versión (git pull, o unzip del ZIP de release).
git pull
# o:
# unzip -o /tmp/dni_processor_vX.Y.Z.zip

# Si cambiaron las deps:
source .venv/bin/activate
pip install -r requirements.txt

# Re-correr el preload (idempotente; sólo descarga si falta algo nuevo).
python scripts/preload_models.py

# Si cambió el unit file:
sudo cp deployment/dni_processor.service /etc/systemd/system/
sudo systemctl daemon-reload

# Arrancar de nuevo.
sudo systemctl start dni_processor
systemctl status dni_processor
```

---

## 10. Backup

Single-user en LAN: el modelo de backup más simple es snapshot del
directorio del proyecto entero. Con las sesiones siendo ephemeral
(TTL 24h), normalmente no hace falta backup del `data/` — pero si
querés que un trámite a medio terminar sobreviva al reinicio, está
incluido en el snapshot.

```bash
# Ejemplo: rsync diario a otra máquina del tailnet.
rsync -av --delete \
    --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    /home/hernan/dni_processor/ \
    backup-host:/srv/backups/dni_processor/
```

`.venv` se reconstruye con `pip install -r requirements.txt`; los modelos
con `scripts/preload_models.py`. Lo importante es código + `.env` +
opcionalmente `data/` si te importa preservar trámites en curso.

---

## 11. Troubleshooting

### `systemctl status` dice "failed"

```bash
journalctl -u dni_processor -n 50 --no-pager
```

Causas típicas:
- **`ModuleNotFoundError`**: el venv no está bien armado o `ExecStart=`
  apunta a un python que no es el del venv. Verificá que existe
  `/home/hernan/dni_processor/.venv/bin/python` y que `pip list` adentro
  lista todas las deps.
- **`Address already in use`**: otro proceso (¿Escriba?) ya está en
  `DNI_PORT`. Cambialo en `.env` y reiniciá.
- **`PermissionError` sobre `data/`**: el unit corre como `hernan` y
  alguien (vos con sudo, alguna vez) dejó el dir con owner `root`.
  Arregla con `sudo chown -R hernan:hernan /home/hernan/dni_processor/data`.

### `/api/v1/health` devuelve `"status":"degraded"`

Alguno de los modelos no está en cache. Re-corré:

```bash
sudo -u hernan /home/hernan/dni_processor/.venv/bin/python \
    /home/hernan/dni_processor/scripts/preload_models.py
```

(Importante: como `hernan`, no como `root`, para que los archivos queden
con los permisos correctos.)

### El servicio responde por loopback pero no por Tailscale

- ¿`tailscale status` muestra el server up?
- ¿La otra máquina está en el mismo tailnet?
- ¿El firewall del server (ufw) está bloqueando el puerto? Tailscale
  inyecta reglas pero a veces ufw tiene la última palabra:

```bash
sudo ufw allow in on tailscale0
```

### Los logs no aparecen en tiempo real con `journalctl -f`

El unit ya setea `Environment=PYTHONUNBUFFERED=1` para evitar eso. Si
aún así no ves nada, probablemente el servicio no está escribiendo
nada (idle). Hacé una request al `/api/v1/health` para forzar una línea
de log.
