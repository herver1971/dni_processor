# tests/fixtures/images

Esta carpeta contiene imágenes para tests del pipeline.

## Política CRÍTICA — Imágenes reales NO se commitean

Las imágenes reales de DNIs **nunca** deben subirse al repositorio.
Esto es un requisito de privacidad y cumplimiento legal en contexto notarial.

El `.gitignore` raíz excluye automáticamente:

- `tests/fixtures/images/real/` — Carpeta dedicada para tu set local de pruebas
- Cualquier `*.jpg`, `*.jpeg`, `*.png`, `*.heic`, `*.webp` directo en esta carpeta

## Estructura recomendada

```
tests/fixtures/images/
├── README.md               (este archivo, sí se commitea)
├── real/                   (gitignored — tus imágenes locales)
│   ├── frentes/
│   └── dorsos/
└── synthetic/              (gitignored por defecto, ver más abajo)
```

## Imágenes sintéticas

Los tests unitarios generan sus propias imágenes sintéticas en runtime vía
`tests/conftest.py` (`_generate_synthetic_dni_image()`). No requieren archivos
en disco.

Si en algún momento querés generar un set sintético persistente para tests
de integración, podés:

1. Crearlo bajo `tests/fixtures/images/synthetic/`
2. Excluirlo manualmente del `.gitignore` con `!tests/fixtures/images/synthetic/`
3. Verificar que las imágenes no contengan datos personales

## Fase 2 — Calibración con set real

Para la Fase 2 vas a usar un set de ~20-30 imágenes reales. El procedimiento será:

1. Colocarlas en `tests/fixtures/images/real/frentes/` y `.../dorsos/`
2. Correr `pytest tests/integration -m real_data` (marcador que excluye estos
   tests de la batería regular)
3. **Nunca** hacer `git add` de esa carpeta — el `.gitignore` lo previene,
   pero verificá con `git status` antes de commitear.
