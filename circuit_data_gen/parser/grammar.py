"""Grammar rendering: TO_SKIN_CONFIG -> human-readable grammar listing
(used for the LLM grammar docs and for the parse errors fed back to it)."""

from . import macro
from .errors import SpecError


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


def _display_name(entry: dict, idx) -> str:
    """The placeholder name of a value slot in the grammar: its
    `display_alias`, else its first `alias`. A slot with neither cannot be
    rendered (a positional_only slot such as F/H/W's Vcontrol has no alias,
    since it has no key=value spelling, and must declare a display_alias)."""
    name = entry.get("display_alias")
    if name is None:
        name = (entry.get("alias") or [None])[0]
    if name is None:
        raise SpecError(f"Value slot {idx!r} needs a 'display_alias' or an 'alias' to be rendered.")
    return name


def _render_value_slot(entry: dict, idx) -> str:
    """Render ONE value/keyword slot as its grammar token.

    A positional slot is a VALUE: the token written on the line IS the
    value (`1k`), so the placeholder is shown in angle brackets — `<r>`
    required, `[<c>]` optional — named by _display_name (display_alias,
    else the first alias).

    A keyword-only slot (is_positional "keyword") and a "positional_with_key"
    slot (B's V=, E's vol=: at its index, but only as key=value) are written
    in key=<value> form, so they show the real key spellings: required ones
    unbracketed (`v=<expr>`, `z0=<value>`), optional ones as
    `[key=<value>|key2=<value>]` showing every accepted alias. The value
    placeholder is the slot's display_alias, else `value`; an `is_expression`
    slot shows the braces ngspice requires (`vol={<expr>}`, `q={<charge>}`).
    A `functions` slot renders as the alternation of its transient functions
    (`[SIN(...)|PULSE(...)|...]`).
    """
    is_optional = entry.get("is_optional", True)
    if entry.get("functions"):
        # a transient function value, written NAME(...) (V/I SIN(...), PULSE(...))
        forms = "|".join(f"{name.upper()}(...)" for name in entry["functions"])
        return f"[{forms}]" if is_optional else forms
    if entry.get("is_positional", "positional") not in ("keyword", "positional_with_key"):
        name = _display_name(entry, idx)
        return f"[<{name}>]" if is_optional else f"<{name}>"
    aliases = entry.get("alias") or []
    if not aliases:
        raise SpecError(f"Keyword value slot {idx!r} needs an 'alias': its key=value spelling.")
    placeholder = f"<{entry.get('display_alias') or 'value'}>"
    if entry.get("is_expression"):
        placeholder = "{" + placeholder + "}"
    if not is_optional:
        return f"{aliases[0]}={placeholder}"
    return "[" + "|".join(f"{a}={placeholder}" for a in aliases) + "]"


def _render_specifiers(spec: dict, prefix: str | None = None) -> list[str]:
    """Specifier placeholders, e.g. `ac [<mag>] [<phase>]`.

    Each specifier renders as the bare keyword followed by one bracketed
    placeholder per ref, named after the slot the operand fills
    (_display_name: display_alias, else first alias; the ref key itself when
    the spec declares no such slot).

    A specifier whose SINGLE ref is an int is an alternative spelling of
    that positional slot (DC 5 == bare 5); it is rendered as a slot
    alternation by the caller and skipped here.

    A specifier with `is_optional: False` renders UNBRACKETED, and so does
    one that not every spec of `prefix` declares: it is what selects this
    spec (AC on the AC-source spec, which is optional for parsing only so
    that `ac=` keywords can select the spec too). Others keep `[...]`.
    """
    specifiers = spec.get("specifiers", {})
    if not specifiers:
        return []
    arg_specs = spec.get("args_to_values", {})
    siblings = macro.TO_SKIN_CONFIG.get(prefix, [spec]) if prefix is not None else [spec]
    shared = set.intersection(*(set(other.get("specifiers", {})) for other in siblings))
    parts = []
    for sp_key, sp_def in specifiers.items():
        refs = sp_def.get("refs", [])
        if len(refs) == 1 and isinstance(refs[0], int):
            continue  # rendered as a slot alternation by the caller
        seg = [sp_key]
        for ref in refs:
            name = _display_name(arg_specs[ref], ref) if ref in arg_specs else ref
            seg.append(f"[<{name}>]")
        rendered = " ".join(seg)
        if sp_def.get("is_optional", False) and sp_key in shared:
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
    if prefix in macro.EXPLICIT_SPICE_GENERICS:
        # generic specs declare no ports: the pins come from a definition
        if prefix == macro.SUBCKT_PREFIX:
            return f"{prefix.upper()}name N1 ... Nn SUBNAM [param=<value> ...]"
        return f"{prefix.upper()}name N1 ... Nn <definition name>"

    # the element-name placeholder keeps the letter upper case (prefixes are
    # lower case in macro): `Mname ... mname` stays readable for M
    parts = [f"{prefix.upper()}name"]

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
        if entry.get("is_positional", "positional") == "keyword":
            continue
        slot = _render_value_slot(entry, idx_str)
        first = _display_name(entry, idx_str)
        alt_key = alternations.get(int(idx_str))
        if alt_key:
            # a specifier whose single operand binds THIS positional slot by
            # index is an alternative SPELLING of the same value rather than
            # a separate keyword (DC 5 == 5). Render the slot once, as an
            # alternation, instead of printing the value slot twice.
            slot = f"[<{first}>|{alt_key} <{first}>]"
        indexed[int(idx_str)] = slot

    # a {TYPE: {"ref_arg", "before_arg"}} model_type pins the model name
    # next to one argument (C1 a b mname <C>, W1 a b <Vcontrol> mname)
    model_types = spec.get("model_type")
    rules = [r for r in model_types.values() if r] if isinstance(model_types, dict) else []
    anchor = rules[0] if rules and rules[0].get("ref_arg") is not None else None
    # a flag, not `"mname" in parts`: the M prefix renders as "mname" too
    model_placed = False
    for idx in sorted(indexed.keys()):
        if anchor and idx == anchor["ref_arg"] and anchor.get("before_arg", True):
            parts.append("mname")
            model_placed = True
        parts.append(indexed[idx])
        if anchor and idx == anchor["ref_arg"] and not anchor.get("before_arg", True):
            parts.append("mname")
            model_placed = True

    # ngspice order: the model name after all nodes and positional values,
    # before any key=value argument; the kind keyword (off, ON/OFF) after it
    if spec.get("model_type") and not model_placed:
        parts.append("mname")
    if spec.get("kind"):
        parts.append("|".join(spec["kind"]))

    # keyword-only value slots (is_positional "keyword", any key type) go last
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
        if str(k).isdigit() and entry.get("is_positional", "positional") != "keyword":
            continue  # already interleaved by index above
        if k in spec_bound:
            continue
        parts.append(_render_value_slot(entry, k))

    # declared specifiers render as bare-keyword segments
    parts.extend(_render_specifiers(spec, prefix))

    pattern = " ".join(parts)
    if mark_dropped:
        hidden = _hidden_slots(spec.get("arg_to_ports", {}))
        if hidden:
            pattern = f"{pattern} , hidden:{' '.join(hidden)}"
    if show_skin:
        skin_alias = spec.get("skin_alias", None)
        if skin_alias is None:
            skin = "none (parse-only)"
        elif isinstance(skin_alias, list):
            # specs alternate skin variants (r_h|r_v); random.choice picks
            # one at render time, so the grammar shows the full set.
            skin = "|".join(str(s) for s in skin_alias)
        else:
            skin = str(skin_alias)
        pattern = f"{pattern} , skin:{skin}"
    return pattern


def config_to_grammar(
    show_skin: bool = False,
    mark_dropped: bool = False,
) -> list[str]:
    """Render TO_SKIN_CONFIG into a list of grammar strings, one per spec
    (names and keys lower case, as macro stores them; SPICE ignores case).

    Each entry has the shape used by the docstring grammar listing:

        format:<prefix>name <slots...> [keyword...]

    Options
    -------
    show_skin : append ` , skin:<skin_alias>` to each line (default False —
        the LLM grammar does not need the skin mapping).
    mark_dropped : append ` , hidden:<slots>` listing the port slots whose
        connection is DROPPED at render time (the skin has no pin for it),
        e.g. `format:Ename N+ N- NC+ NC- <gain> , hidden:NC+ NC-`,
        so a reader can tell which node tokens are written but not drawn.
        Hidden nodes are still REQUIRED plain node tokens in the element
        line; the slots themselves are never marked (no `{NC+}`).

    Positional slots come from `arg_to_ports`, value slots from
    `args_to_values` (see _render_value_slot): optional slots render
    bracketed (`[<c>]`, keyword ones as `[key=<value>|key2=<value>]`),
    required ones unbracketed (`<r>`, F/W's `<Vcontrol>`, `z0=<value>`),
    and a specifier that selects the spec (ac on the AC-source spec) drops
    its brackets. `mname` goes where ngspice reads the model name, the
    `kind` keyword right after it. The generic letters other than X
    (A/U/P/N) are left out: their definitions are not part of the grammar.
    """
    grammar_lines: list[str] = []
    for prefix, component_specs in macro.TO_SKIN_CONFIG.items():
        if prefix in macro.EXPLICIT_SPICE_GENERICS and prefix != macro.SUBCKT_PREFIX:
            continue  # A/U/P/N: their definitions are not part of the grammar
        for spec in component_specs:
            pattern = _render_spec_grammar(prefix, spec, show_skin=show_skin, mark_dropped=mark_dropped)
            grammar_lines.append(f"format:{pattern}")
    return grammar_lines


def _render_zero_slot(entry: dict, name: str) -> str:
    """A param declared `is_zero` renders as `key=0`, bracketed when it may
    also be omitted."""
    slot = f"{(entry.get('alias') or [name])[0]}=0"
    return f"[{slot}]" if entry.get("is_optional", True) else slot


def model_config_to_grammar(only_declared: bool = True) -> list[str]:
    """Render MODEL_CONFIG into one `.model` card template per configuration:

        format:.model mname ltra r=<value> l=<value> c=<value> len=<value> g=0

    .model params are keyword-only, so each declared param renders like a
    keyword-only value slot (required unbracketed, optional bracketed); an
    "is_zero" param renders as `key=0`. The configuration's `variant` label,
    when set, is appended as an annotation, like the ` , skin:` annotation
    of element specs.
    With `only_declared` (default), configurations that declare no params
    are skipped: their bare card `.model mname TYPE` is all ngspice needs.
    """
    grammar_lines: list[str] = []
    for model_type, variants in macro.MODEL_CONFIG.items():
        for variant in variants:
            args_to_values = variant.get("args_to_values", {})
            if only_declared and not args_to_values:
                continue
            slots = [
                # an "is_zero" param can only be written as 0, so show that
                # instead of a <value> placeholder
                _render_zero_slot(entry, name)
                if entry.get("is_zero")
                else _render_value_slot({**entry, "is_positional": "keyword"}, name)
                for name, entry in args_to_values.items()
            ]
            pattern = " ".join([".model mname", model_type, *(s for s in slots if s is not None)])
            if variant.get("variant"):
                pattern += f" , {variant['variant']}"
            grammar_lines.append(f"format:{pattern}")
    return grammar_lines
