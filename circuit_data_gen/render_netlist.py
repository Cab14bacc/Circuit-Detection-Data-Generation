"""Render a lcapy netlist via convert.py + our auto-generated Circuitikz skin.

All netlist cleaning (comments, directives, continuation lines, probes,
coupling statements, subcircuit bodies) lives in convert.py.
"""

import json
import subprocess
from pathlib import Path
from .convert import to_yosys_json
from .configs.config import get_config_path_value, get_logger

logger = get_logger(__name__)


def load_netlist(netlist_path):
    raw_bytes = Path(netlist_path).read_bytes()

    try:
        return raw_bytes.decode(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        logger.warning(f"Failed to decode {netlist_path} as UTF-8, trying cp1252...")

    try:
        return raw_bytes.decode(encoding="cp1252", errors="strict")
    except UnicodeDecodeError:
        logger.warning(
            f"Failed to decode {netlist_path} as UTF-8 and cp1252, decoding as UTF-8 with replace..."
        )

    # Absolute fallback if all else fails
    return raw_bytes.decode(encoding="utf-8", errors="replace")


def render_netlist(
    netlist_path,
    skin_path,
    out_file,
    annotation_path: Path | None = None,
    module_name="circuit",
    scale: float = 5.0,
    format: str | None = None,
    debug_overlay: bool = False,
    debug_overlay_path: Path | None = None,
) -> tuple[Path, dict]:
    """Render a netlist via convert.py + netlistsvg.

    Parameters
    ----------
    out_file : Path
        Output image. Exactly one image is written. Format: --format wins;
        otherwise inferred from the extension (.svg | .png | .jpg). When
        --format overrides the extension, the file is re-extended to match.
    annotation_path : Path | None
        If given, a structured annotation JSON (components with nested
        labels and exact pin points) is written here. Annotations are in
        OUTPUT coordinates: SVG user units for svg, pixels for png/jpg
        (pixels = SVG units * scale). *Norm fields are scale-independent.
    scale : float
        Raster scale for png/jpg output: pixels = SVG units * scale.
    format : 'svg' | 'png' | 'jpg' | None
        Explicit output format; overrides the out_file extension.
    debug_overlay : bool
        When True and a raster format is used, additionally writes an
        overlay PNG with the annotation boxes drawn on the render (visual
        verification aid). Requires --format png/jpg.
    debug_overlay_path : Path | None
        Where to write the overlay PNG. When None, netlistsvg writes it
        next to the output as <stem>.debug.png.
    """
    netlistsvg_bin = get_config_path_value("netlistsvg", "bin_path")

    if netlistsvg_bin is None:
        raise ValueError("bin_path in netlistsvg config not found.")

    if format is not None and format not in ("svg", "png", "jpg"):
        raise ValueError(f"Unsupported format {format!r}; use 'svg', 'png' or 'jpg'.")

    logger.debug(f"loading netlist: {netlist_path}")
    netlist_text = load_netlist(netlist_path)
    out_file = Path(out_file)
    # when format overrides the extension, re-extend so the file is named
    # after its actual format
    if format is not None:
        out_file = out_file.with_suffix(f".{format}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_file.with_suffix(".json")

    yosys, parsed_netlist = to_yosys_json(netlist_text, module_name)

    logger.debug(f"writing yosys json to: {json_path}")
    json_path.write_text(json.dumps(yosys, indent=2))
    logger.debug(f"rendering netlist to: {out_file}")
    cmd = [
        "node",
        str(netlistsvg_bin),
        str(json_path),
        "--skin",
        str(skin_path),
        "-o",
        str(out_file),
    ]
    if annotation_path is not None:
        annotation_path = Path(annotation_path)
        annotation_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--annotation", str(annotation_path)]
        # the dataset's class registry is embedded in the annotation JSON and
        # validated against the rendered components
        classes_path = get_config_path_value("netlistsvg", "annotation.classes_path")
        if not classes_path.exists():
            raise FileNotFoundError(
                f"Class registry not found at {classes_path}; run "
                f"'cirdg build_skin --all --write-classes' to generate it"
                f"to default skin path defined in config"
            )
        cmd += ["--classes", str(classes_path)]
    if scale != 1.0:
        cmd += ["--scale", str(scale)]
    if debug_overlay:
        if debug_overlay_path is not None:
            cmd += ["--debug-overlay", str(debug_overlay_path)]
        else:
            cmd += ["--debug-overlay"]
    subprocess.run(cmd, check=True)
    return json_path, parsed_netlist


if __name__ == "__main__":
    # simple test of render_netlist function
    # take in netlist raw, and put in temp file, then render to temp svg

    import tempfile
    import sys

    # take in netlist raw:
    netlist_text = sys.stdin.read()

    with tempfile.TemporaryDirectory() as tmp_dir:
        netlist_path = Path(tmp_dir) / "netlist.txt"
        netlist_path.write_text(netlist_text)
        skin_path = Path(tmp_dir) / "skin"
        out_svg = Path(tmp_dir) / "output.svg"
        render_netlist(netlist_path, skin_path, out_svg)
