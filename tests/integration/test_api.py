"""
Tests de integración de la API REST.

Cubren los endpoints de Sprint 2a sin depender del detector facial real
(que requiere descargar el modelo). Para los tests que sí requieren el
detector, usamos un fixture que mockea `get_face_net()`.

Estos tests se ejecutan contra una instancia REAL de la app FastAPI vía
TestClient (httpx). Cada test tiene su propio directorio temporal de
sesiones para aislamiento.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.main import create_app


pytestmark = pytest.mark.integration


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_sessions_dir(tmp_path, monkeypatch):
    """Aísla las sesiones de cada test en un directorio temporal."""
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
    """TestClient sobre una app aislada."""
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_image_bytes():
    """Bytes de una imagen JPG válida (sin DNI real, para tests de upload)."""
    img = np.full((400, 600, 3), 128, dtype=np.uint8)
    # Agregar variación para que sea una imagen real, no constante
    img[50:150, 50:200] = 200
    success, encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert success
    return encoded.tobytes()


# ============================================================
# Health
# ============================================================

class TestHealth:
    def test_health_endpoint(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        # Sprint 4b: el endpoint ahora reporta "ok" cuando los modelos
        # están en cache y "degraded" cuando alguno falta. Acá no
        # asumimos que estén preloadeados — el entorno de test no
        # garantiza que ~/.cache/dni_processor y ~/.EasyOCR existan.
        # Lo importante es que el endpoint responde 200 con la estructura
        # esperada. Los tests específicos de status="ok"/"degraded"
        # están en test_security.py::TestEnrichedHealth con mocks.
        assert data["status"] in ("ok", "degraded")
        assert "version" in data
        assert "models" in data
        assert set(data["models"].keys()) == {"face", "ocr"}


# ============================================================
# Sesiones
# ============================================================

class TestSessions:
    def test_create_session(self, client):
        r = client.post("/api/v1/sessions")
        assert r.status_code == 201
        data = r.json()
        assert "session_id" in data
        assert data["status"] == "created"

    def test_get_session_returns_state(self, client):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.get(f"/api/v1/sessions/{session_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == session_id
        assert data["images"] == []
        assert data["crops"] == []
        assert data["n_pending_crops"] == 0
        assert data["all_confirmed"] is False  # vacío = False

    def test_get_nonexistent_session_returns_404(self, client):
        r = client.get("/api/v1/sessions/inexistente-uuid")
        assert r.status_code == 404

    def test_delete_session(self, client):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.delete(f"/api/v1/sessions/{session_id}")
        assert r.status_code == 204

        # Confirmar que ya no existe
        r = client.get(f"/api/v1/sessions/{session_id}")
        assert r.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/v1/sessions/inexistente")
        assert r.status_code == 404


# ============================================================
# Upload de imágenes
# ============================================================

class TestUploadImages:
    def test_upload_single_frente(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "frente"},
            files=[("files", ("foto1.jpg", sample_image_bytes, "image/jpeg"))],
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0]["declared_side"] == "frente"
        assert data["uploaded"][0]["original_filename"] == "foto1.jpg"

    def test_upload_multiple_files(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[
                ("files", ("d1.jpg", sample_image_bytes, "image/jpeg")),
                ("files", ("d2.jpg", sample_image_bytes, "image/jpeg")),
                ("files", ("d3.jpg", sample_image_bytes, "image/jpeg")),
            ],
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["uploaded"]) == 3
        assert all(u["declared_side"] == "dorso" for u in data["uploaded"])

    def test_upload_invalid_side_rejected(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "lateral"},
            files=[("files", ("f.jpg", sample_image_bytes, "image/jpeg"))],
        )
        assert r.status_code == 400

    def test_upload_invalid_extension_skipped(self, client):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "frente"},
            files=[("files", ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"))],
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["uploaded"]) == 0
        assert len(data["skipped"]) == 1

    def test_upload_to_nonexistent_session_returns_404(self, client, sample_image_bytes):
        r = client.post(
            "/api/v1/sessions/no-existe/images",
            data={"side": "frente"},
            files=[("files", ("a.jpg", sample_image_bytes, "image/jpeg"))],
        )
        assert r.status_code == 404

    def test_session_state_reflects_uploaded_images(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "frente"},
            files=[("files", ("f1.jpg", sample_image_bytes, "image/jpeg"))],
        )
        client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[("files", ("d1.jpg", sample_image_bytes, "image/jpeg"))],
        )

        r = client.get(f"/api/v1/sessions/{session_id}")
        data = r.json()
        assert len(data["images"]) == 2
        sides = sorted(img["declared_side"] for img in data["images"])
        assert sides == ["dorso", "frente"]


# ============================================================
# Servido de archivos
# ============================================================

class TestServeFiles:
    def test_serve_uploaded_image(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        upload = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "frente"},
            files=[("files", ("f.jpg", sample_image_bytes, "image/jpeg"))],
        )
        image_id = upload.json()["uploaded"][0]["image_id"]

        r = client.get(f"/api/v1/sessions/{session_id}/images/{image_id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 100  # tiene contenido real

    def test_serve_nonexistent_image_returns_404(self, client):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        r = client.get(f"/api/v1/sessions/{session_id}/images/nope")
        assert r.status_code == 404


# ============================================================
# Recorte manual (dorso o frente sin detección automática)
# ============================================================

class TestManualCrop:
    def test_create_manual_crop_for_dorso(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        upload = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        image_id = upload.json()["uploaded"][0]["image_id"]

        # Bbox sobre la imagen normalizada (600x400 en sample)
        r = client.post(
            f"/api/v1/sessions/{session_id}/images/{image_id}/crops",
            json={
                "bbox": {"x": 50, "y": 50, "width": 400, "height": 250},
                "side": "dorso",
                "rotation_degrees": 0,
            },
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["status"] == "confirmed"
        assert "final_crop_url" in data

        # Verificar que se puede descargar el recorte final
        r = client.get(data["final_crop_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_invalid_rotation_rejected(self, client, sample_image_bytes):
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        upload = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        image_id = upload.json()["uploaded"][0]["image_id"]

        r = client.post(
            f"/api/v1/sessions/{session_id}/images/{image_id}/crops",
            json={
                "bbox": {"x": 50, "y": 50, "width": 400, "height": 250},
                "side": "dorso",
                "rotation_degrees": 45,  # NO es múltiplo de 90
            },
        )
        assert r.status_code == 400

    def test_multiple_manual_crops_on_same_image(self, client, sample_image_bytes):
        """Soporte para múltiples DNIs en una sola foto."""
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]

        upload = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[("files", ("multi.jpg", sample_image_bytes, "image/jpeg"))],
        )
        image_id = upload.json()["uploaded"][0]["image_id"]

        # Crear 3 recortes manuales sobre la misma imagen
        for i in range(3):
            r = client.post(
                f"/api/v1/sessions/{session_id}/images/{image_id}/crops",
                json={
                    "bbox": {
                        "x": 10 + i * 100,
                        "y": 10,
                        "width": 150,
                        "height": 100,
                    },
                    "side": "dorso",
                    "rotation_degrees": 0,
                },
            )
            assert r.status_code == 201

        # Verificar que la sesión tiene 3 crops
        r = client.get(f"/api/v1/sessions/{session_id}")
        data = r.json()
        assert len(data["crops"]) == 3
        assert all(c["status"] == "confirmed" for c in data["crops"])


# ============================================================
# Lifecycle completo (sin procesamiento — eso requiere el modelo)
# ============================================================

class TestSessionLifecycle:
    def test_full_lifecycle_with_manual_crops_only(self, client, sample_image_bytes):
        """
        Lifecycle completo sin invocar detección automática:
        1. Crear sesión
        2. Subir imágenes
        3. Crear recortes manuales
        4. Verificar all_confirmed
        5. Descartar sesión
        """
        # 1. Crear sesión
        r = client.post("/api/v1/sessions")
        session_id = r.json()["session_id"]
        assert r.json()["status"] == "created"

        # 2. Subir
        upload = client.post(
            f"/api/v1/sessions/{session_id}/images",
            data={"side": "dorso"},
            files=[
                ("files", ("d1.jpg", sample_image_bytes, "image/jpeg")),
                ("files", ("d2.jpg", sample_image_bytes, "image/jpeg")),
            ],
        )
        image_ids = [u["image_id"] for u in upload.json()["uploaded"]]

        # 3. Recorte manual en cada imagen
        for img_id in image_ids:
            r = client.post(
                f"/api/v1/sessions/{session_id}/images/{img_id}/crops",
                json={
                    "bbox": {"x": 50, "y": 50, "width": 400, "height": 250},
                    "side": "dorso",
                    "rotation_degrees": 0,
                },
            )
            assert r.status_code == 201

        # 4. Estado debería ser READY_FOR_MATCH
        r = client.get(f"/api/v1/sessions/{session_id}")
        data = r.json()
        assert data["status"] == "ready_for_match"
        assert data["all_confirmed"] is True
        assert data["n_confirmed_crops"] == 2
        assert data["n_pending_crops"] == 0

        # 5. Descartar
        r = client.delete(f"/api/v1/sessions/{session_id}")
        assert r.status_code == 204
