#!/usr/bin/env python3
"""
probe_detectors_v2.py — Segunda iteración del probe de detección.

CAMBIOS RESPECTO A v1:

  1. CARGA CON EXIF RESPETADO. Las imágenes se cargan con Pillow primero
     (que lee EXIF y rota físicamente si hace falta) y luego se convierten
     a OpenCV. Esto resuelve el caso de fotos con metadata "rotada 90°"
     donde OpenCV puro ve la imagen acostada.

  2. PREPROCESAMIENTO CLAHE. Antes de detectar, se aplica CLAHE
     (Contrast Limited Adaptive Histogram Equalization) sobre el canal V
     del espacio HSV. Mejora contraste local en fotos con iluminación
     irregular sin alterar el color general.

  3. THRESHOLD DE CONFIANZA BAJADO. De 0.5 a 0.3 por defecto. Las caras
     en fotos de DNI son chicas dentro del frame; un threshold más
     permisivo recupera detecciones reales que el modelo marca con
     confianza intermedia.

  4. SOLO MIDE FRENTES. Los dorsos los abandonamos para detección
     automática (pyzbar dio 5.6%, no recuperable). En el flujo final
     los dorsos van directo al recorte manual.

  5. DETECCIÓN DETALLADA POR IMAGEN. Reporte incluye distribución
     completa: cuántas imágenes con 0, 1, 2, 3, 4+ caras. Esto valida
     que también enganche correctamente las "columnas" con múltiples
     DNIs apilados.

POLÍTICA DE PRIVACIDAD: idéntica a v1 (IDs opacos por defecto, sin
filenames, sin contenido).

DEPENDENCIAS:
    pip install opencv-python-headless numpy pillow pillow-heif typer rich
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import typer
from PIL import Image, ImageOps
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False


console = Console()
app = typer.Typer(
    help="Probe v2: detección de caras en frentes con EXIF respetado + CLAHE.",
    add_completion=False,
)

ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")


# ============================================================
# Modelo de caras (igual que v1)
# ============================================================

FACE_PROTO_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/"
    "face_detector/deploy.prototxt"
)
FACE_WEIGHTS_URL = (
    "https://raw.githubusercontent.com/opencv/opencv_3rdparty/"
    "dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
)


def _ensure_face_model(cache_dir: Path) -> tuple[Path, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    proto = cache_dir / "deploy.prototxt"
    weights = cache_dir / "res10_300x300_ssd_iter_140000.caffemodel"
    if not proto.exists():
        console.print("[dim]Descargando prototxt del detector de caras...[/dim]")
        urllib.request.urlretrieve(FACE_PROTO_URL, proto)
    if not weights.exists():
        console.print("[dim]Descargando weights (~10 MB)...[/dim]")
        urllib.request.urlretrieve(FACE_WEIGHTS_URL, weights)
    return proto, weights


# ============================================================
# Helpers de identidad opaca
# ============================================================

def _opaque_id(path: Path, include_filenames: bool) -> str:
    if include_filenames:
        return path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"img_{hashlib.sha1(f'{path.name}:{size}'.encode()).hexdigest()[:8]}"


def _list_images(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )


# ============================================================
# Carga con EXIF respetado
# ============================================================

def _load_image_exif_aware(path: Path) -> tuple[np.ndarray | None, dict]:
    """
    Carga una imagen respetando EXIF orientation.

    Pillow lee el EXIF y `ImageOps.exif_transpose()` aplica físicamente
    la rotación al array de píxeles. El resultado se convierte a BGR
    para OpenCV.

    Returns:
        Tupla (imagen BGR, metadata sobre la rotación aplicada).
        Imagen es None si falló la carga.
    """
    meta = {"original_size": None, "loaded_size": None, "exif_orientation": None, "rotated": False}
    try:
        pil = Image.open(path)
        meta["original_size"] = pil.size

        # Capturar el EXIF orientation antes de transponer
        try:
            exif = pil.getexif()
            # Tag 274 es Orientation
            orient = exif.get(274) if exif else None
            meta["exif_orientation"] = orient
            if orient and orient != 1:
                meta["rotated"] = True
        except Exception:
            pass

        # Aplicar rotación EXIF si corresponde
        pil = ImageOps.exif_transpose(pil)
        # Asegurar RGB (algunas HEIC vienen en otros modos)
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        meta["loaded_size"] = pil.size

        # Convertir a BGR para OpenCV
        bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        return bgr, meta
    except Exception as e:
        meta["error"] = f"{type(e).__name__}: {e}"
        return None, meta


# ============================================================
# Preprocesamiento CLAHE
# ============================================================

def _apply_clahe(image_bgr: np.ndarray, clip_limit: float = 3.0) -> np.ndarray:
    """
    Aplica CLAHE sobre el canal V (luminosidad) del espacio HSV.

    CLAHE = Contrast Limited Adaptive Histogram Equalization.
    Mejora el contraste en regiones locales sin saturar globalmente,
    útil para fotos con iluminación irregular o reflejos parciales.

    El canal V (Value en HSV) representa luminosidad: equalizarlo no
    altera los colores percibidos, solo redistribuye luz/sombra.
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    v_eq = clahe.apply(v)
    hsv_eq = cv2.merge([h, s, v_eq])
    return cv2.cvtColor(hsv_eq, cv2.COLOR_HSV2BGR)


# ============================================================
# Detector de caras
# ============================================================

def _detect_faces(
    image: np.ndarray,
    net: cv2.dnn.Net,
    confidence_threshold: float,
) -> list[tuple[int, int, int, int, float]]:
    h, w = image.shape[:2]
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
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            faces.append((x1, y1, x2, y2, conf))

    # NMS
    if len(faces) > 1:
        boxes = [(f[0], f[1], f[2] - f[0], f[3] - f[1]) for f in faces]
        confs = [f[4] for f in faces]
        indices = cv2.dnn.NMSBoxes(boxes, confs, confidence_threshold, 0.3)
        if len(indices) > 0:
            indices = indices.flatten() if hasattr(indices, "flatten") else indices
            faces = [faces[i] for i in indices]

    return faces


def _detect_faces_multi_scale(
    image: np.ndarray,
    net: cv2.dnn.Net,
    confidence_threshold: float,
    enable_clahe: bool,
) -> tuple[list, str]:
    """
    Intenta detección en cascada:
    1. Imagen original (con EXIF ya aplicado)
    2. Si no encuentra nada, aplica CLAHE
    3. Si CLAHE tampoco encuentra, intenta con la imagen rotada 90°
       (cubre el raro caso de EXIF mal escrito o sin EXIF)

    Returns:
        (lista_de_caras, estrategia_que_funcionó)
    """
    # Intento 1: imagen original
    faces = _detect_faces(image, net, confidence_threshold)
    if faces:
        return faces, "original"

    # Intento 2: CLAHE
    if enable_clahe:
        enhanced = _apply_clahe(image)
        faces = _detect_faces(enhanced, net, confidence_threshold)
        if faces:
            return faces, "clahe"

    # Intento 3: rotación 90° antihorario (por si EXIF estaba mal)
    rotated_ccw = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    faces = _detect_faces(rotated_ccw, net, confidence_threshold)
    if faces:
        return faces, "rotated_90_ccw"

    # Intento 4: rotación 90° horario
    rotated_cw = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    faces = _detect_faces(rotated_cw, net, confidence_threshold)
    if faces:
        return faces, "rotated_90_cw"

    # Intento 5: rotación 180°
    rotated_180 = cv2.rotate(image, cv2.ROTATE_180)
    faces = _detect_faces(rotated_180, net, confidence_threshold)
    if faces:
        return faces, "rotated_180"

    return [], "none"


# ============================================================
# Resultado por imagen
# ============================================================

@dataclass
class FrenteResult:
    id: str
    n_faces: int = 0
    strategy: str = "none"
    exif_orientation: int | None = None
    exif_was_applied: bool = False
    error: str | None = None
    avg_confidence: float = 0.0


# ============================================================
# Procesamiento principal
# ============================================================

def _probe_frentes(
    images: list[Path],
    net: cv2.dnn.Net,
    confidence_threshold: float,
    enable_clahe: bool,
    include_filenames: bool,
) -> list[FrenteResult]:
    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]Frentes[/cyan]: {task.description}"),
        TextColumn("[bold]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Procesando...", total=len(images))

        for img_path in images:
            r = FrenteResult(id=_opaque_id(img_path, include_filenames))
            img, meta = _load_image_exif_aware(img_path)
            r.exif_orientation = meta.get("exif_orientation")
            r.exif_was_applied = meta.get("rotated", False)

            if img is None:
                r.error = meta.get("error", "no_se_pudo_cargar")
            else:
                try:
                    faces, strategy = _detect_faces_multi_scale(
                        img, net, confidence_threshold, enable_clahe
                    )
                    r.n_faces = len(faces)
                    r.strategy = strategy
                    if faces:
                        r.avg_confidence = sum(f[4] for f in faces) / len(faces)
                except Exception as e:
                    r.error = f"deteccion_fallo: {type(e).__name__}: {e}"

            results.append(r)
            progress.advance(task)
    return results


# ============================================================
# Reporte
# ============================================================

def _summarize(results: list[FrenteResult], target_rate: float) -> dict:
    n_total = len(results)
    n_with_detection = sum(1 for r in results if r.n_faces >= 1)
    n_with_error = sum(1 for r in results if r.error is not None)
    n_exif_applied = sum(1 for r in results if r.exif_was_applied)
    rate = n_with_detection / n_total if n_total else 0

    detection_counts = Counter(r.n_faces for r in results)
    strategy_counts = Counter(r.strategy for r in results if r.n_faces > 0)
    confidences = [r.avg_confidence for r in results if r.n_faces > 0]

    return {
        "n_total": n_total,
        "n_with_detection": n_with_detection,
        "n_zero_detection": sum(1 for r in results if r.n_faces == 0 and r.error is None),
        "n_errors": n_with_error,
        "n_exif_applied": n_exif_applied,
        "detection_rate": round(rate, 4),
        "target_rate": target_rate,
        "passes_target": rate >= target_rate,
        "distribution": {str(k): v for k, v in sorted(detection_counts.items())},
        "strategy_distribution": dict(strategy_counts),
        "avg_confidence_p50": round(_percentile(confidences, 0.5), 4) if confidences else 0,
        "avg_confidence_p90": round(_percentile(confidences, 0.9), 4) if confidences else 0,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] * (c - k) + s[c] * (k - f)


def _print_summary(summary: dict, v1_rate: float | None = None) -> None:
    table = Table(title="📊 Resumen — Probe v2 (frentes)")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", justify="right", style="bold")

    table.add_row("Total imágenes", str(summary["n_total"]))
    table.add_row(
        "Con detección (≥1)",
        f"{summary['n_with_detection']} ({summary['detection_rate']*100:.1f}%)",
    )
    table.add_row("Sin detección", str(summary["n_zero_detection"]))
    table.add_row("Errores de carga", str(summary["n_errors"]))
    table.add_row("EXIF aplicado (rotación)", str(summary["n_exif_applied"]))
    table.add_row("Target", f"≥ {summary['target_rate']*100:.0f}%")

    status = "[green]✓ PASA[/green]" if summary["passes_target"] else "[yellow]✗ NO PASA[/yellow]"
    table.add_row("Resultado", status)

    if v1_rate is not None:
        delta = summary["detection_rate"] - v1_rate
        delta_str = f"{delta * 100:+.1f}pp"
        color = "green" if delta > 0 else "red"
        table.add_row(
            "vs. v1 (sin EXIF/CLAHE)",
            f"[{color}]{delta_str}[/{color}] (v1 era {v1_rate * 100:.1f}%)",
        )

    table.add_row("Confianza P50", f"{summary['avg_confidence_p50']:.3f}")
    table.add_row("Confianza P90", f"{summary['avg_confidence_p90']:.3f}")

    console.print(table)

    console.print("\n[bold]Distribución de caras detectadas por imagen:[/bold]")
    console.print(f"  {summary['distribution']}")
    console.print(
        "  [dim](N=0 → imágenes sin detección; N>1 → fotos con múltiples DNIs)[/dim]"
    )

    console.print("\n[bold]Qué estrategia rescató cada detección:[/bold]")
    for strategy, count in sorted(summary["strategy_distribution"].items(), key=lambda x: -x[1]):
        console.print(f"  {strategy}: {count}")


# ============================================================
# Comando principal
# ============================================================

@app.command()
def main(
    frentes: Path = typer.Option(..., "--frentes", "-f", exists=True, file_okay=False),
    output: Path = typer.Option(
        Path("./probe_v2_report"),
        "--output", "-o",
        help="Path base para los reportes (.txt y .json)",
    ),
    face_confidence: float = typer.Option(
        0.3,
        "--face-confidence",
        help="Umbral de confianza para detección de caras (default 0.3, más permisivo que v1)",
    ),
    enable_clahe: bool = typer.Option(
        True,
        "--enable-clahe/--no-clahe",
        help="Aplicar CLAHE si la detección falla en la imagen original",
    ),
    model_cache: Path = typer.Option(
        Path.home() / ".cache" / "dni_probe",
        "--model-cache",
    ),
    include_filenames: bool = typer.Option(
        False,
        "--include-filenames",
        help="Incluir nombres reales (USO LOCAL — no compartir el reporte)",
    ),
    v1_rate: float = typer.Option(
        0.778,
        "--v1-rate",
        help="Tasa que dio el probe v1 sobre el mismo set (para comparación)",
    ),
) -> None:
    """
    Corre el detector de caras sobre las imágenes de FRENTES con
    todas las mejoras (EXIF, CLAHE, threshold más permisivo, fallback
    por rotación).

    Solo testea frentes. Los dorsos se abandonan a recorte manual.
    """
    console.print("[bold cyan]DNI Probe v2 — Detección de frentes con EXIF + CLAHE[/bold cyan]\n")

    if not HEIC_OK:
        console.print(
            "[yellow]⚠ pillow-heif no instalado. Fotos HEIC no se podrán leer.[/yellow]\n"
        )

    images = _list_images(frentes)
    if not images:
        console.print("[red]✗ Carpeta de frentes vacía.[/red]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Frentes a procesar:[/cyan] {len(images)}\n")

    proto_path, weights_path = _ensure_face_model(model_cache)
    face_net = cv2.dnn.readNetFromCaffe(str(proto_path), str(weights_path))

    results = _probe_frentes(
        images, face_net, face_confidence, enable_clahe, include_filenames
    )

    summary = _summarize(results, target_rate=0.90)
    console.print()
    _print_summary(summary, v1_rate=v1_rate)

    problematic = [
        {"id": r.id, "reason": "sin_cara_detectada", "exif_orientation": r.exif_orientation}
        for r in results
        if r.n_faces == 0 and r.error is None
    ]
    errors = [
        {"id": r.id, "error": r.error}
        for r in results
        if r.error is not None
    ]

    if problematic:
        console.print("\n[yellow]⚠ Imágenes sin detección (revisar localmente):[/yellow]")
        for p in problematic:
            exif_note = f" [EXIF orient={p['exif_orientation']}]" if p['exif_orientation'] else ""
            console.print(f"  {p['id']}: {p['reason']}{exif_note}")

    if errors:
        console.print("\n[red]⚠ Errores de carga:[/red]")
        for e in errors:
            console.print(f"  {e['id']}: {e['error']}")

    # Reportes
    output.parent.mkdir(parents=True, exist_ok=True)

    json_path = output.with_suffix(".json")
    with json_path.open("w") as f:
        json.dump({
            "version": "probe_v2",
            "summary": summary,
            "problematic": problematic,
            "errors": errors,
            "config": {
                "face_confidence_threshold": face_confidence,
                "clahe_enabled": enable_clahe,
                "heic_available": HEIC_OK,
            },
        }, f, indent=2)

    txt_path = output.with_suffix(".txt")
    with txt_path.open("w") as f:
        f.write("# DNI Probe v2 — Reporte de detección de frentes\n\n")
        f.write("## Configuración\n")
        f.write(f"  face_confidence_threshold: {face_confidence}\n")
        f.write(f"  clahe_enabled: {enable_clahe}\n")
        f.write(f"  heic_available: {HEIC_OK}\n\n")

        f.write("## Resultado\n")
        f.write(f"  Total: {summary['n_total']}\n")
        f.write(f"  Con detección: {summary['n_with_detection']} ({summary['detection_rate']*100:.1f}%)\n")
        f.write(f"  Sin detección: {summary['n_zero_detection']}\n")
        f.write(f"  Errores: {summary['n_errors']}\n")
        f.write(f"  EXIF aplicado: {summary['n_exif_applied']}\n")
        f.write(f"  Target: ≥ {summary['target_rate']*100:.0f}%\n")
        f.write(f"  Resultado: {'PASA' if summary['passes_target'] else 'NO PASA'}\n")
        f.write(f"  Distribución: {summary['distribution']}\n")
        f.write(f"  Estrategias: {summary['strategy_distribution']}\n")
        f.write(f"  Confianza P50: {summary['avg_confidence_p50']}\n")
        f.write(f"  Confianza P90: {summary['avg_confidence_p90']}\n\n")

        f.write("## Problemáticas\n")
        if not problematic:
            f.write("  (ninguna)\n")
        else:
            for p in problematic:
                exif_note = f" [EXIF orient={p['exif_orientation']}]" if p['exif_orientation'] else ""
                f.write(f"  {p['id']}: {p['reason']}{exif_note}\n")

        if errors:
            f.write("\n## Errores\n")
            for e in errors:
                f.write(f"  {e['id']}: {e['error']}\n")

    console.print(f"\n[green]✓ Reporte JSON:[/green] {json_path}")
    console.print(f"[green]✓ Reporte TXT:[/green]  {txt_path}")

    if not include_filenames:
        console.print("[dim]IDs opacos. El reporte es compartible.[/dim]")


if __name__ == "__main__":
    app()
