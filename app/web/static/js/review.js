/* ========================================================
   Review screen — Cropper.js + acciones por crop
   ========================================================

   Maneja dos modos de Cropper.js:

   1. AUTO-CROP MODE: para crops detectados automáticamente.
      La imagen mostrada es el "wide crop" amplio, y el rectángulo
      inicial se carga desde data-suggested-* (coords relativas al
      wide crop). Al confirmar, envía las coords actuales al endpoint
      /confirm.

   2. MANUAL-CROP MODE: para dorsos y frentes que fallaron detección.
      La imagen mostrada es la imagen original normalizada (post-EXIF).
      El usuario marca un rectángulo desde cero. Al hacer click en
      "Agregar recorte", envía las coords al endpoint /crops manual.
      Después del POST, refresca el partial via HTMX y permite marcar
      otro DNI en la misma imagen.

   ESTADO:
   - `croppers` mapea elemento → instancia de Cropper para destruirlas
     correctamente al refrescar.
   - `rotations` mapea (image_id o crop_id) → rotación acumulada (0/90/180/270).
   ======================================================== */

(function () {
    'use strict';

    const sessionId = document.querySelector('[data-session-id]').dataset.sessionId;

    // === Estado ===
    const croppers = new WeakMap();  // elemento img → instancia Cropper
    const rotations = new Map();     // id → grados acumulados (0/90/180/270)

    // ========================================================
    // Helpers
    // ========================================================

    function toast(message, kind = '') {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ` toast--${kind}` : '');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => el.remove(), 5000);
    }

    function refreshReview() {
        // HTMX se encarga del swap. Disparamos un evento custom para que el
        // contenedor escuchando con hx-trigger se actualice.
        if (window.htmx) {
            window.htmx.trigger(document.body, 'refreshReview');
        }
    }

    function destroyAllCroppers() {
        document.querySelectorAll('[data-component="auto-crop"], [data-component="manual-crop"]')
            .forEach((img) => {
                const cropper = croppers.get(img);
                if (cropper) {
                    cropper.destroy();
                    croppers.delete(img);
                }
            });
        // Limpiar el estado de rotaciones después de un swap completo del DOM
        // (los crop_id/image_id pueden seguir siendo los mismos pero las
        // instancias de Cropper se recrean desde cero en orientación normal).
        rotations.clear();
    }

    // ========================================================
    // Inicialización de croppers
    // ========================================================

    function initAutoCropper(img) {
        // Esperar a que la imagen cargue para tener dimensiones naturales
        const setup = () => {
            const naturalW = img.naturalWidth;
            const naturalH = img.naturalHeight;
            const sx = parseInt(img.dataset.suggestedX || 0);
            const sy = parseInt(img.dataset.suggestedY || 0);
            const sw = parseInt(img.dataset.suggestedW || naturalW);
            const sh = parseInt(img.dataset.suggestedH || naturalH);

            const cropper = new Cropper(img, {
                viewMode: 1,            // No permite que la crop box salga de la imagen
                dragMode: 'none',       // El usuario solo ajusta, no mueve la imagen
                aspectRatio: NaN,       // libre — usuario decide
                autoCropArea: 1,
                background: false,
                guides: true,
                center: true,
                highlight: false,
                cropBoxResizable: true,
                cropBoxMovable: true,
                toggleDragModeOnDblclick: false,
                responsive: true,
                ready() {
                    // Aplicar el bbox sugerido como crop inicial
                    cropper.setData({
                        x: sx,
                        y: sy,
                        width: sw,
                        height: sh,
                    });
                },
            });
            croppers.set(img, cropper);
        };

        if (img.complete && img.naturalWidth > 0) {
            setup();
        } else {
            img.addEventListener('load', setup, { once: true });
        }
    }

    function initManualCropper(img) {
        const setup = () => {
            const cropper = new Cropper(img, {
                viewMode: 1,
                dragMode: 'crop',       // El usuario dibuja desde cero
                aspectRatio: NaN,
                autoCrop: false,        // No empezar con un crop pre-marcado
                background: false,
                guides: true,
                center: true,
                highlight: false,
                toggleDragModeOnDblclick: false,
                responsive: true,
            });
            croppers.set(img, cropper);
        };

        if (img.complete && img.naturalWidth > 0) {
            setup();
        } else {
            img.addEventListener('load', setup, { once: true });
        }
    }

    function initAllCroppers() {
        document.querySelectorAll('[data-component="auto-crop"]').forEach(initAutoCropper);
        document.querySelectorAll('[data-component="manual-crop"]').forEach(initManualCropper);
    }

    // ========================================================
    // Acciones
    // ========================================================

    async function handleConfirm(cropId) {
        const img = document.querySelector(`img[data-crop-id="${cropId}"]`);
        const cropper = croppers.get(img);
        if (!cropper) {
            toast('Cropper no inicializado', 'danger');
            return;
        }
        const data = cropper.getData(true);  // true = redondear a enteros
        const rotation = rotations.get(cropId) || 0;

        const body = {
            final_bbox: {
                x: Math.max(0, data.x),
                y: Math.max(0, data.y),
                width: Math.max(1, data.width),
                height: Math.max(1, data.height),
            },
            rotation_degrees: rotation,
        };

        try {
            const resp = await fetch(
                `/api/v1/sessions/${sessionId}/crops/${cropId}/confirm`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                },
            );
            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error(`Falló confirmación: ${txt}`);
            }
            toast('Recorte confirmado', 'success');
            destroyAllCroppers();
            refreshReview();
        } catch (e) {
            console.error(e);
            toast(e.message || 'Error', 'danger');
        }
    }

    async function handleAddManualCrop(imageId, side) {
        const img = document.querySelector(`img[data-image-id="${imageId}"]`);
        const cropper = croppers.get(img);
        if (!cropper) {
            toast('Cropper no inicializado', 'danger');
            return;
        }
        const data = cropper.getData(true);
        if (!data.width || !data.height) {
            toast('Marcá un rectángulo primero', 'danger');
            return;
        }
        const rotation = rotations.get(imageId) || 0;

        const body = {
            bbox: {
                x: Math.max(0, data.x),
                y: Math.max(0, data.y),
                width: Math.max(1, data.width),
                height: Math.max(1, data.height),
            },
            side: side,
            rotation_degrees: rotation,
        };

        try {
            const resp = await fetch(
                `/api/v1/sessions/${sessionId}/images/${imageId}/crops`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                },
            );
            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error(`Falló creación: ${txt}`);
            }
            toast('Recorte agregado', 'success');
            destroyAllCroppers();
            refreshReview();
        } catch (e) {
            console.error(e);
            toast(e.message || 'Error', 'danger');
        }
    }

    async function handleDiscard(cropId) {
        try {
            const resp = await fetch(
                `/api/v1/sessions/${sessionId}/crops/${cropId}`,
                { method: 'DELETE' },
            );
            if (!resp.ok) throw new Error('No se pudo descartar');
            toast('Recorte descartado');
            destroyAllCroppers();
            refreshReview();
        } catch (e) {
            toast(e.message || 'Error', 'danger');
        }
    }

    function handleRotate(id, btn) {
        // Buscar la imagen asociada (puede estar identificada por crop-id o image-id)
        const img = document.querySelector(
            `img[data-crop-id="${id}"], img[data-image-id="${id}"]`,
        );
        if (!img) {
            toast('Imagen no encontrada para rotar', 'danger');
            return;
        }
        const cropper = croppers.get(img);
        if (!cropper) {
            toast('Cropper no inicializado', 'danger');
            return;
        }

        // Rotar visualmente la imagen 90° en sentido horario
        cropper.rotate(90);

        // Acumular en estado (0/90/180/270)
        const current = rotations.get(id) || 0;
        const next = (current + 90) % 360;
        rotations.set(id, next);

        // Actualizar el label y estilo del botón
        if (btn) {
            updateRotateButton(btn, next);
        }
    }

    function updateRotateButton(btn, degrees) {
        if (degrees === 0) {
            btn.textContent = 'Rotar 90°';
            btn.classList.remove('button--rotate-active');
        } else {
            btn.textContent = `↻ ${degrees}°`;
            btn.classList.add('button--rotate-active');
        }
    }

    async function handleDiscardSession(sid) {
        if (!confirm('¿Descartar esta sesión completa? Se borran todos los archivos.')) {
            return;
        }
        try {
            const resp = await fetch(`/api/v1/sessions/${sid}`, { method: 'DELETE' });
            if (!resp.ok) throw new Error('No se pudo descartar');
            window.location.href = '/';
        } catch (e) {
            toast(e.message || 'Error', 'danger');
        }
    }

    // ========================================================
    // Event delegation
    // ========================================================

    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.dataset.action;
        const cropId = btn.dataset.cropId;
        const imageId = btn.dataset.imageId;
        const side = btn.dataset.side;

        switch (action) {
            case 'confirm':
                handleConfirm(cropId);
                break;
            case 'discard':
            case 'discard-confirmed':
                if (confirm('¿Descartar este recorte?')) handleDiscard(cropId);
                break;
            case 'add-manual-crop':
                handleAddManualCrop(imageId, side);
                break;
            case 'rotate':
                handleRotate(cropId, btn);
                break;
            case 'rotate-manual':
                handleRotate(imageId, btn);
                break;
            case 'discard-session':
                handleDiscardSession(btn.dataset.sessionId);
                break;
        }
    });

    // ========================================================
    // Inicialización
    // ========================================================

    // Inicializar al cargar
    initAllCroppers();

    // Re-inicializar después de cada swap de HTMX (refresh del partial)
    document.body.addEventListener('htmx:afterSwap', (e) => {
        if (e.target.id === 'review-content') {
            initAllCroppers();
        }
    });
})();
