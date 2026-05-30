"""
Tests de integración para las rutas web de matcheo + completed
(Sprint 3b).
"""

from __future__ import annotations

import cv2
import numpy as np
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


@pytest.fixture
def sample_image_bytes():
    img = np.full((400, 600, 3), 128, dtype=np.uint8)
    success, encoded = cv2.imencode(".jpg", img)
    assert success
    return encoded.tobytes()


def _setup_session_with_crops(client, sample_bytes, n=2):
    """Crea una sesión con n frentes + n dorsos confirmados manualmente."""
    sid = client.post("/api/v1/sessions").json()["session_id"]

    upload_f = client.post(
        f"/api/v1/sessions/{sid}/images",
        data={"side": "frente"},
        files=[("files", (f"f{i}.jpg", sample_bytes, "image/jpeg")) for i in range(n)],
    )
    upload_d = client.post(
        f"/api/v1/sessions/{sid}/images",
        data={"side": "dorso"},
        files=[("files", (f"d{i}.jpg", sample_bytes, "image/jpeg")) for i in range(n)],
    )
    fimgs = [u["image_id"] for u in upload_f.json()["uploaded"]]
    dimgs = [u["image_id"] for u in upload_d.json()["uploaded"]]

    fcrops, dcrops = [], []
    for img_id in fimgs:
        fcrops.append(client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={"bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                  "side": "frente", "rotation_degrees": 0},
        ).json()["crop_id"])
    for img_id in dimgs:
        dcrops.append(client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={"bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                  "side": "dorso", "rotation_degrees": 0},
        ).json()["crop_id"])

    return sid, fcrops, dcrops


class TestMatchPage:
    def test_match_page_renders_without_pairs(self, client, sample_image_bytes):
        """Sin pares: empty-state + botón para generar sugerencias."""
        sid, _, _ = _setup_session_with_crops(client, sample_image_bytes, 2)
        r = client.get(f"/sessions/{sid}/match")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "empty-state" in r.text
        # Cargas JS críticas
        assert "/static/js/match.js" in r.text
        assert "sortable" in r.text.lower()  # SortableJS CDN

    def test_match_page_renders_with_pairs(self, client, sample_image_bytes):
        """Con pares: muestra pair-rows con badges de origen."""
        sid, fcrops, dcrops = _setup_session_with_crops(client, sample_image_bytes, 2)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={"pairs": [
                {"frente_crop_id": fcrops[0], "dorso_crop_id": dcrops[0], "position": 0},
                {"frente_crop_id": fcrops[1], "dorso_crop_id": dcrops[1], "position": 1},
            ]},
        )
        r = client.get(f"/sessions/{sid}/match")
        assert r.status_code == 200
        assert "pair-row" in r.text
        assert "pair-row__badge" in r.text
        # Botón generate-pdf habilitado
        assert 'data-action="generate-pdf"' in r.text

    def test_match_page_shows_button_disabled_on_imbalance(self, client, sample_image_bytes):
        """Si hay más frentes que dorsos, el botón Generar PDF queda disabled."""
        sid = client.post("/api/v1/sessions").json()["session_id"]
        # 2 frentes pero solo 1 dorso
        upload_f = client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "frente"},
            files=[("files", ("f1.jpg", sample_image_bytes, "image/jpeg")),
                   ("files", ("f2.jpg", sample_image_bytes, "image/jpeg"))],
        )
        for img_id in [u["image_id"] for u in upload_f.json()["uploaded"]]:
            client.post(
                f"/api/v1/sessions/{sid}/images/{img_id}/crops",
                json={"bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                      "side": "frente", "rotation_degrees": 0},
            )
        upload_d = client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        img_id = upload_d.json()["uploaded"][0]["image_id"]
        client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={"bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                  "side": "dorso", "rotation_degrees": 0},
        )

        r = client.get(f"/sessions/{sid}/match")
        assert r.status_code == 200
        # El mensaje de imbalance debe estar visible
        assert "Faltan" in r.text or "faltan" in r.text.lower()
        # Y el botón debe estar disabled
        # Buscamos el botón generate-pdf con disabled
        assert "disabled" in r.text

    def test_match_nonexistent_session_returns_404(self, client):
        r = client.get("/sessions/inexistente/match")
        assert r.status_code == 404


class TestMatchPartial:
    def test_partial_renders(self, client, sample_image_bytes):
        sid, _, _ = _setup_session_with_crops(client, sample_image_bytes, 1)
        r = client.get(f"/sessions/{sid}/match/partial")
        assert r.status_code == 200
        # No incluye el layout
        assert "<html" not in r.text
        assert "<head" not in r.text
        # Pero sí los stats
        assert "stats-bar" in r.text

    def test_partial_404(self, client):
        r = client.get("/sessions/nope/match/partial")
        assert r.status_code == 404


class TestCompletedPage:
    def test_completed_renders(self, client, sample_image_bytes):
        """Tras generar PDF, /completed muestra preview + botones."""
        sid, fcrops, dcrops = _setup_session_with_crops(client, sample_image_bytes, 1)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={"pairs": [
                {"frente_crop_id": fcrops[0], "dorso_crop_id": dcrops[0], "position": 0},
            ]},
        )
        client.post(f"/api/v1/sessions/{sid}/generate-pdf")

        r = client.get(f"/sessions/{sid}/completed")
        assert r.status_code == 200
        assert "completed-preview__frame" in r.text
        # Link de descarga
        assert "output.pdf" in r.text
        # Botón start-new
        assert 'data-action="start-new"' in r.text

    def test_completed_renders_even_before_pdf(self, client, sample_image_bytes):
        """La página renderiza aunque el PDF no exista. El iframe fallará
        en cargar, pero el resto de la UI está OK (el usuario igual puede
        descartar e iniciar otro)."""
        sid, _, _ = _setup_session_with_crops(client, sample_image_bytes, 1)
        r = client.get(f"/sessions/{sid}/completed")
        assert r.status_code == 200
        assert "PDF generado" in r.text or "tu" in r.text.lower()

    def test_completed_404(self, client):
        r = client.get("/sessions/nope/completed")
        assert r.status_code == 404


class TestReviewLinksToMatch:
    """El botón "Continuar al matcheo" del review apunta a /match cuando
    todos los crops están confirmados."""

    def test_review_with_all_confirmed_links_to_match(self, client, sample_image_bytes):
        sid, _, _ = _setup_session_with_crops(client, sample_image_bytes, 1)
        r = client.get(f"/sessions/{sid}/review")
        assert r.status_code == 200
        # El href apunta a /match
        assert f'href="/sessions/{sid}/match"' in r.text


class TestMatchJsServed:
    def test_match_js_is_served(self, client):
        r = client.get("/static/js/match.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert "Sortable" in r.text
        assert "generate-pdf" in r.text

    def test_completed_js_is_served(self, client):
        r = client.get("/static/js/completed.js")
        assert r.status_code == 200
        assert "start-new" in r.text
        assert "redirect_to" in r.text
