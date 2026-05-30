# Sesiones y estado

DNI Processor no tiene base de datos. El estado de cada trámite vive en un directorio efímero en disco.

## Estructura en disco

```
data/
└── sessions/
    └── <uuid>/
        ├── session.json          # Estado completo de la sesión
        ├── originals/            # Fotos normalizadas EXIF
        │   ├── <image_id>.jpg
        │   └── ...
        ├── crops/
        │   ├── wide/             # Recortes amplios (auto-generados por el detector)
        │   │   └── <crop_id>.jpg
        │   └── final/            # Recortes finales confirmados por el usuario
        │       └── <crop_id>.jpg
        └── output.pdf            # PDF generado al completar
```

Cada imagen subida se renombra internamente a un UUID para evitar colisiones y eliminar cualquier información del nombre original en las rutas internas. El nombre original se preserva sólo en los metadatos del `session.json`.

## `session.json`

El archivo de estado serializa el schema `SessionState` (ver `app/schemas/web.py`):

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "review",
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:31:45Z",
  "images": {
    "<image_id>": {
      "image_id": "...",
      "original_filename": "foto_01.jpg",
      "declared_side": "frente",
      "status": "detected",
      "normalized_path": "originals/<image_id>.jpg",
      "crop_ids": ["<crop_id_1>", "<crop_id_2>"]
    }
  },
  "crops": {
    "<crop_id>": {
      "crop_id": "...",
      "source_image_id": "...",
      "side": "frente",
      "status": "confirmed",
      "wide_crop_path": "crops/wide/<crop_id>.jpg",
      "final_crop_path": "crops/final/<crop_id>.jpg",
      "suggested_bbox": {"x": 10, "y": 15, "width": 320, "height": 200},
      "final_bbox": {"x": 12, "y": 14, "width": 318, "height": 202},
      "rotation_degrees": 0,
      "dni_number": null
    }
  },
  "pairs": {
    "<pair_id>": {
      "pair_id": "...",
      "frente_crop_id": "...",
      "dorso_crop_id": "...",
      "position": 0,
      "origin": "ocr_match",
      "match_distance": 1
    }
  }
}
```

## Escritura atómica

Toda actualización del estado usa la secuencia:

```python
# Escribir a archivo temporal en el mismo filesystem
tmp = session_file.with_suffix(".tmp")
tmp.write_text(json.dumps(state.model_dump(), ...))
# Rename atómico (garantía del kernel)
tmp.rename(session_file)
```

El rename atómico garantiza que nunca se lee un `session.json` parcialmente escrito, incluso si el proceso es interrumpido entre writes.

## TTL y cleanup automático

Las sesiones tienen un TTL de **24 horas** contado desde `updated_at`. Un task en background corre cada `CLEANUP_INTERVAL_MINUTES` minutos y llama a `cleanup_expired_sessions()`, que elimina el directorio completo de cada sesión expirada.

El cleanup se implementa como `asyncio.Task` en el `lifespan` del servidor:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_loop())
    yield
    cleanup_task.cancel()
```

Si el servidor se reinicia (por ejemplo por deploy de una versión nueva), la sesión puede expirar entre reinicios si pasaron más de 24 horas. No hay mecanismo de persistencia entre reinicios — las sesiones en estado `COMPLETED` ya tienen su PDF descargado, y las en estado intermedio requieren que el usuario empiece de nuevo.

## API de sesiones — módulo `app/core/sessions.py`

Las funciones principales que el resto del código usa:

| Función | Descripción |
|---|---|
| `create_session()` | Crea un nuevo directorio y `session.json` vacío |
| `load_session(session_id)` | Deserializa `session.json` → `SessionState` |
| `save_session(state, paths)` | Serializa `SessionState` → `session.json` (atómico) |
| `discard_session(session_id)` | Elimina el directorio completo |
| `cleanup_expired_sessions(sessions_dir)` | Elimina sesiones con TTL expirado |
| `add_image_to_session(...)` | Agrega una imagen al estado de la sesión |
| `add_crop_to_session(...)` | Agrega un crop al estado de la sesión |

La clase `SessionPaths` resuelve todos los paths relativos a una sesión dado su ID, centralizando la lógica de layout en disco.
