"""
Limiter singleton de slowapi.

Vive en su propio módulo para que los routers puedan importarlo sin
crear un ciclo con `app.main` (que registra el handler de RateLimitExceeded).

El limiter se construye con `enabled=True` y `refresh_limiter_enabled()`
lo sincroniza con `Settings.rate_limit_enabled` cuando arranca la app.
En tests, `DNI_RATE_LIMIT_ENABLED=false` + reset_settings() + recrear
la app hace que `enabled` quede en False y los decoradores @limit son no-op.

Estrategia de keying:
    Usamos `get_remote_address`. En producción detrás de Tailscale, cada
    device del tailnet tiene IP única (100.x.y.z), así que el keying por
    IP discrimina bien entre clientes. Si en el futuro el servicio se
    pone detrás de un reverse proxy, hay que cambiar a leer X-Forwarded-For
    con cuidado de no confiar en el header crudo.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings


limiter = Limiter(
    key_func=get_remote_address,
    enabled=True,  # se sincroniza con refresh_limiter_enabled() en create_app
)


def refresh_limiter_enabled() -> None:
    """
    Sincroniza `limiter.enabled` con el setting actual.

    Se invoca al arrancar la app y puede invocarse en tests si se cambia
    el setting en runtime (vía monkeypatch + reset_settings + recrear app).
    """
    limiter.enabled = get_settings().rate_limit_enabled
