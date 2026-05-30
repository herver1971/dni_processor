#!/usr/bin/env python3
"""
CLI — process_batch.py

Punto de entrada de línea de comandos para Fase 1 del DNI Processor.
Procesa dos carpetas (frentes y dorsos), genera el PDF organizado y
reporta estadísticas.

Uso:
    python scripts/process_batch.py \\
        --frentes /path/to/frentes \\
        --dorsos /path/to/dorsos \\
        --output /path/to/output.pdf

Opciones adicionales:
    --work-dir DIR     Directorio para recortes intermedios (default: tmp UUID)
    --verbose          Logging detallado (DEBUG)
    --quiet            Solo errores

Códigos de salida:
    0   Procesamiento exitoso
    1   Error de validación (carpetas faltantes, sin imágenes, etc.)
    2   Error inesperado durante el procesamiento
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

# Permitir ejecutar el script desde la raíz del proyecto sin instalar
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.pipeline import process_batch  # noqa: E402
from app.main import __version__  # noqa: E402

app = typer.Typer(
    help="DNI Processor — Organiza fotos de DNIs en un PDF A4 listo para imprimir.",
    add_completion=False,
)
console = Console()


def _setup_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _print_summary(result) -> None:
    """Imprime un resumen visual del resultado."""
    table = Table(title="📋 Resumen del Procesamiento", show_header=False)
    table.add_column("Métrica", style="cyan", no_wrap=True)
    table.add_column("Valor", style="bold")

    table.add_row("Sesión", result.session_id)
    table.add_row("Pares matcheados", str(result.total_pairs))
    table.add_row("Frentes huérfanos", str(len(result.unpaired_frentes)))
    table.add_row("Dorsos huérfanos", str(len(result.unpaired_dorsos)))
    table.add_row("Imágenes no procesadas", str(result.total_unprocessed))
    table.add_row("PDF generado", str(result.output_pdf_path))

    console.print(table)

    # Detalles de huérfanos y no procesadas (si hay)
    if result.unpaired_frentes or result.unpaired_dorsos:
        console.print("\n[yellow]⚠ Huérfanos detectados — requieren matcheo manual:[/yellow]")
        for orphan in result.unpaired_frentes:
            console.print(
                f"  • Frente de {orphan.detected.source_image.name} — {orphan.reason}"
            )
        for orphan in result.unpaired_dorsos:
            console.print(
                f"  • Dorso de {orphan.detected.source_image.name} — {orphan.reason}"
            )

    if result.unprocessed_images:
        console.print("\n[yellow]⚠ Imágenes sin DNI detectado:[/yellow]")
        for unproc in result.unprocessed_images:
            console.print(f"  • {unproc.source_image.name} — {unproc.reason}")


@app.command()
def main(
    frentes: Path = typer.Option(
        ...,
        "--frentes", "-f",
        help="Carpeta con fotos de FRENTES de DNI",
        exists=True, file_okay=False, dir_okay=True, readable=True,
    ),
    dorsos: Path = typer.Option(
        ...,
        "--dorsos", "-d",
        help="Carpeta con fotos de DORSOS de DNI",
        exists=True, file_okay=False, dir_okay=True, readable=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output", "-o",
        help="Ruta del PDF de salida",
    ),
    work_dir: Path | None = typer.Option(
        None,
        "--work-dir", "-w",
        help="Directorio para recortes intermedios (default: /tmp/dni_processor/<uuid>)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Logging detallado"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Solo errores"),
) -> None:
    """Procesa un batch de imágenes de DNI y genera un PDF organizado."""
    _setup_logging(verbose=verbose, quiet=quiet)
    console.print(f"[bold cyan]DNI Processor v{__version__}[/bold cyan]\n")

    try:
        result = process_batch(
            frentes_dir=frentes,
            dorsos_dir=dorsos,
            output_pdf=output,
            work_dir=work_dir,
        )
    except ValueError as e:
        console.print(f"[red]✗ Error de validación:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]✗ Error inesperado:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=2)

    console.print()
    _print_summary(result)
    console.print(f"\n[green]✓ Procesamiento completo[/green]")


if __name__ == "__main__":
    app()
