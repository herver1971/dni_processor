/* ========================================================
   Completed screen — descarga + empezar otro trámite
   ======================================================== */

(function () {
    'use strict';

    const sessionId = document.querySelector('[data-session-id]').dataset.sessionId;

    function toast(message, kind = '') {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'toast' + (kind ? ` toast--${kind}` : '');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => el.remove(), 5000);
    }

    async function handleStartNew() {
        const confirmed = confirm(
            '¿Empezar un trámite nuevo? Se descartará la sesión actual y todos sus archivos.\n\n'
            + 'Asegurate de haber descargado el PDF antes de continuar.'
        );
        if (!confirmed) return;

        try {
            const resp = await fetch(
                `/api/v1/sessions/${sessionId}/reset`,
                { method: 'POST' },
            );
            if (!resp.ok) {
                const txt = await resp.text();
                throw new Error(`Falló reset: ${txt}`);
            }
            const data = await resp.json();
            window.location.href = data.redirect_to || '/';
        } catch (err) {
            console.error(err);
            toast(err.message || 'Error', 'danger');
        }
    }

    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        if (btn.dataset.action === 'start-new') {
            handleStartNew();
        }
    });
})();
