#!/usr/bin/env python3
"""
detect_frentes.py — Script CLI de Sprint 1 (v0.2.1).

OBJETIVO: validar el nuevo backend de detección facial PRODUCIENDO LOS
RECORTES REALES para que el usuario pueda inspeccionarlos visualmente
en su carpeta y confirmar que el cálculo geométrico está bien calibrado.

Este script NO genera PDFs ni hace matcheo — eso quedó para Sprint 2-3
con la UI completa. Su único trabajo es:

1. Tomar una carpeta de imágenes de frentes
2. Correr el detector facial con todas las mejoras (EXIF, CLAHE,
   fallback de rotación)
3. Calcular el bbox de cada DNI a partir de la cara
4. Guardar los recortes en una carpeta de salida
5. Producir un reporte de qué estrategia rescató cada detección

POLÍTICA DE PRIVACIDAD: este script CORRE LOCALMENTE. Los recortes
contienen los DNIs reales — NO los compartas. Solo el reporte JSON
es compartible (usa IDs opacos).

Uso:
    python scripts/detect_frentes.py \\
        --frentes ~/dni_calibracion/frentes \\
        --output  ./crops_inspeccion \\
        --report  ./detect_frentes_report.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.vision import extract_frentes_from_image, get_face_net  # noqa: E402
from app.main import __version__  # noqa: E402


app = typer.Typer(
    help="Detección de frentes con inspección visual (Sprint 1, v0.2.1)",
    add_completion=False,
)
console = Console()


def _opaque_id(path: Path, include_filenames: bool) -> str:
    if include_filenames:
        return path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"img_{hashlib.sha1(f'{path.name}:{size}'.encode()).hexdigest()[:8]}"


@app.command()
def main(
    frentes: Path = typer.Option(
        ..., "--frentes", "-f",
        exists=True, file_okay=False, dir_okay=True, readable=True,
        help="Carpeta con imágenes de frentes",
    ),
    output: Path = typer.Option(
        ..., "--output", "-o",
        help="Carpeta donde guardar los recortes para inspección visual",
    ),
    report: Path = typer.Option(
        Path("./detect_frentes_report.json"),
        "--report", "-r",
        help="Path del reporte JSON (compartible, con IDs opacos)",
    ),
    include_filenames: bool = typer.Option(
        False, "--include-filenames",
        help="Incluir nombres reales en el reporte (USO LOCAL — no compartir)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """
    Detecta DNIs en una carpeta de frentes y produce los recortes.

    Después de correr, revisar visualmente la carpeta de OUTPUT:
    - ¿Los recortes incluyen el DNI completo?
    - ¿El padding perimetral se ve generoso (preserva integridad)?
    - ¿Hay recortes con DNIs cortados o sobrados?

    Si los recortes están mal calibrados, ajustar los ratios en
    `app/core/constants.py` (DNI_EXTEND_*_RATIO) y re-ejecutar.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False)],
    )

    console.print(
        f"[bold cyan]DNI Processor v{__version__} — Detección de frentes (Sprint 1)[/bold cyan]\n"
    )

    # Listar imágenes
    images = sorted(
        p for p in frentes.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
    )
    if not images:
        console.print("[red]✗ No se encontraron imágenes válidas[/red]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Imágenes a procesar:[/cyan] {len(images)}")
    output.mkdir(parents=True, exist_ok=True)

    # Pre-cargar el modelo de caras
    net = get_face_net()

    # Procesar cada imagen
    per_image_results = []
    strategy_counter: Counter[str] = Counter()
    crop_counter = 0
    no_detection_count = 0
    error_count = 0
    t_start = time.perf_counter()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[bold]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Detectando...", total=len(images))
        for img_path in images:
            t0 = time.perf_counter()
            opaque = _opaque_id(img_path, include_filenames)
            try:
                # El módulo vision usa UUIDs para los crops; los renombramos
                # con el ID opaco para que el usuario pueda mapearlos.
                temp_crops_dir = output / "_temp"
                dnis, strategy = extract_frentes_from_image(
                    img_path, temp_crops_dir, net=net,
                )

                # Renombrar los crops con un naming inspeccionable
                renamed_paths = []
                for idx, dni in enumerate(dnis):
                    new_name = f"{opaque}_face{idx + 1}.jpg"
                    new_path = output / new_name
                    dni.crop_path.rename(new_path)
                    renamed_paths.append(str(new_path.name))
                    crop_counter += 1

                strategy_counter[strategy] += 1
                if not dnis:
                    no_detection_count += 1

                per_image_results.append({
                    "id": opaque,
                    "n_faces": len(dnis),
                    "strategy": strategy,
                    "crops": renamed_paths,
                    "elapsed_seconds": round(time.perf_counter() - t0, 3),
                    "face_confidences": [
                        round(d.side_confidence, 3) for d in dnis
                    ],
                })
            except Exception as e:
                error_count += 1
                per_image_results.append({
                    "id": opaque,
                    "error": f"{type(e).__name__}: {e}",
                })
                logging.error(f"Error en {img_path.name}: {e}")
            progress.advance(task)

    # Limpiar directorio temp si quedó vacío
    temp_dir = output / "_temp"
    if temp_dir.exists():
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    elapsed = time.perf_counter() - t_start
    detection_rate = (len(images) - no_detection_count - error_count) / len(images)

    # Resumen visual
    table = Table(title="📊 Resumen — Sprint 1 (detección de frentes)")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="bold", justify="right")
    table.add_row("Imágenes procesadas", str(len(images)))
    table.add_row("Con detección", f"{len(images) - no_detection_count - error_count}")
    table.add_row("Sin detección", str(no_detection_count))
    table.add_row("Errores", str(error_count))
    table.add_row("Tasa de detección", f"{detection_rate * 100:.1f}%")
    table.add_row("Recortes generados", str(crop_counter))
    table.add_row("Tiempo total", f"{elapsed:.2f}s")
    table.add_row("Tiempo medio/img", f"{elapsed / len(images):.3f}s")
    console.print()
    console.print(table)

    console.print("\n[bold]Estrategias usadas:[/bold]")
    for strategy, count in strategy_counter.most_common():
        console.print(f"  {strategy}: {count}")

    # Guardar reporte
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w") as f:
        json.dump({
            "version": __version__,
            "sprint": 1,
            "input": {
                "n_images": len(images),
                "frentes_dir_opaque_hash": hashlib.sha1(str(frentes).encode()).hexdigest()[:8],
            },
            "summary": {
                "n_with_detection": len(images) - no_detection_count - error_count,
                "n_zero_detection": no_detection_count,
                "n_errors": error_count,
                "detection_rate": round(detection_rate, 4),
                "n_crops_generated": crop_counter,
                "strategy_distribution": dict(strategy_counter),
                "elapsed_seconds_total": round(elapsed, 3),
                "elapsed_seconds_avg_per_image": round(elapsed / len(images), 4),
            },
            "per_image": per_image_results,
        }, f, indent=2)

    console.print(f"\n[green]✓ Recortes en:[/green] {output}")
    console.print(f"[green]✓ Reporte JSON:[/green] {report}")

    if not include_filenames:
        console.print("[dim]IDs opacos. El reporte JSON es compartible.[/dim]")
    else:
        console.print("[yellow]⚠ --include-filenames activado. NO compartas el reporte.[/yellow]")

    console.print(
        "\n[bold yellow]PRÓXIMO PASO:[/bold yellow] Inspeccionar visualmente "
        f"los recortes en {output}.\n"
        "  • ¿Los DNIs aparecen completos? (no cortados, no sobre-recortados)\n"
        "  • ¿Se ve el padding perimetral con fondo original?\n"
        "  • ¿La cara está dentro del recorte en la posición esperada?\n"
        "  Si algo está sistemáticamente mal, ajustar DNI_EXTEND_*_RATIO en\n"
        "  app/core/constants.py y re-ejecutar."
    )


if __name__ == "__main__":
    app()
