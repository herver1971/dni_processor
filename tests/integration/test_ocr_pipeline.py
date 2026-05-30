"""
Tests de integración del flujo de OCR.

Validan que el OCR se INVOCA en los puntos correctos del pipeline,
incluso si el modelo de EasyOCR no está disponible (mock).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings


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
def client_with_mocked_ocr(isolated_sessions_dir, monkeypatch):
    """
    Cliente con extract_dni_number mockeado para devolver un número fijo.

    Esto permite validar que el OCR se invoca en los puntos correctos
    del pipeline sin necesidad de tener EasyOCR instalado.
    """
    def fake_extract(path):
        # Simular que el OCR leyó "12345678" con alta confianza
        return ("12345678", 0.95)

    # Patchear donde se importa (no donde se define)
    import app.api.v1.routes_processing
    monkeypatch.setattr(
        app.api.v1.routes_processing, "extract_dni_number", fake_extract,
    )

    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_image_bytes():
    img = np.full((400, 600, 3), 128, dtype=np.uint8)
    success, encoded = cv2.imencode(".jpg", img)
    assert success
    return encoded.tobytes()


class TestOcrInvocation:
    def test_ocr_runs_on_manual_crop(self, client_with_mocked_ocr, sample_image_bytes):
        """Al crear un recorte manual, OCR se ejecuta y guarda dni_number."""
        client = client_with_mocked_ocr
        sid = client.post("/api/v1/sessions").json()["session_id"]

        upload = client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        img_id = upload.json()["uploaded"][0]["image_id"]

        r = client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={
                "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                "side": "dorso",
                "rotation_degrees": 0,
            },
        )
        crop_id = r.json()["crop_id"]

        # Verificar que el crop ahora tiene el dni_number leído
        state = client.get(f"/api/v1/sessions/{sid}").json()
        crop_info = next(c for c in state["crops"] if c["crop_id"] == crop_id)
        assert crop_info["dni_number"] == "12345678"

    def test_ocr_failure_does_not_break_crop_creation(
        self, isolated_sessions_dir, sample_image_bytes, monkeypatch,
    ):
        """Si OCR tira excepción, el crop queda con dni_number=None pero
        sigue siendo válido."""
        def failing_extract(path):
            raise RuntimeError("Simulated OCR failure")

        import app.api.v1.routes_processing
        monkeypatch.setattr(
            app.api.v1.routes_processing, "extract_dni_number", failing_extract,
        )

        from app.main import create_app
        app = create_app()
        client = TestClient(app)

        sid = client.post("/api/v1/sessions").json()["session_id"]
        upload = client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        img_id = upload.json()["uploaded"][0]["image_id"]

        # El crop debe crearse exitosamente a pesar de que OCR falla
        r = client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={
                "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                "side": "dorso",
                "rotation_degrees": 0,
            },
        )
        assert r.status_code == 201
        crop_id = r.json()["crop_id"]

        # Y dni_number queda en None
        state = client.get(f"/api/v1/sessions/{sid}").json()
        crop_info = next(c for c in state["crops"] if c["crop_id"] == crop_id)
        assert crop_info["dni_number"] is None
