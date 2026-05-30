"""
Tests del hardening de Sprint 4a.

Cubren:
- SecurityHeadersMiddleware: presencia y formato de cada header
- Content-Security-Policy: directivas críticas presentes
- RequestSizeLimitMiddleware: rechazo por Content-Length excesivo
- slowapi: rate limit dispara 429 después del threshold cuando está
  habilitado (verificación puntual, el resto de la suite corre con
  rate_limit_enabled=False)

Los tests de rate limit construyen una app DEDICADA con
DNI_RATE_LIMIT_ENABLED=true para no perturbar el resto de la suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.main import create_app
from app.middleware import CSP_HEADER_VALUE, DEFAULT_MAX_REQUEST_BYTES


pytestmark = pytest.mark.integration


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    """App con storage aislado y rate limit DESHABILITADO."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
    reset_settings()
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_settings()


@pytest.fixture
def isolated_app_with_rate_limit(tmp_path, monkeypatch):
    """App con rate limit HABILITADO, para tests específicos de 429."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "true")
    reset_settings()
    # El limiter singleton mantiene contadores in-memory entre apps; lo
    # reseteamos para que cada test arranque con cuota limpia.
    from app.rate_limiter import limiter
    limiter.reset()
    app = create_app()
    with TestClient(app) as c:
        yield c
    limiter.reset()
    reset_settings()


# ============================================================
# SecurityHeadersMiddleware
# ============================================================

class TestSecurityHeaders:
    """Cada response debe traer los headers de hardening."""

    def test_health_endpoint_has_all_security_headers(self, isolated_app):
        r = isolated_app.get("/api/v1/health")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Content-Security-Policy" in r.headers
        assert "Permissions-Policy" in r.headers

    def test_html_page_has_csp_with_critical_directives(self, isolated_app):
        r = isolated_app.get("/")
        assert r.status_code == 200
        csp = r.headers["Content-Security-Policy"]
        # Las directivas que matan XSS si están bien configuradas
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'self'" in csp
        # script-src NO debe permitir unsafe-inline (la flag DEBUG va via
        # data-attribute, no via <script> inline)
        script_directive = next(
            d.strip() for d in csp.split(";") if d.strip().startswith("script-src")
        )
        assert "'unsafe-inline'" not in script_directive

    def test_csp_allows_required_cdns(self, isolated_app):
        """unpkg (HTMX), cdnjs (Cropper), jsdelivr (Sortable), Google Fonts."""
        r = isolated_app.get("/")
        csp = r.headers["Content-Security-Policy"]
        assert "https://unpkg.com" in csp
        assert "https://cdnjs.cloudflare.com" in csp
        assert "https://cdn.jsdelivr.net" in csp
        assert "https://fonts.googleapis.com" in csp
        assert "https://fonts.gstatic.com" in csp

    def test_csp_header_module_value_matches_response(self, isolated_app):
        """Sanity: el header servido es exactamente el que arma el módulo."""
        r = isolated_app.get("/api/v1/health")
        assert r.headers["Content-Security-Policy"] == CSP_HEADER_VALUE

    def test_no_hsts_header(self, isolated_app):
        """HSTS no aplica en HTTP detrás de Tailscale."""
        r = isolated_app.get("/api/v1/health")
        assert "Strict-Transport-Security" not in r.headers


# ============================================================
# RequestSizeLimitMiddleware
# ============================================================

class TestRequestSizeLimit:
    """Content-Length que excede el máximo debe responder 413."""

    def test_oversized_request_rejected_413(self, isolated_app):
        # Mandamos un POST con Content-Length deliberadamente excesivo.
        # No mandamos el body real: el middleware se fija en el header
        # antes de tocar el body.
        huge_size = DEFAULT_MAX_REQUEST_BYTES + 1
        r = isolated_app.post(
            "/api/v1/sessions",
            headers={"Content-Length": str(huge_size)},
            content=b"",  # body vacío; el middleware no llega a leerlo
        )
        assert r.status_code == 413
        assert "demasiado grande" in r.json()["detail"]

    def test_normal_sized_request_passes(self, isolated_app):
        """Sanity: requests dentro del límite no se ven afectados."""
        r = isolated_app.post("/api/v1/sessions")
        assert r.status_code == 201

    def test_get_without_content_length_passes(self, isolated_app):
        """GETs sin Content-Length no deben ser rechazados."""
        r = isolated_app.get("/api/v1/health")
        assert r.status_code == 200


# ============================================================
# Rate limiting
# ============================================================

class TestRateLimiting:
    """
    Con rate_limit_enabled=True, los endpoints "caros" responden 429
    después del threshold. Usamos create_new_session (30/min) que es
    el límite más generoso pero alcanzable en un test.
    """

    def test_create_session_eventually_returns_429(
        self,
        isolated_app_with_rate_limit,
    ):
        # Disparamos 35 requests; el threshold es 30/min, así que
        # alrededor del request 31 debería empezar a fallar.
        codes = []
        for _ in range(35):
            r = isolated_app_with_rate_limit.post("/api/v1/sessions")
            codes.append(r.status_code)

        assert 201 in codes, "Al menos las primeras deberían pasar"
        assert 429 in codes, (
            f"Se esperaba al menos un 429 tras 35 requests; vi: {set(codes)}"
        )

    def test_health_endpoint_not_rate_limited(
        self,
        isolated_app_with_rate_limit,
    ):
        """/api/v1/health no tiene @limiter.limit, no debe nunca devolver 429."""
        for _ in range(50):
            r = isolated_app_with_rate_limit.get("/api/v1/health")
            assert r.status_code == 200

    def test_get_session_not_rate_limited(
        self,
        isolated_app_with_rate_limit,
    ):
        """Los GETs de estado no se limitan; la UI los pega muy seguido."""
        # Primero creamos una sesión (consume cuota de POST pero no afecta
        # el GET subsiguiente que es endpoint distinto).
        create = isolated_app_with_rate_limit.post("/api/v1/sessions")
        assert create.status_code == 201
        sid = create.json()["session_id"]

        # 100 GETs no deberían disparar 429
        for _ in range(100):
            r = isolated_app_with_rate_limit.get(f"/api/v1/sessions/{sid}")
            assert r.status_code in (200, 404), f"unexpected: {r.status_code}"


# ============================================================
# Debug flag → data-debug attribute
# ============================================================

class TestDebugAttribute:
    """
    Settings.debug controla `data-debug="true"` en <html>. Sin debug,
    el atributo no debe aparecer (no queremos `data-debug="false"`,
    queremos que SÓLO esté presente cuando está activo).
    """

    def test_data_debug_absent_when_debug_false(self, isolated_app):
        r = isolated_app.get("/")
        assert r.status_code == 200
        assert "data-debug" not in r.text

    def test_data_debug_present_when_debug_true(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("DNI_DEBUG", "true")
        reset_settings()
        app = create_app()
        with TestClient(app) as c:
            r = c.get("/")
        reset_settings()
        assert r.status_code == 200
        assert 'data-debug="true"' in r.text


# ============================================================
# Health endpoint enriquecido (Sprint 4b)
# ============================================================

class TestEnrichedHealth:
    """
    /api/v1/health reporta presencia de modelos en cache sin
    instanciarlos. Devuelve siempre 200 — `status` discrimina entre
    "ok" y "degraded".
    """

    def test_health_returns_required_fields(self, isolated_app):
        r = isolated_app.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
        assert "version" in body
        assert "models" in body
        assert set(body["models"].keys()) == {"face", "ocr"}

    def test_health_status_ok_when_both_models_present(
        self, tmp_path, monkeypatch
    ):
        """Con face + ocr cacheados, status='ok'."""
        from unittest.mock import patch

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
        reset_settings()
        # Mockeamos ambos chequeos a True. Usamos los nombres
        # importados por main.py, no los originales en core/*.
        with patch("app.main.is_face_model_cached", return_value=True), \
             patch("app.main.is_ocr_model_cached", return_value=True):
            app = create_app()
            with TestClient(app) as c:
                r = c.get("/api/v1/health")
        reset_settings()
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["models"] == {"face": True, "ocr": True}

    def test_health_status_degraded_when_face_missing(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import patch

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
        reset_settings()
        with patch("app.main.is_face_model_cached", return_value=False), \
             patch("app.main.is_ocr_model_cached", return_value=True):
            app = create_app()
            with TestClient(app) as c:
                r = c.get("/api/v1/health")
        reset_settings()
        assert r.status_code == 200  # Sigue siendo 200, no 503.
        body = r.json()
        assert body["status"] == "degraded"
        assert body["models"]["face"] is False
        assert body["models"]["ocr"] is True

    def test_health_status_degraded_when_ocr_missing(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import patch

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
        reset_settings()
        with patch("app.main.is_face_model_cached", return_value=True), \
             patch("app.main.is_ocr_model_cached", return_value=False):
            app = create_app()
            with TestClient(app) as c:
                r = c.get("/api/v1/health")
        reset_settings()
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["models"]["face"] is True
        assert body["models"]["ocr"] is False

    def test_health_does_not_instantiate_models(
        self, tmp_path, monkeypatch
    ):
        """
        El endpoint debe ser barato — verifica archivos en disco, no
        carga modelos en memoria. Garantía importante para monitoreo
        de polling.
        """
        from unittest.mock import patch

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
        reset_settings()
        # Si el endpoint accidentalmente instanciara los modelos,
        # estos mocks dispararían. Los dejamos como sentinelas.
        with patch("app.core.vision.get_face_net") as mock_face, \
             patch("app.core.ocr.get_reader") as mock_reader:
            app = create_app()
            with TestClient(app) as c:
                r = c.get("/api/v1/health")
        reset_settings()
        assert r.status_code == 200
        # NINGUNO de los getters de modelo debió ser llamado.
        mock_face.assert_not_called()
        mock_reader.assert_not_called()
