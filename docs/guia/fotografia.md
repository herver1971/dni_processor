# Consejos para fotografiar DNIs

La calidad de las fotos impacta directamente en la tasa de detección automática y en la nitidez del PDF final. Esta guía resume lo que funcionó mejor en las pruebas con imágenes reales.

## Lo que el sistema necesita

El detector de caras (ResNet-10 SSD) necesita:

1. **La cara del titular visible y sin obstrucciones** — Ni dedos, ni sombras sobre la cara, ni reflejos que la oculten.
2. **Suficiente resolución** — Una foto de 2 MP o más es suficiente. Las cámaras de celular actuales tienen de sobra.
3. **El DNI dentro del frame** — No hace falta que sea el único objeto en la foto, pero tiene que estar completo o casi completo.

## Recomendaciones de toma

**Iluminación:**

- Luz natural difusa es la mejor opción. Una ventana lateral (no directa) ilumina de forma pareja.
- Evitá flash directo — genera reflejo especular sobre la lámina del DNI que puede borrar la foto.
- Evitá mezclar fluorescente y natural — genera colores inconsistentes, aunque no afecta la detección.

**Ángulo:**

- Lo más perpendicular posible al plano del DNI, sin necesidad de ser exacto.
- El sistema tolera inclinaciones de hasta ~15° en cualquier eje sin problemas.
- Inclinaciones mayores van a ser detectadas igual, pero el recorte va a quedar con más fondo visible.

**Distancia:**

- El DNI tiene que ocupar al menos 30% del ancho de la foto para que la cara sea detectable.
- No hace falta rellenar el frame — podés tener varios DNIs en la misma foto.

**Fondo:**

- Fondo neutro y con contraste respecto al DNI ayuda, pero no es requisito.
- El detector trabaja por cara, no por borde del DNI, así que el fondo no afecta la detección.

## Casos que el sistema maneja

| Situación | Resultado |
|---|---|
| DNI recto, buena luz | Detección automática exitosa |
| DNI levemente inclinado (≤15°) | Detección exitosa, el recorte incluirá algo de fondo |
| DNI muy inclinado o rotado 90° | El sistema prueba rotaciones; si detecta, reporta la orientación encontrada |
| Múltiples DNIs en una foto | Detecta cada cara por separado, genera un recorte por DNI |
| Foto con EXIF de rotación (típico en celulares) | Corregida automáticamente antes de procesar |
| Reflejo leve sobre la cara | La mayoría de los casos se detectan igual con el threshold permisivo (0.3) |
| Cara parcialmente obstruida | Puede fallar → el usuario recorta manualmente |
| DNI en bolsillo plástico | Puede fallar si hay mucho reflejo sobre la cara |

## Si la detección falla

La detección automática falla cuando:

- La cara está muy obstruida (dedo, objeto, reflejo fuerte).
- El DNI está en ángulo extremo (>30°).
- La foto está muy oscura o muy sobreexpuesta.

En ese caso la imagen aparece marcada como "detección fallida" en la pantalla de revisión, y el sistema te muestra la foto completa para que recortes el frente manualmente con Cropper.js. No hay penalidad — el flujo continúa normal.

## Sobre los dorsos

Los dorsos no tienen cara, así que **siempre** se recortan manualmente. No hay detección automática. El flujo:

1. Subís las fotos de dorsos.
2. En la pantalla de revisión, cada foto de dorso aparece en Cropper.js.
3. Dibujás el rectángulo sobre el DNI y confirmás.

Recomendación: para los dorsos la foto puede ser más relajada — lo que importa es que el código de barras o la información impresa sea legible, pero el sistema no la lee (el matcheo usa el número del frente).
