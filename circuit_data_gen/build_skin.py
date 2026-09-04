"""Generate netlistsvg skin entries from Circuitikz-rendered components.

For each component type, this script:
1. Writes a standalone .tex file with a to[CPT] component
2. Compiles with pdflatex, converts to SVG with pdftocairo
3. Parses the SVG to extract body paths, lead paths, glyph defs
4. Computes the bbox and pin positions
5. Emits a <g> skin entry
"""

# ruff: noqa: E501
import re
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
from .configs.config import get_logger, get_config_value, get_config_path_value

logger = get_logger(__name__)
try:
    COMPONENTS = get_config_value("build_skin", "skin_config_path", "COMPONENTS")
    if not isinstance(COMPONENTS, dict):
        raise ValueError("COMPONENTS is not a dictionary")

    LINE_WIDTH = get_config_value("build_skin", "LINE_WIDTH")
    if not isinstance(LINE_WIDTH, (int, float)):
        raise ValueError("LINE_WIDTH is not a number")
except (ValueError, TypeError) as e:
    raise ValueError("Failed to retrieve configuration values") from e


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# Marker colors used to locate circuitikz anchors in the PDF->SVG render.
# Matched with tolerance against pdftocairo's "rgb(...%)" output.
MARKER_RGB = {
    # Primaries & Secondaries
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    # Warm Tones
    "orange": (1.0, 0.5, 0.0),
    "coral": (1.0, 0.5, 0.31),
    "gold": (1.0, 0.84, 0.0),
    "maroon": (0.5, 0.0, 0.0),
    "pink": (1.0, 0.41, 0.71),
    "crimson": (0.86, 0.08, 0.24),
    # Cool Tones
    "purple": (0.5, 0.0, 0.5),
    "indigo": (0.29, 0.0, 0.51),
    "violet": (0.58, 0.0, 0.83),
    "navy": (0.0, 0.0, 0.5),
    "dodger_blue": (0.12, 0.56, 1.0),
    "teal": (0.0, 0.5, 0.5),
    "turquoise": (0.25, 0.88, 0.82),
    # Greens & Earth Tones
    "lime": (0.75, 1.0, 0.0),
    "forest_green": (0.13, 0.55, 0.13),
    "olive": (0.5, 0.5, 0.0),
    "spring_green": (0.0, 1.0, 0.5),
    "brown": (0.59, 0.29, 0.0),
}


def _render_circuitikz(name, tex_content, work_dir):
    """Write .tex, compile to PDF, convert to SVG. Returns None if tex_content empty."""
    if not tex_content:
        return None

    _check_external_tools()
    work_dir.mkdir(parents=True, exist_ok=True)
    tex_path = work_dir / f"{name}.tex"
    tex_path.write_text(tex_content)
    r = subprocess.run(
        [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(work_dir),
            tex_path.name,
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-2000:])
        sys.stderr.write(r.stderr[-2000:])
        sys.exit(f"pdflatex failed for {name}")
    pdf_path = work_dir / f"{name}.pdf"
    svg_prefix = work_dir / f"{name}_render.svg"
    subprocess.run(
        ["pdftocairo", "-svg", str(pdf_path), str(svg_prefix)],
        check=True,
        capture_output=True,
    )
    return svg_prefix


def _parse_matrix(s):
    """matrix(a,b,c,d,e,f) -> tuple of floats, or identity."""
    if not s:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    m = re.match(r"\s*matrix\s*\(([^)]+)\)", s)
    if m:
        return tuple(float(x) for x in m.group(1).split(","))
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _transform_point(p, m):
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def _path_bbox(d, transform):
    """Approximate bbox by sampling endpoints of M/L/C/S/Q/H/V commands."""
    a, b, c, d_, e, f = transform
    tokens = re.findall(r"[A-Za-z]|-?\d+\.?\d*(?:[eE][-+]?\d+)?", d)
    pts = []
    i = 0
    cur_cmd = None
    cur = (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cur_cmd = t
            i += 1
            if cur_cmd == "Z":
                continue
            continue
        if cur_cmd in ("M", "L", "T"):
            x, y = float(tokens[i]), float(tokens[i + 1])
            cur = (x, y)
            pts.append(cur)
            if cur_cmd == "M":
                cur_cmd = "L"
            i += 2
        elif cur_cmd == "C":
            x, y = float(tokens[i + 4]), float(tokens[i + 5])
            cur = (x, y)
            pts.append(cur)
            i += 6
        elif cur_cmd in ("S", "Q"):
            x, y = float(tokens[i + 2]), float(tokens[i + 3])
            cur = (x, y)
            pts.append(cur)
            i += 4
        elif cur_cmd == "H":
            x = float(tokens[i])
            cur = (x, cur[1])
            pts.append(cur)
            i += 1
        elif cur_cmd == "V":
            y = float(tokens[i])
            cur = (cur[0], y)
            pts.append(cur)
            i += 1
        else:
            i += 1
    if not pts:
        return None
    transformed = [_transform_point(p, (a, b, c, d_, e, f)) for p in pts]
    xs = [p[0] for p in transformed]
    ys = [p[1] for p in transformed]
    return (min(xs), min(ys), max(xs), max(ys))


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
        m = re.search(r"rgb\(([^)]*)\)", val)
        if not m:
            continue
        try:
            rgb = tuple(float(v.strip().rstrip("%")) / 100.0 for v in m.group(1).split(","))
        except ValueError:
            continue
        for name, ref in MARKER_RGB.items():
            if len(rgb) == 3 and all(abs(a - b) < 0.02 for a, b in zip(rgb, ref)):
                return name
    return None


def _parse_render_svg(svg_path):
    """Returns (body_paths, lead_paths, glyph_defs, glyph_uses, markers).
    markers maps marker color name -> (x, y) anchor position."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    body_paths, lead_paths = [], []
    glyph_defs = {}
    markers = {}
    parent_map = {c: p for p in tree.iter() for c in p}
    for p in root.iter(f"{{{SVG_NS}}}path"):
        d = p.get("d", "")
        if not d:
            continue
        marker = _match_marker_color(p, parent_map)
        if marker is not None:
            bb = _path_bbox(d, _parse_matrix(p.get("transform", "")))
            if bb is not None:
                markers[marker] = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
            continue
        sw = p.get("stroke-width", math.inf)
        xform = _parse_matrix(p.get("transform", ""))
        has_fill = p.get("fill", "none") not in ("none", None) or p.get("fill-rule")
        if math.isclose(float(sw), LINE_WIDTH * 2, rel_tol=0.05):
            body_paths.append((d, xform))
        elif math.isclose(float(sw), LINE_WIDTH, rel_tol=0.05):
            lead_paths.append((d, xform))
        elif has_fill:
            # Filled shapes (arrows, dots) — treat as body
            body_paths.append((d, xform))
    for g in root.iter(f"{{{SVG_NS}}}g"):
        gid = g.get("id", "")
        if gid.startswith("glyph"):
            inner = g.find(f"{{{SVG_NS}}}path")
            if inner is not None:
                glyph_defs[gid] = inner.get("d", "")
    glyph_uses = []
    for u in root.iter(f"{{{SVG_NS}}}use"):
        href = u.get(f"{{{XLINK_NS}}}href") or u.get("href") or ""
        x = float(u.get("x", 0))
        y = float(u.get("y", 0))
        glyph_uses.append((href.lstrip("#"), x, y))
    return body_paths, lead_paths, glyph_defs, glyph_uses, markers


def _union_bbox(boxes):
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _lead_direction(lead_paths):
    """If leads exist, return 'horizontal' or 'vertical' based on their extent.
    Compares the lead bbox width vs height."""
    if not lead_paths:
        return None
    boxes = [_path_bbox(d, t) for d, t in lead_paths]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    bb = _union_bbox(boxes)
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    return "horizontal" if w > h else "vertical"


def _compute_skin_geometry(body_paths, lead_paths):
    """Compute bbox

    Skin bbox = body bbox only.
    """
    body_boxes = [_path_bbox(d, t) for d, t in body_paths]
    body_boxes = [b for b in body_boxes if b is not None]
    if not body_boxes:
        return None
    bbox = _union_bbox(body_boxes)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    ld = _lead_direction(lead_paths)
    if ld is None:
        ld = "horizontal" if width > height * 1.5 else "vertical"

    return bbox, width, height


def _adjust_transform_to_skin(body_transform, body_bbox, skin_bbox):
    bx = (body_bbox[0] + body_bbox[2]) / 2
    by = (body_bbox[1] + body_bbox[3]) / 2
    sx = (skin_bbox[0] + skin_bbox[2]) / 2
    sy = (skin_bbox[1] + skin_bbox[3]) / 2
    dx = sx - bx
    dy = sy - by
    a, b, c, d, e, f = body_transform
    return (a, b, c, d, e + dx, f + dy)


def _adjust_use_position(x, y, body_bbox, skin_bbox):
    return (x + (skin_bbox[0] - body_bbox[0]), y + (skin_bbox[1] - body_bbox[1]))


def _build_generic_entry():
    """Generic cell for unknown components.
    Pins must use 'in' or 'out' prefix per netlistsvg convention.
    Provides 4 input + 4 output pins. convert.py emits pin names
    like A, B, C, D for unknown component prefixes, but those are
    remapped here by reusing the prefix slots.
    """
    w, h = 30.0, 30.0
    n_pins = 2
    margin = 6
    spacing = (h - 2 * margin) / max(1, n_pins - 1)
    out = [
        f'<g s:type="generic" s:class="generic" s:width="{w}" s:height="{h}">',
        '  <s:alias val="generic"/>',
        f'  <text x="{w / 2}" y="-4" text-anchor="middle" dominant-baseline="middle" class="nodelabel" s:attribute="ref">name</text>',
        f'  <rect width="{w}" height="{h}" x="0" y="0" s:generic="body" class="symbol"/>',
        f'  <text x="{w / 2}" y="13" text-anchor="middle" dominant-baseline="middle" class="nodelabel" s:attribute="value">value</text>',
    ]
    for i in range(n_pins):
        y_in = margin + i * spacing
        y_out = margin + i * spacing
        out.append(f'  <g s:x="0"   s:y="{y_in:.3f}"  s:pid="in{i}"  s:position="left">')
        out.append(f'    <text x="-5" y="-2" text-anchor="end" class="nodelabel">in{i}</text>')
        out.append("  </g>")
        out.append(f'  <g s:x="{w}" s:y="{y_out:.3f}" s:pid="out{i}" s:position="right">')
        out.append(f'    <text x="5" y="-2" text-anchor="start" class="nodelabel">out{i}</text>')
        out.append("  </g>")
    out.append("</g>")
    return "\n".join(out)


def _write_debug_render(spec, svg_path, geom, pins, labels, debug_dir):
    """Copy the circuitikz render SVG into DEBUG_DIR with a red marker and
    pid label stamped at each skin pin position (for visual pin review)."""
    bbox = geom[0]
    tree = ET.parse(svg_path)
    root = tree.getroot()
    # to prevent debug text clipping
    viewBox = [float(x) for x in root.attrib["viewBox"].split()]
    viewBox = [viewBox[0] - 25, viewBox[1] - 25, viewBox[2] + 50, viewBox[3] + 50]
    root.set("viewBox", " ".join(f"{v:.3f}" for v in viewBox))
    overlay = ET.SubElement(root, f"{{{SVG_NS}}}g")

    # translucent bbox of the computed skin cell (body + leads + pin extent)
    r = ET.SubElement(overlay, f"{{{SVG_NS}}}rect")
    r.set("x", f"{bbox[0]:.3f}")
    r.set("y", f"{bbox[1]:.3f}")
    r.set("width", f"{bbox[2] - bbox[0]:.3f}")
    r.set("height", f"{bbox[3] - bbox[1]:.3f}")
    r.set("fill", "lime")
    r.set("fill-opacity", "0.15")
    r.set("stroke", "lime")
    r.set("stroke-width", "0.3")
    r.set("stroke-dasharray", "2 1.5")

    for pid, _pos, rx, ry in pins:
        ax, ay = rx + bbox[0], ry + bbox[1]
        c = ET.SubElement(overlay, f"{{{SVG_NS}}}circle")
        c.set("cx", f"{ax:.3f}")
        c.set("cy", f"{ay:.3f}")
        c.set("r", "1.2")
        c.set("fill", "red")
        c.set("fill-opacity", "0.35")
        c.set("stroke", "red")
        c.set("stroke-width", "0.3")
        t = ET.SubElement(overlay, f"{{{SVG_NS}}}text")
        t.set("x", f"{ax + 2:.3f}")
        t.set("y", f"{ay - 1.5:.3f}")
        t.set("fill", "red")
        t.set("stroke", "none")
        t.set("font-size", "4")
        t.text = str(pid)

    for label_name, text_anchor, rx, ry in labels:
        ax, ay = rx + bbox[0], ry + bbox[1]
        c = ET.SubElement(overlay, f"{{{SVG_NS}}}circle")
        c.set("cx", f"{ax:.3f}")
        c.set("cy", f"{ay:.3f}")
        c.set("r", "1.2")
        c.set("fill", "blue")
        c.set("fill-opacity", "0.35")
        c.set("stroke", "blue")
        c.set("stroke-width", "0.3")

        # x_fixed = -2 if text_anchor == "left" else 2 if text_anchor == "right" else 0
        # y_fixed = -2 if text_anchor == "above" else 2 if text_anchor == "below" else 0
        # x_offset = (
        #     -1.5 if text_anchor == "left" else 1.5 if text_anchor == "right" else 0
        # )
        # y_offset = (
        #     -1.5 if text_anchor == "above" else 1.5 if text_anchor == "below" else 0
        # )

        svg_text_anchor = "end" if text_anchor == "left" else "start" if text_anchor == "right" else "middle"
        svg_dominant_baseline = (
            "baseline" if text_anchor == "above" else "hanging" if text_anchor == "below" else "middle"
        )

        t = ET.SubElement(overlay, f"{{{SVG_NS}}}text")
        t.set("x", f"{ax}")
        t.set("y", f"{ay}")
        # t.set("x", f"{ax + x_fixed + x_offset * len(label_name):.3f}")
        # t.set("y", f"{ay + y_fixed + y_offset:.3f}")
        t.set("fill", "blue")
        t.set("stroke", "none")
        t.set("font-size", "4")
        t.set("text-anchor", svg_text_anchor)
        t.set("dominant-baseline", svg_dominant_baseline)
        t.text = str(label_name)

    debug_dir.mkdir(parents=True, exist_ok=True)
    tree.write(debug_dir / f"{spec['skin_type']}.svg")


def _is_node(spec):
    """Node-style components (transistors, opamp, rails, ...) vs bipoles."""
    return spec.get("if_node", True)


def _anchor_lists(spec):
    """(anchor, pid, side, color) for every pin of the component."""

    pin_anchors = spec.get("pin_anchors", ["west", "east"])
    label_anchors = spec.get("label_anchors", None)
    if label_anchors is None:
        if _is_node(spec):
            label_anchors = [("", "north", "above", "ref")]
        else:
            vertical = spec.get("vertical", False)
            if vertical:
                label_anchors = [
                    ("label", "west", "right", "ref"),
                    ("annotation", "east", "left", "value"),
                ]
            else:
                label_anchors = [
                    ("label", "south", "above", "ref"),
                    ("annotation", "north", "below", "value"),
                ]

    extra_anchors = spec.get("extra_anchors", [])
    pids = spec.get("pin_ids", ["+", "-"])
    sides = spec.get("pin_sides", ("left", "right"))
    colors = spec.get("colors", list(MARKER_RGB.keys()))

    if len(pin_anchors) != len(pids) or len(pin_anchors) != len(sides):
        sys.exit(f"{spec['skin_type']}: anchors/pin_ids/sides length mismatch")

    if len(colors) < len(pin_anchors) + len(extra_anchors) + len(label_anchors):
        sys.exit(f"{spec['skin_type']}: not enough colors for anchors + extra_anchors + labels")

    extra_anchor_list = [[a, p, s, colors[i]] for i, (a, p, s) in enumerate(extra_anchors)]

    # Keep the spec-declared anchor colors; allocate the rest to west/east.
    pin_palette = colors[len(extra_anchor_list) :]

    pin_anchor_list = []
    for i, (anchor, pid, side) in enumerate(zip(pin_anchors, pids, sides)):
        color = pin_palette[i]
        pin_anchor_list.append((anchor, pid, side, color))

    pin_anchor_list.extend(extra_anchor_list)

    label_palette = colors[len(pin_anchor_list) :]

    label_anchor_list = []
    for i, (label_subnode, anchor, text_anchor, label_name) in enumerate(label_anchors):
        color = label_palette[i]
        label_anchor_list.append((label_subnode, anchor, text_anchor, label_name, color))

    return pin_anchor_list, label_anchor_list


def _component_tex(spec, pin_anchors, label_anchors):
    """Standalone .tex: the component named T, with a colored marker dot on
    each pin anchor."""
    opt = f", {spec['opts']}" if spec.get("opts") else ""
    vertical = spec.get("vertical", False)
    if _is_node(spec):
        # Node components have no auto-generated subnodes; use the body anchors
        # (T.north, T.B, ...) directly, per the label_subnode="" convention.
        lines = [f"\\node[{spec['cpt']}{opt}] (T) at (0,0) {{}};"]
    else:
        # circuitikz parses the FIRST key as the component kind, so name=T
        # must come after the kind (e.g. to[R, name=T]).
        target = "(0,-3)" if vertical else "(3,0)"
        # Tlabel, and other subnodes only exists
        # if the bipole gets an explicit <subnode>=... label value
        label_subnodes = [
            label_subnode for label_subnode, _, _, _, _ in label_anchors if len(label_subnode) > 0
        ]
        label_opt = ", " + ", ".join(f"{subnode}=$$" for subnode in label_subnodes) if label_subnodes else ""
        lines = [f"\\draw (0,0) to[{spec['cpt']}, name=T{opt}{label_opt}] {target};"]

    for anchor, _pid, _side, color in pin_anchors:
        lines.append(f"\\fill[{color}] (T.{anchor}) circle (0.2mm);")

    for label_subnode, anchor, _text_anchor, label_name, color in label_anchors:
        lines.append(f"\\fill[{color}] (T{label_subnode}.{anchor}) circle (0.2mm);")

    return (
        "\\PassOptionsToPackage{rgb}{xcolor}"
        "\\documentclass[border=5pt]{standalone}\n"
        "\\usepackage{circuitikz}\n"
        + spec.get("preamble_extra", "")
        + "\\begin{document}\n"
        + f"\\begin{{circuitikz}}[line width={LINE_WIDTH}pt]\n"
        + "\n".join(lines)
        + "\n\\end{circuitikz}\n"
        "\\end{document}\n"
    )


def _pins_from_markers(spec, geom, markers, pin_anchors):
    """Final pin list (pid, position, x, y) from color-marked anchors."""
    bbox, width, height = geom
    cx = (bbox[0] + bbox[2]) / 2
    pins = []
    for anchor, pid, side, color in pin_anchors:
        if color not in markers:
            sys.exit(
                f"{spec['skin_type']}: marker {color!r} for pin anchor {anchor!r} not found in rendered SVG"
            )
        x, y = markers[color]
        pos = side or ("left" if x < cx else "right")
        rx, ry = x - bbox[0], y - bbox[1]
        if not (-1.0 <= rx <= width + 1.0 and -1.0 <= ry <= height + 1.0):
            sys.exit(
                f"{spec['skin_type']}: pin {pid} at ({rx:.2f}, {ry:.2f}) "
                f"outside cell {width:.2f}x{height:.2f}"
            )
        pins.append((pid, pos, rx, ry))
    return pins


def _label_from_markers(spec, geom, markers, label_anchors):
    """Final label list (label_name, text_anchor, x, y) from color-marked anchors."""

    bbox, width, height = geom
    labels = []
    for _label_subnode, anchor, text_anchor, label_name, color in label_anchors:
        if color not in markers:
            sys.exit(
                f"{spec['skin_type']}: marker {color!r} for label anchor {anchor!r} not found in rendered SVG"
            )

        x, y = markers[color]
        margin = 5
        x_offset = -margin if text_anchor == "left" else margin if text_anchor == "right" else 0
        y_offset = -margin if text_anchor == "above" else margin if text_anchor == "below" else 0
        rx, ry = x - bbox[0] + x_offset, y - bbox[1] + y_offset

        # if (1 <= rx <= width - 1.0 and 1.0 <= ry <= height - 1.0):
        #     sys.exit(f"{spec['skin_type']}: label {label_name} at ({rx:.2f}, {ry:.2f}) "
        #              f"overlapps cell {width:.2f}x{height:.2f}")

        labels.append((label_name, text_anchor, rx, ry))
    return labels


def _build_component(name, work_dir, debug_dir: Path | None = None):
    spec = COMPONENTS[name]
    logger.info(f"Building skin for {name}")
    if spec.get("is_generic"):
        return _build_generic_entry()
    pin_anchors, label_anchors = _anchor_lists(spec)
    svg_path = _render_circuitikz(name, _component_tex(spec, pin_anchors, label_anchors), work_dir)
    body_paths, lead_paths, glyph_defs, glyph_uses, markers = _parse_render_svg(svg_path)
    if not body_paths:
        logger.error(f"No body paths found for {name}")
        raise
    geom = _compute_skin_geometry(body_paths, lead_paths)
    if geom is None:
        logger.error(f"Could not compute geometry for {name}")
        raise

    symbol_paths = body_paths
    if _is_node(spec):
        # Node components (transistors, opamp, ground, rails, ...): leads
        # are part of the symbol and pins sit at the lead tips, so the cell
        # bbox must cover body + leads (unlike bipoles, where the wire
        # replaces the lead).
        boxes = [b for b in (_path_bbox(d, t) for d, t in body_paths + lead_paths) if b is not None]
        bbox = _union_bbox(boxes)
        geom = (bbox, bbox[2] - bbox[0], bbox[3] - bbox[1])
        symbol_paths = body_paths + lead_paths

    # Extend the cell bbox so every anchor pin lies on the cell edge
    # (e.g. the switch shape's design width exceeds its drawn body).
    pin_marker_pts = [markers[color] for _a, _pid, _s, color in pin_anchors if color in markers]

    if pin_marker_pts:
        b = geom[0]
        xs = [p[0] for p in pin_marker_pts] + [b[0], b[2]]
        ys = [p[1] for p in pin_marker_pts] + [b[1], b[3]]
        nb = (min(xs), min(ys), max(xs), max(ys))
        geom = (nb, nb[2] - nb[0], nb[3] - nb[1])

    pins = _pins_from_markers(spec, geom, markers, pin_anchors)
    labels = _label_from_markers(spec, geom, markers, label_anchors)

    if debug_dir is not None:
        _write_debug_render(spec, svg_path, geom, pins, labels, debug_dir)

    return _emit_skin_entry(spec, symbol_paths, lead_paths, glyph_defs, glyph_uses, geom, pins, labels)


def _emit_skin_entry(spec, body_paths, lead_paths, glyph_defs, glyph_uses, geom, pins, labels):
    skin_type = spec["skin_type"]
    annotation_class = spec["annotation_class"]
    bbox, width, height = geom
    skin_bbox = (0.0, 0.0, width, height)
    out = []
    out.append(
        f'<g s:type="{skin_type}" s:class="{annotation_class}" s:width="{width:.3f}" s:height="{height:.3f}">'
    )
    out.append(f'  <s:alias val="{skin_type}"/>')

    if glyph_defs:
        out.append("  <defs>")
        out.append("  <g>")
        for old_id, d in glyph_defs.items():
            new_id = f"{skin_type}-glyph-{old_id[len('glyph-') :]}"
            out.append(f'    <g id="{new_id}">')
            out.append(f'      <path d="{d}"/>')
            out.append("    </g>")
        out.append("  </g>")
        out.append("  </defs>")

    for d, t in body_paths:
        new_t = _adjust_transform_to_skin(t, bbox, skin_bbox)
        matrix_str = f"matrix({','.join(f'{x:.6f}' for x in new_t)})"
        out.append(f'  <g transform="{matrix_str}">')
        out.append(f'    <path class="symbol" d="{d}" fill="none"/>')
        out.append("  </g>")

    for href, x, y in glyph_uses:
        new_x, new_y = _adjust_use_position(x, y, bbox, skin_bbox)
        new_id = f"{skin_type}-glyph-{href[len('glyph-') :]}"
        out.append(f'  <use xlink:href="#{new_id}" x="{new_x:.3f}" y="{new_y:.3f}"/>')

    for pid, position, rx, ry in pins:
        out.append(f'  <g s:x="{rx:.3f}" s:y="{ry:.3f}" s:pid="{pid}" s:position="{position}"/>')

    for label_name, text_anchor, rx, ry in labels:
        svg_text_anchor = "end" if text_anchor == "left" else "start" if text_anchor == "right" else "middle"
        svg_dominant_baseline = (
            "baseline" if text_anchor == "above" else "hanging" if text_anchor == "below" else "middle"
        )

        out.append(
            f'  <text x="{rx:.3f}" y="{ry:.3f}" text-anchor="{svg_text_anchor}" '
            f'dominant-baseline="{svg_dominant_baseline}"'
            f'class="nodelabel" s:attribute="{label_name}">name</text>'
        )

    out.append("</g>")
    return "\n".join(out)


def _check_external_tools():
    """Preflight: ensure pdflatex (MiKTeX) and pdftocairo (Poppler) are on PATH."""
    missing = [t for t in ("pdflatex", "pdftocairo") if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"Missing required external tools: {', '.join(missing)}. "
            "Skin building shells out to LaTeX/Cairo, these are NOT pip-installable. \n"
            "Read README.md for installation instructions:\n"
            "  1. MiKTeX (provides pdflatex)\n"
            "  2. Poppler (provides pdftocairo)\n"
            "Then restart the terminal so the updated PATH is picked up."
        )


def emit_full_skin(component_names, work_dir: Path | None = None, if_write_classes: bool = False):
    debug_dir = get_config_path_value("build_skin", "debug_dir")
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp())

    if if_write_classes:
        classes_path = get_config_path_value("netlistsvg", "annotation.classes_path")
    font_size = get_config_value("netlistsvg", "font_size")
    font_char_width = get_config_value("netlistsvg", "font_char_width")
    font_char_height = get_config_value("netlistsvg", "font_char_height")
    font_cap_height = get_config_value("netlistsvg", "font_cap_height")
    font_desc_shift = get_config_value("netlistsvg", "font_desc_shift")
    wire_stroke_width = get_config_value("netlistsvg", "wire_stroke_width")
    symbol_stroke_width = LINE_WIDTH * 2

    try:
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="https://github.com/nturley/netlistsvg" xmlns:xlink="http://www.w3.org/1999/xlink">',
            '  <s:properties constants="false" splitsAndJoins="false" genericsLaterals="true"'
            f' fontCharWidth="{font_char_width}" fontCharHeight="{font_char_height}"'
            f' fontCapHeight="{font_cap_height}" fontDescShift="{font_desc_shift}"'
            f' wireStrokeWidth="{wire_stroke_width}"/>',
            "  <style>",
            "    svg { stroke: #000; fill: none; }",
            f'    text {{ fill: #000; stroke: none; font-size: {font_size}px; font-weight: bold; font-family: "Courier New", monospace; }}',
            f'    .nodelabel {{ fill: #000; stroke: none; font-size: {font_size}px; font-family: "Courier New", monospace; }}',
            f"    .symbol {{ stroke: #000; stroke-width: {symbol_stroke_width}; fill: none; }}",
            "  </style>",
        ]
        classes = set()
        for name in component_names:
            spec = COMPONENTS[name]
            if spec.get("is_generic"):
                classes.add("generic")
                parts.append(_build_generic_entry())
                parts.append("")
                continue
            # Multiple skins may share one annotation class (e.g. horizontal
            # and vertical variants); every skin is still emitted, the class
            # set is just deduplicated for the registry. The registry holds
            # component classes only: pins are exact points and labels are
            # attributes (ref/value) in the structured annotation JSON.
            classes.add(spec["annotation_class"])
            parts.append(_build_component(name, work_dir, debug_dir))
            parts.append("")
        parts.append("</svg>")
        classes = sorted(classes)
        if if_write_classes:
            classes_path.write_text("\n".join(classes) + "\n")
            logger.info(f"Wrote class registry: {classes_path}")
    finally:
        shutil.rmtree(work_dir)

    return "\n".join(parts)
