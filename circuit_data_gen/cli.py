import datetime
import typer
import asyncio
from pathlib import Path
from typing import Optional


from .configs.config import get_config_path_value, get_logger

logger = get_logger(__name__)

app = typer.Typer(help="Circuit Data Generation CLI")


@app.command("render")
def render_cmd(
    input_file_or_dir: Path = typer.Argument(help="Netlist file or directory"),
    output_file_or_dir: Optional[Path] = typer.Argument(
        None,
        help="Output image path or directory. You can specify the output "
        "format from extension: .svg | .png | .jpg",
    ),
    annotation_dir: Optional[Path] = typer.Option(
        None,
        "--annotation",
        "-a",
        help="Directory for structured annotation JSON files "
        "(components with nested labels and exact pin points). "
        "Writes <stem>.json per input. Annotations are in OUTPUT coordinates: "
        "SVG units for svg output, pixels for png/jpg.",
    ),
    format: Optional[str] = typer.Option(
        None,
        "--format",
        "-f",
        help="Output format: svg | png | jpg. "
        "Overrides the output extension (the file is re-extended to match). "
        "Defaults to the extension, or svg when no output is given.",
    ),
    scale: float = typer.Option(
        1.0,
        "--scale",
        help="Raster scale for png/jpg output: "
        "pixels = SVG units * scale. Annotations follow the output space.",
    ),
    debug_dir: Optional[Path] = typer.Option(
        None,
        "--debug",
        help="Debug directory: writes annotation overlay PNGs "
        "(annotation boxes drawn on the render) here. Requires raster output "
        "(--format png/jpg or raster extension); ignored for svg.",
    ),
):
    from .render_netlist import render_netlist  # noqa: PLC0415

    try:
        skin_path = get_config_path_value("netlistsvg", "skin_path").resolve()
    except Exception:
        raise typer.BadParameter("Skin path not found in config under 'netlistsvg.skin_path'")

    if format is not None and format not in ("svg", "png", "jpg"):
        raise typer.BadParameter("--format must be 'svg', 'png' or 'jpg'.")

    # overlay needs raster pixels; silently skip for svg output
    debug_overlay = debug_dir is not None and format in ("png", "jpg")
    if debug_dir is not None:
        if not debug_overlay:
            logger.warning("--debug ignored: overlay requires png/jpg output (use --format).")
        else:
            debug_dir.mkdir(parents=True, exist_ok=True)

    if output_file_or_dir is None:
        try:
            default_render_dir = get_config_path_value("cli", "render.default_render_dir").resolve()
        except ValueError:
            raise typer.BadParameter(
                "No output path given and no 'default_render_dir' under 'cli.render' in config.py"
            )

        logger.warning(f"No output path specified. Defaulting to {default_render_dir}")

        if input_file_or_dir.is_dir():
            output_file_or_dir = default_render_dir
        else:
            output_file_or_dir = default_render_dir / (Path(input_file_or_dir).stem + ".svg")

    if annotation_dir is not None:
        annotation_dir.mkdir(parents=True, exist_ok=True)

    if input_file_or_dir.is_dir():
        if not output_file_or_dir.is_dir():
            raise typer.BadParameter("When input is a directory, output must also be a directory.")
        extensions = ["*.net", "*.sch"]
        out_ext = "." + (format or "svg")
        for netlist_path in [f for ext in extensions for f in input_file_or_dir.glob(ext)]:
            output_file_path = output_file_or_dir / (Path(netlist_path).stem + out_ext)
            annotation_path = annotation_dir / (Path(netlist_path).stem + ".json") if annotation_dir else None
            jp, _ = render_netlist(
                netlist_path,
                skin_path,
                output_file_path,
                annotation_path=annotation_path,
                scale=scale,
                format=format,
                debug_overlay=debug_overlay,
                debug_overlay_path=(
                    debug_dir / (Path(netlist_path).stem + "_overlay.png") if debug_overlay else None
                ),
            )
            logger.info(f"Wrote {output_file_path} (json: {jp})")
    else:
        annotation_path = None
        if annotation_dir is not None:
            annotation_path = (
                annotation_dir / (Path(input_file_or_dir).stem + ".json")
                if annotation_dir.is_dir() or not annotation_dir.suffix
                else annotation_dir
            )
        jp, _ = render_netlist(
            input_file_or_dir,
            skin_path,
            output_file_or_dir,
            annotation_path=annotation_path,
            scale=scale,
            format=format,
            debug_overlay=debug_overlay,
            debug_overlay_path=(
                debug_dir / (Path(input_file_or_dir).stem + "_overlay.png")
                if debug_overlay and debug_dir is not None
                else None
            ),
        )
        logger.info(f"Wrote {output_file_or_dir} (json: {jp})")


@app.command(name="build_skin")
def build_skin_cmd(
    component_name: Optional[str] = typer.Argument(None, help="Single component to emit"),
    output_path: Optional[Path] = typer.Argument(None, help="Output skin SVG path"),
    all: bool = typer.Option(False, "--all", help="Build skin for all components"),
    write_classes: bool = typer.Option(
        False, "--write-classes", help="Write class registry (list of skin types)"
    ),
):
    from .build_skin import emit_full_skin, COMPONENTS  # noqa: PLC0415

    if component_name is not None and all:
        raise typer.BadParameter("Cannot specify both a component name and --all option.")

    if component_name is None and not all:
        raise typer.BadParameter("Provide a component name or pass --all.")

    if output_path is None:
        logger.warning("No output path specified. Using default output path.")

        try:
            output_path = get_config_path_value("build_skin", "skin_path").resolve()
        except ValueError:
            raise typer.BadParameter("Output path not found in config under 'build_skin.skin_path'")

    if component_name is not None:
        if component_name not in COMPONENTS:
            raise typer.BadParameter(f"Unknown component: {component_name}")
        skin_svg = emit_full_skin([component_name], if_write_classes=write_classes)
    else:
        skin_svg = emit_full_skin(list(COMPONENTS.keys()), if_write_classes=write_classes)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(skin_svg)

    logger.info("Skin build complete.")


@app.command("convert")
def convert_cmd(
    input_file_or_dir: Path = typer.Argument(help="Netlist file or directory"),
    output_file_or_dir: Optional[Path] = typer.Argument(None, help="Output SVG path or directory"),
):
    """converts lcapy style netlists into yosys json"""
    from .convert import to_yosys_json  # noqa: PLC0415
    import json

    if output_file_or_dir is None:
        try:
            default_convert_dir = get_config_path_value("cli", "convert.default_convert_dir").resolve()
        except ValueError:
            raise typer.BadParameter(
                "No output path given and no 'default_convert_dir' under 'cli.convert' in config.py"
            )

        logger.warning(f"No output path specified. Defaulting to {default_convert_dir}")

        if input_file_or_dir.is_dir():
            output_file_or_dir = default_convert_dir
        else:
            output_file_or_dir = default_convert_dir / (Path(input_file_or_dir).stem + ".json")

    if input_file_or_dir.is_dir():
        if not output_file_or_dir.is_dir():
            raise typer.BadParameter("When input is a directory, output must also be a directory.")
        extensions = ["*.net", "*.sch"]
        for netlist_path in [f for ext in extensions for f in input_file_or_dir.glob(ext)]:
            with open(netlist_path, "r") as f:
                netlist_content = f.read()
            output_file_path = output_file_or_dir / (Path(netlist_path).stem + ".json")
            yosys_json = to_yosys_json(netlist_content)

            with open(output_file_path, "w") as f:
                json.dump(yosys_json, f, indent=2)

            logger.info(f"Wrote {output_file_path}")
    else:
        yosys_json = to_yosys_json(input_file_or_dir)
        with open(output_file_or_dir, "w") as f:
            json.dump(yosys_json, f, indent=2)
        logger.info(f"Wrote {output_file_or_dir}")


# generate netlist
@app.command(name="gen_data")
def generate_data_cmd(
    root_output_dir: Optional[Path] = typer.Option(
        None,
        "--output-root",
        "-o",
        help="Dataset root dir; first a project directory is created "
        "inside, followed by netlists/, schematics/, logs/ directories. "
        "Defaults to circuit_data_gen/../dataset/project_{timestamp}",
    ),
    project_name: Optional[str] = typer.Option(
        None,
        "--project-name",
        "-p",
        help="Name of the project (also used to compute default output dir)",
    ),
    num_of_netlists: int = typer.Option(1, "--num", "-n", help="Number of netlists to generate"),
    concurrency: int = typer.Option(4, "--concurrency", "-c", help="Max simultaneous LLM calls"),
    min_components: int = typer.Option(
        5, "--min-components", "-cmin", help="Minimum number of components in a netlist"
    ),
    max_components: int = typer.Option(
        25, "--max-components", "-cmax", help="Maximum number of components in a netlist"
    ),
    gen_seed: Optional[str] = typer.Option(
        None,
        "--gen-seed",
        help="Seed for the seed-prompt generator (reproducible batches)",
    ),
    temperature: float = typer.Option(0.9, "--temperature", "-t", help="LLM sampling temperature"),
):
    from .netlist_gen.parallel import scale_generation  # noqa: PLC0415

    if root_output_dir is None:
        root_output_dir = get_config_path_value("cli", "gen_netlist.default_projects_dir").resolve()
        logger.warning(f"No output root specified. Defaulting output root to {root_output_dir}")

    if project_name is None:
        project_name = f"project_{datetime.datetime.now().strftime('%y%m%d_%H%M%S')}"
        if_dir_exists = (root_output_dir / project_name).exists()
        while if_dir_exists:
            project_name = f"project_{datetime.datetime.now().strftime('%y%m%d_%H%M%S')}"
            if_dir_exists = (root_output_dir / project_name).exists()
        logger.warning(f"No project name specified. Defaulting to {project_name}")

    output_dir = root_output_dir / project_name

    results = asyncio.run(
        scale_generation(
            output_dir=output_dir,
            batch_size=num_of_netlists,
            concurrency=concurrency,
            num_components_range=(min_components, max_components),
            gen_seed=gen_seed,
            temperature=temperature,
            project_name=project_name,
        )
    )

    failures = [r for r in results if not r.ok]
    if failures:
        for r in failures[:5]:
            why = r.exception or r.render_error or "; ".join(r.sanity_errors[:2])
            logger.error(f"  FAILED {r.worker_id}: {why}")
        if len(failures) > 5:
            logger.error(f"  … and {len(failures) - 5} more (see logs/)")
        raise typer.Exit(code=1 if len(failures) == len(results) else 0)


if __name__ == "__main__":
    app()
