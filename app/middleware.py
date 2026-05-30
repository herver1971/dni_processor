"""
Middlewares de hardening (Sprint 4a).

Dos piezas:

1. **SecurityHeadersMiddleware** — agrega headers de seguridad a TODA
   respuesta (incluyendo /static/*). Es defense-in-depth: el servicio
   corre detrás de Tailscale, pero hardening en profundidad es barato.

   La CSP está calibrada para los assets que carga la UI hoy:
       - HTMX desde unpkg
       - Cropper.js desde cdnjs (CSS + JS)
       - SortableJS desde jsdelivr
       - Google Fonts (CSS de googleapis + woff2 de gstatic)
       - Todo lo demás de 'self'

   `style-src` mantiene `'unsafe-inline'` porque varios templates usan
   `style="..."` inline (por ejemplo `margin-top: var(--s-4)`). Migrar
   esos a clases está fuera de scope y el riesgo de XSS por CSS es ínfimo.

   `script-src` NO usa `'unsafe-inline'`: todos los <script> son external
   y la flag DEBUG del frontend se lee via `data-debug` en el <html>, no
   via <script> inline.

2. **RequestSizeLimitMiddleware** — rechaza con 413 cualquier request
   cuyo `Content-Length` exceda el límite, ANTES de bufferear el body.
   Es honesto: protege contra el caso "macro accidental con un blob
   gigante" (cliente honesto que manda el header), pero un cliente
   malicioso puede mentir el header o usar `Transfer-Encoding: chunked`
   sin `Content-Length`. La defensa real sigue siendo la validación
   app-level en `routes_images.py` (MAX_IMAGE_SIZE_BYTES por imagen,
   MAX_SESSION_SIZE_BYTES por sesión).

   El límite global es generoso (MAX_SESSION_SIZE_BYTES + slack) porque
   un mismo POST puede traer múltiples imágenes (upload multipart). El
   sliceado fino por imagen lo hace el endpoint.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.constants import MAX_SESSION_SIZE_BYTES

logger = logging.getLogger(__name__)


# ============================================================
# Content Security Policy
# ============================================================

# Construida en partes para que sea legible y diffeable.
_CSP_DIRECTIVES: dict[str, str] = {
    "default-src": "'self'",
    "script-src": (
        "'self' "
        "https://unpkg.com "
        "https://cdnjs.cloudflare.com "
        "https://cdn.jsdelivr.net"
    ),
    "style-src": (
        "'self' 'unsafe-inline' "
        "https://fonts.googleapis.com "
        "https://cdnjs.cloudflare.com"
    ),
    "font-src": "'self' https://fonts.gstatic.com",
    "img-src": "'self' data:",
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

CSP_HEADER_VALUE: str = "; ".join(
    f"{k} {v}" for k, v in _CSP_DIRECTIVES.items()
)


# ============================================================
# SecurityHeadersMiddleware
# ============================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Agrega headers de seguridad a cada respuesta.

    Headers:
        - Content-Security-Policy: política estricta, ver CSP_HEADER_VALUE
        - X-Content-Type-Options: nosniff
        - X-Frame-Options: DENY (redundante con CSP frame-ancestors pero
          algunos browsers viejos no implementan frame-ancestors)
        - Referrer-Policy: strict-origin-when-cross-origin
        - Permissions-Policy: bloquea sensores/devices que no usamos

    NO se setea HSTS: Tailscale termina TLS por nosotros, el servicio
    bindea a 127.0.0.1 en HTTP, y HSTS sólo aplica sobre HTTPS.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP_HEADER_VALUE
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        return response


# ============================================================
# RequestSizeLimitMiddleware
# ============================================================

# El límite global incluye un slack del 10% sobre el máximo de sesión
# para tolerar el overhead del multipart envelope (boundaries, headers
# de cada parte) sin bloquear uploads válidos.
_REQUEST_SIZE_SLACK_RATIO: float = 1.10
DEFAULT_MAX_REQUEST_BYTES: int = int(
    MAX_SESSION_SIZE_BYTES * _REQUEST_SIZE_SLACK_RATIO
)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rechaza con 413 cualquier request cuyo `Content-Length` exceda
    `max_bytes`, antes de bufferear el body.

    Limitaciones documentadas:
    - Sólo protege contra clientes que mandan Content-Length honestamente.
      Un cliente con `Transfer-Encoding: chunked` sin Content-Length pasa
      este filtro.
    - La defensa real contra uploads abusivos sigue siendo la validación
      app-level en los endpoints (MAX_IMAGE_SIZE_BYTES, MAX_SESSION_SIZE_BYTES).
    - Este middleware existe para fail-fast en el caso típico de "subí
      accidentalmente un blob de 5GB" sin bufferearlo en memoria.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    ) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length_str = request.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
            except ValueError:
                # Header malformado; dejamos que el handler decida
                return await call_next(request)
            if content_length > self.max_bytes:
                logger.warning(
                    "Request rechazado por tamaño: %d bytes > %d (path=%s)",
                    content_length,
                    self.max_bytes,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Request demasiado grande "
                            f"({content_length} bytes > {self.max_bytes})."
                        )
                    },
                )
        return await call_next(request)
