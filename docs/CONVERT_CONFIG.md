# Convert config — SPICE netlist → skin mapping

**File:** `circuit_data_gen/configs/convert/spice_convert_config.py`
**Selected by:** `CONVERT_CONFIG["CONVERT_SPICE_CONFIG_PATH"]` in `configs/config.py`.
**Read by:** `circuit_data_gen/parser/`, through `parser/macro.py`, which loads
the tables below once and normalizes them (see [Letter case](#letter-case)).

The file defines which netlist lines the pipeline accepts and how each one
becomes a skin cell. Its module docstring is a human-readable grammar. The
LLM system prompt (`netlist_gen/prompts/spice_agent_system_prompt.md`) keeps a
hand-written copy of the rules; the component lists of each task are rendered
from this config by `config_to_grammar()` (`parser/grammar.py`), so the two
must agree.

## `CONVERT_CONFIG` (in `config.py`)

| Key | Meaning |
| --- | --- |
| `CONVERT_SPICE_CONFIG_PATH` | Path to the convert config. |
| `STRICT_PARSING` | Default for strict parsing in `render`/`convert` and `gen_data --strict`: reject anything ngspice would misread or refuse. Strict generation also runs the ngspice smoke test when ngspice is found. |

## Concepts

An element line is `<Name> <nodes...> [values] [model name] [kind keyword] [key=value ...]`.
The parser takes the special tokens out of the line first, then matches what
is left (nodes and values) by position:

- **Model name:** a token naming a `.model` card of the netlist, e.g. `QN` in
  `Q1 c b e QN` with `.model QN NPN(BF=100)`. Preprocessing collects every card
  into the *model table* (name → TYPE); at parse time, a token found in the
  table is removed from the line, and its TYPE is the line's **model type**.
  The spec's `model_type` must list it.
- **Kind keyword:** a bare word without a value that picks a variant, e.g.
  `off` in `D1 a b DM off`, or `ON` / `OFF` for a switch. It is removed
  wherever it appears; strict mode requires it right after the model name.
- **Specifier:** a bare keyword that opens a segment with operands, only on V
  and I sources: `DC <value>` and `AC [<mag> [<phase>]]`. The keyword and its
  operands are removed, and the operands go straight into value slots (see
  [Specifiers](#specifiers)).
- **Keyword argument:** a `key=value` token (`Z0=50`, `ic=0`). It fills the
  slot whose `alias` lists the key, or is kept as an unknown instance
  parameter (`unknown_<key>`) with a warning.
- **Nodes and positional values:** what remains, matched by index against
  `arg_to_ports` (nodes) and the positional `args_to_values` slots.

## How a line is parsed

1. **Preprocessing** (`_preprocess_lines`): merges `+` continuations, drops
   comments, and collects the directives: `.model` cards into the model table,
   `.param` values, `.subckt` port lists and bodies, `K` coupling lines. Other
   directives are kept only for the strict check.
2. **Prefix** (`_get_prefix`): the element letter (`R1` → `r`) selects the
   list of specs in `TO_SKIN_CONFIG`. `X`, `A`, `U`, `P`, `N` are *explicit
   generics* (`macro.EXPLICIT_SPICE_GENERICS`): parsed without specs into a
   generic box. For `X`, the pins are named after the `.subckt` ports
   (numbered `1`, `2`, ... when the definition is missing).
3. **Spec matching** (`_match_spec`): **every** spec of the prefix is tried on
   the line, removing the model name, specifiers and kind keywords, then
   matching nodes and values. Each attempt collects errors (the spec cannot
   own the line) and warnings (e.g. an undeclared `key=value`).
4. **Spec selection:** the spec with the fewest errors wins, then the fewest
   warnings, then the earliest in the list. A plain `V1 a 0 5` matches both V
   specs and goes to the DC source because it comes first. When every spec
   has errors, the error lists each spec's grammar line and why it failed;
   this text is fed back to the LLM.
5. **Strict checks** (`strict._strict_violations`, strict mode only). All
   violations are raised together in one error.

## Letter case

SPICE ignores letter case, so `macro.py` lower-cases the config when it loads
it: prefixes (`"r"`), slot aliases, specifier keys, kind keywords, model types
and references. Slot keys keep their spelling (`"AC"`). The model table's keys and TYPEs are lower case too (each card
keeps its written name under `"name"`), and so are `element["prefix"]`,
`element["model_type"]` and `element["model_name"]`. Component names and node
names keep their spelling; nodes are compared ignoring case (`Out` and `out`
are one net). The config file itself may use either case.

## `TO_SKIN_CONFIG`

```python
TO_SKIN_CONFIG = {"<letter>": [<spec>, <spec>, ...], ...}
```

One key per element letter, each a **list** of specs: the variants of that
element (DC vs AC source, NPN vs PNP, 3- vs 4-node BJT, ...).

### Spec fields

| Field | Required | What it is |
| --- | --- | --- |
| `skin_alias` | yes | Skin cell(s) to draw: a list, or `"generic"`. Emitted as the yosys cell `type`; with several entries one is picked at random per render (`["r_h", "r_v"]`). Must match a `skin_type` in the skin config. |
| `arg_to_ports` | yes | `{index: {"alias": port, "drop": bool}}` for the node arguments; see [Nodes](#nodes-arg_to_ports). |
| `args_to_values` | yes (may be `{}`) | Value slots, keyed by **index** (positional) or by **name** (keyword-only); see [Value slots](#value-slots-args_to_values). |
| `port_directions` | yes | `{port: "input" \| "output"}`, copied into the yosys JSON; netlistsvg's fallback when a skin pin has no side. |
| `model_type` | no | The `.model` TYPEs this spec stands for; see [Model types](#model-types-model_type). A spec without it rejects lines that carry a model name. |
| `kind` | no | Bare keywords this spec requires, e.g. `["off"]`, `["on"]`. They must be on the line (any case); a kind keyword of another spec rejects this one. |
| `specifiers` | no | V/I mode segments; see [Specifiers](#specifiers). |
| `parse_only` | no (`False`) | Sampling never asks for this spec; the parser treats it like any other. |

### Nodes (`arg_to_ports`)

The token at `index` (counted after the model name, specifiers, kind keywords
and keyword arguments are removed) connects to pin `alias`, which must match a
skin `pin_ids` entry. Port and positional value indices together must be
consecutive from 0.

`drop: True` makes the node **hidden**: written and electrically connected,
but the skin has no pin for it (the control pair of E/G/S, the far end of
T/O/Y, the bulk of M). Hidden connections are left out of the render and of
the connectivity checks, so their nodes need two other drawn connections.

### Value slots (`args_to_values`)

| Field | Default | Meaning |
| --- | --- | --- |
| `skin_label` | — | Skin label it fills (`"value"`, ...); `None` = parsed but not shown. |
| `alias` | — | Keys accepted in `key=value` form (`["r"]`, `["ac", "acmag"]`). |
| `display_alias` | first `alias` | Placeholder name in the rendered grammar (`<value>`, `<mag>`, `<Vcontrol>`). Required when `alias` is empty. |
| `is_positional` | `"positional"` | Where and how the value is written; see the next table. |
| `is_optional` | `True` | `False` = the line must provide it. A required keyword slot also tells specs apart (B `V=` vs `I=`). |
| `default_value` | — | Used when an optional slot is left out. |
| `is_expression` | `False` | The value is an expression ngspice only reads in braces or quotes (`Q={2n}`, `vol={2*v(a)}`); strict mode rejects it unwrapped. The grammar shows the braces. |
| `functions` | — | Marks the transient-function slot of V/I: the function names it accepts (`sin`, `pulse`, `pwl`, ...). A `SIN(...)` token goes here, next to the DC value; at most one per line, after any bare DC value. |
| `is_reference` | — | The token must name an element of that letter (`"V"` for F/H/W's controlling source). Checked in strict mode; sampling adds a V source whenever such an element is drawn. |

`is_positional` modes, all checked against ngspice 47:

| Mode | Written as | Example |
| --- | --- | --- |
| `"positional"` | At its index, bare or as its own `key=value` there. | `L1 a b 1m`, `L1 a b l=1m`; not `L1 a b m=1 l=1m` |
| `"positional_only"` | At its index, bare only. | F/H/W `<Vcontrol>`: `F1 a b V1 2` |
| `"positional_or_keyword"` | Bare at its index, or its `key=value` anywhere after the positional part. | `R1 a b 1k`, `R1 a b tc1=0 r=1k` |
| `"positional_with_key"` | At its index, and only as `key=value`. | B `V=`/`I=`, E `vol=`, G `cur=`: ngspice rejects a bare value and `B1 a b tc1=0 V=2` |
| `"keyword"` | `key=value` anywhere after the positional part, never bare. | T `Z0=50`, C `Q={2n}`, V/I `ac=1` |

A `key=value` whose key no slot lists is kept as `unknown_<key>` with a
warning. Instance-parameter names are not checked yet, so a wrong key only
fails in ngspice.

### Model types (`model_type`)

Either a list of TYPEs (`["npn"]`, `["r", "res"]`), or a dict
`{TYPE: {"ref_arg": i, "before_arg": bool}}` when ngspice reads the model name
on only one side of argument `i`:

| Prefix | Rule | Valid | Rejected by ngspice |
| --- | --- | --- | --- |
| C, L, Y | before the value (`before_arg: True`) | `C1 a b CM 1u` | `C1 a b 1u CM` |
| W | after the controlling source (`before_arg: False`) | `W1 a b V1 WM` | `W1 a b WM V1` |

With `ref_arg`, the grammar prints `mname` on that side, and strict parsing
rejects the other side; lenient parsing accepts both. In every spec, the model
name must come after all the nodes (`Q1 a b c QN 2` is 3 nodes and an area,
never 4 nodes), and strict mode also requires it before any `key=value`
argument.

### Specifiers

```python
"specifiers": {
    "DC": {"refs": [2], "is_optional": True},
    "AC": {"refs": ["AC", "phase"], "is_optional": True, "default": "1"},
}
```

- `refs` are the slots the operands fill, in order: an int is a positional
  slot, a str a keyword slot. `DC 5` fills slot 2, so it is another spelling of
  the bare `5`; `AC 1 90` fills the `AC` (magnitude) and `phase` slots.
- `_read_specifiers` removes each keyword with its operands; a segment ends
  after its last operand, or earlier at a `key=value`, a kind keyword or
  another specifier keyword. Missing operands take the slot's default, and a
  bare keyword takes the specifier's `default` (`AC` = `AC 1`). Segments are
  independent: `DC 5 AC 1` = `AC 1 DC 5`. The same specifier twice is an error.
- Specifiers also select the spec: one with `is_optional: False` must be on
  the line, and a specifier declared only by another spec of the prefix
  rejects this one. `AC` is declared only by the AC-source spec, so
  `V1 a 0 AC 1` can only be drawn as an AC source.
- `element["specifiers"]` lists the specifiers the matched spec declares, not
  the ones written on the line; sampling uses it to tell DC and AC sources
  apart.

## `MODEL_CONFIG`

`.model` TYPE → list of accepted card **configurations**; a card is valid when
one configuration fits (`strict._match_model_card`, strict mode only). Every
`model_type` used in `TO_SKIN_CONFIG` must be a key here.

| Field | Meaning |
| --- | --- |
| `args_to_values` | Card parameters, keyed by canonical name: `alias`, `is_optional` (default `True`), `default_value`, and `is_zero` (must be 0 or absent, e.g. LTRA's `G`). Most TYPEs declare none: ngspice defaults them. |
| `variant` | Label used in error messages when a TYPE has several configurations. |

`model_config_to_grammar()` renders these as `.model` templates for prompts.

## Other tables

| Name | Where | Meaning |
| --- | --- | --- |
| `EXPRESSION_CONSTANTS` | the config | Names allowed in `{expressions}` without a `.param` (`pi`, `time`, ...). Checked by strict parsing. |
| `GROUND_NAMES` | `parser/macro.py` | `0` and `gnd`, compared ignoring case. |
| `EXPLICIT_SPICE_GENERICS` | `parser/macro.py` | `x`, `a`, `u`, `p`, `n`. Only `X` is in the grammar and in sampling. |
| `COUPLED_INDUCTOR_PREFIXES` | `parser/macro.py` | `k`: K lines are read in preprocessing; they are not components and are not drawn. |

## Example: `Q1 c b e 0 Qp off` with `.model Qp PNP`

1. Prefix `q`; the model table gives `qp → pnp`.
2. The NPN specs fail (wrong model type). Of the PNP ones, the 3-node specs
   fail (one argument too many) and the one without `kind` fails (unexpected
   `off`).
3. The 4-node PNP spec with `kind: ["off"]` wins: `c`, `b`, `e` connect, `0`
   goes to the hidden substrate pin, and `Qp` and `off` are consumed.
4. Element: `prefix = "q"`, `model_type = ["pnp"]`, `model_name = "qp"`,
   `kind = ["off"]`. Cell: `type = "q_pnp"`, pins `c`, `b`, `e` drawn.

## Adding a component

1. Add the skin (`SKIN_STRUCTURE.md`) and build it.
2. Add a spec here: `skin_alias` = the skin's `skin_type`, `arg_to_ports`
   aliases = its `pin_ids`, `skin_label`s = its label names.
3. If the element needs a model, add its TYPE to `MODEL_CONFIG`.
4. Update the grammar docstring and the rule list in the system prompt, and
   check the rendered grammar (`config_to_grammar()`) against ngspice 47.
5. Check: `pytest tests/test_convert_parsing.py tests/test_convert_strict.py tests/test_sampling.py`.

## Current limitations

- **Instance-parameter names are not checked:** an undeclared `key=value` only
  warns. ngspice rejects most wrong keys (`L1 b 0 Lm L=1m`), but TXL ignores
  them silently.
- **Spec selection is not guaranteed unique:** ties are broken by warnings,
  then by order in the list.
- **Unsupported forms:** E/G `value=`, `POLY`, `TABLE`; `VDMOS`; `$` comments;
  the `A`/`U`/`P`/`N` definitions.
- **A node named like a model** is read as the model name.
- **Error messages quote lower-cased** prefixes and model names.
