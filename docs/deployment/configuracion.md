# Configuración

Todas las variables de entorno del servicio. Se leen desde `.env` en la raíz del proyecto (o desde el entorno del proceso, con precedencia sobre `.env`).

Para crear el archivo de configuración:

```bash
cp .env.example .env
```

## Referencia completa

### Server

| Variable | Default | Descripción |
|---|---|---|
| `DNI_HOST` | `127.0.0.1` | Host de bind. **No cambiar** — el servicio no debe ser accesible directamente desde la red. Tailscale rutea el tráfico al loopback. |
| `DNI_PORT` | `8001` | Puerto. Cambiar si choca con otro servicio en el mismo servidor (por ejemplo Escriba). |

### Storage

| Variable | Default | Descripción |
|---|---|---|
| `DNI_DATA_DIR` | `./data` | Directorio raíz para datos persistentes. Relativo al `WorkingDirectory` del unit systemd, que es la raíz del proyecto. |
| `DNI_SESSIONS_DIR` | `<DATA_DIR>/sessions` | Subdir para sesiones. Si se omite, se calcula automáticamente. |
| `DNI_MODEL_CACHE_DIR` | `~/.cache/dni_processor` | Cache del detector de caras (ResNet-10 SSD). EasyOCR usa su propio cache en `~/.EasyOCR/model/` — no configurable. |

### Behavior

| Variable | Default | Descripción |
|---|---|---|
| `DNI_LOG_LEVEL` | `INFO` | Nivel de logging. Valores válidos: `DEBUG`, `INFO`, `WARNING`, `ERROR`. `DEBUG` loguea detalles del procesamiento (útil para diagnóstico, verboso en producción). |
| `DNI_RUN_OCR` | `true` | Si `false`, el matcheo no corre OCR y las sugerencias se generan solo por orden de upload. Útil para tests rápidos si el modelo de EasyOCR no está disponible. |

### Hardening

| Variable | Default | Descripción |
|---|---|---|
| `DNI_DEBUG` | `false` | Cuando es `true`, los templates emiten `data-debug="true"` en el `<html>`. Los scripts del frontend activan `console.log` diagnósticos. **Dejar en `false` en producción.** |
| `DNI_RATE_LIMIT_ENABLED` | `true` | Habilita slowapi sobre los endpoints "caros". En producción siempre `true`. Solo se desactiva en tests (via `monkeypatch`) para que invocaciones consecutivas no gatillen 429. |

## Seguridad del `.env`

El `.env` contiene la configuración de producción. Verificar que:

- Está en `.gitignore` (ya incluido por defecto en el proyecto)
- Los permisos son `600` (solo legible por el owner):

```bash
chmod 600 .env
```

El `.env.example` está versionado (con los defaults y comentarios, sin valores sensibles) para facilitar el onboarding.

## Configuración en systemd

El unit (`deployment/dni_processor.service`) no usa `EnvironmentFile=` para evitar duplicación — pydantic-settings lee el `.env` automáticamente desde el `WorkingDirectory`. La única variable que el unit setea por su cuenta es:

```ini
Environment=PYTHONUNBUFFERED=1
```

Esto garantiza que los logs llegan a `journald` en tiempo real sin buffering.
