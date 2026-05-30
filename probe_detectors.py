#!/usr/bin/env python3
"""
probe_detectors.py — Prueba rápida de detectores nativos de OpenCV.

OBJETIVO: validar si OpenCV puede detectar caras (en frentes) y códigos
de barras PDF417 (en dorsos) en tu set real de fotos, ANTES de invertir
tiempo en rediseñar el pipeline completo.

Este script NO genera PDFs ni recortes. Solo cuenta detecciones y produce
un reporte agregado que vos podés evaluar para decidir si la estrategia
es viable.

POLÍTICA DE PRIVACIDAD (idéntica al script de calibración):
- IDs opacos por defecto para imágenes problemáticas (hash SHA1 truncado).
- Sin filenames, sin contenido de imagen, sin datos personales en el reporte.
- Flag --include-filenames disponible para uso local ÚNICAMENTE.

QUÉ MIDE:
1. Sobre la carpeta de FRENTES: cuántas caras detecta OpenCV DNN.
2. Sobre la carpeta de DORSOS: cuántos códigos de barras detecta OpenCV.
3. Distribución de detecciones por foto (1, 2, 3, 4 detecciones).
4. Imágenes problemáticas (0 detecciones).

CRITERIO DE ÉXITO:
- Frentes: tasa de fotos con AL MENOS 1 cara detectada >= 90%.
- Dorsos: tasa de fotos con AL MENOS 1 PDF417 detectado >= 75%
  (los códigos de barras son más sensibles al ángulo).
- Para fotos con múltiples DNIs, validar que detecte el número correcto
  (necesitamos que el usuario indique manualmente cuántos DNIs reales
  hay en cada foto multi-DNI — esto va en el reporte).

DEPENDENCIAS:
    pip install opencv-python-headless pyzbar pillow pillow-heif rich typer

NOTA: pyzbar requiere zbar a nivel sistema:
    sudo apt-get install libzbar0
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# HEIC support para iPhone
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

# pyzbar para PDF417 (más confiable que el BarcodeDetector de OpenCV)
try:
    from pyzbar import pyzbar
    PYZBAR_OK = True
except ImportError:
    PYZBAR_OK = False


console = Console()
app = typer.Typer(
    help="Probe rápido: ¿OpenCV detecta caras en frentes y barcodes en dorsos?",
    add_completion=False,
)

ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


# ============================================================
# Modelos pre-entrenados — descarga lazy
# ============================================================

# OpenCV DNN face detector: modelo ResNet-10 SSD, ~10 MB.
# Mucho mejor que Haar cascades para fotos reales con variación de pose/luz.
FACE_MODEL_URL_PROTO = (
    "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/"
    "face_detector/deploy.prototxt"
)
FACE_MODEL_URL_WEIGHTS = (
    "https://raw.githubusercontent.com/opencv/opencv_3rdparty/"
    "dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
)


def _ensure_face_model(cache_dir: Path) -> tuple[Path, Path]:
    """Descarga el modelo de detección de caras si no está en cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    proto_path = cache_dir / "deploy.prototxt"
    weights_path = cache_dir / "res10_300x300_ssd_iter_140000.caffemodel"

    if not proto_path.exists():
        console.print(f"[dim]Descargando prototxt del detector de caras...[/dim]")
        urllib.request.urlretrieve(FACE_MODEL_URL_PROTO, proto_path)
    if not weights_path.exists():
        console.print(f"[dim]Descargando weights del detector de caras (~10 MB)...[/dim]")
        urllib.request.urlretrieve(FACE_MODEL_URL_WEIGHTS, weights_path)

    return proto_path, weights_path


# ============================================================
# Helpers
# ============================================================

def _opaque_id(path: Path, include_filenames: bool) -> str:
    if include_filenames:
        return path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"img_{hashlib.sha1(f'{path.name}:{size}'.encode()).hexdigest()[:8]}"


def _load_image(path: Path) -> np.ndarray | None:
    """Carga una imagen como BGR. Devuelve None si falla."""
    try:
        if path.suffix.lower() in (".heic", ".heif"):
            from PIL import Image
            pil = Image.open(path).convert("RGB")
            return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def _list_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )


# ============================================================
# Detector de caras
# ============================================================

def _detect_faces(
    image: np.ndarray,
    net: cv2.dnn.Net,
    confidence_threshold: float = 0.5,
) -> list[tuple[int, int, int, int, float]]:
    """
    Detecta caras en la imagen.

    Returns:
        Lista de (x1, y1, x2, y2, confidence).
    """
    h, w = image.shape[:2]
    # El modelo SSD-ResNet espera 300x300, BGR, con mean subtraction.
    blob = cv2.dnn.blobFromImage(
        cv2.resize(image, (300, 300)),
        1.0,
        (300, 300),
        (104.0, 177.0, 123.0),
    )
    net.setInput(blob)
    detections = net.forward()

    faces = []
    for i in range(detections.shape[2]):
        conf = float(detections[0, 0, i, 2])
        if conf < confidence_threshold:
            continue
        # Las coordenadas vienen normalizadas [0, 1]
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        # Clamp a los límites de la imagen
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            faces.append((x1, y1, x2, y2, conf))

    # Deduplicación por NMS (puede haber detecciones solapadas)
    if len(faces) > 1:
        boxes = [(f[0], f[1], f[2] - f[0], f[3] - f[1]) for f in faces]
        confs = [f[4] for f in faces]
        indices = cv2.dnn.NMSBoxes(boxes, confs, 0.5, 0.3)
        if len(indices) > 0:
            indices = indices.flatten() if hasattr(indices, "flatten") else indices
            faces = [faces[i] for i in indices]

    return faces


# ============================================================
# Detector de PDF417 (códigos de barras del dorso)
# ============================================================

def _detect_barcodes(image: np.ndarray) -> list[dict]:
    """
    Detecta códigos de barras (cualquier tipo) en la imagen.
    Prefiere PDF417 pero acepta cualquiera, ya que en algunas fotos
    el código puede leerse como otro tipo por baja resolución.

    Returns:
        Lista de dicts con 'type', 'rect' (x, y, w, h).
    """
    if not PYZBAR_OK:
        # Fallback al BarcodeDetector nativo de OpenCV (menos confiable)
        detector = cv2.barcode.BarcodeDetector()
        ok, decoded, types, points = detector.detectAndDecodeWithType(image)
        results = []
        if ok and points is not None:
            for i, pts in enumerate(points):
                x_coords = pts[:, 0]
                y_coords = pts[:, 1]
                x, y = int(x_coords.min()), int(y_coords.min())
                w = int(x_coords.max() - x)
                h = int(y_coords.max() - y)
                results.append({
                    "type": types[i] if types is not None else "unknown",
                    "rect": (x, y, w, h),
                })
        return results

    # pyzbar: más robusto, especialmente para PDF417 en fotos de DNI
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    decoded = pyzbar.decode(gray)
    return [
        {
            "type": str(d.type),
            "rect": (d.rect.left, d.rect.top, d.rect.width, d.rect.height),
        }
        for d in decoded
    ]


# ============================================================
# Resultado de procesamiento por imagen
# ============================================================

@dataclass
class ImageProbeResult:
    id: str
    n_faces: int = 0
    n_barcodes: int = 0
    barcode_types: list[str] = field(default_factory=list)
    error: str | None = None


# ============================================================
# Procesamiento de FRENTES
# ============================================================

def _probe_frentes(
    images: list[Path],
    net: cv2.dnn.Net,
    include_filenames: bool,
) -> list[ImageProbeResult]:
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Frentes[/cyan]: {task.description}"),
        TextColumn("[bold]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detectando caras...", total=len(images))
        for img_path in images:
            r = ImageProbeResult(id=_opaque_id(img_path, include_filenames))
            img = _load_image(img_path)
            if img is None:
                r.error = "no_se_pudo_cargar"
            else:
                try:
                    faces = _detect_faces(img, net)
                    r.n_faces = len(faces)
                except Exception as e:
                    r.error = f"deteccion_fallo: {type(e).__name__}"
            results.append(r)
            progress.advance(task)
    return results


# ============================================================
# Procesamiento de DORSOS
# ============================================================

def _probe_dorsos(
    images: list[Path],
    include_filenames: bool,
) -> list[ImageProbeResult]:
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Dorsos[/cyan]: {task.description}"),
        TextColumn("[bold]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detectando códigos de barras...", total=len(images))
        for img_path in images:
            r = ImageProbeResult(id=_opaque_id(img_path, include_filenames))
            img = _load_image(img_path)
            if img is None:
                r.error = "no_se_pudo_cargar"
            else:
                try:
                    barcodes = _detect_barcodes(img)
                    r.n_barcodes = len(barcodes)
                    r.barcode_types = [b["type"] for b in barcodes]
                except Exception as e:
                    r.error = f"deteccion_fallo: {type(e).__name__}"
            results.append(r)
            progress.advance(task)
    return results


# ============================================================
# Reporte
# ============================================================

def _summarize_side(
    results: list[ImageProbeResult],
    detection_attr: str,
    label: str,
    target_rate: float,
) -> dict:
    n_total = len(results)
    n_with_detection = sum(1 for r in results if getattr(r, detection_attr) >= 1)
    n_with_error = sum(1 for r in results if r.error is not None)
    rate = n_with_detection / n_total if n_total else 0

    detection_counts = Counter(getattr(r, detection_attr) for r in results)

    return {
        "label": label,
        "n_total": n_total,
        "n_with_detection": n_with_detection,
        "n_zero_detection": sum(1 for r in results if getattr(r, detection_attr) == 0 and r.error is None),
        "n_errors": n_with_error,
        "detection_rate": round(rate, 4),
        "target_rate": target_rate,
        "passes_target": rate >= target_rate,
        "distribution": {str(k): v for k, v in sorted(detection_counts.items())},
    }


def _print_summary_table(summary_frentes: dict, summary_dorsos: dict) -> None:
    table = Table(title="📊 Resumen del probe")
    table.add_column("Métrica", style="cyan")
    table.add_column("Frentes (caras)", justify="right")
    table.add_column("Dorsos (PDF417)", justify="right")

    table.add_row("Total imágenes", str(summary_frentes["n_total"]), str(summary_dorsos["n_total"]))
    table.add_row(
        "Con detección (≥1)",
        f"{summary_frentes['n_with_detection']} ({summary_frentes['detection_rate']*100:.1f}%)",
        f"{summary_dorsos['n_with_detection']} ({summary_dorsos['detection_rate']*100:.1f}%)",
    )
    table.add_row(
        "Sin detección",
        str(summary_frentes["n_zero_detection"]),
        str(summary_dorsos["n_zero_detection"]),
    )
    table.add_row(
        "Errores de carga",
        str(summary_frentes["n_errors"]),
        str(summary_dorsos["n_errors"]),
    )
    table.add_row(
        "Target",
        f"≥ {summary_frentes['target_rate']*100:.0f}%",
        f"≥ {summary_dorsos['target_rate']*100:.0f}%",
    )

    status_f = "[green]✓ PASA[/green]" if summary_frentes["passes_target"] else "[red]✗ NO PASA[/red]"
    status_d = "[green]✓ PASA[/green]" if summary_dorsos["passes_target"] else "[red]✗ NO PASA[/red]"
    table.add_row("Resultado", status_f, status_d)

    console.print(table)

    # Distribución
    console.print("\n[bold]Distribución de detecciones por imagen:[/bold]")
    console.print(f"  Frentes — {summary_frentes['distribution']}")
    console.print(f"  Dorsos — {summary_dorsos['distribution']}")


# ============================================================
# Comando principal
# ============================================================

@app.command()
def main(
    frentes: Path = typer.Option(..., "--frentes", "-f", exists=True, file_okay=False),
    dorsos: Path = typer.Option(..., "--dorsos", "-d", exists=True, file_okay=False),
    output: Path = typer.Option(
        Path("./probe_report"),
        "--output", "-o",
        help="Path base para los reportes (.txt y .json)",
    ),
    face_confidence: float = typer.Option(
        0.5,
        "--face-confidence",
        help="Umbral de confianza para detección de caras (0-1)",
    ),
    model_cache: Path = typer.Option(
        Path.home() / ".cache" / "dni_probe",
        "--model-cache",
        help="Directorio para guardar el modelo de detección de caras",
    ),
    include_filenames: bool = typer.Option(
        False,
        "--include-filenames",
        help="Incluir nombres reales (USO LOCAL — no compartir el reporte)",
    ),
) -> None:
    """
    Corre los detectores sobre tus carpetas y reporta tasas agregadas.

    NO procesa imágenes (no genera recortes ni PDFs). Solo mide si los
    detectores enganchan o no, para decidir si la estrategia de visión
    basada en caras (frentes) y códigos de barras (dorsos) es viable
    en tu set real.
    """
    console.print("[bold cyan]DNI Probe — Test de detectores nativos[/bold cyan]\n")

    # Validar dependencias
    if not PYZBAR_OK:
        console.print(
            "[yellow]⚠ pyzbar no instalado. Se usará BarcodeDetector de OpenCV (menos confiable).\n"
            "  Para mejor performance: pip install pyzbar  (requiere `sudo apt-get install libzbar0`)[/yellow]\n"
        )
    if not HEIC_OK:
        console.print(
            "[yellow]⚠ pillow-heif no instalado. Fotos HEIC (iPhone) no se podrán leer.\n"
            "  Para soporte HEIC: pip install pillow-heif[/yellow]\n"
        )

    # Listar imágenes
    frente_images = _list_images(frentes)
    dorso_images = _list_images(dorsos)

    if not frente_images or not dorso_images:
        console.print("[red]✗ Una de las carpetas está vacía.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[cyan]Frentes:[/cyan] {len(frente_images)}  "
        f"[cyan]Dorsos:[/cyan] {len(dorso_images)}\n"
    )

    # Descargar/cargar modelo de caras
    proto_path, weights_path = _ensure_face_model(model_cache)
    face_net = cv2.dnn.readNetFromCaffe(str(proto_path), str(weights_path))

    # Correr los probes
    frente_results = _probe_frentes(frente_images, face_net, include_filenames)
    dorso_results = _probe_dorsos(dorso_images, include_filenames)

    # Resumir
    summary_frentes = _summarize_side(frente_results, "n_faces", "frentes", target_rate=0.90)
    summary_dorsos = _summarize_side(dorso_results, "n_barcodes", "dorsos", target_rate=0.75)

    console.print()
    _print_summary_table(summary_frentes, summary_dorsos)

    # Imágenes problemáticas (sin detección, sin error de carga)
    problematic_frentes = [
        {"id": r.id, "reason": "sin_cara_detectada"}
        for r in frente_results
        if r.n_faces == 0 and r.error is None
    ]
    problematic_dorsos = [
        {"id": r.id, "reason": "sin_barcode_detectado"}
        for r in dorso_results
        if r.n_barcodes == 0 and r.error is None
    ]

    if problematic_frentes or problematic_dorsos:
        console.print("\n[yellow]⚠ Imágenes sin detección (revisar localmente):[/yellow]")
        for p in problematic_frentes:
            console.print(f"  [frente] {p['id']}: {p['reason']}")
        for p in problematic_dorsos:
            console.print(f"  [dorso] {p['id']}: {p['reason']}")

    # Guardar reportes
    output.parent.mkdir(parents=True, exist_ok=True)

    # JSON estructurado
    json_path = output.with_suffix(".json")
    with json_path.open("w") as f:
        json.dump({
            "summary": {
                "frentes": summary_frentes,
                "dorsos": summary_dorsos,
            },
            "problematic_frentes": problematic_frentes,
            "problematic_dorsos": problematic_dorsos,
            "config": {
                "face_confidence_threshold": face_confidence,
                "pyzbar_available": PYZBAR_OK,
                "heic_available": HEIC_OK,
            },
        }, f, indent=2)

    # TXT legible
    txt_path = output.with_suffix(".txt")
    with txt_path.open("w") as f:
        f.write("# DNI Probe — Reporte de viabilidad de detectores\n\n")

        f.write("## Configuración\n")
        f.write(f"  face_confidence_threshold: {face_confidence}\n")
        f.write(f"  pyzbar_available: {PYZBAR_OK}\n")
        f.write(f"  heic_available: {HEIC_OK}\n\n")

        for label, s in [("FRENTES (caras)", summary_frentes), ("DORSOS (barcodes)", summary_dorsos)]:
            f.write(f"## {label}\n")
            f.write(f"  Total: {s['n_total']}\n")
            f.write(f"  Con detección: {s['n_with_detection']} ({s['detection_rate']*100:.1f}%)\n")
            f.write(f"  Sin detección: {s['n_zero_detection']}\n")
            f.write(f"  Errores de carga: {s['n_errors']}\n")
            f.write(f"  Target: ≥ {s['target_rate']*100:.0f}%\n")
            f.write(f"  Resultado: {'PASA' if s['passes_target'] else 'NO PASA'}\n")
            f.write(f"  Distribución de detecciones: {s['distribution']}\n\n")

        f.write("## Problemáticas\n")
        if not problematic_frentes and not problematic_dorsos:
            f.write("  (ninguna)\n")
        else:
            for p in problematic_frentes:
                f.write(f"  [frente] {p['id']}: {p['reason']}\n")
            for p in problematic_dorsos:
                f.write(f"  [dorso] {p['id']}: {p['reason']}\n")

    console.print(f"\n[green]✓ Reporte JSON:[/green] {json_path}")
    console.print(f"[green]✓ Reporte TXT:[/green]  {txt_path}")

    if not include_filenames:
        console.print("[dim]IDs opacos. El reporte es compartible.[/dim]")
    else:
        console.print("[yellow]⚠ --include-filenames activado. NO compartas estos reportes.[/yellow]")


if __name__ == "__main__":
    app()
