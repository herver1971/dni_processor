# ROADMAP — DNI Processor

**Versión actual:** 0.4.0  
**Última actualización:** 2026-05-30  
**Estado general:** En producción (modo standalone). Integración con Escriba pendiente.

---

## Estado de un vistazo

| Fase | Descripción | Estado | Versión |
|---|---|---|---|
| 0 | Diseño y decisiones de arquitectura | ✅ Completa | — |
| 1 | MVP del pipeline (CLI) | ✅ Completa | v0.1.0 |
| 2 | Calibración con imágenes reales | ✅ Completa | v0.2.0 |
| 3 | Capa web (FastAPI + HTMX) | ✅ Completa | v0.3.x |
| 4 | Clasificación automática frente/dorso | ❌ Descartada | — |
| 5 | Integración con Escriba | ⏳ Pendiente | v0.5.0 |
| 6 | Deployment y hardening | ✅ Completa | v0.4.0 |

**Lo que falta para v1.0.0:** Únicamente la Fase 5 (integración con Escriba).

---

## Qué hay en producción hoy (v0.4.0)

El servicio corre como systemd en el servidor Kubuntu, accesible vía Tailscale en `https://hernan-disco.tail778471.ts.net:8443/`. Flujo completo operativo:

1. El usuario sube fotos de frentes y dorsos por separado.
2. El servicio detecta automáticamente los frentes usando el detector de caras ResNet-10 SSD.
3. El usuario revisa cada recorte, ajusta el encuadre con Cropper.js, y recorta los dorsos manualmente.
4. El servicio sugiere pares frente↔dorso usando OCR + distancia Levenshtein.
5. El usuario reordena los pares si hace falta (drag-and-drop).
6. Se genera el PDF A4 (4 pares por hoja, escala real ID-1, sin texto agregado).

---

## Qué falta hacer

### Fase 5 — Integración con Escriba ⏳

**Versión objetivo:** v0.5.0  
**Prerequisito:** Decidir si la integración va por API (Escriba llama a DNI Processor) o por UI (el usuario abre DNI Processor desde un link en Escriba).

**Opción A — Integración por API** (más compleja, más seamless)

DNI Processor expone un endpoint nuevo que Escriba invoca pasando el `operacion_id`. El PDF generado se escribe directamente en la carpeta de la operación. El usuario puede necesitar abrir la pantalla de revisión si la detección automática no fue suficiente.

Endpoint propuesto:
```
POST /api/v1/process
  operacion_id: str
  output_path: str
  frentes[]: file[]
  dorsos[]: file[]
```

Cambios requeridos en Escriba (sprint separado):
- Botón "Procesar DNIs" en la vista de operación
- Llamada HTTP al servicio
- Si necesita revisión, abrir la URL en nueva pestaña
- Guardar el PDF resultante en el expediente

**Opción B — Integración por UI** (más simple, suficiente para el caso de uso)

Un link desde Escriba abre DNI Processor en una nueva pestaña. El usuario trabaja en DNI Processor, descarga el PDF, y lo sube manualmente al expediente en Escriba. No requiere cambios en la API de DNI Processor.

**Pendientes de decisión antes de implementar:**
- [ ] Confirmar qué opción (A o B) es preferida
- [ ] Si opción A: definir cómo manejar la revisión manual dentro del flujo integrado
- [ ] Tailscale ACL específica para autorizar las llamadas de Escriba a DNI Processor
- [ ] Decidir si se registra el procesamiento en un audit log (hoy descartado, evaluar si hay requisito notarial real)

---

## Deuda técnica y mejoras menores pendientes

Estas tareas no bloquean el uso actual pero serían buenas de resolver. Sin orden de prioridad:

- [ ] **README.md** desactualizado — todavía describe la v0.1.0 con CLI. Actualizarlo para reflejar v0.4.0 con web UI y deployment.
- [ ] **Cobertura de tests** — objetivo ≥70% sobre `app/core/`. No se midió formalmente; los módulos con menor cobertura estimada son `pipeline.py` y `composer.py`.
- [ ] **Clasificación automática frente/dorso** — originalmente era Fase 4, se descartó porque el flujo de dos zonas de upload (frentes separado de dorsos) es suficiente en la práctica. Evaluar si aparece demanda real.
- [ ] **Acceso por subpath en lugar de puerto alternativo** — hoy DNI Processor está en `:8443` y Escriba en `:443`. Ponerlos en el mismo puerto con paths distintos (`/dni-processor/`) requiere configurar `root_path` en FastAPI y refactorear los templates para usar `url_for()` en lugar de paths absolutos.
- [ ] **Validación de MIME via magic bytes** — hoy sólo se valida extensión. Bajo riesgo (single-user, Tailscale) pero sería más robusto.
- [ ] **README_DEPLOY.md actualizado para nuevo path** — el deploy actual usa `/home/hernan/Documentos/Proyectos_Github/dni_processor` pero el README original documentaba `/home/hernan/dni_processor`.

---

## Historial de fases completadas

### Fase 0 — Diseño y decisiones ✅

Definición del stack, topología, contratos de API, estructura de carpetas, convenciones de versionado. Resultado: este ROADMAP original.

**Decisiones que sobrevivieron:** FastAPI + Jinja2 + HTMX, OpenCV DNN para detección, EasyOCR para OCR, FPDF2 para PDF, Pydantic v2, sin base de datos (estado en JSON en disco), Tailscale para acceso remoto.

**Decisiones que cambiaron en la práctica:**
- Detección de DNI por bordes (Canny + contornos) → reemplazada por detección facial en v0.2.1 (23% de tasa con contornos, 100% con detección facial en set real).
- SQLite para auditoría → descartado, las sesiones son efímeras y Escriba puede registrar por su lado.
- Clasificación automática frente/dorso → descartada, dos zonas de upload es suficiente.

---

### Fase 1 — MVP CLI ✅ (v0.1.0)

Pipeline funcional vía línea de comandos: `scripts/process_batch.py` recibe dos carpetas (frentes y dorsos) y genera el PDF. Sin web.

Implementó: `vision.py` (detección por contornos, primer intento), `ocr.py` (EasyOCR), `matcher.py` (Levenshtein), `composer.py` (layout A4).

---

### Fase 2 — Calibración con imágenes reales ✅ (v0.2.0)

Pivote arquitectónico mayor: la detección por contornos Canny tuvo 23% de tasa en imágenes reales. Se reemplazó completamente por el detector facial ResNet-10 SSD (OpenCV DNN).

El detector de caras con la cascada de fallbacks (imagen original → CLAHE → rotaciones 90°/270°/180°) alcanzó 100% de detección sobre el set de calibración de 18 imágenes.

Se agregó `geometry.py` para calcular el bbox del DNI completo a partir del bbox de la cara usando la geometría conocida del DNI argentino tarjeta.

---

### Fase 3 — Capa web ✅ (v0.3.x)

Web UI completa con FastAPI + Jinja2 + HTMX. Cuatro pantallas: upload, revisión de recortes, matcheo, descarga del PDF. Cropper.js para el editor de recortes, SortableJS para el reordenamiento de pares.

El sprint se dividió en sub-entregas: 2a (backend API REST), 2b (UI web), 3a (matcheo backend), 3b (matcheo UI). Varios patches intermedios (2.x, 3.x) para ajustes de UX e integración.

Tests: 150 pasando al cierre de v0.3.1b.4.

---

### Fase 4 — Clasificación automática frente/dorso ❌ Descartada

Originalmente planificada para eliminar la división en dos zonas de upload. Descartada porque el flujo con dos zonas separadas (el usuario declara si cada lote es de frentes o dorsos) funciona bien en la práctica y no genera fricción significativa. El esfuerzo de implementar el clasificador no justificó el beneficio.

---

### Fase 6 — Deployment y hardening ✅ (v0.3.2 + v0.4.0)

Ejecutado en dos sub-entregas:

**Sprint 4a — v0.3.2 (hardening de app):**
- Security headers (CSP estricta, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy)
- Rate limiting con slowapi (configurable por env var, desactivable en tests)
- RequestSizeLimitMiddleware (fail-fast 413 por Content-Length)
- DEBUG configurable via `data-debug` en `<html>` (compatible con CSP sin `'unsafe-inline'`)
- Fix de `datetime.utcnow()` deprecated
- 13 tests nuevos de seguridad

**Sprint 4b — v0.4.0 (deployment a producción):**
- `deployment/dni_processor.service`: systemd unit con hardening del proceso (NoNewPrivileges, ProtectSystem, PrivateTmp, etc.)
- `scripts/preload_models.py`: pre-descarga idempotente de modelos EasyOCR y detector de caras
- `.env.example`: plantilla con todas las env vars documentadas
- `deployment/README_DEPLOY.md`: guía paso a paso para Kubuntu
- Health endpoint enriquecido: reporta presencia de modelos en cache (`{status, version, models: {face, ocr}}`)
- Documentación MkDocs completa para GitHub Pages
- 5 tests nuevos del health endpoint

---

## Principios de diseño (resumen)

Estas decisiones son fijas y no cambian con nuevas fases:

**Integridad documental** — Solo recortes rectangulares axis-aligned y rotaciones múltiplo de 90°. Sin warp, sin deskew, sin interpolación. Si el DNI está inclinado en la foto, queda inclinado en el PDF.

**Matcheo conservador** — Ante la duda, huérfano explícito. Un match incorrecto (frente de A con dorso de B) es peor que pedirle al usuario que lo resuelva manualmente. Threshold Levenshtein = 2.

**OCR como sugerencia, no como verdad** — El número de DNI extraído por OCR sólo se usa para sugerir pares. No se almacena en ningún log persistente, no aparece en el PDF.

**Privacidad por diseño** — Procesamiento 100% local. Las imágenes nunca salen del servidor. Acceso restringido por Tailscale.

**Sin base de datos** — Estado en JSON en disco, sesiones efímeras con TTL 24h. Sin migraciones, sin Alembic, backup simple con rsync.
