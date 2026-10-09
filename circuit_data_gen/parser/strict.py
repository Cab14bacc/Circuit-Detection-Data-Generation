"""Strict SPICE parsing: the checks that make a parsed netlist proper
ngspice input (see _strict_violations). Used by convert._parse_netlist."""

import re

from . import macro
from .tokens import _get_tokens
from .errors import NetlistError, SpecError

# SPICE strict parsing: the only dot-directives a netlist may contain.
# Analyses (.op/.tran/.ac/.dc) are not part of a generated netlist (the
# ngspice smoke test adds its own), and file access (.lib/.include) or
# .control scripts (which can run shell commands) are never accepted.
STRICT_ALLOWED_DIRECTIVES = {".model", ".param", ".params", ".subckt", ".ends", ".end"}


# a SPICE number: mantissa, optional exponent, then any letters (SI scale
# suffix and/or unit: 1k, 2.2u, 1e-6, 10Meg, 5V, 1uF)
_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?[a-z]*", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*", re.IGNORECASE)
_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
# SPICE scale suffixes, longest first so 'meg' wins over 'm' (milli)
_SCALE_SUFFIXES = [
    ("meg", 1e6),
    ("g", 1e9),
    ("k", 1e3),
    ("m", 1e-3),
    ("u", 1e-6),
    ("n", 1e-9),
    ("p", 1e-12),
    ("f", 1e-15),
]


def _expression_symbols(expr: str) -> tuple[set[str], list[tuple[str, list[str]]]]:
    """Scan a SPICE value/expression for the symbols it references.

    Returns the bare identifiers (which must be .param names or built-in
    constants) and the probe calls v(...)/i(...) with their arguments.
    Numbers are skipped, and so are the names of other function calls
    (SIN(...), sqrt(...)): an unknown function is reported by ngspice itself.
    """
    identifiers: set[str] = set()
    probes: list[tuple[str, list[str]]] = []
    pos = 0
    while pos < len(expr):
        number = _NUMBER_RE.match(expr, pos)
        if number and not expr[pos].isalpha():
            pos = number.end()
            continue
        identifier = _IDENTIFIER_RE.match(expr, pos)
        if identifier is None:
            pos += 1
            continue
        name = identifier.group()
        pos = identifier.end()
        after = expr[pos:].lstrip()
        if not after.startswith("("):
            identifiers.add(name)
        elif name.lower() in ("v", "i"):
            open_idx = expr.index("(", pos)
            close_idx = expr.find(")", open_idx)
            close_idx = len(expr) if close_idx == -1 else close_idx
            args = [a.strip() for a in expr[open_idx + 1 : close_idx].split(",") if a.strip()]
            probes.append((name.lower(), args))
            pos = close_idx + 1
    return identifiers, probes


def _spice_number(text: str) -> float | None:
    """Parse a SPICE number (0, 1k, 2.2u, 10Meg, 1e-6, 5V) into a float, or
    None when the token is not a plain number (an expression, a param name).
    The scale suffix is matched longest-first ('meg' before 'm'), any trailing
    unit letters are ignored, exactly like ngspice."""
    match = re.match(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*([a-z]*)\s*$", text, re.IGNORECASE)
    if match is None:
        return None
    value, suffix = float(match.group(1)), match.group(2).lower()
    for name, scale in _SCALE_SUFFIXES:
        if suffix.startswith(name):
            return value * scale
    return value


def _is_zero(value: str | None, params: dict) -> bool:
    """True when a .model param is absent or evaluates to 0. A value that
    cannot be evaluated (an expression) counts as non-zero: the safe
    assumption, since it decides which configuration a card claims."""
    if value is None:
        return True
    number = _spice_number(value)
    if number is None:
        number = _spice_number(str(params.get(value.lower(), "")))
    return number == 0


def _match_model_card(model_type: str, given: dict, variants: list[dict], params: dict) -> list[str]:
    """Check a .model card against its configurations in MODEL_CONFIG.

    A configuration fits when every required param is present (0 is a valid
    value) and every "is_zero" param is 0 (or absent, when optional).
    Returns the reasons each configuration failed, empty when one matched
    (mirrors _match_spec: a card is valid when ONE configuration fits).
    """
    per_variant: list[str] = []
    for variant in variants:
        reasons: list[str] = []
        for name, entry in variant.get("args_to_values", {}).items():
            value = next((given[a] for a in entry.get("alias", [name]) if a in given), None)
            optional = entry.get("is_optional", True) or entry.get("default_value") is not None
            if value is None:
                # required = present on the card; 0 is a valid value
                if not optional:
                    reasons.append(f"{name} is missing")
            elif entry.get("is_zero") and not _is_zero(value, params):
                reasons.append(f"{name} must be 0")
        if not reasons:
            return []
        label = variant.get("variant")
        per_variant.append(f"{label}: {', '.join(reasons)}" if label else ", ".join(reasons))
    return per_variant


def _subckt_violations(subckt_table: dict, meta: dict, model_table: dict) -> tuple[list[str], bool]:
    """Strict parsing of the .subckt bodies, which the render never draws but
    ngspice runs. Each body is parsed like a netlist of its own (its element
    lines must match a spec) and checked with the strict rules, with the
    netlist's models and .params plus the body's own and the header
    parameters; it needs no ground of its own. Additionally every port must
    be used inside the body, internal nodes need two connections (like
    hanging nodes at the top level), and definitions may not nest.

    Returns the violations (prefixed with the subcircuit) and whether any
    body connects to ground, which is global in SPICE and so counts for the
    top-level ground check.
    """
    from .convert import _parse_line, _preprocess_lines

    violations: list[str] = []
    uses_ground = False
    ground = macro.GROUND_NAMES
    for info in meta.get("subckts", {}).values():
        where = f".subckt {info['name']}"
        lines, body_models, body_subckts, body_meta = _preprocess_lines(info["lines"])
        if body_subckts:
            violations.append(
                f"{where}: nested .subckt definitions are not allowed; "
                f"define every subcircuit at the top level."
            )
        models = {**model_table, **body_models}
        body_meta["params"] = {**meta["params"], **info["params"], **body_meta["params"]}

        parsed: dict = {}
        for line in lines:
            try:
                name, element = _parse_line(line, model_table=models, subckt_table=subckt_table)
            except (NetlistError, SpecError, ValueError) as e:
                violations.append(f"{where}: {e}")
                continue
            if name in parsed:
                violations.append(f"{where}: duplicate component name '{name}'.")
                continue
            parsed[name] = element
        if not lines:
            violations.append(
                f"{where}: the body has no elements; add the components the subcircuit is made of."
            )

        violations += [
            f"{where}: {v}"
            for v in _strict_violations(parsed, subckt_table, body_meta, models, top_level=False)
        ]

        # nodes counted ignoring letter case (as ngspice), reported in the
        # spelling first seen in the body
        counts: dict[str, int] = {}
        spellings: dict[str, str] = {}
        for element in parsed.values():
            for conn in element["connections"].values():
                node_key = conn["node_name"].lower()
                counts[node_key] = counts.get(node_key, 0) + 1
                spellings.setdefault(node_key, conn["node_name"])
        uses_ground |= bool(counts.keys() & ground)
        ports = {port.lower() for port in info["ports"]}
        for port in info["ports"]:
            if parsed and port.lower() not in counts:
                violations.append(f"{where}: port {port} is not connected to anything inside the subcircuit.")
        floating = sorted(
            spellings[n] for n, c in counts.items() if c == 1 and n not in ports and n not in ground
        )
        if floating:
            violations.append(
                f"{where}: internal node(s) {', '.join(floating)} connect to only one element; "
                f"connect each to another element or to a port."
            )
    return violations, uses_ground


def _undefined_symbols(
    where: str, expr: str, violations: list[str], params: dict, node_names: set, element_prefixes: dict
) -> None:
    """
    Check a SPICE expression for undefined symbols, resolving symbols against constants
    and .param names. Also checks v(node)/i(Vsrc) probes for dangling references.
    """
    identifiers, probes = _expression_symbols(expr)
    undefined = sorted(
        i for i in identifiers if i.lower() not in params and i.lower() not in macro.EXPRESSION_CONSTANTS
    )
    if undefined:
        violations.append(
            f"{where}: value '{expr}' uses undefined symbol(s) {', '.join(undefined)}; "
            f"define them with .param (e.g. .param {undefined[0]}=1k) or write a number."
        )
    for probe, args in probes:
        if probe == "v":
            for node in args:
                if node.lower() not in node_names:
                    violations.append(
                        f"{where}: v({node}) refers to node {node}, which is not in the netlist."
                    )
        else:
            for source in args:
                if element_prefixes.get(source.lower()) != "v":
                    violations.append(
                        f"{where}: i({source}) must name a voltage source (V element) of the netlist."
                    )


def _strict_violations(
    parsed_netlist: dict,
    subckt_table: dict,
    meta: dict,
    model_table: dict | None = None,
    top_level: bool = True,
    ground_elsewhere: bool = False,
) -> list[str]:
    """SPICE strict parsing: everything ngspice would misread or refuse,
    worded as instructions for the LLM. Returns an empty list when clean.

    Checks: braced node tokens ({n1}), node-name form, kind keywords
    (off, ON/OFF) written before the model name, dot-directives outside
    STRICT_ALLOWED_DIRECTIVES, undefined symbols in expressions (.param
    names, built-in constants, v(node)/i(Vsrc) probes), and dangling
    references (controlling V sources, K inductors, X subckts), and .model
    cards missing a param their type requires (MODEL_CONFIG; cards of types
    not listed there, e.g. XSPICE code models, are left to ngspice). Unknown
    models are rejected earlier, in both modes, by _parse_line: no spec
    matches a model name without a .model card.

    ngspice ignores letter case, so names that differ only in case clash:
    two components (R1, r1) are one device to ngspice. Node names are
    compared ignoring case throughout (Out and out are one net, as in
    ngspice), so they need no check here.

    `top_level` is False for a .subckt body (convert._subckt_violations),
    which needs no ground of its own. At the top level a ground node is
    required, unless `ground_elsewhere` (a subcircuit body uses ground,
    which is global in SPICE).
    """
    violations: list[str] = []

    body_violations, body_uses_ground = _subckt_violations(subckt_table, meta, model_table)
    ground_elsewhere |= body_uses_ground
    violations.extend(body_violations)

    params = meta["params"]
    element_prefixes = {name.lower(): element["prefix"] for name, element in parsed_netlist.items()}
    # lower case: v(node) lookups ignore letter case, as in ngspice
    node_names = {
        conn["node_name"].lower()
        for element in parsed_netlist.values()
        for conn in element["connections"].values()
    } | macro.GROUND_NAMES

    # check a ground node exists: ngspice measures every voltage against it,
    # and without one it only reports a singular matrix
    ground = macro.GROUND_NAMES
    if top_level and not ground_elsewhere:
        uses_ground = any(
            conn["node_name"].lower() in ground
            for element in parsed_netlist.values()
            for conn in element["connections"].values()
        )
        if not uses_ground:
            violations.append(
                "The netlist has no ground node: connect at least one element to node 0 (or GND); "
                "ngspice measures every voltage against it."
            )

    # check forbidden directives
    for directive, line in meta["directives"]:
        if directive == ".control":
            violations.append("A .control block is not allowed; remove the whole .control ... .endc block.")
        elif directive not in STRICT_ALLOWED_DIRECTIVES:
            violations.append(
                f"Directive '{line}' is not allowed; remove it. Only .model, .param, .subckt/.ends "
                f"and .end may appear (the validator adds its own analysis)."
            )
    # check .model cards against their configurations (MODEL_CONFIG)
    for model_name, model in (model_table or {}).items():
        model_type = model["model_type"]
        variants = macro.MODEL_CONFIG.get(model_type)
        if not variants:
            continue  # type not described here (e.g. XSPICE code models): ngspice's job
        given = dict(arg.split("=", 1) for arg in (a.lower() for a in model.get("args", [])) if "=" in arg)
        reasons = _match_model_card(model_type, given, variants, params)
        if not reasons:
            continue
        card = f".model {model.get('name', model_name)} {model_type}"
        if len(reasons) == 1:
            violations.append(f"{card}: {reasons[0]}.")
        else:
            violations.append(
                f"{card} matches none of the configurations ngspice implements:\n"
                + "\n".join(f"    {reason}" for reason in reasons)
            )

    # check undefined symbols/identifiers
    for name, value in params.items():
        _undefined_symbols(f".param {name}", value, violations, params, node_names, element_prefixes)

    for component_name, element in parsed_netlist.items():
        # check invalid node name format
        for conn in element["connections"].values():
            node = conn["node_name"]
            if conn.get("braced"):
                violations.append(
                    f"Component {component_name}: node {{{node}}} is written in curly braces; "
                    f"write it as {node}. Braces are not allowed on node names."
                )
            elif not _NODE_NAME_RE.match(node):
                violations.append(
                    f"Component {component_name}: node name '{node}' may only contain letters, "
                    f"digits and underscores."
                )

        # check the model name is not written after a key=value argument:
        # ngspice reads everything after the first one as further
        # parameters (`R1 a b R=1k RM` fails with "unknown parameter (rm)")
        model_span = element.get("model_span")
        if model_span is not None:
            before = [
                kw for kw, span in element.get("keyword_arg_spans", {}).items() if span[0] < model_span[0]
            ]
            if before:
                violations.append(
                    f"Component {component_name}: the model name '{element.get('model_name')}' must come "
                    f"before '{before[0]}'; ngspice reads every argument after a key=value pair as another "
                    f"parameter."
                )

        # check the model name's order relative to a value, where the spec
        # pins it (model_type {TYPE: {"ref_arg", "before_arg"}}): C and L
        # before the value, W after Vcontrol. "After all nodes" is enforced
        # while matching the spec (_match_spec).
        model_span = element.get("model_span")
        orders = element.get("model_order")
        if orders and model_span is not None:
            for order in orders:
                if order.get("ref_span") is None:
                    continue

                if (model_span[0] < order["ref_span"][0]) != order["before"]:
                    side = "before" if order["before"] else "after"
                    violations.append(
                        f"Component {component_name}: the model name '{element.get('model_name')}' must come "
                        f"{side} '{order['ref']}'; ngspice cannot read it on the other side."
                    )

        # check kind keyword order: ngspice requires off / ON / OFF AFTER the
        # model name (D1 a b Dm off), the lenient grammar accepts any order
        # (the variant of a model-typed element comes from its card's TYPE;
        # kind keywords only set a state: off, ON / OFF)
        model_span = element.get("model_span")
        for keyword, span in element.get("kind_spans", {}).items():
            if model_span is not None and span[0] < model_span[0]:
                violations.append(
                    f"Component {component_name}: '{keyword}' must come after the model name "
                    f"'{element['model_name']}'; write `... {element['model_name']} {keyword}`."
                )

        # check referenced component exists (e.g. referenced by control v source.)
        for skin_label, value in element["values"].items():
            if element["if_generic"] and skin_label == "value":
                continue  # a generic's trailing name token (subckt/model name), not an expression
            reference = value.get("reference")
            if reference is not None:
                if element_prefixes.get(str(value["value"]).lower()) != reference:
                    violations.append(
                        f"Component {component_name}: '{value.get('raw_token', value['value'])}' must name a "
                        f"{reference.upper()} element of the netlist; elements inside a .subckt body "
                        f"cannot be named from outside it."
                    )
                continue
            _undefined_symbols(
                f"Component {component_name}",
                str(value["value"]),
                violations,
                params,
                node_names,
                element_prefixes,
            )

        for skin_label, value in element["values"].items():
            if value.get("unwrapped_expression"):
                key, has_key, val = value["raw_token"].partition("=")
                if has_key:
                    fix = f"write {key}={{{val}}} or {key}='{val}'"
                    what = f"{key}="
                else:
                    fix = f"write {{{key}}} or '{key}'"
                    what = f"'{key}'"
                violations.append(
                    f"Component {component_name}: {what} is an expression, which ngspice requires in braces "
                    f"or quotes; {fix}."
                )

        subckt = element.get("subckt")
        if subckt is not None and subckt.lower() not in subckt_table:
            violations.append(
                f"Component {component_name}: subcircuit '{subckt}' is not defined; "
                f"add a .subckt {subckt} ... .ends block."
            )
        elif subckt is not None and len(element["connections"]) != len(subckt_table[subckt.lower()]):
            ports = subckt_table[subckt.lower()]
            violations.append(
                f"Component {component_name}: subcircuit '{subckt}' has {len(ports)} ports "
                f"({' '.join(ports)}), but {len(element['connections'])} nodes are connected."
            )

        # check X instance parameters against the subcircuit header: ngspice
        # silently ignores an undeclared one (the header default is used), and
        # fails when the header declares none. Only the definitions of this
        # netlist level are known here (meta["subckts"]), so an X inside a
        # subcircuit body that names a top-level definition is not checked.
        definition = meta.get("subckts", {}).get(subckt.lower()) if subckt is not None else None
        if definition is not None:
            declared = definition.get("params", {})
            for skin_label, value in element["values"].items():
                if skin_label == "value":
                    continue  # the subckt name label, not a parameter
                key = value["alias"][0]
                if key.lower() in declared:
                    continue
                if declared:
                    hint = f"declared parameters: {', '.join(declared)}"
                else:
                    hint = f"the header declares none; add `params: {key}=<default>` to .subckt {subckt}"
                violations.append(
                    f"Component {component_name}: subcircuit '{subckt}' has no parameter '{key}' ({hint})."
                )

    # checks the coupled-inductor lines (K), they must reference inductors.
    for line in meta["coupling"]:
        tokens = _get_tokens(line)
        for inductor in tokens[1:-1]:
            if element_prefixes.get(inductor.lower()) != "l":
                violations.append(
                    f"Coupling {tokens[0]}: inductor '{inductor}' is not an L element of the netlist."
                )
        if len(tokens) > 1:
            _undefined_symbols(
                f"Coupling {tokens[0]}", str(tokens[-1]), violations, params, node_names, element_prefixes
            )

    return violations
