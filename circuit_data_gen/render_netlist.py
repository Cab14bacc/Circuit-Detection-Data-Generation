"""Render a lcapy netlist via convert.py + our auto-generated Circuitikz skin.

Pre-processes the .sch to drop probe lines (P*) which are lcapy
voltage-marker annotations, not components.
"""
import json
import subprocess
import tomllib
import sys
from pathlib import Path
from .convert import to_yosys_json
from .helpers import get_config_path_value


def load_netlist(netlist_path):
    """Read .sch, drop option lines (leading ;) and probe lines (P*)."""
    lines = Path(netlist_path).read_text().splitlines()
    kept = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith(';') or s.startswith('#'):
            continue
        first_tok = s.split()[0] if s.split() else ''
        # EXPLICITLY drop probes (P*) — they are lcapy voltage measurement
        # annotations (e.g. "P1 A B; down, v=V_mid"), NOT circuit components.
        # We drop them rather than pass through to convert.py for three reasons:
        #   1. convert.py's generic-cell fallback emits them as "generic"
        #      cells with pin names A/B, which don't match our generic skin
        #      entry (in0..out3), so they render as disconnected boxes.
        #   2. convert.py's wire-merging logic would union the probe's two
        #      endpoints, electrically shorting nodes that shouldn't be
        #      shorted (probes measure voltage without connecting nodes).
        #   3. Even if both above were fixed, rendering boxes for voltage
        #      markers adds visual noise to the schematic with no value.
        # The probe label info (v=V_mid) is lost; we accept that tradeoff.
        # If we later need probe labels, we can render them as text labels
        # next to the relevant net instead.
        if first_tok.startswith('P') and first_tok[1:].isdigit():
            continue
        kept.append(raw)
    return '\n'.join(kept)


def render_netlist(netlist_path, skin_path, out_svg, module_name='circuit'):
    netlistsvg_bin = get_config_path_value("netlistsvg", "bin_path")

    netlist_text = load_netlist(netlist_path)
    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_svg.with_suffix('.json')
    yosys = to_yosys_json(netlist_text, module_name)
    json_path.write_text(json.dumps(yosys, indent=2))
    subprocess.run(
        ['node', netlistsvg_bin, str(json_path), '--skin', skin_path, '-o', str(out_svg)],
        check=True,
    )
    return json_path


if __name__ == '__main__':
    config_path = Path(__file__).parent / "configs" / "config.toml"

    with open(config_path, 'rb') as f:
        config = tomllib.load(f)
    
    SKIN_PATH = config["netlistsvg"]["skin_path"]
    NETLISTSVG_BIN = config["netlistsvg"]["bin_path"]


    if len(sys.argv) < 3:
        print('Usage: render_netlist.py <input_netlist> <output.svg>', file=sys.stderr)
        sys.exit(1)
    jp = render_netlist(sys.argv[1], sys.argv[2])
    print(f'Wrote {sys.argv[2]} (json: {jp})')
