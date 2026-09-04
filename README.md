# Circuit Detection Data Generation

This repository contains a tool for generating circuit data: **netlists**, **rendered schematics** (SVG/PNG/JPG), and **annotations** (bounding boxes of components and labels, exact pin coordinates). The pipeline is:

1. Generate a random netlist using an LLM. The netlist is in a simplified [lcapy](https://github.com/mph-/lcapy) styled format (following `grammar.py` in lcapy), simplified as in supporting only a subset of components, and not reading lcapy hints (arguments after `;`). To see which components are supported, read the system prompt in `circuit_data_gen/netlist_gen/prompts/agent_system_prompt.md`.
2. Validate the candidate with a smoke test: the netlist must parse, render, contain the required components, and form a single electrically-connected graph. Invalid candidates are fed back to the LLM with the error messages for regeneration.
3. Render the validated netlist to a schematic using our [netlistsvg fork](https://github.com/Cab14bacc/netlistsvg). netlistsvg lays out the netlist with ELK (a multipurpose graph layout algorithm) and draws components from a **skin file** (SVG). The default skin is `dataset/skins/lcapy.svg`, built by `circuit_data_gen/build_skin.py`.
4. Emit structured **annotations** alongside the render: component bounding boxes, nested label (ref/value) boxes, and exact pin coordinates — all in output-image coordinates.

## Prerequisites

- **Python >= 3.12**
- **Node.js** (for netlistsvg)
- **Poppler** for `pdftocairo` — used by the skin builder to rasterize LaTeX output. Linux (Ubuntu/Debian):

  ```bash
  sudo apt update
  sudo apt install poppler-utils
  ```

  Windows/macOS: precompiled binaries from the [poppler website](https://poppler.freedesktop.org/).

- **MiKTeX** for `pdflatex` — used by the skin builder to draw each component with CircuitTikZ. Follow the instructions on the [MiKTeX website](https://miktex.org/download). The `circuitikz` package is installed automatically: the MiKTeX console pops up and prompts for it the first time `build_skin` runs.

## Installation

```bash
# clone with submodules, we use the netlistsvg fork from
# https://github.com/Cab14bacc/netlistsvg.git
git clone --recurse-submodules https://github.com/Cab14bacc/Circuit-Detection-Data-Generation.git
cd Circuit-Detection-Data-Generation
pip install -e .

# install the netlistsvg fork's node dependencies (the renderer runs via node)
cd netlistsvg
npm install
cd ..
```

This exposes the `cirdg` (circuit data generation) command.

### Environment Variables

Create a `.env` file in the root directory (or export the variables). These are needed for the LLM that generates netlists. Any OpenAI-compatible endpoint works (we use LangChain's `ChatOpenAI`):

```env
CIRDG_API_KEY=<API key to model endpoint>
CIRDG_MODEL_NAME=<Model name to model endpoint>
CIRDG_BASE_URL=<Model endpoint base url>
```

## Quick Start

Using the default output directories should be easier. All outputs should fall under `dataset/` (configurable in `circuit_data_gen/configs/config.py`).

### 1. Generate random circuit data

```bash
cirdg gen_data -n 5
```

Generates 5 random circuits into `dataset/projects/project_<yymmdd>_<hhmmss>/` — a fresh timestamped project per run. Useful options (all in `cirdg gen_data --help`):

```bash
cirdg gen_data -n 20 -c 4                   # 20 samples, 4 parallel LLM calls
cirdg gen_data -n 20 --gen-seed myseed      # reproducible batch (seeded prompts)
cirdg gen_data -n 10 -cmin 8 -cmax 15       # component-count range
cirdg gen_data -n 5 -o path/to/out -p myproj  # custom output root + project name
```

Each generation worker:

- samples a random component-count target and a random subset of **required** component types (seeded by `--gen-seed`, so batches are reproducible),
- asks the LLM for a netlist inside `<netlist>...</netlist>` tags,
- smoke-tests it: parse → render → validate (min components, required components present, no hanging nodes, single connected graph),
- on failure, feeds the error messages back to the LLM for regeneration.

Per-worker logs (Initial seed prompt, LLM responses, validation errors) are written to `circuit_data_gen/debug/netlist_gen_logs/<project_name>/debug_<gen_seed>_<worker>.log` (the initial system prompt is not logged).

### 2. Build the skin (once per dataset)

```bash
cirdg build_skin --all --write-classes
```

This renders every supported component via CircuitTikZ and emits the skin to `dataset/skins/lcapy.svg`. `--write-classes` also writes the shared class registry `dataset/classes.txt` (one annotation class per skin type). Renders validate their components against this registry, so it must exist before rendering.

### 3. Render an existing lcapy-styled netlist

```bash
cirdg render path/to/netlist.net path/to/out.png --annotation path/to/ann --debug path/to/debug
```

## CLI Reference

| Command | Purpose |
| --- | --- |
| `cirdg gen_data` | Full pipeline: LLM generation → validation → rendering. |
| `cirdg render <in> [out]` | Render a netlist file or a directory of them (`*.net`, `*.sch`). |
| `cirdg build_skin [name] --all [--write-classes]` | Build the skin SVG from the CircuitTikz component specs. |
| `cirdg convert <in> [out]` | Convert lcapy-style netlist(s) to yosys JSON without rendering. |

## Annotation Format

`--annotation <dir>` writes one JSON per netlist. All absolute coordinates are in coordinates corresponding to the output format: SVG user units for `.svg`, pixels for `.png`/`.jpg` (pixels = SVG units × scale). The `*Norm` fields are normalized to the canvas [0, 1] and are resolution-independent.

```jsonc
{
  "image": "render.json",          // path of the intermediate yosys json
  "canvas": { "width": 463, "height": 421 },  // output canvas in px (or svg units)
  "classes": ["vsource", "resistor", "..."],  // class registry snapshot
  "components": [
    {
      "key": "R1",                 // component reference
      "class": "resistor",         // annotation class (from classes.txt)
      "bbox":     { "x": 79.1, "y": 51.0, "w": 11.7, "h": 32.0 },
      "bboxNorm": { "x": 0.17, "y": 0.12, "w": 0.025, "h": 0.076 },
      "labels": [                  // nested label boxes
        { "attr": "ref",   "text": "R1", "bbox": {...}, "bboxNorm": {...} },
        { "attr": "value", "text": "1k", "bbox": {...}, "bboxNorm": {...} }
      ],
      "pins": [                    // exact connection points (not boxes)
        { "pid": "+", "x": 84.9, "y": 51.0, "xNorm": 0.183, "yNorm": 0.121,
          "text": null, "textBbox": null }
      ]
    }
  ]
}
```

Notes:

- Pins are exact points (`x/y`); only generic cells carry pin *name* text (`text`, `textBbox`).
- `textBbox` for pin names and label boxes account for the font's cap height and a descender shift, so boxes hug the rendered glyphs (see `FONT_*` config below).
- The overlay PNGs written during `gen_data` (or via `--debug`) visualize the annotations on the render: **blue** = component boxes, **orange** = ref labels, **purple** = value labels, **pink** = pin points/pin text.
- Skin to netlist components is currently NOT a one-to-one mapping, some components correspond to the same skin. The classes in the annotation follows the skin, so the same class may correspond to multiple netlist components (e.g. `vcvs` and `ccvs` (defined by "E" prefix) both map to the same "controlled voltage source" skin, and thus the same annotation class). `dataset/classes.txt` contains the unique skin classes.

## Centralized Configuration

All tunable parameters live in `circuit_data_gen/configs/config.py`, accessed via `get_config_value(section, dotted_key)` (case-insensitive, e.g. `get_config_value("netlistsvg", "font_char_width")`). For example, to access `"cli.render.default_render_dir"`, write `get_config_value("cli", "render.default_render_dir")`. The sections are:

- **`build_skin`** — `LINE_WIDTH` (CircuitTikz line width; the skin's symbol stroke is `LINE_WIDTH * 2`), `SKIN_PATH` (where the skin is emitted).
- **`netlistsvg`** — everything the renderer needs: `BIN_PATH` (node entry point), `SKIN_PATH` (shared skin), label font metrics (`FONT_SIZE`, `FONT_CHAR_WIDTH`, `FONT_CHAR_HEIGHT`, `FONT_CAP_HEIGHT`, `FONT_DESC_SHIFT`), `WIRE_STROKE_WIDTH`, and `ANNOTATION.CLASSES_PATH`.
- **`cli`** — default output dirs for each command.
- **`convert`** — path to `TO_SKIN_CONFIG` (the per-component skin mapping: lcapy prefix → skin alias, ports, value specs, drop rules).

Font metrics and stroke widths are **emitted into the skin** as `<s:properties ...>` attributes by `build_skin.py`; netlistsvg reads them from there at render time. The skin is the single runtime source of truth — after changing `config.py`, rebuild with `cirdg build_skin --all` or the new values won't take effect.

## Package Structure

```ascii
└── Circuit-Detection-Data-Generation/         # Project root
    ├── circuit_data_gen/                      # The package source
    │   ├── build_skin.py                      # Skin svg builder using latex package CircuitTikz
    │   ├── validate.py                        # hanging-node + connectivity checks
    │   ├── render_netlist.py                  # A python interface to the netlistsvg
    │   ├── convert.py                         # lcapy-style netlist -> yosys JSON
    │   ├── convert_old.py                     # Spice netlist -> yosys JSON (not guranteed to work, kept for reference)
    │   ├── cli.py                             # Interface to package, typer CLI (defines the cirdg command)
    │   ├── configs/
    │   │   ├── config.py                      # centralized config, contains most parameters for the package
    │   │   ├── convert/default_convert_config.py
    │   │   ├── logging/default_logging_config.py
    │   │   └── skins/default_skin_config.py
    │   ├── debug/                             # logs
    │   │   ├── circuitikz_renders/
    │   │   ├── netlist_gen_logs/<project>/debug_<gen_seed>_<worker>.log
    │   │   └── app.log
    │   └── netlist_gen/                       # LLM generation pipeline
    │       ├── prompts/agent_system_prompt.md # supported components + netlist grammar
    │       ├── setup.py                       # LangGraph generate -> validate loop
    │       └── parallel.py                    # batch workers + requirements sampling
    ├── tests/                                 # pytest suite (one file per pipeline stage)
    ├── dataset/                               # default dataset dir (configurable)
    │   ├── classes.txt                        # shared annotation-class registry
    │   ├── projects/project_<yymmdd>_<hhmmss>/
    │   │   ├── netlists/netlist_<id>.net      # validated SPICE-style netlists
    │   │   ├── schematics/
    │   │   |   ├── schematic_<id>.png         # rendered schematics
    │   │   |   └── yosys_<id>.json            # yosys JSON output (intermediate format from convert.py)
    │   │   └── annotations/
    │   │       ├── annotation_<id>.json       # structured annotations
    │   │       └── overlay_<id>.png           # debug overlay (boxes on render)
    │   ├── skins/lcapy.svg                    # the built skin
    │   └── renders_default/                   # default `cirdg render` output
    ├── netlistsvg/                            # netlistsvg fork (git submodule)
    │   ├── bin/netlistsvg.js                  # CLI entry (called by render_netlist)
    │   ├── bin/overlay.js                     # annotation overlay tool (plain JS)
    │   ├── lib/*.ts                           # TypeScript source
    │   └── built/                             # compiled JS (what node actually runs)
    └── pytest.ini
```

### Dataset concept

A **dataset** is the `dataset/` directory (relocate via `DATASET_DIR` in `config.py`). Everything a model consumes is scoped to it: the skin (`dataset/skins/lcapy.svg`), the class registry (`dataset/classes.txt`), and the generated `projects/`. One skin per dataset: the skin defines which components exist and how they render, and `classes.txt` is derived from it — so all projects in a dataset share one component vocabulary. If you regenerate the skin, the vocabulary changes; rebuild it (`cirdg build_skin --all --write-classes`) before generating more data, and keep skin + registry consistent per dataset.

## The netlistsvg Fork

`netlistsvg/` is a fork of [nturley/netlistsvg](https://github.com/nturley/netlistsvg) with these additions:

- **Annotation output** (`--annotation out.json`): component/label boxes and exact pin points in output coordinates.
- **Raster output** (`--format png|jpg`, `--scale`) with annotations emitted in raster pixel space.
- **Other rendering parameters**: `<s:properties>` carries font metrics, wire stroke width, and the label descender shift, all baked in by `build_skin.py`.

### Rebuilding netlistsvg

The CLI runs the **compiled** output (`built/`), after editing `netlistsvg/lib/*.ts`:

```bash
cd netlistsvg
npx tsc
```

## Development

### Tests

```bash
# run the test suite (one file per pipeline stage; needs pytest + pytest-asyncio)
python -m pytest tests/
```

### Linter

```bash
# lint (line length 110)
ruff check 
# or ruff check --fix
ruff format
```

### Other notes

- Some component ports are **dropped** when the skin can't render them (e.g. a VCVS's control inputs on a bipole symbol). Dropped connections don't count for connectivity/hanging checks — mirroring what actually appears in the schematic.
- After changing any `netlistsvg` config value, rebuild the skin (`cirdg build_skin --all`); after changing `netlistsvg/lib/*.ts`, rebuild the JS (`npx tsc`).
