# netlistsvg Skin SVG — Structure Reference

This document describes the skin SVG format that `circuit_data_gen/build_skin.py`
generates and the netlistsvg fork consumes. Read it when adding a new component
to the skin config, changing the builder, or debugging layout/annotation issues.

## Pipeline: how a component becomes a skin entry

```
TO_SKIN_CONFIG (configs/convert/default_convert_config.py)
    │  lcapy prefix + kind -> skin_alias (+ port/drop/value specs)
    ▼
COMPONENTS (configs/skins/default_skin_config.py)
    │  skin_alias -> CircuitTikz spec (cpt, anchors, label slots)
    │
    ▼
build_skin.py  ──  pdflatex + pdftocairo ──►  per-component CircuitTikz SVG
    │                                         (body paths, leads, color
    ▼                                          markers, glyph defs)
dataset/skins/lcapy.svg  ── the skin this document describes
    │
    ▼
netlistsvg (fork) ── ELK layout + SVG render + annotation JSON
```

Two mappings work together:

- **`TO_SKIN_CONFIG`** (`configs/convert/default_convert_config.py`): maps a
  lcapy prefix (+ optional kind keyword) to a `skin_alias` — e.g. `R` →
  `["r_h", "r_v"]` (orientation picked randomly per render), `D led` →
  `d_led_h`. `convert.py` emits that alias as the Yosys cell's `type`.
- **`COMPONENTS`** (`configs/skins/default_skin_config.py`): the skin builder's
  spec table, keyed by `skin_alias` — which CircuitTikz component (`cpt`) to
  draw, where its pins/labels anchor, and which annotation class it maps to.

## How netlistsvg finds a component's skin

`Skin.findSkinType(type)` (in `lib/Skin.ts`) does two things:

1. Walks the skin tree looking for an `<s:alias val="{type}"/>` node, then
   returns that node's **parent `<g>`** as the template.
2. If no alias matches, it falls back to the first `<g>` whose `s:type` is
   `"generic"`.

So the lookup chain is: **Yosys cell `type` (from convert.py) → skin
`<s:alias>` → parent `<g>` template**.

> Note: matching is on the alias `val`, not on `s:type`. The `s:type`
> attribute exists for netlistsvg's special-case branching (`generic`,
> `join`, `split`) and carries the default class fallback.

---

## Top-level skin structure

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:s="https://github.com/nturley/netlistsvg"
     xmlns:xlink="http://www.w3.org/1999/xlink">

  <!-- 1. Rendering parameters: config values baked in by build_skin.py -->
  <s:properties constants="false" splitsAndJoins="false" genericsLaterals="true"
       fontCharWidth="6" fontCharHeight="11"
       fontDescShift="0.3" wireStrokeWidth="0.8"/>

  <!-- 2. CSS styles applied to rendered cells -->
  <style>
    svg { stroke: #000; fill: none; }
    text { ... font-size: 10px; font-weight: bold; "Courier New", monospace }
    .nodelabel { ... }
    .symbol { stroke: #000; stroke-width: 0.8; fill: none; }
  </style>

  <!-- 3. One <g> per component type -->
  <g s:type="r_h" s:class="resistor" s:width="32.070" s:height="11.730">
    <s:alias val="r_h"/>
    <text class="nodelabel" s:attribute="ref">name</text>
    <text class="nodelabel" s:attribute="value">value</text>
    <g transform="matrix(...)"><path class="symbol" d="..."/></g>
    <g s:x="0.000" s:y="5.867" s:pid="+" s:position="left"/>
    <g s:x="32.070" s:y="5.867" s:pid="-" s:position="right"/>
  </g>
  ...
</svg>
```

---

## Component `<g>` template — attribute reference

### `<g s:type="r_h" s:class="resistor" s:width="32.070" s:height="11.730">`

| Attribute | Purpose |
|-----------|---------|
| `s:type`  | Template name. Checked for special values: `"generic"`, `"join"`, `"split"`. Matching itself happens on `<s:alias>`. |
| `s:class` | The **annotation class** — copied into the annotation JSON as `components[].class` and validated against `dataset/classes.txt`. Multiple skins may share one class (e.g. `r_h`/`r_v` are both `resistor`). Falls back to `s:type` when absent. |
| `s:width` | Cell width in SVG units — passed to ELK as the node width. |
| `s:height`| Cell height in SVG units — ELK node height (generic cells override dynamically). |

### `<s:alias val="r_h"/>`

Declares: "when the Yosys JSON has `type: "r_h"`, use THIS `<g>` as the
template." One skin can carry several aliases (each `<g>` may also contain
additional `<s:low_priority_alias>` elements, which `getLowPriorityAliases`
collects for layout decisions).

### `<text class="nodelabel" s:attribute="ref">name</text>`

A **label slot**. netlistsvg's `addLabels()` traverses the template looking for
`<text>` nodes with `s:attribute`:

- `s:attribute="ref"` → filled with the component's **name** (e.g. `R1`)
- `s:attribute="value"` → filled with the component's value attribute (if
  present; the `<text>` is blanked when the component has no value)

The text content (`name`/`value`) is a placeholder, replaced during `render()`
via `setTextAttribute()`.

The builder places these slots from `label_anchors` in the component spec and
offsets them ~5px away from the body per `text_anchor` (`above` →
`dominant-baseline="baseline"`, `below` → `"hanging"`, sides →
`text-anchor="end"/"start"`). netlistsvg's annotation boxes map those three
baselines to label boxes using the skin's `fontCharHeight`/`fontDescShift`
properties (see `Cell.svgTextToElkBox`).

### `<g transform="matrix(...)"><path class="symbol" d="..."/></g>`

The **visual drawing**, extracted from CircuitTikz output (`pdflatex` +
`pdftocairo -svg`):

- `class="symbol"` — styled by the `<style>` block. Stroke width comes from
  `LINE_WIDTH * 2` in the config; the builder separates "body" paths (this
  width) from "leads" (`LINE_WIDTH`).
- `transform="matrix(a,b,c,d,e,f)"` — `build_skin.py` computes this to move
  the CircuitTikz body bbox into the skin's local coordinate system (origin at
  the cell's top-left, size = body bbox).
- `<use xlink:href="#...">` + `<defs>` — some CircuitTikz glyphs (text-like
  symbols) are emitted as reusable glyph defs; ids are prefixed with the skin
  type (`{skin_type}-glyph-N`) to stay unique across the file.

### `<g s:x="0.000" s:y="5.867" s:pid="+" s:position="left"/>`

A **pin (port) definition** — the most important part for layout.

| Attribute | Purpose |
|-----------|---------|
| `s:x`, `s:y` | Pin position relative to the cell's top-left corner. ELK uses `FIXED_POS` constraints, so it must route to exactly these points. |
| `s:pid` | Pin ID. Must match the port name that `convert.py` emits in the Yosys JSON connections (e.g. `R` → `"+"`/`"-"`). |
| `s:position` | Routing side: `"left"` / `"right"` / `"top"` / `"bottom"`. |

#### How ports are classified

`Cell.fromYosysCell()` first classifies the Yosys cell's ports by comparing
their names against the skin template:

- `getInputPids(template)` → pins with `s:position="top"`
- `getOutputPids(template)` → pins with `s:position="bottom"`
- `getLateralPortPids(template)` → `s:position="left"` or `"right"`

Any port not classified this way falls back to the Yosys JSON's
`port_directions` hints (which `convert.py` fills from the skin spec's port
aliases). For analog bipoles (R, L, C, V, I, D, SW) both pins are **lateral**
(`left`/`right`), so wires route in from the sides. For node components
(transistors etc.) the pins sit at the lead tips and the builder includes
leads in the cell bbox.

Pin positions come from **color markers**: the builder's generated `.tex`
places a colored dot (`MARKER_RGB`) on each CircuitTikz anchor
(`pin_anchors`), the rendered SVG is scanned for those exact `rgb(...)`
colors, and each marker's center becomes a pin `s:x/s:y`. That's why
`TO_SKIN_CONFIG` specs list anchors like `west`/`east`/`north` — they're
CircuitTikz anchor names on the rendered component.

---

## The `generic` cell — fallback for unknown components

When `findSkinType(type)` finds no alias, it returns the `generic` template:

```xml
<g s:type="generic" s:class="generic" s:width="30.0" s:height="30.0">
  <s:alias val="generic"/>
  <text x="15.0" y="-4" ... s:attribute="ref">name</text>
  <rect width="30.0" height="30.0" s:generic="body" class="symbol"/>
  <text x="15.0" y="13" ... s:attribute="value">value</text>
  <g s:x="0" s:y="6" s:pid="in0" s:position="left">
    <text x="-5" y="-2" text-anchor="end" class="nodelabel">in0</text>
  </g>
  <g s:x="30.0" s:y="6" s:pid="out0" s:position="right">
    <text x="5" y="-2" text-anchor="start" class="nodelabel">out0</text>
  </g>
  ...
</g>
```

Key differences from normal cells:

1. **Pin IDs use `in`/`out` prefixes**: `in0, in1, ...`, `out0, out1, ...`.
   netlistsvg's `getPortsWithPrefix(template, 'in'/'out')` requires this
   convention for generic cells.

2. **Dynamic size**: `getGenericWidth()/getGenericHeight()` size the cell from
   its label lengths and port count; `setGenericSize()` resizes the
   `rect[s:generic="body"]`, recenters middle-anchored texts, and re-spaces
   the port slots. ELK routes with `FIXED_POS` on these generated ports.

3. **`genericsLaterals` property**: when `true` (our setting), generic cell
   ports are treated as laterals (sideways routing) rather than top/bottom.

4. **Port-name remap**: `convert.py` emits bare numbers (`1`, `2`, ...) for
   unknown components; `Port.getGenericElkPort` assigns them to the `in`/`out`
   slots in order (inputs to `in{i}`, outputs to `out{i}`).

---

## `s:properties` — rendering parameters baked in by build_skin.py

```xml
<s:properties constants="false" splitsAndJoins="false" genericsLaterals="true"
     fontCharWidth="6" fontCharHeight="11"
     fontDescShift="0.3" wireStrokeWidth="0.8"/>
```

Read by `Skin.getProperties()`. These come from `NETLIST_SVG_CONFIG` in
`config.py` — **the skin is the runtime source of truth**, so rebuild the skin
after changing config values.

| Property | Source (config.py) | Effect |
|----------|--------------------|--------|
| `constants` | — (fixed `false`) | Render constant value cells. |
| `splitsAndJoins` | — (fixed `false`) | Render split/join junction dots. (Doesn't matter for our use case for analog circuits.) |
| `genericsLaterals` | — (fixed `true`) | Generic ports route sideways. |
| `fontCharWidth` | `FONT_CHAR_WIDTH` | Per-character advance for label box widths. |
| `fontCharHeight` | `FONT_CHAR_HEIGHT` | Line height (1.1 em); annotation label boxes use this as their height. |
| `fontDescShift` | `FONT_DESC_SHIFT` | Downward shift (fraction of box height) for baseline-anchored label boxes so descenders (`y`, `g`) stay inside. |
| `wireStrokeWidth` | `WIRE_STROKE_WIDTH` | Stroke width of wires drawn between components (matches `.symbol` = `LINE_WIDTH * 2`). |

---

## How the builder fills the template (`build_skin.py`)

For each component in `COMPONENTS`:

1. **`_component_tex`** — generates a standalone CircuitTikz document drawing
   the component at `name=T`, with a colored marker dot (`\fill[red] ...`)
   on every `pin_anchors` / `label_anchors` / `extra_anchors` position.
2. **`_render_circuitikz`** — `pdflatex` → PDF, `pdftocairo` → SVG.
3. **`_parse_render_svg`** — splits paths into **body** (stroke width =
   `LINE_WIDTH * 2`), **leads** (`LINE_WIDTH`), filled shapes (→ body), and
   locates each marker's center by its exact RGB color. Also collects
   CircuitTikz `glyph` defs/uses (reusable text-like subpaths).
4. **Geometry** — cell bbox = union of body paths (plus leads for node
   components, plus every pin marker so pins sit on the cell edge). The body
   transform is translated so the bbox origin is `(0,0)`.
5. **`_pins_from_markers` / `_label_from_markers`** — convert marker centers
   into pin `(pid, position, x, y)` and label `(name, text_anchor, x, y)`
   lists, relative to the cell origin.
6. **`_emit_skin_entry`** — writes the `<g>` template: alias, defs, body
   paths (with adjusted transforms), glyph uses, pins, label slots, and
   `s:type`/`s:class`/`s:width`/`s:height`.

With `--debug <dir>`, `_write_debug_render` copies each CircuitTikz render
into `circuit_data_gen/debug/circuitikz_renders/<skin_type>.svg` stamped with
the computed cell bbox (lime), pin dots (red, labeled with pid), and label
anchor dots (blue) — the fastest way to review a new/changed component.

The class registry (`classes.txt`) is the deduplicated set of every emitted
`annotation_class` (+ `generic`), written when `build_skin` runs with
`--write-classes`.

---

## Summary: what netlistsvg looks for in the skin

1. **`<s:properties>`** — layout options + rendering parameters (font metrics,
   wire stroke, descender shift) baked in from `config.py`.
2. **`<style>`** — CSS for `symbol`/`nodelabel` classes.
3. **`<g s:type="..." s:class="..." s:width="..." s:height="...">`** — one per
   component type:
   - `<s:alias val="..."/>` — **type matching** (`findSkinType` searches this)
   - `<text s:attribute="ref|value">` — label slots (component name / value)
   - `<path class="symbol" d="..."/>` — visual drawing (from CircuitTikz)
   - `<g s:x s:y s:pid s:position/>` — **pin definitions**:
     - `s:pid` must match the Yosys JSON port names
     - `s:position` classifies input (top) / output (bottom) / lateral (left/right)
     - `s:x`/`s:y` fix pin positions for ELK (`FIXED_POS`)
   - `<s:class>` doubles as the annotation class → `classes.txt` registry
4. **`generic` cell** — fallback for unknown types; pins use `in`/`out`
   prefixes; body rect marked `s:generic="body"` for dynamic resizing.

### Why `s:pid` must match the Yosys port names

`Cell.fromYosysCell()` reads the Yosys cell's connections and first tries to
classify each port by matching its name against the template's
input/output pids (`s:position="top"/"bottom"`). Ports that don't match fall
back to the Yosys JSON's `port_directions` hints. A mismatch between the
skin's `s:pid` and `convert.py`'s emitted port names means ports land in the
wrong class — or the whole port falls to the fallback path — so keep
`TO_SKIN_CONFIG`'s `arg_to_ports` aliases and the skin's `s:pid` values in
agreement (both are driven by the same spec).

### Debugging checklist

- Wrong/no drawing → check `circuit_data_gen/debug/circuitikz_renders/<skin_type>.svg`:
  lime box = computed cell, red dots = pins, blue dots = label anchors.
- Port misclassified → compare Yosys JSON `connections` keys with the skin's
  `s:pid` values, and `port_directions` vs `s:position` classification.
- Label boxes off → check `FONT_CHAR_HEIGHT`/`FONT_DESC_SHIFT` vs rendered
  glyphs (see the overlay from `cirdg render --debug`).
