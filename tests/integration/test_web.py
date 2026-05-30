"""
Tests de integración para las rutas web (HTML server-rendered).

Validan que las páginas se renderizan correctamente, que el static
está montado, y que el partial de HTMX devuelve solo el contenido
necesario.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.main import create_app


pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_sessions_dir(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("DNI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DNI_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("DNI_RATE_LIMIT_ENABLED", "false")
    reset_settings()
    yield sessions_dir
    reset_settings()


@pytest.fixture
def client(isolated_sessions_dir):
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestHomePage:
    def test_home_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # Contenido distintivo de la página
        assert "Frentes" in r.text
        assert "Dorsos" in r.text
        assert "upload.js" in r.text  # script del componente

    def test_home_has_drop_zones(self, client):
        r = client.get("/")
        assert 'data-zone="frentes"' in r.text
        assert 'data-zone="dorsos"' in r.text


class TestStaticFiles:
    def test_main_css_is_served(self, client):
        r = client.get("/static/css/main.css")
        assert r.status_code == 200
        assert "css" in r.headers["content-type"]
        # Contenido reconocible del CSS
        assert "--paper:" in r.text
        assert ".drop-zone" in r.text

    def test_upload_js_is_served(self, client):
        r = client.get("/static/js/upload.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert "filesByZone" in r.text

    def test_review_js_is_served(self, client):
        r = client.get("/static/js/review.js")
        assert r.status_code == 200
        assert "Cropper" in r.text


class TestReviewPage:
    def test_review_page_renders(self, client):
        # Crear una sesión primero
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.get(f"/sessions/{session_id}/review")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # Contenido distintivo de la página
        assert "Revisar" in r.text
        assert session_id in r.text  # session_id se inyecta como data attr
        assert "review.js" in r.text

    def test_review_nonexistent_session_returns_404(self, client):
        r = client.get("/sessions/inexistente/review")
        assert r.status_code == 404


class TestReviewPartial:
    def test_partial_renders_empty_session(self, client):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.get(f"/sessions/{session_id}/review/partial")
        assert r.status_code == 200
        # El partial NO debe incluir el layout completo (base.html)
        assert "<html" not in r.text
        assert "<head" not in r.text
        # Pero sí debe incluir los stats
        assert "stats-bar" in r.text

    def test_partial_nonexistent_session_returns_404(self, client):
        r = client.get("/sessions/inexistente/review/partial")
        assert r.status_code == 404


class TestFullFlowFromBrowser:
    """
    Simula el flujo completo que haría el browser: crear sesión via API,
    visitar la página de revisión, y verificar que se renderiza con el
    estado correcto.
    """

    def test_review_shows_uploaded_images(self, client):
        import cv2
        import numpy as np

        # Crear sesión y subir una imagen
        sid = client.post("/api/v1/sessions").json()["session_id"]

        # Generar JPG válido
        img = np.full((400, 600, 3), 128, dtype=np.uint8)
        _, encoded = cv2.imencode(".jpg", img)
        img_bytes = encoded.tobytes()

        client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "dorso"},
            files=[("files", ("d1.jpg", img_bytes, "image/jpeg"))],
        )

        # La página de review debe mostrar la imagen
        r = client.get(f"/sessions/{sid}/review")
        assert r.status_code == 200
        # El dorso pendiente debe aparecer en la sección manual
        assert "Marcar manualmente" in r.text
