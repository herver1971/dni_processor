#!/usr/bin/env python3
"""
CLI — calibrate.py

Script de calibración local para Fase 2 del DNI Processor.

EJECUTA LOCALMENTE EN TU MÁQUINA con imágenes reales. Mide métricas
agregadas del pipeline sin exponer ningún dato sensible.

Funciona en dos modos:

  1. SWEEP — Barre rangos de parámetros (Canny thresholds, tolerancia
     de aspect ratio, padding) y reporta para cada combinación cuántos
     DNIs se detectaron y cuántas imágenes quedaron sin procesar.
     Salida: tabla CSV con todas las combinaciones probadas.

  2. EVAL — Corre el pipeline completo con los parámetros actuales
     (o los pasados por flag) y produce un reporte de métricas:
     - Tasa de detección (% de imágenes con ≥1 DNI detectado)
     - Tasa de matcheo (% de pares matcheados sobre el mínimo de
       frentes/dorsos)
     - Distribución de confianzas de OCR
     - Tiempos por etapa (detección, OCR, matcheo, PDF)
     - Cantidad de huérfanos por causa

POLÍTICA DE PRIVACIDAD:
- Por defecto, NO se loguean números de DNI ni nombres de archivo originales.
  Los archivos se renombran a hashes opacos (ej: "img_a3f2b1.jpg") en la salida.
- El reporte solo contiene métricas agregadas y, opcionalmente, los hashes
  de las imágenes problemáticas para que vos puedas identificarlas localmente.
- Se puede activar `--include-filenames` si necesitás trazabilidad local
  (NO compartas ese reporte con nadie).

Uso típico:

    # 1. Barrido inicial de parámetros
    python scripts/calibrate.py sweep \\
        --frentes ./fotos/frentes \\
        --dorsos  ./fotos/dorsos \\
        --output  ./calibracion_sweep.csv

    # 2. Evaluación final con parámetros elegidos
    python scripts/calibrate.py eval \\
        --frentes ./fotos/frentes \\
        --dorsos  ./fotos/dorsos \\
        --canny-low 50 --canny-high 150 \\
        --aspect-tolerance 0.15 \\
        --output ./calibracion_report.txt
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Permitir ejecutar el script desde la raíz del proyecto sin instalar
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core import constants  # noqa: E402
from app.core.constants import ALLOWED_IMAGE_EXTENSIONS  # noqa: E402
from app.main import __version__  # noqa: E402

app = typer.Typer(
    help="Calibrador del pipeline de DNI Processor (corre localmente, no expone datos).",
    add_completion=False,
)
console = Console()


# ============================================================
# Utilidades de privacidad
# ============================================================

def _opaque_id(path: Path, include_filename: bool = False) -> str:
    """
    Devuelve un identificador opaco de la imagen para reportes.
    Por defecto: SHA1 truncado del nombre + tamaño en bytes.
    Si include_filename=True, devuelve el nombre original (uso local únicamente).
    """
    if include_filename:
        return path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    digest = hashlib.sha1(f"{path.name}:{size}".encode()).hexdigest()[:8]
    return f"img_{digest}"


def _list_images(directory: Path) -> list[Path]:
    """Lista imágenes válidas en un directorio."""
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
    )


# ============================================================
# Modo SWEEP — Barrido de parámetros
# ============================================================

@dataclass
class SweepResult:
    canny_low: int
    canny_high: int
    aspect_tolerance: float
    min_area_ratio: float
    images_processed: int
    images_with_detection: int
    total_dnis_detected: int
    avg_dnis_per_image: float
    elapsed_seconds: float

    @property
    def detection_rate(self) -> float:
        if self.images_processed == 0:
            return 0.0
        return self.images_with_detection / self.images_processed


def _run_detection_only(
    image_paths: list[Path],
    canny_low: int,
    canny_high: int,
    aspect_tol: float,
    min_area: float,
) -> tuple[int, int, float]:
    """
    Corre SOLO la detección de bordes con un set de parámetros dado.
    Devuelve (total_dnis, imágenes_con_deteccion, tiempo_total).

    Monkeypatcha temporalmente las constantes del módulo de visión
    para que el barrido sea no destructivo.
    """
    # Importar bajo demanda (evita carga al importar el script)
    from app.core import vision

    # Snapshot de constantes originales
    original_low = constants.CANNY_THRESHOLD_LOW
    original_high = constants.CANNY_THRESHOLD_HIGH
    original_amin = constants.DNI_ASPECT_MIN
    original_amax = constants.DNI_ASPECT_MAX
    original_min_area = constants.MIN_CONTOUR_AREA_RATIO

    try:
        # Aplicar parámetros del sweep
        constants.CANNY_THRESHOLD_LOW = canny_low
        constants.CANNY_THRESHOLD_HIGH = canny_high
        constants.DNI_ASPECT_MIN = constants.DNI_ASPECT_RATIO * (1 - aspect_tol)
        constants.DNI_ASPECT_MAX = constants.DNI_ASPECT_RATIO * (1 + aspect_tol)
        constants.MIN_CONTOUR_AREA_RATIO = min_area

        # Reimportar módulo vision para que tome las constantes nuevas
        # (las funciones leen las constantes en tiempo de ejecución, así que
        # con el monkeypatch alcanza, pero forzamos una verificación)
        total_dnis = 0
        images_with_detection = 0
        start = time.perf_counter()

        for img_path in image_paths:
            try:
                image = vision.load_image(img_path)
                bboxes = vision.detect_dni_bboxes(image)
                if bboxes:
                    images_with_detection += 1
                    total_dnis += len(bboxes)
            except (FileNotFoundError, ValueError, IOError):
                continue

        elapsed = time.perf_counter() - start
        return total_dnis, images_with_detection, elapsed

    finally:
        # Restaurar constantes
        constants.CANNY_THRESHOLD_LOW = original_low
        constants.CANNY_THRESHOLD_HIGH = original_high
        constants.DNI_ASPECT_MIN = original_amin
        constants.DNI_ASPECT_MAX = original_amax
        constants.MIN_CONTOUR_AREA_RATIO = original_min_area


@app.command()
def sweep(
    frentes: Path = typer.Option(..., "--frentes", "-f", exists=True, file_okay=False),
    dorsos: Path = typer.Option(..., "--dorsos", "-d", exists=True, file_okay=False),
    output: Path = typer.Option(..., "--output", "-o", help="CSV con resultados"),
    canny_low_values: str = typer.Option(
        "30,50,75,100",
        "--canny-low-values",
        help="Lista de valores a probar para Canny low threshold (separados por coma)",
    ),
    canny_high_values: str = typer.Option(
        "100,150,200,250",
        "--canny-high-values",
        help="Lista de valores a probar para Canny high threshold",
    ),
    aspect_tolerance_values: str = typer.Option(
        "0.10,0.15,0.20,0.25",
        "--aspect-tolerance-values",
        help="Lista de tolerancias de aspect ratio a probar",
    ),
    min_area_values: str = typer.Option(
        "0.005,0.01,0.02",
        "--min-area-values",
        help="Lista de ratios de área mínima a probar",
    ),
) -> None:
    """
    Barre combinaciones de parámetros de detección y reporta métricas
    agregadas. NO usa OCR — solo mide la fase de visión, que es la
    más sensible a calibración.
    """
    console.print(f"[bold cyan]DNI Processor v{__version__} — Sweep[/bold cyan]")

    # Parsear listas de valores
    def _parse_int_list(s: str) -> list[int]:
        return [int(x.strip()) for x in s.split(",") if x.strip()]

    def _parse_float_list(s: str) -> list[float]:
        return [float(x.strip()) for x in s.split(",") if x.strip()]

    canny_lows = _parse_int_list(canny_low_values)
    canny_highs = _parse_int_list(canny_high_values)
    aspect_tols = _parse_float_list(aspect_tolerance_values)
    min_areas = _parse_float_list(min_area_values)

    images = _list_images(frentes) + _list_images(dorsos)
    if not images:
        console.print("[red]✗ No se encontraron imágenes en las carpetas indicadas[/red]")
        raise typer.Exit(code=1)

    # Filtrar combinaciones inválidas (high <= low)
    combos = [
        (cl, ch, at, ma)
        for cl in canny_lows
        for ch in canny_highs
        for at in aspect_tols
        for ma in min_areas
        if ch > cl
    ]

    console.print(
        f"[cyan]Imágenes a procesar:[/cyan] {len(images)}  "
        f"[cyan]Combinaciones:[/cyan] {len(combos)}  "
        f"[cyan]Total runs:[/cyan] {len(combos) * len(images)}"
    )
    console.print()

    results: list[SweepResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[bold]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Procesando combinaciones...", total=len(combos))

        for cl, ch, at, ma in combos:
            total_dnis, detected_imgs, elapsed = _run_detection_only(
                images, cl, ch, at, ma
            )
            result = SweepResult(
                canny_low=cl,
                canny_high=ch,
                aspect_tolerance=at,
                min_area_ratio=ma,
                images_processed=len(images),
                images_with_detection=detected_imgs,
                total_dnis_detected=total_dnis,
                avg_dnis_per_image=total_dnis / len(images) if images else 0,
                elapsed_seconds=elapsed,
            )
            results.append(result)
            progress.advance(task)

    # Guardar CSV
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "canny_low", "canny_high", "aspect_tolerance", "min_area_ratio",
            "images_processed", "images_with_detection", "detection_rate",
            "total_dnis_detected", "avg_dnis_per_image", "elapsed_seconds",
        ])
        for r in results:
            writer.writerow([
                r.canny_low, r.canny_high, r.aspect_tolerance, r.min_area_ratio,
                r.images_processed, r.images_with_detection,
                f"{r.detection_rate:.4f}",
                r.total_dnis_detected, f"{r.avg_dnis_per_image:.4f}",
                f"{r.elapsed_seconds:.3f}",
            ])

    # Top 10 por tasa de detección
    top = sorted(results, key=lambda r: (-r.detection_rate, r.elapsed_seconds))[:10]
    table = Table(title="🏆 Top 10 combinaciones (mejor tasa de detección)")
    table.add_column("Canny Low", justify="right")
    table.add_column("Canny High", justify="right")
    table.add_column("Aspect Tol", justify="right")
    table.add_column("Min Area", justify="right")
    table.add_column("Detección", justify="right", style="bold green")
    table.add_column("DNIs/img", justify="right")
    table.add_column("Tiempo (s)", justify="right")

    for r in top:
        table.add_row(
            str(r.canny_low),
            str(r.canny_high),
            f"{r.aspect_tolerance:.2f}",
            f"{r.min_area_ratio:.3f}",
            f"{r.detection_rate * 100:.1f}%",
            f"{r.avg_dnis_per_image:.2f}",
            f"{r.elapsed_seconds:.2f}",
        )

    console.print()
    console.print(table)
    console.print(f"\n[green]✓ Reporte completo guardado en:[/green] {output}")
    console.print("[dim]El CSV contiene solo métricas agregadas. No incluye nombres de archivo ni números de DNI.[/dim]")


# ============================================================
# Modo EVAL — Evaluación completa del pipeline
# ============================================================

@dataclass
class StageTiming:
    detection: list[float] = field(default_factory=list)
    ocr: list[float] = field(default_factory=list)
    matching: float = 0.0
    pdf_generation: float = 0.0


@dataclass
class EvalReport:
    version: str
    parameters: dict
    n_frente_images: int
    n_dorso_images: int
    n_frentes_detected: int
    n_dorsos_detected: int
    n_frentes_with_ocr: int
    n_dorsos_with_ocr: int
    detection_rate_frentes: float
    detection_rate_dorsos: float
    ocr_rate_frentes: float
    ocr_rate_dorsos: float
    n_pairs: int
    n_orphan_frentes: int
    n_orphan_dorsos: int
    match_rate: float
    ocr_confidence_p50: float
    ocr_confidence_p90: float
    timings: dict
    problematic_images: list[dict]


def _percentile(values: list[float], pct: float) -> float:
    """Percentil simple sin numpy para mantener la salida portable."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


@app.command()
def eval(
    frentes: Path = typer.Option(..., "--frentes", "-f", exists=True, file_okay=False),
    dorsos: Path = typer.Option(..., "--dorsos", "-d", exists=True, file_okay=False),
    output: Path = typer.Option(..., "--output", "-o", help="Reporte (txt + json)"),
    canny_low: int = typer.Option(constants.CANNY_THRESHOLD_LOW, "--canny-low"),
    canny_high: int = typer.Option(constants.CANNY_THRESHOLD_HIGH, "--canny-high"),
    aspect_tolerance: float = typer.Option(
        constants.DNI_ASPECT_TOLERANCE, "--aspect-tolerance"
    ),
    min_area_ratio: float = typer.Option(
        constants.MIN_CONTOUR_AREA_RATIO, "--min-area"
    ),
    match_max_distance: int = typer.Option(
        constants.MATCH_MAX_DISTANCE, "--match-max-distance"
    ),
    include_filenames: bool = typer.Option(
        False,
        "--include-filenames",
        help="Incluir nombres reales en el reporte (USO LOCAL ÚNICAMENTE — no compartir)",
    ),
    skip_pdf: bool = typer.Option(
        False,
        "--skip-pdf",
        help="No generar el PDF final (solo medir métricas)",
    ),
) -> None:
    """
    Ejecuta el pipeline completo con los parámetros indicados y
    produce un reporte con métricas agregadas para compartir.
    """
    console.print(f"[bold cyan]DNI Processor v{__version__} — Evaluación[/bold cyan]")

    # Aplicar parámetros (monkeypatch sobre el módulo constants)
    constants.CANNY_THRESHOLD_LOW = canny_low
    constants.CANNY_THRESHOLD_HIGH = canny_high
    constants.DNI_ASPECT_MIN = constants.DNI_ASPECT_RATIO * (1 - aspect_tolerance)
    constants.DNI_ASPECT_MAX = constants.DNI_ASPECT_RATIO * (1 + aspect_tolerance)
    constants.MIN_CONTOUR_AREA_RATIO = min_area_ratio
    constants.MATCH_MAX_DISTANCE = match_max_distance

    # Importar módulos del pipeline DESPUÉS del monkeypatch
    from app.core import vision, ocr, matcher, composer
    from app.schemas.session import DNISide

    frente_images = _list_images(frentes)
    dorso_images = _list_images(dorsos)

    if not frente_images or not dorso_images:
        console.print("[red]✗ Carpetas vacías o sin imágenes válidas[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[cyan]Frentes:[/cyan] {len(frente_images)}  "
        f"[cyan]Dorsos:[/cyan] {len(dorso_images)}"
    )
    console.print()

    timings = StageTiming()
    crops_dir = Path("/tmp/dni_calibration_crops")
    crops_dir.mkdir(parents=True, exist_ok=True)
    problematic: list[dict] = []

    # ----- Detección + OCR por lado -----
    def _process_side(images: list[Path], side: DNISide, label: str):
        detected = []
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{label}[/cyan]: {{task.description}}"),
            TextColumn("[bold]{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Procesando...", total=len(images))
            for img_path in images:
                # Detección
                t0 = time.perf_counter()
                try:
                    image = vision.load_image(img_path)
                    bboxes = vision.detect_dni_bboxes(image)
                except Exception as e:
                    problematic.append({
                        "id": _opaque_id(img_path, include_filenames),
                        "stage": "detection",
                        "error": str(e),
                    })
                    progress.advance(task)
                    continue
                timings.detection.append(time.perf_counter() - t0)

                if not bboxes:
                    problematic.append({
                        "id": _opaque_id(img_path, include_filenames),
                        "stage": "detection",
                        "error": "no_dni_detected",
                    })
                    progress.advance(task)
                    continue

                # Para cada bbox, recortar y correr OCR
                for bbox in bboxes:
                    crop = vision.crop_with_padding(image, bbox)
                    crop_path = crops_dir / f"{_opaque_id(img_path, False)}_{len(detected)}.jpg"
                    vision.save_crop(crop, crop_path)

                    t1 = time.perf_counter()
                    number, conf = ocr.extract_dni_number(crop_path)
                    timings.ocr.append(time.perf_counter() - t1)

                    detected.append({
                        "source_id": _opaque_id(img_path, include_filenames),
                        "crop_path": crop_path,
                        "bbox": bbox,
                        "side": side,
                        "dni_number": number,
                        "ocr_confidence": conf,
                    })

                    if number is None:
                        problematic.append({
                            "id": _opaque_id(img_path, include_filenames),
                            "stage": "ocr",
                            "error": "no_number_extracted",
                        })

                progress.advance(task)
        return detected

    frentes_detected = _process_side(frente_images, DNISide.FRENTE, "Frentes")
    dorsos_detected = _process_side(dorso_images, DNISide.DORSO, "Dorsos")

    # ----- Matcheo -----
    t0 = time.perf_counter()
    # Reconstruimos DetectedDNI para el matcher
    from app.schemas.session import BoundingBox, DetectedDNI
    import uuid as _uuid

    def _to_detected(d) -> DetectedDNI:
        return DetectedDNI(
            crop_id=str(_uuid.uuid4()),
            source_image=Path(d["source_id"]),  # opaque
            bbox=d["bbox"],
            crop_path=d["crop_path"],
            side=d["side"],
            dni_number=d["dni_number"],
            ocr_confidence=d["ocr_confidence"],
        )

    pairs, orphan_f, orphan_d = matcher.match_frentes_dorsos(
        [_to_detected(d) for d in frentes_detected],
        [_to_detected(d) for d in dorsos_detected],
    )
    timings.matching = time.perf_counter() - t0

    # ----- PDF (opcional) -----
    if not skip_pdf:
        t0 = time.perf_counter()
        pdf_path = output.parent / f"{output.stem}_preview.pdf"
        composer.compose_pdf(pairs, orphan_f, orphan_d, pdf_path)
        timings.pdf_generation = time.perf_counter() - t0
        console.print(f"[dim]PDF de preview generado: {pdf_path}[/dim]")

    # ----- Métricas -----
    confidences = [d["ocr_confidence"] for d in frentes_detected + dorsos_detected if d["ocr_confidence"] > 0]
    frentes_with_ocr = sum(1 for d in frentes_detected if d["dni_number"] is not None)
    dorsos_with_ocr = sum(1 for d in dorsos_detected if d["dni_number"] is not None)
    min_side = min(len(frentes_detected), len(dorsos_detected))

    report = EvalReport(
        version=__version__,
        parameters={
            "canny_low": canny_low,
            "canny_high": canny_high,
            "aspect_tolerance": aspect_tolerance,
            "min_area_ratio": min_area_ratio,
            "match_max_distance": match_max_distance,
        },
        n_frente_images=len(frente_images),
        n_dorso_images=len(dorso_images),
        n_frentes_detected=len(frentes_detected),
        n_dorsos_detected=len(dorsos_detected),
        n_frentes_with_ocr=frentes_with_ocr,
        n_dorsos_with_ocr=dorsos_with_ocr,
        detection_rate_frentes=len(frentes_detected) / len(frente_images),
        detection_rate_dorsos=len(dorsos_detected) / len(dorso_images),
        ocr_rate_frentes=frentes_with_ocr / max(1, len(frentes_detected)),
        ocr_rate_dorsos=dorsos_with_ocr / max(1, len(dorsos_detected)),
        n_pairs=len(pairs),
        n_orphan_frentes=len(orphan_f),
        n_orphan_dorsos=len(orphan_d),
        match_rate=len(pairs) / min_side if min_side else 0,
        ocr_confidence_p50=_percentile(confidences, 0.5),
        ocr_confidence_p90=_percentile(confidences, 0.9),
        timings={
            "detection_total": sum(timings.detection),
            "detection_avg_per_image": statistics.mean(timings.detection) if timings.detection else 0,
            "ocr_total": sum(timings.ocr),
            "ocr_avg_per_crop": statistics.mean(timings.ocr) if timings.ocr else 0,
            "matching": timings.matching,
            "pdf_generation": timings.pdf_generation,
            "pipeline_total": (
                sum(timings.detection) + sum(timings.ocr)
                + timings.matching + timings.pdf_generation
            ),
        },
        problematic_images=problematic,
    )

    # ----- Salida en consola -----
    table = Table(title="📊 Reporte de Evaluación")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="bold")

    table.add_row("Versión", report.version)
    table.add_row("Imágenes frentes", str(report.n_frente_images))
    table.add_row("Imágenes dorsos", str(report.n_dorso_images))
    table.add_row(
        "Detección frentes",
        f"{report.n_frentes_detected} ({report.detection_rate_frentes * 100:.1f}%)",
    )
    table.add_row(
        "Detección dorsos",
        f"{report.n_dorsos_detected} ({report.detection_rate_dorsos * 100:.1f}%)",
    )
    table.add_row(
        "OCR frentes",
        f"{report.n_frentes_with_ocr} ({report.ocr_rate_frentes * 100:.1f}%)",
    )
    table.add_row(
        "OCR dorsos",
        f"{report.n_dorsos_with_ocr} ({report.ocr_rate_dorsos * 100:.1f}%)",
    )
    table.add_row("Pares matcheados", str(report.n_pairs))
    table.add_row("Huérfanos frentes", str(report.n_orphan_frentes))
    table.add_row("Huérfanos dorsos", str(report.n_orphan_dorsos))
    table.add_row("Tasa de matcheo", f"{report.match_rate * 100:.1f}%")
    table.add_row("Confianza OCR (P50)", f"{report.ocr_confidence_p50:.3f}")
    table.add_row("Confianza OCR (P90)", f"{report.ocr_confidence_p90:.3f}")
    table.add_row(
        "Tiempo total pipeline",
        f"{report.timings['pipeline_total']:.2f}s",
    )
    table.add_row(
        "Tiempo medio detección/img",
        f"{report.timings['detection_avg_per_image']:.3f}s",
    )
    table.add_row(
        "Tiempo medio OCR/crop",
        f"{report.timings['ocr_avg_per_crop']:.3f}s",
    )

    console.print()
    console.print(table)

    # ----- Salida en archivo -----
    output.parent.mkdir(parents=True, exist_ok=True)

    # Reporte JSON (estructurado, para compartir)
    json_path = output.with_suffix(".json")
    with json_path.open("w") as f:
        # Convertir a dict serializable
        report_dict = {
            "version": report.version,
            "parameters": report.parameters,
            "input": {
                "n_frente_images": report.n_frente_images,
                "n_dorso_images": report.n_dorso_images,
            },
            "detection": {
                "n_frentes_detected": report.n_frentes_detected,
                "n_dorsos_detected": report.n_dorsos_detected,
                "rate_frentes": round(report.detection_rate_frentes, 4),
                "rate_dorsos": round(report.detection_rate_dorsos, 4),
            },
            "ocr": {
                "n_frentes_with_number": report.n_frentes_with_ocr,
                "n_dorsos_with_number": report.n_dorsos_with_ocr,
                "rate_frentes": round(report.ocr_rate_frentes, 4),
                "rate_dorsos": round(report.ocr_rate_dorsos, 4),
                "confidence_p50": round(report.ocr_confidence_p50, 4),
                "confidence_p90": round(report.ocr_confidence_p90, 4),
            },
            "matching": {
                "n_pairs": report.n_pairs,
                "n_orphan_frentes": report.n_orphan_frentes,
                "n_orphan_dorsos": report.n_orphan_dorsos,
                "rate": round(report.match_rate, 4),
            },
            "timings_seconds": {k: round(v, 4) for k, v in report.timings.items()},
            "problematic_images": report.problematic_images,
        }
        json.dump(report_dict, f, indent=2)

    # Reporte TXT (legible para humanos)
    txt_path = output.with_suffix(".txt")
    with txt_path.open("w") as f:
        f.write(f"# DNI Processor — Reporte de Evaluación\n")
        f.write(f"Versión: {report.version}\n")
        f.write(f"Fecha local: (generado por el script)\n\n")

        f.write("## Parámetros aplicados\n")
        for k, v in report.parameters.items():
            f.write(f"  {k}: {v}\n")
        f.write("\n")

        f.write("## Input\n")
        f.write(f"  Imágenes frentes: {report.n_frente_images}\n")
        f.write(f"  Imágenes dorsos:  {report.n_dorso_images}\n\n")

        f.write("## Detección\n")
        f.write(f"  Frentes detectados: {report.n_frentes_detected} "
                f"({report.detection_rate_frentes * 100:.1f}%)\n")
        f.write(f"  Dorsos detectados:  {report.n_dorsos_detected} "
                f"({report.detection_rate_dorsos * 100:.1f}%)\n\n")

        f.write("## OCR\n")
        f.write(f"  Frentes con número leído: {report.n_frentes_with_ocr} "
                f"({report.ocr_rate_frentes * 100:.1f}%)\n")
        f.write(f"  Dorsos con número leído:  {report.n_dorsos_with_ocr} "
                f"({report.ocr_rate_dorsos * 100:.1f}%)\n")
        f.write(f"  Confianza P50: {report.ocr_confidence_p50:.3f}\n")
        f.write(f"  Confianza P90: {report.ocr_confidence_p90:.3f}\n\n")

        f.write("## Matcheo\n")
        f.write(f"  Pares: {report.n_pairs}\n")
        f.write(f"  Huérfanos frentes: {report.n_orphan_frentes}\n")
        f.write(f"  Huérfanos dorsos:  {report.n_orphan_dorsos}\n")
        f.write(f"  Tasa de matcheo: {report.match_rate * 100:.1f}%\n\n")

        f.write("## Tiempos (segundos)\n")
        for k, v in report.timings.items():
            f.write(f"  {k}: {v:.3f}\n")
        f.write("\n")

        f.write("## Imágenes problemáticas\n")
        if not report.problematic_images:
            f.write("  (ninguna)\n")
        else:
            for p in report.problematic_images:
                f.write(f"  [{p['stage']}] {p['id']}: {p['error']}\n")

    console.print(f"\n[green]✓ Reporte JSON:[/green] {json_path}")
    console.print(f"[green]✓ Reporte TXT:[/green]  {txt_path}")

    if not include_filenames:
        console.print(
            "[dim]Los reportes usan IDs opacos. Podés compartirlos sin exponer datos.[/dim]"
        )
    else:
        console.print(
            "[yellow]⚠ --include-filenames activado. NO compartas estos reportes.[/yellow]"
        )

    # Limpieza de crops
    import shutil
    shutil.rmtree(crops_dir, ignore_errors=True)


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    app()
