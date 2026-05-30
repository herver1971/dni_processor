"""
DNI Processor — Entry point FastAPI.

Esta versión expone la API REST de Sprint 2a:
- POST   /api/v1/sessions
- GET    /api/v1/sessions/{id}
- DELETE /api/v1/sessions/{id}
- POST   /api/v1/sessions/{id}/images
- GET    /api/v1/sessions/{id}/images/{image_id}
- GET    /api/v1/sessions/{id}/crops/{crop_id}/wide
- GET    /api/v1/sessions/{id}/crops/{crop_id}/final
- POST   /api/v1/sessions/{id}/process
- POST   /api/v1/sessions/{id}/crops/{crop_id}/confirm
- POST   /api/v1/sessions/{id}/images/{image_id}/crops
- DELETE /api/v1/sessions/{id}/crops/{crop_id}

La UI HTMX se agrega en Sprint 2b.

Sprint 4a (v0.3.2) agrega hardening de aplicación:
- SecurityHeadersMiddleware (CSP estricta + X-Frame-Options + nosniff + etc.)
- RequestSizeLimitMiddleware (fail-fast 413 por Content-Length)
- slowapi sobre endpoints "caros" (configurable por DNI_RATE_LIMIT_ENABLED)

Sprint 4b (v0.4.0) agrega deployment a producción:
- Health endpoint enriquecido: reporta disponibilidad de modelos en cache
- systemd unit + script de preload de modelos + README de deployment
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.routes_images import router as images_router
from app.api.v1.routes_matching import router as matching_router
from app.api.v1.routes_processing import router as processing_router
from app.api.v1.routes_sessions import router as sessions_router
from app.config import get_settings
from app.core.constants import CLEANUP_INTERVAL_MINUTES
from app.core.ocr import is_ocr_model_cached
from app.core.sessions import cleanup_expired_sessions
from app.core.vision import is_face_model_cached
from app.middleware import (
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.rate_limiter import limiter, refresh_limiter_enabled
from app.web.routes import router as web_router

__version__ = "0.4.0"
__app_name__ = "DNI Processor"


logger = logging.getLogger(__name__)


# ============================================================
# Background task: cleanup periódico
# ============================================================

_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop() -> None:
    """Tarea periódica que limpia sesiones expiradas."""
    settings = get_settings()
    while True:
        try:
            n_deleted = cleanup_expired_sessions(settings.get_sessions_dir())
            if n_deleted > 0:
                logger.info(f"Cleanup: {n_deleted} sesión(es) expirada(s) eliminadas")
        except Exception as e:
            logger.error(f"Cleanup falló: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle del servidor: arranca cleanup en background al iniciar."""
    settings = get_settings()
    settings.get_sessions_dir().mkdir(parents=True, exist_ok=True)
    logger.info(f"DNI Processor v{__version__} arrancando...")
    logger.info(f"Sesiones en: {settings.get_sessions_dir()}")

    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    # Shutdown
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    logger.info("DNI Processor apagándose.")


# ============================================================
# App factory
# ============================================================

def create_app() -> FastAPI:
    """Crea la instancia de FastAPI. Usado por ASGI y por tests."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = FastAPI(
        title=__app_name__,
        version=__version__,
        description=(
            "Servicio auto-alojado para organizar fotografías de DNIs "
            "argentinos en un PDF A4 listo para impresión. Preserva "
            "integridad documental (sin warp ni transformaciones de "
            "perspectiva)."
        ),
        lifespan=lifespan,
    )

    # --- Rate limiting (slowapi) ---
    # Sincroniza el limiter con el setting actual. En tests con
    # DNI_RATE_LIMIT_ENABLED=false, `limiter.enabled` queda en False
    # y los decoradores @limiter.limit(...) son no-op.
    refresh_limiter_enabled()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # --- Security middlewares ---
    # add_middleware agrega en el lado outermost, así que el último
    # registrado es el primero en ejecutarse. Queremos que el size limit
    # se evalúe antes que cualquier otra cosa para fail-fast.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)

    # Routers API
    app.include_router(sessions_router)
    app.include_router(images_router)
    app.include_router(processing_router)
    app.include_router(matching_router)

    # Router de páginas web (HTML)
    app.include_router(web_router)

    # Static files (CSS, JS)
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict:
        """
        Health check del servicio.

        Devuelve siempre 200 (incluso si faltan modelos), para que
        Tailscale y monitoreo externo no marquen el servicio como down
        por una condición que sólo afecta el procesamiento, no la
        capacidad del servicio de responder.

        Campos:
            status: "ok" si todos los modelos están en cache,
                    "degraded" si falta al menos uno (la primera request
                    real va a disparar la descarga lazy)
            version: string de versión
            models: dict con presencia de cada modelo en disco
                face: bool — detector ResNet-10 SSD para detección de caras
                ocr:  bool — EasyOCR (CRAFT + modelos de idioma)

        Notable: este endpoint NO instancia los modelos, sólo verifica
        archivos en disco. Es rápido y sin side-effects, apto para
        polling de monitoreo.
        """
        settings = get_settings()
        face_ok = is_face_model_cached(settings.model_cache_dir)
        ocr_ok = is_ocr_model_cached()
        all_ok = face_ok and ocr_ok
        return {
            "status": "ok" if all_ok else "degraded",
            "version": __version__,
            "models": {
                "face": face_ok,
                "ocr": ocr_ok,
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
