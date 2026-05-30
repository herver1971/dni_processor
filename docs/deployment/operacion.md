# Operación y monitoreo

## Comandos de operación diaria

```bash
# Estado del servicio
systemctl status dni_processor

# Logs en vivo
journalctl -u dni_processor -f

# Últimas N líneas del log
journalctl -u dni_processor -n 50

# Logs desde ayer
journalctl -u dni_processor --since yesterday

# Reiniciar (por ejemplo después de actualizar .env)
sudo systemctl restart dni_processor

# Parar sin deshabilitar el arranque al boot
sudo systemctl stop dni_processor

# Deshabilitar el arranque al boot
sudo systemctl disable dni_processor
```

## Health check

El endpoint `/api/v1/health` es el punto de monitoreo programático:

```bash
curl http://127.0.0.1:8001/api/v1/health
```

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

| `status` | Significado | Acción |
|---|---|---|
| `"ok"` | Todo bien, modelos en cache | Ninguna |
| `"degraded"` | Falta algún modelo en cache | Correr `python scripts/preload_models.py` y reiniciar |

El endpoint siempre devuelve HTTP 200 — no confundir `"degraded"` con un servicio caído.

## Restart automático

El unit está configurado con `Restart=on-failure`. Si el proceso crashea, systemd lo reinicia después de 5 segundos.

Límite de reintentos: `StartLimitBurst=5` / `StartLimitIntervalSec=300`. Si el servicio crashea 5 veces en 5 minutos, systemd se rinde y marca el servicio como `failed`. Eso es intencionado: en lugar de un loop infinito silencioso, el servicio queda en estado `failed` que es visible con `systemctl status`.

Para forzar el inicio después de `failed`:

```bash
sudo systemctl reset-failed dni_processor
sudo systemctl start dni_processor
```

## Backup

Las sesiones son efímeras (TTL 24h) — normalmente no necesitás hacer backup del `data/` a menos que haya un trámite en curso que no puedas repetir.

Lo importante de versionar/backupear:

- El código (ya en git)
- El `.env` (NO en git — guardarlo separado o en un gestor de passwords)
- Los modelos ML en `~/.cache/dni_processor/` y `~/.EasyOCR/model/` (se pueden re-descargar con `preload_models.py`, pero tarda)

Ejemplo de rsync diario a otra máquina del tailnet:

```bash
rsync -av --delete \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='data/' \
    /home/hernan/Documentos/Proyectos_Github/dni_processor/ \
    backup-host:/srv/backups/dni_processor/
```

## Sesiones en curso

Si el servidor se reinicia mientras hay un trámite en curso, la sesión sigue existiendo en disco (con su UUID). El usuario puede continuar si:

1. Recuerda la URL (`/sessions/<uuid>/review` o `/sessions/<uuid>/match`)
2. La sesión no expiró (TTL 24h desde `updated_at`)

No hay mecanismo de recovery automático — si el usuario no recuerda la URL o expiró la sesión, tiene que empezar de nuevo.

## Monitoreo desde Escriba

Cuando se implemente la integración en Fase 5, Escriba puede hacer polling al health endpoint como sanity check:

```python
import httpx

r = httpx.get("http://127.0.0.1:8001/api/v1/health", timeout=5)
data = r.json()
if data["status"] != "ok":
    # alertar / loguear
    pass
```

Desde el mismo servidor: acceso directo a loopback, sin pasar por Tailscale.
