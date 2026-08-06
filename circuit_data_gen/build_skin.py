"""Generate netlistsvg skin entries from Circuitikz-rendered components.

For each component type, this script:
1. Writes a standalone .tex file with a to[CPT] component
2. Compiles with pdflatex, converts to SVG with pdftocairo
3. Parses the SVG to extract body paths, lead paths, glyph defs
4. Computes the bbox and pin positions
5. Emits a <g> skin entry
"""
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile

from .helpers import get_config_path_value

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', XLINK_NS)


# Marker colors used to locate circuitikz anchors in the PDF->SVG render.
# Matched with tolerance against pdftocairo's "rgb(...%)" output.
MARKER_RGB = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
}

# anchors list: list of the (anchor, pid, side, color) for each pin of the component.
#   anchors: defaults to west/east, need to specify if not bipole. 
#   pin_ids: defaults to ["+", "-"], list of pin ids used in yosys json
#   sides: defaults to lateral (i.e. left/right). If not lateral, it overrides the port_direction in yosys json.
#   colors: defaults to red/green/blue/magenta, assigned in this order. Specify if want to override.
# if_node: defaults to True. If False, the component is treated as a bipole (2-pin) instead of a node (multi-pin).
COMPONENTS = {
    "generic": {
        "skin_type": "generic",
        "is_generic": True,  # generic is hand-crafted below
    },

    "resistor":  {"cpt": "R", "preamble_extra": "\\ctikzset{american}\n",
                  "skin_type": "r_h", "if_node": False},
    "vsource":   {"cpt": "V", "vertical": True,
                  "preamble_extra": "\\ctikzset{american voltages, american currents}\n",
                  "skin_type": "v",      "sides": ("bottom", "top"), "if_node": False},  # + drives
    "isource":   {"cpt": "I", "vertical": True,
                  "preamble_extra": "\\ctikzset{american currents}\n",
                  "skin_type": "i",      "sides": ("bottom", "top"), "if_node": False},  # + drives
    "capacitor": {"cpt": "C", "skin_type": "c_h", "if_node": False},
    "inductor":  {"cpt": "L", "skin_type": "l_h", "if_node": False},
    "ground":    {"cpt": "ground", "skin_type": "gnd",
                  "anchors": ["north"],
                  "pin_ids": ["A"],
                  "sides": ["left"]},
    "switch":    {"cpt": "switch", "skin_type": "sw", "if_node": False},
    "diode":     {"cpt": "D", "skin_type": "d_h", "if_node": False},

    # --- 2-terminal variants ---

    "resistor_var":    {"cpt": "vR",          "skin_type": "r_var_h", "if_node": False},
    "capacitor_polar": {"cpt": "cC",          "skin_type": "c_polar_h", "if_node": False},
    "capacitor_var":   {"cpt": "vC",          "skin_type": "c_var_h", "if_node": False},
    "inductor_choke":  {"cpt": "cute choke",  "skin_type": "l_choke_h", "if_node": False},
    "inductor_var":    {"cpt": "vL",          "skin_type": "l_var_h", "if_node": False},
    "svsource": {
        "cpt": "sV", "vertical": True,
        "preamble_extra": "\\ctikzset{american voltages, american currents}\n",
        "skin_type": "sv",
        "sides": ("bottom", "top"),  # + drives
        "if_node": False
    },
    "sisource": {
        "cpt": "sI", "vertical": True,
        "preamble_extra": "\\ctikzset{american currents}\n",
        "skin_type": "si",
        "sides": ("bottom", "top"),  # + drives
        "if_node": False
    },
    "diode_led":      {"cpt": "leD",   "skin_type": "d_led_h", "if_node": False},
    "diode_schottky": {"cpt": "sD",    "skin_type": "d_sk_h", "if_node": False},
    "diode_zener":    {"cpt": "zD",    "skin_type": "d_zener_h", "if_node": False},
    "diode_photo":    {"cpt": "pD",    "skin_type": "d_photo_h", "if_node": False},
    "switch_no":   {"cpt": "nos",           "skin_type": "sw_no",   "pin_ids": ["p", "n"], "if_node": False},
    "switch_nc":   {"cpt": "ncs",           "skin_type": "sw_nc",   "pin_ids": ["p", "n"], "if_node": False},
    "switch_push": {"cpt": "push button",   "skin_type": "sw_push", "pin_ids": ["p", "n"], "if_node": False},
    "crystal":   {"cpt": "piezoelectric", "skin_type": "xtal",     "pin_ids": ["a", "b"], "if_node": False},
    "battery":   {"cpt": "battery",       "skin_type": "battery", "if_node": False},
    "ammeter":   {"cpt": "ammeter",       "skin_type": "ammeter", "if_node": False},
    "voltmeter": {"cpt": "voltmeter",     "skin_type": "voltmeter", "if_node": False},

    # --- 1-pin monopoles ---

    "antenna": {"cpt": "antenna", "skin_type": "antenna", 
                "pin_ids": ["a"],
                "anchors": ["south"], 
                "sides": ["left"]},
    "vcc":     {"cpt": "vcc",     "skin_type": "vcc",     
                "pin_ids": ["A"],
                "anchors": ["south"], 
                "sides": ["left"]},
    "vee":     {"cpt": "vee",     "skin_type": "vee",    
                "pin_ids": ["A"],
                "anchors": ["north"], 
                "sides": ["left"]},

    # --- Multi-pin node components (pins from anchors) ---

    "q_npn": {
        "cpt": "npn",
        "skin_type": "q_npn", 
        "anchors": ["B", "C", "E"],
        "pin_ids": ["b", "c", "e"],
        "sides": ["left", "right", "right"],
    },
    "q_pnp": {
        "cpt": "pnp",
        "skin_type": "q_pnp", 
        "anchors": ["B", "C", "E"],
        "pin_ids": ["b", "c", "e"],
        "sides": ["left", "right", "right"],
    },  
    "jfet_n": {
        "cpt": "njfet",
        "skin_type": "jfet_n", 
        "anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "sides": ["left", "right", "right"],
    },
    "jfet_p": {
        "cpt": "pjfet",
        "skin_type": "jfet_p", "anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "sides": ["left", "right", "right"],
    },
    "mos_n": {
        "cpt": "nmos",
        "skin_type": "mos_n", "anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "sides": ["left", "right", "right"],
    },
    "mos_p": {
        "cpt": "pmos",
        "skin_type": "mos_p", "anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "sides": ["left", "right", "right"],
    },
    "op": {
        "cpt": "op amp",
        "skin_type": "op", 
        "anchors": ["+", "-", "out"],
        "pin_ids": ["+", "-", "out"],
        "sides": ["left", "left", "right"],
    },
    "spdt": {
        "cpt": "spdt",
        "skin_type": "sw_spdt", "anchors": ["in", "out 1", "out 2"],
        "pin_ids": ["p", "n", "common"],
        "sides": ["left", "right", "right"],
    },
    # bipole with an extra anchor
    "pot": {
        "cpt": "pR", "if_node": False,  
        "skin_type": "pot", "pin_ids": ["p", "n"],
        "extra_anchors": [("wiper", "wiper", "left", "red")],
    },
}


def render_circuitikz(name, tex_content, work_dir):
    """Write .tex, compile to PDF, convert to SVG. Returns None if tex_content empty."""
    if not tex_content:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    tex_path = work_dir / f"{name}.tex"
    tex_path.write_text(tex_content)
    r = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
         "-output-directory", str(work_dir), tex_path.name],
        cwd=work_dir, capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-2000:])
        sys.stderr.write(r.stderr[-2000:])
        sys.exit(f"pdflatex failed for {name}")
    pdf_path = work_dir / f"{name}.pdf"
    svg_prefix = work_dir / f"{name}_render.svg"
    subprocess.run(
        ["pdftocairo", "-svg", str(pdf_path), str(svg_prefix)],
        check=True, capture_output=True,
    )
    return svg_prefix


def parse_matrix(s):
    """matrix(a,b,c,d,e,f) -> tuple of floats, or identity."""
    if not s:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    m = re.match(r'\s*matrix\s*\(([^)]+)\)', s)
    if m:
        return tuple(float(x) for x in m.group(1).split(','))
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def transform_point(p, m):
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def path_bbox(d, transform):
    """Approximate bbox by sampling endpoints of M/L/C/S/Q/H/V commands."""
    a, b, c, d_, e, f = transform
    tokens = re.findall(r'[A-Za-z]|-?\d+\.?\d*(?:[eE][-+]?\d+)?', d)
    pts = []
    i = 0
    cur_cmd = None
    cur = (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cur_cmd = t
            i += 1
            if cur_cmd == 'Z':
                continue
            continue
        if cur_cmd in ('M', 'L', 'T'):
            x, y = float(tokens[i]), float(tokens[i+1])
            cur = (x, y)
            pts.append(cur)
            if cur_cmd == 'M':
                cur_cmd = 'L'
            i += 2
        elif cur_cmd == 'C':
            x, y = float(tokens[i+4]), float(tokens[i+5])
            cur = (x, y)
            pts.append(cur)
            i += 6
        elif cur_cmd in ('S', 'Q'):
            x, y = float(tokens[i+2]), float(tokens[i+3])
            cur = (x, y)
            pts.append(cur)
            i += 4
        elif cur_cmd == 'H':
            x = float(tokens[i]); cur = (x, cur[1]); pts.append(cur); i += 1
        elif cur_cmd == 'V':
            y = float(tokens[i]); cur = (cur[0], y); pts.append(cur); i += 1
        else:
            i += 1
    if not pts:
        return None
    transformed = [transform_point(p, (a, b, c, d_, e, f)) for p in pts]
    xs = [p[0] for p in transformed]; ys = [p[1] for p in transformed]
    return (min(xs), min(ys), max(xs), max(ys))



def path_points(d, transform):
    """Extract all (x, y) control points from a path string and return
    them transformed into viewBox coordinates."""
    a, b, c, d_, e, f = transform
    tokens = re.findall(r'[A-Za-z]|-?\d+\.?\d*(?:[eE][-+]?\d+)?', d)
    pts, i = [], 0
    cur_cmd, cur = None, (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cur_cmd = t
            i += 1
            if cur_cmd == 'Z':
                continue
            continue
        if cur_cmd in ('M', 'L', 'T'):
            x, y = float(tokens[i]), float(tokens[i+1])
            cur = (x, y); pts.append(cur)
            if cur_cmd == 'M':
                cur_cmd = 'L'
            i += 2
        elif cur_cmd == 'C':
            x, y = float(tokens[i+4]), float(tokens[i+5])
            cur = (x, y); pts.append(cur)
            i += 6
        elif cur_cmd in ('S', 'Q'):
            x, y = float(tokens[i+2]), float(tokens[i+3])
            cur = (x, y); pts.append(cur)
            i += 4
        elif cur_cmd == 'H':
            x = float(tokens[i]); cur = (x, cur[1]); pts.append(cur); i += 1
        elif cur_cmd == 'V':
            y = float(tokens[i]); cur = (cur[0], y); pts.append(cur); i += 1
        else:
            i += 1
    return [transform_point(p, (a, b, c, d_, e, f)) for p in pts]


def _match_marker_color(el, parent_map):
    """Detect a marker path by its fill/stroke rgb color (also checks
    inherited attrs on the parent group, which pdftocairo may use)."""
    for attr in ("fill", "stroke"):
        val = el.get(attr)
        if not val:
            parent = parent_map.get(el)
            val = parent.get(attr) if parent is not None else None
        if not val:
            continue
        m = re.search(r'rgb\(([^)]*)\)', val)
        if not m:
            continue
        try:
            rgb = tuple(float(v.strip().rstrip('%')) / 100.0
                        for v in m.group(1).split(','))
        except ValueError:
            continue
        for name, ref in MARKER_RGB.items():
            if len(rgb) == 3 and all(abs(a - b) < 0.02 for a, b in zip(rgb, ref)):
                return name
    return None


def parse_render_svg(svg_path):
    """Returns (body_paths, lead_paths, glyph_defs, glyph_uses, markers).
    markers maps marker color name -> (x, y) anchor position."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    body_paths, lead_paths = [], []
    glyph_defs = {}
    markers = {}
    parent_map = {c: p for p in tree.iter() for c in p}
    for p in root.iter(f"{{{SVG_NS}}}path"):
        d = p.get('d', '')
        if not d:
            continue
        marker = _match_marker_color(p, parent_map)
        if marker is not None:
            bb = path_bbox(d, parse_matrix(p.get('transform', '')))
            if bb is not None:
                markers[marker] = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
            continue
        sw = p.get('stroke-width', '')
        xform = parse_matrix(p.get('transform', ''))
        has_fill = p.get('fill', 'none') not in ('none', None) or p.get('fill-rule')
        if sw == "0.797":
            body_paths.append((d, xform))
        elif sw == "0.3985":
            lead_paths.append((d, xform))
        elif has_fill:
            # Filled shapes (arrows, dots) — treat as body
            body_paths.append((d, xform))
    for g in root.iter(f"{{{SVG_NS}}}g"):
        gid = g.get('id', '')
        if gid.startswith('glyph'):
            inner = g.find(f"{{{SVG_NS}}}path")
            if inner is not None:
                glyph_defs[gid] = inner.get('d', '')
    glyph_uses = []
    for u in root.iter(f"{{{SVG_NS}}}use"):
        href = u.get(f'{{{XLINK_NS}}}href') or u.get('href') or ''
        x = float(u.get('x', 0))
        y = float(u.get('y', 0))
        glyph_uses.append((href.lstrip('#'), x, y))
    return body_paths, lead_paths, glyph_defs, glyph_uses, markers


def union_bbox(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def lead_direction(lead_paths):
    """If leads exist, return 'horizontal' or 'vertical' based on their extent.
    Compares the lead bbox width vs height."""
    if not lead_paths:
        return None
    boxes = [path_bbox(d, t) for d, t in lead_paths]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    bb = union_bbox(boxes)
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    return "horizontal" if w > h else "vertical"


def compute_skin_geometry(body_paths, lead_paths):
    """Compute bbox and pin positions.

    Skin bbox = body bbox only.

    Pin positions are where the lead wires meet the body bbox. For
    horizontal components: pin_x is at the bbox edge (bbox[0] or bbox[2]),
    pin_y is the y of the lead (which is roughly constant for horizontal
    leads). For vertical components: pin_y is at bbox[1] or bbox[3],
    pin_x is the x of the lead.

    Falls back to body extremes when leads are absent (or for vertical
    leads crossing the body bbox from outside to outside, like V source).
    """
    body_boxes = [path_bbox(d, t) for d, t in body_paths]
    body_boxes = [b for b in body_boxes if b is not None]
    if not body_boxes:
        return None
    bbox = union_bbox(body_boxes)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    ld = lead_direction(lead_paths)
    if ld is None:
        ld = "horizontal" if width > height * 1.5 else "vertical"

    # Lead axis coord: for horizontal leads, the y is roughly constant
    # (one horizontal line); for vertical leads, the x is constant.
    lead_boxes = [path_bbox(d, t) for d, t in lead_paths]
    lead_boxes = [b for b in lead_boxes if b is not None]
    lead_axis = None
    if lead_boxes:
        lb = union_bbox(lead_boxes)
        if ld == "horizontal":
            lead_axis = (lb[1] + lb[3]) / 2
        else:
            lead_axis = (lb[0] + lb[2]) / 2

    if ld == "horizontal":
        if lead_axis is not None:
            pins = [
                ("+", "left",  bbox[0], lead_axis),
                ("-", "right", bbox[2], lead_axis),
            ]
        else:
            cx = (bbox[1] + bbox[3]) / 2
            pins = [
                ("+", "left",  bbox[0], cx),
                ("-", "right", bbox[2], cx),
            ]
    else:
        if lead_axis is not None:
            pins = [
                ("+", "top",    lead_axis, bbox[1]),
                ("-", "bottom", lead_axis, bbox[3]),
            ]
        else:
            cy = (bbox[0] + bbox[2]) / 2
            pins = [
                ("+", "top",    cy, bbox[1]),
                ("-", "bottom", cy, bbox[3]),
            ]
    return bbox, width, height, pins


def adjust_transform_to_skin(body_transform, body_bbox, skin_bbox):
    bx = (body_bbox[0] + body_bbox[2]) / 2
    by = (body_bbox[1] + body_bbox[3]) / 2
    sx = (skin_bbox[0] + skin_bbox[2]) / 2
    sy = (skin_bbox[1] + skin_bbox[3]) / 2
    dx = sx - bx
    dy = sy - by
    a, b, c, d, e, f = body_transform
    return (a, b, c, d, e + dx, f + dy)


def adjust_use_position(x, y, body_bbox, skin_bbox):
    return (x + (skin_bbox[0] - body_bbox[0]), y + (skin_bbox[1] - body_bbox[1]))


def emit_skin_entry(spec, body_paths, lead_paths, glyph_defs, glyph_uses, geom, pins):
    skin_type = spec["skin_type"]
    bbox, width, height, _auto_pins = geom
    skin_bbox = (0.0, 0.0, width, height)
    out = []
    out.append(f'<g s:type="{skin_type}" s:width="{width:.3f}" s:height="{height:.3f}">')
    out.append(f'  <s:alias val="{skin_type}"/>')
    out.append(f'  <text x="{width/2:.3f}" y="-3" text-anchor="middle" class="nodelabel" s:attribute="ref">name</text>')
    if glyph_defs:
        out.append('  <defs>')
        out.append('  <g>')
        for old_id, d in glyph_defs.items():
            new_id = f"{skin_type}-glyph-{old_id[len('glyph-'):]}"
            out.append(f'    <g id="{new_id}">')
            out.append(f'      <path d="{d}"/>')
            out.append(f'    </g>')
        out.append('  </g>')
        out.append('  </defs>')
    for d, t in body_paths:
        new_t = adjust_transform_to_skin(t, bbox, skin_bbox)
        matrix_str = f"matrix({','.join(f'{x:.6f}' for x in new_t)})"
        out.append(f'  <g transform="{matrix_str}">')
        out.append(f'    <path class="symbol" d="{d}" fill="none"/>')
        out.append(f'  </g>')

    for href, x, y in glyph_uses:
        new_x, new_y = adjust_use_position(x, y, bbox, skin_bbox)
        new_id = f"{skin_type}-glyph-{href[len('glyph-'):]}"
        out.append(f'  <use xlink:href="#{new_id}" x="{new_x:.3f}" y="{new_y:.3f}"/>')
    for pid, position, rx, ry in pins:
        out.append(f'  <g s:x="{rx:.3f}" s:y="{ry:.3f}" s:pid="{pid}" s:position="{position}"/>')
    out.append('</g>')
    return '\n'.join(out)


def build_generic_entry():
    """Generic cell for unknown components.
    Pins must use 'in' or 'out' prefix per netlistsvg convention.
    Provides 4 input + 4 output pins. convert.py emits pin names
    like A, B, C, D for unknown component prefixes, but those are
    remapped here by reusing the prefix slots.
    """
    w, h = 30.0, 40.0
    n_pins = 4
    margin = 6
    spacing = (h - 2 * margin) / max(1, n_pins - 1)
    out = [
        f'<g s:type="generic" s:width="{w}" s:height="{h}">',
        '  <s:alias val="generic"/>',
        f'  <text x="{w/2}" y="-4" text-anchor="middle" class="nodelabel" s:attribute="ref">name</text>',
        f'  <rect width="{w}" height="{h}" x="0" y="0" s:generic="body" class="symbol"/>',
    ]
    for i in range(n_pins):
        y_in = margin + i * spacing
        y_out = margin + i * spacing
        out.append(f'  <g s:x="0"   s:y="{y_in:.3f}"  s:pid="in{i}"  s:position="left">')
        out.append(f'    <text x="-5" y="-2" text-anchor="end" class="nodelabel">in{i}</text>')
        out.append(f'  </g>')
        out.append(f'  <g s:x="{w}" s:y="{y_out:.3f}" s:pid="out{i}" s:position="right">')
        out.append(f'    <text x="5" y="-2" text-anchor="start" class="nodelabel">out{i}</text>')
        out.append(f'  </g>')
    out.append('</g>')
    return '\n'.join(out)


def write_debug_render(spec, svg_path, geom, pins, debug_dir):
    """Copy the circuitikz render SVG into DEBUG_DIR with a red marker and
    pid label stamped at each skin pin position (for visual pin review)."""
    bbox = geom[0]
    tree = ET.parse(svg_path)
    root = tree.getroot()
    overlay = ET.SubElement(root, f"{{{SVG_NS}}}g")
    for pid, _pos, rx, ry in pins:
        ax, ay = rx + bbox[0], ry + bbox[1]
        c = ET.SubElement(overlay, f"{{{SVG_NS}}}circle")
        c.set("cx", f"{ax:.3f}"); c.set("cy", f"{ay:.3f}"); c.set("r", "1.2")
        c.set("fill", "red"); c.set("fill-opacity", "0.35")
        c.set("stroke", "red"); c.set("stroke-width", "0.3")
        t = ET.SubElement(overlay, f"{{{SVG_NS}}}text")
        t.set("x", f"{ax + 2:.3f}"); t.set("y", f"{ay - 1.5:.3f}")
        t.set("fill", "red"); t.set("stroke", "none"); t.set("font-size", "4")
        t.text = str(pid)
    debug_dir.mkdir(parents=True, exist_ok=True)
    tree.write(debug_dir / f"{spec['skin_type']}.svg")



def is_node(spec):
    """Node-style components (transistors, opamp, rails, ...) vs bipoles."""
    return spec.get("if_node", True)


def anchor_list(spec):
    """(anchor, pid, side, color) for every pin of the component."""

    anchors = spec.get("anchors", ["west", "east"])
    pids = spec.get("pin_ids", ["+", "-"])
    sides = spec.get("sides", ("left", "right"))
    colors = spec.get("colors", ["red", "green", "blue", "magenta"])

    if (len(anchors) != len(pids) or len(anchors) != len(sides)):
        sys.exit(f"{spec['skin_type']}: anchors/pin_ids/sides length mismatch")

    used = {c for _a, _p, _s, c in spec.get("extra_anchors", [])}

    if len(colors) < len(anchors) + len(used):
        sys.exit(f"{spec['skin_type']}: not enough colors for anchors + extra_anchors")

    # Keep the spec-declared anchor colors; allocate the rest to west/east.
    palette = [c for c in colors if c not in used]

    anchor_list = []
    for i, (anchor, pid, side) in enumerate(zip(anchors, pids, sides)):
        color = spec.get("anchor_colors", {}).get(anchor, palette[i])
        anchor_list.append((anchor, pid, side, color))

    anchor_list.extend(spec.get("extra_anchors", []))
    return anchor_list


def component_tex(spec, anchors):
    """Standalone .tex: the component named T, with a colored marker dot on
    each pin anchor."""
    opt = f", {spec['opts']}" if spec.get("opts") else ""
    if is_node(spec):
        lines = [f"\\node[{spec['cpt']}{opt}] (T) at (0,0) {{}};"]
    else:
        # circuitikz parses the FIRST key as the component kind, so name=T
        # must come after the kind (e.g. to[R, name=T]).
        target = "(0,-3)" if spec.get("vertical") else "(3,0)"
        lines = [f"\\draw (0,0) to[{spec['cpt']}, name=T{opt}] {target};"]
    for anchor, _pid, _side, color in anchors:
        lines.append(f"\\fill[{color}] (T.{anchor}) circle (0.2mm);")
    return (
        "\\documentclass[border=5pt]{standalone}\n"
        "\\usepackage{circuitikz}\n"
        + spec.get("preamble_extra", "")
        + "\\begin{document}\n"
        "\\begin{circuitikz}\n"
        + "\n".join(lines)
        + "\n\\end{circuitikz}\n"
        "\\end{document}\n"
    )


def pins_from_markers(spec, geom, markers, anchors):
    """Final pin list (pid, position, x, y) from color-marked anchors."""
    bbox, width, height, _auto = geom
    cx = (bbox[0] + bbox[2]) / 2
    pins = []
    for anchor, pid, side, color in anchors:
        if color not in markers:
            sys.exit(f"{spec['skin_type']}: marker {color!r} for anchor "
                     f"{anchor!r} not found in rendered SVG")
        x, y = markers[color]
        pos = side or ("left" if x < cx else "right")
        rx, ry = x - bbox[0], y - bbox[1]
        if not (-1.0 <= rx <= width + 1.0 and -1.0 <= ry <= height + 1.0):
            sys.exit(f"{spec['skin_type']}: pin {pid} at ({rx:.2f}, {ry:.2f}) "
                     f"outside cell {width:.2f}x{height:.2f}")
        pins.append((pid, pos, rx, ry))
    return pins


def build_component(name, work_dir, debug_dir):
    spec = COMPONENTS[name]
    if spec.get("is_generic"):
        return build_generic_entry()
    anchors = anchor_list(spec)
    svg_path = render_circuitikz(name, component_tex(spec, anchors), work_dir)
    body_paths, lead_paths, glyph_defs, glyph_uses, markers = parse_render_svg(svg_path)
    if not body_paths:
        sys.exit(f"No body paths found for {name}")
    geom = compute_skin_geometry(body_paths, lead_paths)
    if geom is None:
        sys.exit(f"Could not compute geometry for {name}")
    symbol_paths = body_paths
    if is_node(spec):
        # Node components (transistors, opamp, ground, rails, ...): leads
        # are part of the symbol and pins sit at the lead tips, so the cell
        # bbox must cover body + leads (unlike bipoles, where the wire
        # replaces the lead).
        boxes = [b for b in (path_bbox(d, t) for d, t in body_paths + lead_paths)
                 if b is not None]
        bbox = union_bbox(boxes)
        geom = (bbox, bbox[2] - bbox[0], bbox[3] - bbox[1], [])
        symbol_paths = body_paths + lead_paths

    # Extend the cell bbox so every anchor pin lies on the cell edge
    # (e.g. the switch shape's design width exceeds its drawn body).
    marker_pts = [markers[color] for _a, _pid, _s, color in anchors
                  if color in markers]
    if marker_pts:
        b = geom[0]
        xs = [p[0] for p in marker_pts] + [b[0], b[2]]
        ys = [p[1] for p in marker_pts] + [b[1], b[3]]
        nb = (min(xs), min(ys), max(xs), max(ys))
        geom = (nb, nb[2] - nb[0], nb[3] - nb[1], geom[3])
    pins = pins_from_markers(spec, geom, markers, anchors)
    write_debug_render(spec, svg_path, geom, pins, debug_dir)
    return emit_skin_entry(spec, symbol_paths, lead_paths, glyph_defs, glyph_uses, geom, pins)


def emit_full_skin(component_names, work_dir: Path | None =None):
    debug_dir = get_config_path_value("build_skin", "debug_dir")
    if work_dir is None:
        work_dir =  Path(tempfile.mkdtemp())

    try:
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="https://github.com/nturley/netlistsvg" xmlns:xlink="http://www.w3.org/1999/xlink">',
            '  <s:properties constants="false" splitsAndJoins="false" genericsLaterals="true"/>',
            '  <style>',
            '    svg { stroke: #000; fill: none; }',
            '    text { fill: #000; stroke: none; font-size: 10px; font-weight: bold; font-family: "Courier New", monospace; }',
            '    .nodelabel { fill: #000; stroke: none; font-size: 10px; font-family: "Courier New", monospace; }',
            '    .symbol { stroke: #000; stroke-width: 0.8; fill: none; }',
            '  </style>',
        ]
        for name in component_names:
            parts.append(build_component(name, work_dir, debug_dir))
            parts.append('')
        parts.append('</svg>')

    finally:
        shutil.rmtree(work_dir)

    return '\n'.join(parts)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build_skin.py <component|all>", file=sys.stderr)
        print(f"Available: {list(COMPONENTS.keys())}", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "all":
        full_skin = emit_full_skin(list(COMPONENTS.keys()))
        print(full_skin)
    else:
        name = sys.argv[1]
        if name not in COMPONENTS:
            sys.exit(f"Unknown component: {name}")
        try:
            work_dir = Path(tempfile.mkdtemp())
            debug_dir = get_config_path_value("build_skin", "debug_dir")
            skin_entry = build_component(name, work_dir, debug_dir)
            print(skin_entry)
        finally:
            shutil.rmtree(work_dir)
