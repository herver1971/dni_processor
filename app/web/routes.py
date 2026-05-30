"""
Router de páginas web (HTML server-rendered con Jinja2 + HTMX).

Rutas:
    GET /                                   → upload (home)
    GET /sessions/{id}/review               → pantalla de revisión de recortes
    GET /sessions/{id}/review/partial       → partial HTMX del estado actualizado
                                              (para polling de progreso)

Las acciones (procesar, confirmar crop, etc.) usan los endpoints de la API
JSON ya construida en Sprint 2a; el JS de la UI los invoca directamente.

Sprint 4a: el contexto de templates incluye `debug` (bool) y `version` (str)
inyectados desde Settings y `app.main.__version__`. La flag debug se expone
como `data-debug` en el <html> para que los JS la lean sin necesidad de
<script> inline (compatible con CSP estricta).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.v1.routes_sessions import state_to_response
from app.config import get_settings
from app.core.sessions import load_session

router = APIRouter(tags=["web"])

# Templates están en app/web/templates/
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# `finalize` se aplica al output al renderizar — convierte Enum a su .value.
# Las comparaciones (==) en los templates siguen siendo enum vs enum, por eso
# en los templates usamos enum_value() para extraer el str cuando hace falta
# comparar contra un literal de string.
templates.env.finalize = lambda v: v.value if isinstance(v, Enum) else v


def _base_ctx() -> dict:
    """
    Contexto compartido por todas las páginas web.

    Incluye:
        - debug: bool — controla `data-debug` en <html>
        - version: str — mostrado en el footer

    Se importa `__version__` lazy para evitar un ciclo con app.main, que
    importa este router.
    """
    from app.main import __version__
    return {
        "debug": get_settings().debug,
        "version": __version__,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """
    Pantalla de upload (home).

    Permite al usuario crear una sesión nueva subiendo imágenes de frentes
    y dorsos por separado. JS dispara la creación de sesión y los uploads
    contra la API JSON.
    """
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"title": "Subir DNIs", **_base_ctx()},
    )


@router.get("/sessions/{session_id}/review", response_class=HTMLResponse)
def review_page(request: Request, session_id: str) -> HTMLResponse:
    """
    Pantalla de revisión de recortes.

    Muestra el estado completo de la sesión:
    - Frentes auto-detectados con Cropper.js sobre cada uno
    - Frentes que fallaron detección, para recorte manual
    - Dorsos para recorte manual
    - Status agregado y botón "Continuar al matcheo" (Sprint 3)
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada o expirada",
        )

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "title": "Revisar recortes",
            "session_id": session_id,
            "state": state_to_response(state),
            **_base_ctx(),
        },
    )


@router.get(
    "/sessions/{session_id}/review/partial",
    response_class=HTMLResponse,
)
def review_partial(request: Request, session_id: str) -> HTMLResponse:
    """
    Partial HTML del estado actual de la sesión.

    Usado por HTMX para refrescar la pantalla de revisión después de:
    - Procesamiento (detección automática)
    - Confirmación de un crop
    - Creación de un recorte manual

    Devuelve solo la sección de contenido, no el layout completo.
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return templates.TemplateResponse(
        request,
        "partials/review_content.html",
        {
            "session_id": session_id,
            "state": state_to_response(state),
        },
    )


@router.get("/sessions/{session_id}/match", response_class=HTMLResponse)
def match_page(request: Request, session_id: str) -> HTMLResponse:
    """
    Pantalla de matcheo asistido (Sprint 3b).

    Layout dos columnas: frentes a la izquierda, dorsos a la derecha.
    Las sugerencias OCR se cargan automáticamente al entrar (vía JS que
    hace POST /api/v1/sessions/{id}/match si no hay pares aún).
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada o expirada",
        )

    return templates.TemplateResponse(
        request,
        "match.html",
        {
            "title": "Emparejar DNIs",
            "session_id": session_id,
            "state": state_to_response(state),
            **_base_ctx(),
        },
    )


@router.get(
    "/sessions/{session_id}/match/partial",
    response_class=HTMLResponse,
)
def match_partial(request: Request, session_id: str) -> HTMLResponse:
    """
    Partial HTML del estado de matcheo. Usado por HTMX y por el JS
    después de operaciones que modifican pares.
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return templates.TemplateResponse(
        request,
        "partials/match_content.html",
        {
            "session_id": session_id,
            "state": state_to_response(state),
        },
    )


@router.get("/sessions/{session_id}/completed", response_class=HTMLResponse)
def completed_page(request: Request, session_id: str) -> HTMLResponse:
    """
    Pantalla post-PDF: descarga + opción de empezar otro trámite.

    No requiere que el PDF ya exista físicamente (puede llegar acá tras
    "Generar PDF" en /match, y mostrar un placeholder mientras se
    genera). Pero típicamente cuando entra a esta ruta el PDF ya está.
    """
    state = load_session(session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sesión {session_id} no encontrada o expirada",
        )

    return templates.TemplateResponse(
        request,
        "completed.html",
        {
            "title": "PDF generado",
            "session_id": session_id,
            "state": state_to_response(state),
            **_base_ctx(),
        },
    )
