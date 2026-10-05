# Skin config — `COMPONENTS`

**File:** `circuit_data_gen/configs/skins/default_skin_config.py`
**Selected by:** `BUILD_SKIN_CONFIG["SKIN_CONFIG_PATH"]` in `configs/config.py`.
**Read by:** `circuit_data_gen/build_skin.py` (`cirdg build_skin`).

`COMPONENTS` defines how every component is **drawn**. For each entry the
builder writes a small CircuitTikz document with colored marker dots on the pin
and label anchors, renders it (`pdflatex` + `pdftocairo`), finds the markers in
the rendered SVG, and emits one `<g>` template into the skin
(`BUILD_SKIN_CONFIG["SKIN_PATH"]`, by default `dataset/skins/skin.svg`) that
netlistsvg uses to draw schematics.

```python
COMPONENTS = {
    "<key>": {"skin_type": ..., "annotation_class": ..., "cpt": ..., ...},
    ...
}
```

The **key** only names the entry for `cirdg build_skin <key>` (build one
component). What the rest of the pipeline refers to is `skin_type`.

## Required fields

| Field | Meaning |
| --- | --- |
| `skin_type` | The skin's `s:type` / `<s:alias>`. The convert config's `skin_alias` must name it; the parser emits it as the yosys cell type. |
| `annotation_class` | Detection class of the component, written as `s:class` and collected into `classes.txt` (`--write-classes`). Variants share a class: `r_h` and `r_v` are both `resistor`. |
| `cpt` | CircuitTikz component, as in `to[<cpt>]` (bipoles) or `\node[<cpt>]` (multi-pin): `"R"`, `"leD"`, `"nmos"`, `"op amp"`. Not used by `generic`. |

## Optional fields

| Field | Default | Meaning |
| --- | --- | --- |
| `if_node` | `True` | `True`: multi-pin node component (transistor, opamp, ...), leads drawn as part of the symbol, pins at the lead tips. `False`: bipole (R, C, sources, diodes, switches), leads dropped (wires replace them), pins on the body edge. |
| `vertical` | `False` | Bipoles only: draw from `(0,0)` to `(0,-3)` instead of `(3,0)`. The default label anchors rotate with it. |
| `pin_anchors` | `["west", "east"]` | CircuitTikz anchor of each pin on the component (`["B", "C", "E"]`, `["in", "out 1", "out 2"]`). |
| `pin_ids` | `["+", "-"]` | Pin names (`s:pid`). Must match the convert config's `arg_to_ports` aliases. |
| `pin_sides` | `("left", "right")` | Side of each pin (`s:position`): `left`/`right` = lateral, `top` = input (signal flows in), `bottom` = output (drives). netlistsvg uses it to orient the cell and route wires. |
| `label_anchors` | see below | One `(label_subnode, anchor, text_anchor, label_name)` per text label. |
| `opts` | none | Extra CircuitTikz options after the component, e.g. `cpt: "twoport"` + `opts: "D"`. |
| `preamble_extra` | `""` | Extra LaTeX placed in the document preamble before the drawing, used to select a style variant: `"\\ctikzset{american}\n"` (zig-zag resistor), `"\\ctikzset{american voltages, american currents}\n"` (source symbols). |
| `colors` | `MARKER_RGB` order | Marker colors to use, by name (keys of `MARKER_RGB` in `build_skin.py`). Only needed when a default marker color also appears in the symbol itself. Must cover every extra anchor, pin and label. |
| `extra_anchors` | `[]` | Additional pins as `(anchor, pid, side)`, appended after `pin_anchors` (they take the first colors). |
| `is_generic` | `False` | Only on `generic`: the hand-drawn fallback box, skipped by the CircuitTikz pipeline. |

`pin_anchors`, `pin_ids` and `pin_sides` must have the same length; the builder
exits otherwise.

### `label_anchors`

- `label_subnode`: anchor target `T<label_subnode>` (`T` is the component; `""`
  = `T` itself). Bipoles have `label` and `annotation` subnodes.
- `anchor`: CircuitTikz anchor on that target (`north`, `south`, `west`, ...).
- `text_anchor`: direction the text grows (`above`, `below`, `left`, `right`).
- `label_name`: the text shown: `ref` (the component name) or a `skin_label`
  from the convert config (`value`, `vcontrol`).

Defaults: horizontal bipole `[("label", "south", "above", "ref"), ("annotation",
"north", "below", "value")]`; vertical bipole `[("label", "west", "right", "ref"),
("annotation", "east", "left", "value")]`; node component `[("", "north",
"above", "ref")]`. Vertical bipoles get no other special handling, so check
their debug render.

## Example

```python
"r_h": {
    "cpt": "R",
    "preamble_extra": "\\ctikzset{american}\n",   # zig-zag style
    "skin_type": "r_h",
    "annotation_class": "resistor",
    "if_node": False,                            # bipole
}
```

Defaults give pins `+` (left) and `-` (right) at `west`/`east`, the ref label
above and the value label below.

## Adding a component

1. Add an entry with the required fields; for a multi-pin component also
   `pin_anchors`, `pin_ids`, `pin_sides`.
2. Build and inspect it: `cirdg build_skin <key>`. Every build also writes a
   debug render per component to `BUILD_SKIN_CONFIG["DEBUG_DIR"]`
   (`circuit_data_gen/debug/circuitikz_renders/<skin_type>.svg`), marking the
   cell box, pins and label anchors.
3. Map netlist syntax to it in the convert config (`CONVERT_CONFIG.md`).
4. Rebuild everything: `cirdg build_skin --all --write-classes`.

Other skin settings (fonts, wire width, ELK layout options) live in
`NETLIST_SVG_CONFIG` in `config.py` and are written into the skin by the same
build.
