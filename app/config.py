"""
Configuración del servicio — leída de variables de entorno via Pydantic Settings.

Variables soportadas (con sus defaults):
    DNI_HOST                : Host de bind (default: 127.0.0.1)
    DNI_PORT                : Puerto (default: 8001)
    DNI_DATA_DIR            : Directorio raíz de datos (default: ./data)
    DNI_SESSIONS_DIR        : Subdir de sesiones (default: <DATA_DIR>/sessions)
    DNI_MODEL_CACHE_DIR     : Cache del modelo de caras (default: ~/.cache/dni_processor)
    DNI_LOG_LEVEL           : Nivel de logging (default: INFO)
    DNI_RUN_OCR             : Si correr OCR (default: true)
    DNI_DEBUG               : Activa logs detallados en el frontend (default: false).
                              Cuando es true, el atributo `data-debug="true"` se
                              inyecta en <html> y los JS lo leen para habilitar
                              console.log diagnósticos.
    DNI_RATE_LIMIT_ENABLED  : Habilita slowapi sobre los endpoints "caros"
                              (default: true). En tests se setea a false para
                              que múltiples invocaciones del mismo endpoint no
                              gatillen 429.

Se pueden setear via .env, variables de entorno, o pasar directamente
al instanciar Settings() en tests.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del servicio."""

    model_config = SettingsConfigDict(
        env_prefix="DNI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8001

    # --- Storage ---
    data_dir: Path = Field(default=Path("./data"))
    sessions_dir: Path | None = None  # Si None, se calcula como data_dir/sessions
    model_cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "dni_processor"
    )

    # --- Behavior ---
    log_level: str = "INFO"
    run_ocr: bool = True

    # --- Hardening (Sprint 4a) ---
    debug: bool = False
    """
    Si True, expone `data-debug="true"` en el <html> de los templates.
    Los JS leen `document.documentElement.dataset.debug` para activar
    console.log diagnósticos. Setear sólo en desarrollo.
    """

    rate_limit_enabled: bool = True
    """
    Si True, slowapi aplica límites a los endpoints "caros". Se
    desactiva en tests para que múltiples invocaciones consecutivas
    del mismo endpoint no gatillen 429.
    """

    def get_sessions_dir(self) -> Path:
        """Devuelve el directorio efectivo de sesiones."""
        if self.sessions_dir is not None:
            return self.sessions_dir
        return self.data_dir / "sessions"


# Instancia global lazy
_settings: Settings | None = None


def get_settings() -> Settings:
    """Devuelve la instancia singleton de Settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Resetea el singleton (usado en tests)."""
    global _settings
    _settings = None
