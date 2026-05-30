"""
Tests de integración para los endpoints de matcheo y generación de PDF
(Sprint 3a).

Cubren:
- POST /sessions/{id}/match (sugerencias por OCR)
- PUT /sessions/{id}/pairs (drag-and-drop declarativo)
- POST /sessions/{id}/generate-pdf (con validación estricta de huérfanos)
- GET /sessions/{id}/output.pdf (descarga)
- POST /sessions/{id}/reset (descartar para empezar otro trámite)

Estos tests construyen sesiones con crops manualmente confirmados
(sin invocar el detector facial), igual que test_api.py. La idea es
validar la API de matcheo, no la pipeline de visión.
"""

from __future__ import annotations

import io

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
    """JPG válido sin DNI real."""
    img = np.full((400, 600, 3), 128, dtype=np.uint8)
    img[50:150, 50:200] = 200
    success, encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert success
    return encoded.tobytes()


def _create_session_with_pairs(
    client: TestClient,
    sample_bytes: bytes,
    n_pairs: int,
) -> tuple[str, list[str], list[str]]:
    """
    Crea una sesión con N pares válidos.

    Sube 2*N imágenes (N frentes + N dorsos) y crea recortes manuales
    sobre cada una. No invoca detección automática.

    Returns:
        (session_id, [frente_crop_ids], [dorso_crop_ids])
    """
    sid = client.post("/api/v1/sessions").json()["session_id"]

    # Subir N frentes
    upload_f = client.post(
        f"/api/v1/sessions/{sid}/images",
        data={"side": "frente"},
        files=[
            ("files", (f"f{i}.jpg", sample_bytes, "image/jpeg"))
            for i in range(n_pairs)
        ],
    )
    frente_img_ids = [u["image_id"] for u in upload_f.json()["uploaded"]]

    # Subir N dorsos
    upload_d = client.post(
        f"/api/v1/sessions/{sid}/images",
        data={"side": "dorso"},
        files=[
            ("files", (f"d{i}.jpg", sample_bytes, "image/jpeg"))
            for i in range(n_pairs)
        ],
    )
    dorso_img_ids = [u["image_id"] for u in upload_d.json()["uploaded"]]

    # Crear recortes manuales para cada uno
    frente_crop_ids = []
    for img_id in frente_img_ids:
        r = client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={
                "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                "side": "frente",
                "rotation_degrees": 0,
            },
        )
        assert r.status_code == 201
        frente_crop_ids.append(r.json()["crop_id"])

    dorso_crop_ids = []
    for img_id in dorso_img_ids:
        r = client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={
                "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                "side": "dorso",
                "rotation_degrees": 0,
            },
        )
        assert r.status_code == 201
        dorso_crop_ids.append(r.json()["crop_id"])

    return sid, frente_crop_ids, dorso_crop_ids


# ============================================================
# Match (sugerencias)
# ============================================================

class TestGenerateSuggestions:
    def test_match_with_no_crops_returns_400(self, client):
        """Sin crops confirmados no se puede matchear."""
        sid = client.post("/api/v1/sessions").json()["session_id"]
        r = client.post(f"/api/v1/sessions/{sid}/match")
        assert r.status_code == 400

    def test_match_creates_pairs(self, client, sample_image_bytes):
        """Con N frentes y N dorsos se generan pares."""
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 3)

        r = client.post(f"/api/v1/sessions/{sid}/match")
        assert r.status_code == 200, r.text
        data = r.json()
        # Sin OCR real (imágenes vacías), el matcher cae a "sin sugerencias",
        # pero los frentes/dorsos quedan disponibles. La cantidad de pares
        # depende del comportamiento exacto del matcher. Validamos al menos
        # que el endpoint corra y devuelva una estructura coherente.
        assert "session_id" in data
        assert "pairs" in data
        assert isinstance(data["pairs"], list)
        assert "n_unpaired_frentes" in data
        assert "n_unpaired_dorsos" in data

    def test_match_updates_session_status_to_matching(self, client, sample_image_bytes):
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 2)
        client.post(f"/api/v1/sessions/{sid}/match")
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.json()["status"] == "matching"

    def test_match_session_not_found(self, client):
        r = client.post("/api/v1/sessions/nope/match")
        assert r.status_code == 404


# ============================================================
# Update pairs (drag-and-drop)
# ============================================================

class TestUpdatePairs:
    def test_set_pairs_manually(self, client, sample_image_bytes):
        """El usuario manda pares manualmente — sin OCR previo."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)

        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[1], "position": 1},
                ]
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["pairs"]) == 2
        assert data["pairs"][0]["position"] == 0
        assert data["pairs"][1]["position"] == 1

    def test_pairs_ordered_by_position(self, client, sample_image_bytes):
        """La respuesta devuelve pares ordenados por position."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 3)
        # Mandamos en orden invertido
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 2},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[1], "position": 0},
                    {"frente_crop_id": fs[2], "dorso_crop_id": ds[2], "position": 1},
                ]
            },
        )
        assert r.status_code == 200
        positions = [p["position"] for p in r.json()["pairs"]]
        assert positions == [0, 1, 2]
        # El primero (position=0) debe ser fs[1]/ds[1]
        assert r.json()["pairs"][0]["frente_crop_id"] == fs[1]

    def test_pair_with_wrong_side_rejected(self, client, sample_image_bytes):
        """No se puede emparejar 'frente' con 'frente'."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    # Mandamos dos frentes intencionalmente
                    {"frente_crop_id": fs[0], "dorso_crop_id": fs[1], "position": 0},
                ]
            },
        )
        assert r.status_code == 400
        assert "no es un dorso" in r.text.lower()

    def test_duplicate_frente_rejected(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                    # Mismo frente otra vez
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[1], "position": 1},
                ]
            },
        )
        assert r.status_code == 400
        assert "múltiples pares" in r.text.lower() or "multiples pares" in r.text.lower()

    def test_duplicate_position_rejected(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[1], "position": 0},
                ]
            },
        )
        assert r.status_code == 400

    def test_nonexistent_crop_rejected(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": "fake-id", "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        assert r.status_code == 400

    def test_empty_pairs_clears_session(self, client, sample_image_bytes):
        """Mandar lista vacía deja la sesión sin pares (estado válido)."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        # Primero crear pares
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        # Después vaciar
        r = client.put(f"/api/v1/sessions/{sid}/pairs", json={"pairs": []})
        assert r.status_code == 200
        assert r.json()["pairs"] == []


# ============================================================
# can_generate_pdf y validación de huérfanos
# ============================================================

class TestCanGeneratePdf:
    def test_no_pairs_means_cannot_generate(self, client, sample_image_bytes):
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.get(f"/api/v1/sessions/{sid}")
        data = r.json()
        assert data["can_generate_pdf"] is False
        assert data["imbalance_message"] is not None

    def test_imbalance_more_frentes_than_dorsos(self, client, sample_image_bytes):
        """Si hay más frentes que dorsos, can_generate_pdf=False con mensaje."""
        sid = client.post("/api/v1/sessions").json()["session_id"]

        # Subir 3 frentes y 1 dorso, hacer recortes manuales en todos
        for i in range(3):
            upload = client.post(
                f"/api/v1/sessions/{sid}/images",
                data={"side": "frente"},
                files=[("files", (f"f{i}.jpg", sample_image_bytes, "image/jpeg"))],
            )
            img_id = upload.json()["uploaded"][0]["image_id"]
            client.post(
                f"/api/v1/sessions/{sid}/images/{img_id}/crops",
                json={
                    "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                    "side": "frente",
                    "rotation_degrees": 0,
                },
            )
        upload = client.post(
            f"/api/v1/sessions/{sid}/images",
            data={"side": "dorso"},
            files=[("files", ("d.jpg", sample_image_bytes, "image/jpeg"))],
        )
        img_id = upload.json()["uploaded"][0]["image_id"]
        client.post(
            f"/api/v1/sessions/{sid}/images/{img_id}/crops",
            json={
                "bbox": {"x": 20, "y": 20, "width": 500, "height": 300},
                "side": "dorso",
                "rotation_degrees": 0,
            },
        )

        r = client.get(f"/api/v1/sessions/{sid}")
        data = r.json()
        assert data["can_generate_pdf"] is False
        msg = data["imbalance_message"].lower()
        assert "faltan" in msg and "dorso" in msg

    def test_balanced_and_all_paired_can_generate(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[1], "position": 1},
                ]
            },
        )
        r = client.get(f"/api/v1/sessions/{sid}")
        data = r.json()
        assert data["can_generate_pdf"] is True
        assert data["imbalance_message"] is None


# ============================================================
# Generación del PDF
# ============================================================

class TestGeneratePdf:
    def test_cannot_generate_without_pairs(self, client, sample_image_bytes):
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 2)
        r = client.post(f"/api/v1/sessions/{sid}/generate-pdf")
        assert r.status_code == 400

    def test_generate_pdf_success(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[1], "position": 1},
                ]
            },
        )

        r = client.post(f"/api/v1/sessions/{sid}/generate-pdf")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["n_pairs"] == 2
        assert data["size_bytes"] > 0
        assert data["pdf_url"].endswith("/output.pdf")

    def test_session_status_completed_after_generate(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 1)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        client.post(f"/api/v1/sessions/{sid}/generate-pdf")

        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.json()["status"] == "completed"

    def test_download_pdf(self, client, sample_image_bytes):
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 1)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        client.post(f"/api/v1/sessions/{sid}/generate-pdf")

        r = client.get(f"/api/v1/sessions/{sid}/output.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        # PDF mágico
        assert r.content[:4] == b"%PDF"

    def test_pdf_default_is_inline_for_iframe_embed(self, client, sample_image_bytes):
        """Sin ?download=1 el PDF se sirve INLINE para poder embeberlo
        en un iframe en /completed. Si fuera attachment, el browser lo
        descargaría automáticamente al cargar el iframe."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 1)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        client.post(f"/api/v1/sessions/{sid}/generate-pdf")

        r = client.get(f"/api/v1/sessions/{sid}/output.pdf")
        assert r.status_code == 200
        disposition = r.headers.get("content-disposition", "")
        assert disposition.startswith("inline"), \
            f"Expected inline disposition, got: {disposition!r}"

    def test_pdf_download_query_forces_attachment(self, client, sample_image_bytes):
        """Con ?download=1 se fuerza attachment para que el browser
        descargue. Lo usa el botón 'Descargar PDF' del /completed."""
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 1)
        client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[0], "position": 0},
                ]
            },
        )
        client.post(f"/api/v1/sessions/{sid}/generate-pdf")

        r = client.get(f"/api/v1/sessions/{sid}/output.pdf?download=1")
        assert r.status_code == 200
        disposition = r.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert "dni_processor_" in disposition

    def test_download_without_generate_returns_404(self, client, sample_image_bytes):
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 1)
        r = client.get(f"/api/v1/sessions/{sid}/output.pdf")
        assert r.status_code == 404


# ============================================================
# Reset (empezar otro trámite)
# ============================================================

class TestReset:
    def test_reset_discards_session(self, client, sample_image_bytes):
        sid, _, _ = _create_session_with_pairs(client, sample_image_bytes, 1)
        r = client.post(f"/api/v1/sessions/{sid}/reset")
        assert r.status_code == 200
        data = r.json()
        assert data["discarded_session_id"] == sid
        assert data["redirect_to"] == "/"

        # La sesión ya no existe
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 404

    def test_reset_nonexistent_returns_404(self, client):
        r = client.post("/api/v1/sessions/nope/reset")
        assert r.status_code == 404


# ============================================================
# Lifecycle completo (sin OCR real)
# ============================================================

class TestFullLifecycle:
    def test_create_to_pdf_to_reset(self, client, sample_image_bytes):
        """
        Lifecycle completo desde matcheo:
        1. Sesión con 2 frentes + 2 dorsos confirmados
        2. PUT pairs manuales
        3. Generar PDF
        4. Descargar
        5. Reset (empezar otro trámite)
        """
        sid, fs, ds = _create_session_with_pairs(client, sample_image_bytes, 2)

        # 1. Estado inicial: hay crops pero no pares
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.json()["can_generate_pdf"] is False

        # 2. Mandar pares manuales
        r = client.put(
            f"/api/v1/sessions/{sid}/pairs",
            json={
                "pairs": [
                    {"frente_crop_id": fs[0], "dorso_crop_id": ds[1], "position": 0},
                    {"frente_crop_id": fs[1], "dorso_crop_id": ds[0], "position": 1},
                ]
            },
        )
        assert r.status_code == 200

        # 3. Ahora sí can_generate_pdf
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.json()["can_generate_pdf"] is True

        # 4. Generar
        r = client.post(f"/api/v1/sessions/{sid}/generate-pdf")
        assert r.status_code == 200

        # 5. Descargar
        r = client.get(f"/api/v1/sessions/{sid}/output.pdf")
        assert r.status_code == 200
        pdf_size = len(r.content)
        assert pdf_size > 100  # PDF mínimo razonable

        # 6. Reset
        r = client.post(f"/api/v1/sessions/{sid}/reset")
        assert r.status_code == 200
        assert r.json()["redirect_to"] == "/"

        # 7. Sesión ya no existe
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 404
