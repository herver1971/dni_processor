# DNI Processor

**Versión 0.4.0** · Servicio auto-alojado para uso notarial

---

DNI Processor es un servicio web que toma fotografías de DNIs argentinos, las organiza en pares frente+dorso, y genera un PDF A4 listo para imprimir.

<div class="grid cards" markdown>

-   :material-shield-lock: **Privacidad total**

    ---

    Procesamiento 100% local. Las imágenes nunca salen del servidor. Acceso restringido por Tailscale.

-   :material-file-document-check: **Integridad documental**

    ---

    Sin warp ni transformaciones de perspectiva. El DNI aparece en el PDF exactamente como fue fotografiado.

-   :material-human-greeting: **Flujo asistido**

    ---

    Detección automática de frentes por visión por computadora. Revisión y corrección manual integrada en la UI.

-   :material-file-pdf-box: **PDF A4 imprimible**

    ---

    4 pares por hoja, a escala real ID-1 (85.6 × 54 mm), listo para archivar o adjuntar a expedientes.

</div>

## Flujo en cinco pasos

```
1. Subir fotos  →  2. Revisión de recortes  →  3. Emparejar  →  4. Generar PDF  →  5. Descargar
```

1. **Subir fotos** — Subís las fotos de frentes y dorsos por separado (pueden ser varias por imagen).
2. **Revisión de recortes** — El servicio detecta automáticamente los frentes con detección facial. Revisás cada recorte y ajustás el encuadre si hace falta. Los dorsos los recortás manualmente.
3. **Emparejar** — El servicio usa OCR sobre el número del DNI para sugerir los pares frente↔dorso. Podés reordenarlos arrastrando.
4. **Generar PDF** — Con un click se genera el A4.
5. **Descargar** — Descargás el PDF y el servicio se resetea para el siguiente trámite.

## Estado del proyecto

| Ítem | Estado |
|---|---|
| Versión actual | `0.4.0` |
| Tests | 168 pasando |
| Deployment | systemd + Tailscale |
| Integración con Escriba | Planificada (Fase 5) |

## Navegación rápida

- ¿Primera vez? → [Quickstart](guia/quickstart.md)
- ¿Vas a hacer deploy? → [Instalación en Kubuntu](deployment/instalacion.md)
- ¿Querés entender cómo funciona? → [Pipeline de procesamiento](arquitectura/pipeline.md)
- ¿Integrando con Escriba? → [Referencia API](referencia/api.md)
