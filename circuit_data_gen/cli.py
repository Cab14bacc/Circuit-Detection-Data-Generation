import sys
import typer
from pathlib import Path
from typing import Optional


from .render_netlist import render_netlist
from .build_skin import emit_full_skin, COMPONENTS
from .helpers import get_config_path_value


app = typer.Typer(help="Circuit Data Generation CLI")

@app.command("render")
def render_cmd(input_file: Path, output_file: Optional[Path] = None):

    skin_path = get_config_path_value("netlistsvg", "skin_path")

    if output_file is None:
        try:
            default_render_dir = get_config_path_value("cli.render", "default_render_dir")
            default_render_dir = default_render_dir.resolve()
        except ValueError as e:
            raise typer.BadParameter(
                "No output path given and no 'default_render_dir' under "
                f"'cli.render' in config.toml"
            )

        print("No output path specified. "
              f"Defaulting to {default_render_dir}", file=sys.stderr)

        output_file = default_render_dir / (Path(input_file).stem + ".svg")

    if input_file.is_dir():
        extensions = ["*.net", "*.sch"]
        for netlist_path in [f for ext in extensions for f in input_file.glob(ext)]:
            jp = render_netlist(netlist_path, skin_path, output_file)
            print(f'Wrote {output_file} (json: {jp})')
    else:
        jp = render_netlist(input_file, skin_path, output_file)
        print(f'Wrote {output_file} (json: {jp})')

@app.command(name="build_skin")
def build_skin_cmd(component_name: Optional[str] = None, output_path: Optional[Path] = None, all: bool = typer.Option(False, "--all", help="Build skin for all components")):
    if component_name is not None and all:
        raise typer.BadParameter("Cannot specify both a component name and --all option.")

    if component_name is None and not all:
        raise typer.BadParameter("Provide a component name or pass --all.")

    if output_path is None:
        print("No output path specified. Using default output path.", file=sys.stderr)
        output_path = get_config_path_value("build_skin", "skin_path")

    if component_name is not None:
        if component_name not in COMPONENTS:
            raise typer.BadParameter(f"Unknown component: {component_name}")
        skin_svg = emit_full_skin([component_name])
    else:
        skin_svg = emit_full_skin(list(COMPONENTS.keys()))

    with open(output_path.absolute(), 'w') as f:
        f.write(skin_svg)
        
    print("Skin build complete.")


# generate netlist
@app.command(name="gennet")
def generate_netlist(output_path: Optional[Path] = None, num_of_netlists: int = typer.Option(1, "--num", "-n", help="Number of netlists to generate")):

    pass

