/* ========================================================
   Upload screen — drag-and-drop + submit
   ======================================================== */

(function () {
    'use strict';

    const ALLOWED_TYPES = [
        'image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif',
    ];
    const ALLOWED_EXTS = ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'];
    const MAX_SIZE_BYTES = 15 * 1024 * 1024;

    const form = document.getElementById('upload-form');
    const submitBtn = document.getElementById('upload-submit');
    const overlay = document.getElementById('processing-overlay');
    const processingMsg = document.getElementById('processing-message');
    const processingDetail = document.getElementById('processing-detail');

    // Estado: archivos por zona
    const filesByZone = { frentes: [], dorsos: [] };

    function isAllowed(file) {
        const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
        return ALLOWED_TYPES.includes(file.type) || ALLOWED_EXTS.includes(ext);
    }

    function updateZoneCount(zone) {
        const dropZone = document.querySelector(`[data-zone="${zone}"]`);
        const counter = dropZone.querySelector('.drop-zone__count');
        const n = filesByZone[zone].length;
        counter.dataset.count = n;
        counter.textContent = n === 1
            ? '1 archivo seleccionado'
            : `${n} archivos seleccionados`;
        updateSubmitState();
    }

    function updateSubmitState() {
        const total = filesByZone.frentes.length + filesByZone.dorsos.length;
        submitBtn.disabled = total === 0;
    }

    function addFiles(zone, fileList) {
        const valid = [];
        const invalid = [];
        for (const f of fileList) {
            if (!isAllowed(f)) {
                invalid.push(`${f.name} (tipo no soportado)`);
                continue;
            }
            if (f.size > MAX_SIZE_BYTES) {
                invalid.push(`${f.name} (excede 15 MB)`);
                continue;
            }
            valid.push(f);
        }
        filesByZone[zone] = filesByZone[zone].concat(valid);
        updateZoneCount(zone);
        if (invalid.length) {
            toast(`Archivos descartados: ${invalid.join(', ')}`, 'danger');
        }
    }

    // === Drag and drop ===
    document.querySelectorAll('.drop-zone').forEach((dropZone) => {
        const zone = dropZone.dataset.zone;
        const input = dropZone.querySelector('input[type=file]');

        // El input file dispara change cuando seleccionan archivos
        input.addEventListener('change', (e) => {
            addFiles(zone, e.target.files);
            // Limpiamos el input para que el usuario pueda agregar más archivos
            // del mismo nombre si quiere
            e.target.value = '';
        });

        // Eventos drag-and-drop sobre la zona (el browser ya los maneja via
        // label+input, pero queremos feedback visual con la clase --dragover)
        ['dragenter', 'dragover'].forEach((ev) => {
            dropZone.addEventListener(ev, (e) => {
                e.preventDefault();
                dropZone.classList.add('drop-zone--dragover');
            });
        });
        ['dragleave', 'drop'].forEach((ev) => {
            dropZone.addEventListener(ev, (e) => {
                e.preventDefault();
                dropZone.classList.remove('drop-zone--dragover');
            });
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            if (e.dataTransfer.files && e.dataTransfer.files.length) {
                addFiles(zone, e.dataTransfer.files);
            }
        });
    });

    // === Submit ===
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!filesByZone.frentes.length && !filesByZone.dorsos.length) {
            return;
        }

        overlay.classList.remove('hidden');

        try {
            // 1. Crear sesión
            processingMsg.textContent = 'Creando sesión…';
            processingDetail.textContent = '';
            const sessionResp = await fetch('/api/v1/sessions', { method: 'POST' });
            if (!sessionResp.ok) throw new Error('No se pudo crear la sesión');
            const session = await sessionResp.json();
            const sessionId = session.session_id;

            // 2. Subir frentes (si hay)
            if (filesByZone.frentes.length) {
                processingMsg.textContent = 'Subiendo frentes…';
                processingDetail.textContent = `0 / ${filesByZone.frentes.length}`;
                await uploadFiles(sessionId, 'frente', filesByZone.frentes, (i) => {
                    processingDetail.textContent = `${i} / ${filesByZone.frentes.length}`;
                });
            }

            // 3. Subir dorsos (si hay)
            if (filesByZone.dorsos.length) {
                processingMsg.textContent = 'Subiendo dorsos…';
                processingDetail.textContent = `0 / ${filesByZone.dorsos.length}`;
                await uploadFiles(sessionId, 'dorso', filesByZone.dorsos, (i) => {
                    processingDetail.textContent = `${i} / ${filesByZone.dorsos.length}`;
                });
            }

            // 4. Disparar detección automática (solo si hay frentes)
            if (filesByZone.frentes.length) {
                processingMsg.textContent = 'Detectando DNIs en frentes…';
                processingDetail.textContent = 'esto puede tardar unos segundos';
                const procResp = await fetch(
                    `/api/v1/sessions/${sessionId}/process`,
                    { method: 'POST' },
                );
                if (!procResp.ok) throw new Error('Falló el procesamiento');
            }

            // 5. Redirigir a la pantalla de revisión
            processingMsg.textContent = 'Listo';
            processingDetail.textContent = 'redirigiendo…';
            window.location.href = `/sessions/${sessionId}/review`;

        } catch (err) {
            console.error(err);
            overlay.classList.add('hidden');
            toast(err.message || 'Error inesperado', 'danger');
        }
    });

    /**
     * Sube archivos en BATCHES (todos juntos en una sola request por side).
     * El backend ya soporta múltiples files en un solo POST.
     */
    async function uploadFiles(sessionId, side, files, onProgress) {
        const formData = new FormData();
        formData.append('side', side);
        for (const f of files) {
            formData.append('files', f);
        }
        const resp = await fetch(`/api/v1/sessions/${sessionId}/images`, {
            method: 'POST',
            body: formData,
        });
        if (!resp.ok) {
            const text = await resp.text();
            throw new Error(`Falló el upload de ${side}s: ${text}`);
        }
        const data = await resp.json();
        if (onProgress) onProgress(data.uploaded.length);
        if (data.skipped && data.skipped.length) {
            const names = data.skipped.map(s => s.filename).join(', ');
            toast(`Archivos saltados: ${names}`, 'danger');
        }
    }

    /**
     * Muestra un toast efímero.
     */
    function toast(message, kind = '') {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ` toast--${kind}` : '');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => el.remove(), 5000);
    }
})();
