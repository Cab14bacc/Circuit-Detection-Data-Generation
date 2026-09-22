"""Grammar rendering: TO_SKIN_CONFIG -> human-readable grammar listing
(used for the LLM grammar docs and for the parse errors fed back to it)."""

from . import dialect


def _render_slots(arg_to_ports: dict) -> list[str]:
    """Positional port placeholders for one spec, sorted by index.

    Node placeholders are capitalised and N-prefixed (grammar convention:
    N* = node): `+`/`-` render as N+/N-, existing N-aliases (ns) render as
    N-prefixed, other aliases get an N prefix. Dropped (hidden) ports are
    still required tokens in the element line — only the skin pin is
    missing — so they render as plain node placeholders, never optional and
    never in curly braces (braced node tokens are invalid SPICE).
    """
    slots = []
    for idx in sorted(arg_to_ports.keys(), key=int):
        alias = arg_to_ports[idx].get("alias", f"arg{idx}")
        if alias in ("+", "-"):
            node_name = f"N{alias}"
        elif alias.upper().startswith("N"):
            node_name = alias.upper()  # already a node alias (ns → NS)
        else:
            node_name = f"N{alias[0].upper()}{alias[1:]}"
        slots.append(node_name)
    return slots


def _hidden_slots(arg_to_ports: dict) -> list[str]:
    """The placeholders (as rendered by _render_slots) of the dropped ports:
    written and connected on the line, but not drawn by the skin."""
    rendered = _render_slots(arg_to_ports)
    ordered = sorted(arg_to_ports.keys(), key=int)
    return [slot for idx, slot in zip(ordered, rendered) if arg_to_ports[idx].get("drop", False)]


def _render_value_slot(entry: dict, idx) -> str | None:
    """Render ONE value/keyword slot as its grammar token.

    A positional slot is a VALUE: the token written on the line IS the
    value (`1k`), so the placeholder is shown in angle brackets — `<r>`
    required, `[<C>]` optional. A silent REQUIRED positional slot
    (consumed token like F/W's Vcontrol, skin_label None) also renders
    `<alias>` — dropping it would make the rendered grammar unparseable.

    A keyword-only slot (is_positional False) is written in Key=<value>
    form: required ones unbracketed (`V=<value>`, `Z0=<value>`), optional
    ones as `[Key=<value>|Key2=<value>]` showing every accepted alias.

    Returns None when the slot has nothing writable (no alias, no label).
    """
    aliases = entry.get("alias", [])
    skin_label = entry.get("skin_label", None)
    is_optional = entry.get("is_optional", True)
    first = aliases[0] if aliases else skin_label
    if first is None:
        return None
    if entry.get("is_positional", True):
        return f"[<{first}>]" if is_optional else f"<{first}>"
    if not is_optional:
        return f"{first}=<value>"
    if not aliases:
        return None
    return "[" + "|".join(f"{a}=<value>" for a in aliases) + "]"


def _render_specifiers(spec: dict) -> list[str]:
    """Specifier placeholders: `[DC [<Value>]] AC [<AC>] [<phase>]`.

    Each specifier renders as the bare keyword followed by one bracketed
    placeholder per ref. A str ref renders just the ref key: the expander
    emits `<ref>=<operand>`, so the ref key is the only spelling that
    reaches the slot (other aliases are never produced). Positional (int)
    refs render their slot's first alias as the operand name.

    A specifier whose SINGLE ref is an int is an alternative spelling of
    that positional slot (DC 5 == bare 5); it is rendered as a slot
    alternation by the caller and skipped here.

    A specifier with `is_optional: False` (the AC segment of the
    AC-source spec) is REQUIRED on the line and therefore renders
    UNBRACKETED; optional ones keep the surrounding `[...]`.
    """
    specifiers = spec.get("specifiers", {})
    if not specifiers:
        return []
    arg_specs = spec.get("args_to_values", {})
    parts = []
    for sp_key, sp_def in specifiers.items():
        refs = sp_def.get("refs", [])
        if len(refs) == 1 and isinstance(refs[0], int):
            continue  # rendered as a slot alternation by the caller
        seg = [sp_key]
        for ref in refs:
            if isinstance(ref, int):
                aliases = arg_specs.get(ref, {}).get("alias", [])
                seg.append(f"[<{aliases[0] if aliases else 'value'}>]")
            else:
                seg.append(f"[<{ref}>]")
        rendered = " ".join(seg)
        if sp_def.get("is_optional", False):
            parts.append(f"[{rendered}]")
        else:
            parts.append(rendered)
    return parts


def _render_spec_grammar(
    prefix: str,
    spec: dict,
    show_skin: bool = False,
    mark_dropped: bool = False,
) -> str:
    """Render one spec as a single `format:`-style grammar string.

    With `mark_dropped` a trailing ` , hidden:<slots>` annotation lists the
    dropped port slots (e.g. ` , hidden:NC+ NC-`); with `show_skin` the
    trailing ` , skin:<skin_alias>` annotation is appended after it.
    """
    parts = [f"{prefix}name"]

    # interleaving: the consecutive-index guarantee means port slots and
    # positional value slots share one index space — merge them by index.
    indexed: dict[int, str] = {}
    # specifiers whose operand binds a positional slot by INDEX are
    # alternative spellings of that slot (DC VALUE == bare VALUE); they are
    # rendered as an alternation on the slot itself, not as a segment.
    alternations: dict[int, str] = {}
    for sp_key, sp_def in spec.get("specifiers", {}).items():
        refs = sp_def.get("refs", [])
        if len(refs) == 1 and isinstance(refs[0], int):
            alternations[refs[0]] = sp_key
    port_slots = _render_slots(spec.get("arg_to_ports", {}))
    for idx_str, alias in zip(
        sorted(spec.get("arg_to_ports", {}).keys(), key=int),
        port_slots,
    ):
        indexed[int(idx_str)] = alias
    for idx_str, entry in sorted(
        ((k, v) for k, v in spec.get("args_to_values", {}).items() if str(k).isdigit()),
        key=lambda kv: int(kv[0]),
    ):
        # positional value slots occupy their index (required slots render
        # bare, optional ones bracketed). Keyword-only slots are appended
        # after all positional content instead.
        if not entry.get("is_positional", True):
            continue
        slot = _render_value_slot(entry, idx_str)
        if slot is None:
            continue
        first = (entry.get("alias") or [None])[0]
        alt_key = alternations.get(int(idx_str))
        if alt_key and first:
            # a specifier whose single operand binds THIS positional slot by
            # index is an alternative SPELLING of the same value rather than
            # a separate keyword (DC 5 == 5). Render the slot once, as an
            # alternation, instead of printing the value slot twice.
            slot = f"[<{first}>|{alt_key} <{first}>]"
        indexed[int(idx_str)] = slot

    for idx in sorted(indexed.keys()):
        parts.append(indexed[idx])

    # keyword-only value slots (is_positional False, any key type) go last
    # in declaration order. Slots that a specifier references are EXCLUDED
    # here: they are binding targets for the segment expansion, not
    # user-facing spellings — the specifier renderer below already prints
    # them (with operand arity).
    spec_bound = {
        ref
        for sp_def in spec.get("specifiers", {}).values()
        for ref in sp_def.get("refs", [])
        if isinstance(ref, str)
    }
    for k, entry in spec.get("args_to_values", {}).items():
        if str(k).isdigit() and entry.get("is_positional", True):
            continue  # already interleaved by index above
        if k in spec_bound:
            continue
        slot = _render_value_slot(entry, k)
        if slot is not None:
            parts.append(slot)

    # declared specifiers render as optional bare-keyword segments
    parts.extend(_render_specifiers(spec))

    # ngspice order: the kind keyword (off, ON/OFF) comes AFTER the model name
    if spec.get("model_type"):
        parts.append("mname")
    if spec.get("kind"):
        parts.append("|".join(spec["kind"]))

    pattern = " ".join(parts)
    if mark_dropped:
        hidden = _hidden_slots(spec.get("arg_to_ports", {}))
        if hidden:
            pattern = f"{pattern} , hidden:{' '.join(hidden)}"
    if show_skin:
        skin_alias = spec.get("skin_alias", None)
        if skin_alias is None:
            skin = "none (wire/parse-only)"
        elif isinstance(skin_alias, list):
            # lcapy specs alternate skin variants (r_h|r_v); random.choice
            # picks one at render time, so the grammar shows the full set.
            skin = "|".join(str(s) for s in skin_alias)
        else:
            skin = str(skin_alias)
        pattern = f"{pattern} , skin:{skin}"
    return pattern


def config_to_grammar(
    show_skin: bool = False,
    mark_dropped: bool = False,
) -> list[str]:
    """Render TO_SKIN_CONFIG into a list of grammar strings, one per spec.

    Each entry has the shape used by the docstring grammar listing:

        format:<prefix>name <slots...> [keyword...]

    Options
    -------
    show_skin : append ` , skin:<skin_alias>` to each line (default False —
        the LLM grammar does not need the skin mapping).
    mark_dropped : append ` , hidden:<slots>` listing the port slots whose
        connection is DROPPED at render time (the skin has no pin for it),
        e.g. `format:Ename N+ N- NC+ NC- <voltage_gain> , hidden:NC+ NC-`,
        so a reader can tell which node tokens are written but not drawn.
        Hidden nodes are still REQUIRED plain node tokens in the element
        line; the slots themselves are never marked (no `{NC+}`).

    Positional slots come from `arg_to_ports`, value slots from
    `args_to_values`: optional slots render bracketed (`[Value=val]`,
    keyword-only ones as `[KEY=val|KEY2=val]`), required slots render
    unbracketed (positional values and consumed tokens like F/W's
    Vcontrol as the bare alias, keyword-only ones as `Key=val`), and a
    required specifier segment (AC on the AC-source spec) drops its
    surrounding brackets. The spec's `kind` and `model_type` are
    appended when declared.
    """
    grammar_lines: list[str] = []
    for prefix, component_specs in dialect.TO_SKIN_CONFIG.items():
        for spec in component_specs:
            pattern = _render_spec_grammar(prefix, spec, show_skin=show_skin, mark_dropped=mark_dropped)
            grammar_lines.append(f"format:{pattern}")
    return grammar_lines


def _render_zero_slot(entry: dict, name: str) -> str:
    """A param declared `is_zero` renders as `Key=0`, bracketed when it may
    also be omitted."""
    slot = f"{(entry.get('alias') or [name])[0]}=0"
    return f"[{slot}]" if entry.get("is_optional", True) else slot


def model_config_to_grammar(only_declared: bool = True) -> list[str]:
    """Render MODEL_CONFIG into one `.model` card template per configuration:

        format:.model mname LTRA R=<value> L=<value> C=<value> LEN=<value>
            , 0/omitted:G , RLC (series loss only)

    .model params are keyword-only, so each declared param renders like a
    keyword-only value slot (required unbracketed, optional bracketed);
    `zero_params` and the configuration's `variant` label are appended as
    annotations, like the ` , skin:` annotation of element specs.
    With `only_declared` (default), configurations that declare no params
    are skipped: their bare card `.model mname TYPE` is all ngspice needs.
    """
    grammar_lines: list[str] = []
    for model_type, variants in dialect.MODEL_CONFIG.items():
        for variant in variants:
            args_to_values = variant.get("args_to_values", {})
            if only_declared and not args_to_values:
                continue
            slots = [
                # an "is_zero" param can only be written as 0, so show that
                # instead of a <value> placeholder
                _render_zero_slot(entry, name)
                if entry.get("is_zero")
                else _render_value_slot({**entry, "is_positional": False}, name)
                for name, entry in args_to_values.items()
            ]
            pattern = " ".join([".model mname", model_type, *(s for s in slots if s is not None)])
            if variant.get("variant"):
                pattern += f" , {variant['variant']}"
            grammar_lines.append(f"format:{pattern}")
    return grammar_lines
