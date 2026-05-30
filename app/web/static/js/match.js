/* ========================================================
   Match screen — Sprint 4a (v0.3.2)
   Drag-and-drop con SortableJS + diagnóstico configurable

   Cambios sobre v0.3.1b.2:
   - DEBUG ya no es constante hardcodeada. Se lee de
     `document.documentElement.dataset.debug` (atributo `data-debug` en
     <html>, inyectado por base.html cuando `Settings.debug` es True).
     En producción queda apagado por default.
   - Mantiene comportamiento histórico: `draggable` explícito en cada
     Sortable.create(), logs en cada evento del DnD, handleSwap se
     invoca desde onAdd y onUpdate, "swap visual" antes de la petición.
   ======================================================== */

(function () {
    'use strict';

    const sessionId = document.querySelector('[data-session-id]').dataset.sessionId;
    const SORTABLE_GROUP_DORSOS = 'dorsos';
    // Configurable via DNI_DEBUG env var → Settings.debug → data-debug en <html>.
    const DEBUG = document.documentElement.dataset.debug === 'true';

    function log() {
        if (!DEBUG) return;
        const args = Array.prototype.slice.call(arguments);
        console.log.apply(console, ['[match]'].concat(args));
    }

    // ========================================================
    // Helpers
    // ========================================================

    function toast(message, kind) {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ' toast--' + kind : '');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(function () { el.remove(); }, 5000);
    }

    function refreshMatch() {
        log('Disparando refreshMatch (HTMX)');
        if (window.htmx) {
            window.htmx.trigger(document.body, 'refreshMatch');
        }
    }

    function collectPairsFromDom() {
        const rows = document.querySelectorAll('#pair-list .pair-row');
        const pairs = [];
        rows.forEach(function (row, index) {
            const frenteId = row.dataset.frenteId;
            const dorsoSlot = row.querySelector('.dorso-slot');
            const dorsoCard = dorsoSlot ? dorsoSlot.querySelector('[data-crop-id]') : null;
            if (!dorsoCard) {
                log('Fila sin dorso después de DnD:', frenteId, '(será descartada en payload)');
                return;
            }
            pairs.push({
                frente_crop_id: frenteId,
                dorso_crop_id: dorsoCard.dataset.cropId,
                position: index,
            });
        });
        log('collectPairsFromDom →', pairs);
        return pairs;
    }

    async function sendPairsUpdate() {
        const pairs = collectPairsFromDom();
        log('Enviando PUT /pairs con', pairs.length, 'pares');
        try {
            const resp = await fetch(
                '/api/v1/sessions/' + sessionId + '/pairs',
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pairs: pairs }),
                },
            );
            log('Respuesta PUT /pairs:', resp.status);
            if (!resp.ok) {
                const txt = await resp.text();
                log('PUT /pairs falló:', txt);
                throw new Error('PUT /pairs falló: ' + txt);
            }
            const data = await resp.json();
            log('PUT /pairs OK,', data.pairs.length, 'pares persistidos');
            refreshMatch();
        } catch (err) {
            console.error('[match] Error:', err);
            toast(err.message || 'Error al actualizar pares', 'danger');
            refreshMatch();
        }
    }

    // ========================================================
    // SortableJS: reordenamiento vertical de filas
    // ========================================================

    function initPairListSortable() {
        const list = document.getElementById('pair-list');
        if (!list || typeof Sortable === 'undefined') {
            log('initPairListSortable: lista no encontrada o Sortable no disponible');
            return;
        }
        Sortable.create(list, {
            handle: '.pair-row__handle',
            draggable: '.pair-row',
            animation: 150,
            ghostClass: 'sortable-ghost',
            chosenClass: 'sortable-chosen',
            onStart: function (evt) {
                log('Pair-list onStart: arrastrando fila', evt.oldIndex);
            },
            onEnd: function (evt) {
                log('Pair-list onEnd:', evt.oldIndex, '→', evt.newIndex);
                if (evt.oldIndex !== evt.newIndex) {
                    sendPairsUpdate();
                }
            },
        });
        log('initPairListSortable OK');
    }

    // ========================================================
    // SortableJS: swap de dorsos entre slots y huérfanos
    // ========================================================

    function handleSwapOrMove(evt) {
        const target = evt.to;
        const source = evt.from;
        const movedItem = evt.item;

        log('handleSwapOrMove:', {
            from: source.id || source.className,
            to: target.id || target.className,
            item: movedItem.dataset && movedItem.dataset.cropId,
        });

        // Buscar otros elementos en el target además del que entró
        const targetChildren = Array.from(target.children).filter(function (el) {
            return el !== movedItem && el.dataset && el.dataset.cropId;
        });

        const isTargetSlot = target.classList.contains('dorso-slot');
        log('Target es slot:', isTargetSlot, '— elementos sobrantes:', targetChildren.length);

        if (isTargetSlot && targetChildren.length > 0) {
            const displaced = targetChildren[0];
            log('Haciendo swap visual: moviendo', displaced.dataset.cropId, 'al source');
            source.appendChild(displaced);
        }

        sendPairsUpdate();
    }

    function initDorsoSlotsSortable() {
        if (typeof Sortable === 'undefined') {
            log('SortableJS no está disponible');
            return;
        }

        const slots = document.querySelectorAll('.dorso-slot');
        log('Inicializando', slots.length, 'dorso-slots como Sortable');

        slots.forEach(function (slot, idx) {
            Sortable.create(slot, {
                group: { name: SORTABLE_GROUP_DORSOS, pull: true, put: true },
                draggable: '.pair-card--dorso',
                animation: 150,
                // Aceptar drops aunque el slot ya tenga un elemento
                // (necesario para hacer swap entre slots ocupados)
                emptyInsertThreshold: 8,
                swapThreshold: 0.65,
                ghostClass: 'sortable-ghost-dorso',
                chosenClass: 'sortable-chosen-dorso',
                onStart: function (evt) {
                    log('Slot[' + idx + '] onStart: agarrando dorso', evt.item.dataset.cropId);
                },
                onAdd: function (evt) {
                    log('Slot[' + idx + '] onAdd: recibió', evt.item.dataset.cropId);
                    handleSwapOrMove(evt);
                },
                onEnd: function (evt) {
                    log('Slot[' + idx + '] onEnd');
                },
            });
        });

        const orphans = document.getElementById('orphan-dorsos');
        if (orphans) {
            log('Inicializando #orphan-dorsos como Sortable');
            Sortable.create(orphans, {
                group: { name: SORTABLE_GROUP_DORSOS, pull: true, put: true },
                draggable: '.orphan-card--draggable',
                animation: 150,
                emptyInsertThreshold: 8,
                ghostClass: 'sortable-ghost-dorso',
                chosenClass: 'sortable-chosen-dorso',
                onStart: function (evt) {
                    log('Orphans onStart: agarrando', evt.item.dataset.cropId);
                },
                onAdd: function (evt) {
                    log('Orphans onAdd: recibió', evt.item.dataset.cropId);
                    handleSwapOrMove(evt);
                },
            });
        }
    }

    // ========================================================
    // Lightbox
    // ========================================================

    function initLightbox() {
        let lightbox = document.getElementById('lightbox');
        if (lightbox) return;
        lightbox = document.createElement('div');
        lightbox.id = 'lightbox';
        lightbox.className = 'lightbox hidden';
        lightbox.innerHTML =
            '<button class="lightbox__close" aria-label="Cerrar">×</button>' +
            '<img class="lightbox__image" alt="Vista ampliada">' +
            '<div class="lightbox__meta"></div>';
        document.body.appendChild(lightbox);

        lightbox.addEventListener('click', function (e) {
            if (e.target === lightbox || e.target.classList.contains('lightbox__close')) {
                closeLightbox();
            }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !lightbox.classList.contains('hidden')) {
                closeLightbox();
            }
        });
    }

    function openLightbox(imgSrc, dniNumber) {
        const lightbox = document.getElementById('lightbox');
        if (!lightbox) return;
        lightbox.querySelector('.lightbox__image').src = imgSrc;
        const meta = lightbox.querySelector('.lightbox__meta');
        if (dniNumber) {
            meta.innerHTML =
                '<span class="lightbox__meta-label">leído por OCR — verificá</span>' +
                '<span class="lightbox__meta-value">' + dniNumber + '</span>';
        } else {
            meta.innerHTML = '<span class="lightbox__meta-label">sin lectura OCR</span>';
        }
        lightbox.classList.remove('hidden');
    }

    function closeLightbox() {
        const lightbox = document.getElementById('lightbox');
        if (lightbox) lightbox.classList.add('hidden');
    }

    // ========================================================
    // Click handlers
    // ========================================================

    document.addEventListener('click', function (e) {
        const img = e.target.closest('.pair-card__image, .orphan-card__image');
        if (img) {
            const card = img.closest('[data-crop-id]');
            const dniNumber = card ? card.dataset.dniNumber : null;
            openLightbox(img.src, dniNumber);
            return;
        }

        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        switch (btn.dataset.action) {
            case 'generate-suggestions': handleGenerateSuggestions(); break;
            case 'generate-pdf': handleGeneratePdf(); break;
        }
    });

    // ========================================================
    // Acciones backend
    // ========================================================

    async function handleGenerateSuggestions() {
        log('handleGenerateSuggestions invocado');
        try {
            const resp = await fetch(
                '/api/v1/sessions/' + sessionId + '/match',
                { method: 'POST' },
            );
            log('POST /match status:', resp.status);
            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error('Falló /match: ' + txt);
            }
            const data = await resp.json();
            log('Sugerencias generadas:', data);
            let msg = data.pairs.length + ' pares sugeridos';
            if (data.n_unpaired_frentes || data.n_unpaired_dorsos) {
                msg += ' — ' + data.n_unpaired_frentes + ' frentes y '
                    + data.n_unpaired_dorsos + ' dorsos sin par';
            }
            toast(msg, 'success');
            refreshMatch();
        } catch (err) {
            console.error('[match] Error en match:', err);
            toast(err.message || 'Error', 'danger');
        }
    }

    async function handleGeneratePdf() {
        log('handleGeneratePdf invocado');
        try {
            const resp = await fetch(
                '/api/v1/sessions/' + sessionId + '/generate-pdf',
                { method: 'POST' },
            );
            log('POST /generate-pdf status:', resp.status);
            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error(txt || 'No se pudo generar el PDF');
            }
            const data = await resp.json();
            toast('PDF generado (' + (data.size_bytes / 1024).toFixed(1) + ' KB)', 'success');
            setTimeout(function () {
                window.location.href = '/sessions/' + sessionId + '/completed';
            }, 500);
        } catch (err) {
            console.error('[match] Error en generate-pdf:', err);
            toast(err.message || 'Error al generar PDF', 'danger');
        }
    }

    function autoGenerateSuggestionsIfNeeded() {
        const pairList = document.getElementById('pair-list');
        if (pairList) {
            log('Ya hay pair-list, no auto-generamos');
            return;
        }
        const stateMarker = document.querySelector('[data-session-id]');
        if (!stateMarker || stateMarker.dataset.autotriggered === '1') return;
        stateMarker.dataset.autotriggered = '1';
        log('Auto-generando sugerencias (no hay pares todavía)');
        handleGenerateSuggestions();
    }

    function init() {
        log('Inicializando match screen, sessionId =', sessionId);
        log('SortableJS disponible:', typeof Sortable !== 'undefined');
        initLightbox();
        initPairListSortable();
        initDorsoSlotsSortable();
        autoGenerateSuggestionsIfNeeded();
    }

    init();

    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (e.target.id === 'match-content') {
            log('HTMX afterSwap: re-inicializando Sortable');
            initPairListSortable();
            initDorsoSlotsSortable();
        }
    });
})();
