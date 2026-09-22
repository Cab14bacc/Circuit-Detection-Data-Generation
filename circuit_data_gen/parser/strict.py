"""Strict SPICE parsing: the checks that make a parsed netlist proper
ngspice input (see _strict_violations). Used by convert._parse_netlist."""

import re

from . import dialect
from .tokens import _get_tokens

# SPICE strict parsing: the only dot-directives a netlist may contain.
# Analyses (.op/.tran/.ac/.dc) are added by the simulation step, and file
# access (.lib/.include) or .control scripts (which can run shell commands)
# are never accepted from generated netlists.
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
        number = _spice_number(str(params.get(value.upper(), "")))
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
            value = next((given[a.lower()] for a in entry.get("alias", [name]) if a.lower() in given), None)
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


def _model_type_simulatable(model_type: str) -> bool:
    """False when every configuration of the .model TYPE is marked
    "simulatable": False in MODEL_CONFIG. Types not listed there (e.g. XSPICE
    code models) are left to ngspice."""
    variants = dialect.MODEL_CONFIG.get(model_type.upper())
    return not variants or any(v.get("simulatable", True) for v in variants)


def _spec_simulatable(spec: dict) -> bool:
    """False when ngspice cannot simulate a TO_SKIN_CONFIG spec: the spec is
    marked "simulatable": False, or none of its model types simulate (the
    Q specs of model type LPNP)."""
    model_types = spec.get("model_type", [])
    return spec.get("simulatable", True) and (
        not model_types or any(_model_type_simulatable(m) for m in model_types)
    )


def _simulation_violations(parsed_netlist: dict, model_table: dict) -> list[str]:
    """Components ngspice cannot simulate: elements whose spec is marked
    "simulatable": False in TO_SKIN_CONFIG (e.g. the N/P/A/U generics), and
    elements whose .model card has a TYPE marked so in MODEL_CONFIG (LPNP)."""
    violations: list[str] = []
    for component_name, element in parsed_netlist.items():
        prefix = element["prefix"]
        if not element.get("simulatable", True):
            violations.append(
                f"Component {component_name}: {prefix} elements cannot be simulated; "
                f"remove it and use another component."
            )
            continue
        model_name = element.get("model_name")
        card = model_table.get(model_name.upper()) if model_name else None
        if card is None or _model_type_simulatable(card["model_type"]):
            continue
        # suggest the model types of the same element that do simulate
        alternatives = sorted(
            {
                model_type.upper()
                for spec in dialect.TO_SKIN_CONFIG.get(prefix, [])
                if _spec_simulatable(spec)
                for model_type in spec.get("model_type", [])
                if _model_type_simulatable(model_type)
            }
        )
        hint = f"use model type {' or '.join(alternatives)} instead." if alternatives else "remove it."
        violations.append(
            f"Component {component_name}: model {model_name} has type {card['model_type'].upper()}, "
            f"which cannot be simulated; {hint}"
        )
    return violations


def _strict_violations(
    parsed_netlist: dict,
    subckt_table: dict,
    meta: dict,
    model_table: dict | None = None,
    simulate: bool = False,
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

    With `simulate` (the netlist will be run by ngspice), components that
    ngspice cannot simulate are rejected too (_simulation_violations).
    Strict parsing alone does not imply simulation, so this is opt-in.
    """
    violations: list[str] = []
    params = meta["params"]
    element_prefixes = {name.upper(): element["prefix"] for name, element in parsed_netlist.items()}
    node_names = {
        conn["node_name"].lower()
        for element in parsed_netlist.values()
        for conn in element["connections"].values()
    } | {g.lower() for g in dialect.GROUND_NAMES}

    def undefined_symbols(where: str, expr: str) -> None:
        """
        Check a SPICE expression for undefined symbols, resolving symbols against constants
        and .param names. Also checks v(node)/i(Vsrc) probes for dangling references.
        """
        identifiers, probes = _expression_symbols(expr)
        undefined = sorted(
            i
            for i in identifiers
            if i.upper() not in params and i.lower() not in dialect.EXPRESSION_CONSTANTS
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
                    if element_prefixes.get(source.upper()) != "V":
                        violations.append(
                            f"{where}: i({source}) must name a voltage source (V element) of the netlist."
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
        model_type = model["model_type"].upper()
        variants = dialect.MODEL_CONFIG.get(model_type)
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

    # check components ngspice cannot simulate (only when simulating)
    if simulate:
        violations.extend(_simulation_violations(parsed_netlist, model_table or {}))

    # check undefined symbols/identifiers
    for name, value in params.items():
        undefined_symbols(f".param {name}", value)

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

        # check kind keyword order: ngspice requires off / ON / OFF AFTER the
        # model name (D1 a b Dm off), the lenient grammar accepts any order
        # kind keywords in SPICE are keywords specified as parameters, unlike in
        # lcapy where the kind keywords specify the component variant (e.g. "amp", "inamp").
        # That job is delegated to the model name in SPICE.
        model_span = element.get("model_span")
        for keyword, span in element.get("kind_spans", {}).items():
            if model_span is not None and span[0] < model_span[0]:
                violations.append(
                    f"Component {component_name}: '{keyword}' must come after the model name "
                    f"'{element['model_name']}'; write `... {element['model_name']} {keyword}`."
                )

        # check referenced component exists (e.g. referenced by control v source.)
        for key, value in element["values"].items():
            if element["if_generic"] and key == "value":
                continue  # a generic's trailing name token (subckt/model name), not an expression
            reference = value.get("reference")
            if reference is not None:
                if element_prefixes.get(str(value["value"]).upper()) != reference:
                    violations.append(
                        f"Component {component_name}: '{value['value']}' must name a {reference} "
                        f"element of the netlist."
                    )
                continue
            undefined_symbols(f"Component {component_name}", str(value["value"]))

        subckt = element.get("subckt")
        if subckt is not None and subckt.upper() not in subckt_table:
            violations.append(
                f"Component {component_name}: subcircuit '{subckt}' is not defined; "
                f"add a .subckt {subckt} ... .ends block."
            )

    # checks the coupled-inductor lines (K), they must reference inductors.
    for line in meta["coupling"]:
        tokens = _get_tokens(line)
        for inductor in tokens[1:-1]:
            if element_prefixes.get(inductor.upper()) != "L":
                violations.append(
                    f"Coupling {tokens[0]}: inductor '{inductor}' is not an L element of the netlist."
                )
        if len(tokens) > 1:
            undefined_symbols(f"Coupling {tokens[0]}", str(tokens[-1]))

    return violations
