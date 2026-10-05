# Convert config — SPICE netlist → skin mapping

**File:** `circuit_data_gen/configs/convert/spice_convert_config.py`
(the lcapy dialect uses `lcapy_convert_config.py` with the same spec format,
minus the SPICE-only keys).
**Selected by:** `CONVERT_CONFIG` in `configs/config.py`.
**Read by:** `circuit_data_gen/parser/` (through `dialect.py`, which loads the
tables below once and is reloaded by the tests to switch dialect).

The file defines which netlist lines the pipeline accepts and how each one
becomes a skin cell. Its module docstring is a human-readable grammar; the LLM
prompts under `netlist_gen/prompts/` are kept in sync with it by hand.

## `CONVERT_CONFIG` (in `config.py`)

| Key | Meaning |
| --- | --- |
| `NETLIST_FORMAT` | `"spice"` or `"lcapy"`: which dialect and convert config are loaded. |
| `CONVERT_SPICE_CONFIG_PATH`, `CONVERT_LCAPY_CONFIG_PATH` | Paths to the two convert configs. |
| `STRICT_PARSING` | Default for strict parsing in `render`/`convert` (and `gen_data --strict`): reject anything ngspice would misread or refuse. `--simulate` always parses strictly. |

## How a line is parsed

1. **Preprocessing** (`_preprocess_lines`): merges `+` continuations, drops
   comments, and collects the directives: `.model` cards into the model table,
   `.param` values, `.subckt` port lists and bodies, `K` coupling lines. Other
   directives are kept only for the strict check.
2. **Prefix** (`_get_prefix`): the element letter (`R1` → `R`) selects the
   list of specs in `TO_SKIN_CONFIG`. `X`, `A`, `U`, `P`, `N` are *explicit
   generics* (`dialect.EXPLICIT_SPICE_GENERICS`): parsed without specs into a
   generic box whose pins are all nodes before the definition name.
3. **Spec matching** (`_match_spec`): **every** spec of the prefix is tried; the
   one with the fewest errors wins. When none matches, the error lists each
   spec's grammar line and why it failed (this text is fed back to the LLM).
4. **Strict checks** (`strict._strict_violations`, strict mode only), and with
   `simulate` also the "cannot be simulated" check. All violations are raised
   together in one error.

## `TO_SKIN_CONFIG`

```python
TO_SKIN_CONFIG = {"<letter>": [<spec>, <spec>, ...], ...}
```

One key per element letter, each a **list** of specs: the variants of that
element (DC vs AC source, NPN vs PNP, 3- vs 4-node BJT, ...).

### Spec fields

| Field | Required | What it is | How the parser uses it |
| --- | --- | --- | --- |
| `skin_alias` | yes | Skin cell(s) to draw: a list, or `"generic"`. | Emitted as the yosys cell `type`; with several entries one is picked at random per render (`["r_h", "r_v"]`). Must match a `skin_type` in the skin config. |
| `arg_to_ports` | yes | `{index: {"alias": port, "drop": bool}}` for the node arguments. | The token at `index` (counted after model name, kind keywords and keyword arguments are removed) connects to pin `alias`, which must match a skin `pin_ids` entry. `drop: True` = the node is written and connected but has no pin (hidden, e.g. control pairs): excluded from the render and from connectivity checks. |
| `args_to_values` | yes (may be `{}`) | Value slots, keyed by **index** (positional) or by **name** (keyword-only). | Fills the cell's labels; see the slot fields below. |
| `port_directions` | yes | `{port: "input" \| "output"}`. | Copied into the yosys JSON; netlistsvg's fallback when a skin pin has no side. |
| `model_type` | no | `.model` TYPEs this spec stands for: a list (`["NPN"]`), or a dict `{TYPE: {"ref_arg": i, "before_arg": bool}}` when ngspice reads the model name only on one side of argument `i` (C, L, Y: before the value, `C1 a b CM 1u`; W: after the controlling source, `W1 a b V1 WM`). | The line's model name is looked up in the model table and its TYPE must be listed here. A spec without `model_type` rejects lines that carry a model name. The model name token is consumed (not a node, not a value). With `ref_arg`, the grammar prints `mname` on that side of the argument, and strict parsing rejects the other side (ngspice 32 to 47 cannot parse it); lenient parsing accepts both. |
| `kind` | no | Bare keywords this spec requires, e.g. `["off"]`, `["ON"]`. | Must be present on the line (any case), and keywords belonging to other specs are rejected. Strict parsing requires them after the model name. |
| `specifiers` | no | V/I mode segments: `{"DC": {"refs": [2], "is_optional": True}, "AC": {"refs": ["AC", "phase"], "is_optional": False, "default": "1"}}`. | `_expand_specifiers` rewrites `DC 5 AC 1 90` into slot values before matching. `refs` are the slots the operands fill (int = positional slot, str = keyword slot); `default` is used for a bare keyword (`AC` = `AC 1`). A required specifier (`is_optional: False`) selects the spec; a specifier declared only by another spec rejects it (DC vs AC source). |
| `simulatable` | no (`True`) | `False` when ngspice cannot run this spec (`N`, `P`, `A`, `U`). | With strict parsing and `simulate`, such lines are rejected; `build_pool(simulate=True)` leaves them out of sampling. |
| `parse_only` | no (`False`) | Recognised but not drawn. | The element is parsed and validated but emitted into no cell. |

### Value slot fields (`args_to_values` entries)

| Field | Default | Meaning |
| --- | --- | --- |
| `skin_label` | — | Skin label it fills (`"value"`, `"vcontrol"`, ...); `None` = parsed but not shown. |
| `alias` | — | Names accepted in `key=value` form (`["Value", "r", "R"]`). |
| `is_positional` | `"positional"` | Where the value is written. `"positional"`: at its index, as a bare value or in its own `key=value` spelling there (`R1 a b 1k`, `R1 a b R=1k`). `"positional_only"`: at its index, bare only (`L1 a b LM 1m`; ngspice rejects `L1 a b LM L=1m`). `"keyword"`: keyword-only (`V=`, `Z0=`, `AC=`), never taken from a bare token. |
| `is_optional` | `True` | `False` = the line must provide it (a keyword-only required slot also discriminates specs, e.g. B `V=` vs `I=`). |
| `default_value` | — | Used when an optional slot is left out. |
| `is_reference` | — | The token must name an element of that letter (`"V"` for F/H/W's controlling source). Checked in strict mode; sampling adds a `V` whenever such an element is drawn. |

## `MODEL_CONFIG`

`.model` TYPE → list of accepted card **configurations**; a card is valid when
one configuration fits (`strict._match_model_card`, strict mode only). Every
`model_type` used in `TO_SKIN_CONFIG` must be a key here.

| Field | Meaning |
| --- | --- |
| `args_to_values` | Card parameters, keyed by canonical name: `alias`, `is_optional` (default `True`), `default_value`, and `is_zero` (must be 0 or absent, e.g. LTRA's `G`). Most TYPEs declare none: ngspice defaults them. |
| `variant` | Label used in error messages when a TYPE has several configurations. |
| `simulatable` | `False` when ngspice cannot run this TYPE (`LPNP`): rejected when simulating, left out of sampling. |

`model_config_to_grammar()` renders these as `.model` templates for prompts.

## Other tables in the file

| Name | Meaning |
| --- | --- |
| `EXPRESSION_CONSTANTS` | Names allowed in `{expressions}` without a `.param` (`pi`, `time`, ...). Checked by strict parsing. |
| `SIMULATION_ANCHORS` | Groups of component samples, one of which every simulated netlist is *allowed* (a V/I source, a plain `R`); used by `sampling.sample_requirements`. Each group is `{"any_of": [matcher, ...]}` with matcher fields `prefix`, `model_type`, `kind`, `specifiers`. |

Fixed in `parser/dialect.py`, not in the config: `GROUND_NAMES` (`0`, `GND`,
`gnd`), `EXPLICIT_SPICE_GENERICS`, `COUPLED_INDUCTOR_PREFIXES` (`K`).

## Example: `Q1 c b e 0 Qp off` with `.model Qp PNP`

1. Prefix `Q`; the model table gives `QP → PNP`.
2. Specs with `model_type` NPN/LPNP fail; of the PNP ones, the 3-node specs fail
   (too many nodes) and the one without `kind` fails (unexpected `off`).
3. The 4-node PNP spec with `kind: ["off"]` wins: `c/b/e` connect, `0` goes to
   the dropped substrate pin, `Qp` and `off` are consumed.
4. Cell: `type = "q_pnp"`, pins `b`, `c`, `e`.

## Adding a component

1. Add the skin (`SKIN_CONFIG.md`) and build it.
2. Add a spec here: `skin_alias` = the skin's `skin_type`, `arg_to_ports` aliases
   = its `pin_ids`, `skin_label`s = its label names.
3. If the element needs a model, add its TYPE to `MODEL_CONFIG`.
4. Update the grammar docstring and the prompts' rule lists.
5. Check: `pytest tests/test_convert_parsing.py tests/test_convert_strict.py tests/test_sampling.py`.
