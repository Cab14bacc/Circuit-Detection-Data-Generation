import logging
import copy
from .errors import NetlistError, SpecError
from . import macro
from .tokens import Token

logger = logging.getLogger(__name__)


def _merge_nodes(parsed_netlist):
    """
    Union-find over the netlist's node names; it merges all ground nodes
    (GROUND_NAMES) into a single canonical node.

    Node names are compared ignoring letter case, as in ngspice (Out and out
    are one net): find() and union() take any spelling and return the
    lower-case representative.
    """
    parent = {}

    def find(x):
        x = x.lower()
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Merge all ground nodes into a single canonical node
    for g in macro.GROUND_NAMES:
        union(list(macro.GROUND_NAMES)[0], g)

    return find, union


def _assign_net_ids(parsed_netlist):
    logger.debug("Assigning net IDs:")

    find, union = _merge_nodes(parsed_netlist)

    # canonical nodes are the representative nodes of each electrical net.
    canonicals = {}
    for component_name, element in parsed_netlist.items():
        nodes = [connection["node_name"] for pid, connection in element["connections"].items()]
        for n in nodes:
            canonical_node = find(n)
            if canonical_node not in canonicals:
                canonicals[canonical_node] = set()
            if n.lower() in macro.GROUND_NAMES:
                canonicals[canonical_node].add("gnd")
            else:
                canonicals[canonical_node].add(component_name)

    canonicals = dict(sorted(canonicals.items()))
    id_map = {node: i + 2 for i, node in enumerate(canonicals.keys())}

    ground_net_id = None
    if find(list(macro.GROUND_NAMES)[0]) in id_map:
        ground_net_id = id_map[find(list(macro.GROUND_NAMES)[0])]

    return id_map, find, ground_net_id


def _format_line_errors(errors: list[str]) -> str:
    """One message for all lines that failed to parse. A single error is
    returned as is; several are numbered, each error's further lines
    (spec listings) indented under its number:

        2 netlist lines have errors:
        1. All specs failed for prefix 'D' on line 'D1 a b'
           Spec 0: Dname N+ N- mname
           - Errors: ...
        2. Duplicate component name 'R1' found in netlist.
    """
    if len(errors) == 1:
        return errors[0]
    blocks = []
    for number, error in enumerate(errors, start=1):
        first, *rest = [line for line in error.strip().splitlines() if line.strip()]
        indent = " " * (len(str(number)) + 2)
        blocks.append("\n".join([f"{number}. {first}", *(indent + line for line in rest)]))
    return f"{len(errors)} netlist lines have errors:\n" + "\n".join(blocks)


def _read_specifiers(args: list[Token], component_spec: dict, prefix: str) -> tuple[list[Token], dict] | None:
    """SPICE only: read the V/I mode segments (DC/AC ...) out of the line.

    A segment is a bare specifier keyword followed by its operands, one per
    ref of the specifier. It ends at its last operand, or earlier at a
    key=value token, a kind keyword, or any specifier keyword (this spec's or
    another spec's of the prefix). Segments are independent, so their order
    on the line does not matter (`AC 1 DC 5` == `DC 5 AC 1`):

        V1 a b DC 5 AC 1 90   ->   [a, b], {2: 5, "AC": 1, "phase": 90}

    Each operand is stored under its ref, the args_to_values key of the slot
    it fills (int = positional slot, str = keyword slot); _extract_values
    writes these straight into the slots. A bare specifier with no operands
    stores the specifier's "default" under its first str ref (AC == AC 1).
    The specifier keywords and their operands are removed from the args;
    everything else (nodes, a bare value, key=value args) is kept in order.

    A specifier declared only by another spec of the prefix means this spec
    cannot own the line (that is what routes V lines into the DC-source vs
    AC-source spec): returns None so the candidate fails.

    Returns (remaining args, {ref: operand}), or None on a foreign specifier.
    """
    specifiers: dict = component_spec.get("specifiers", {})
    if not specifiers:
        return args, {}  # nothing declared -> nothing to do

    # segment openers declared by THIS spec
    openers = {k.lower(): k for k in specifiers}
    # foreign openers: declared by other specs of the same prefix
    foreign: set[str] = set()
    for other in macro.TO_SKIN_CONFIG.get(prefix, []):
        if other is component_spec:
            continue
        foreign.update(k.lower() for k in other.get("specifiers", {}))
    foreign -= set(openers)
    kinds = {k.lower() for k in macro._get_all_kind_keywords()}

    def _terminator(tok: Token) -> bool:
        low = tok.lower()
        return "=" in low or low in kinds or low in openers or low in foreign

    remaining: list[Token] = []
    values: dict = {}
    i = 0
    while i < len(args):
        tok = args[i]
        low = tok.lower()
        if "=" in low:
            remaining.append(tok)
            i += 1
            continue

        if low in openers:
            entry = specifiers[openers[low]]  # {"refs": [...], "is_optional":, "default":}
            refs = entry.get("refs", [])
            i += 1  # consume the specifier keyword itself
            if i >= len(args) or _terminator(args[i]):
                # bare specifier (e.g. 'AC' with no operands): the specifier's
                # own "default" encodes the documented default (AC == AC 1)
                default = entry.get("default", None)
                first_str_ref = next((ref for ref in refs if isinstance(ref, str)), None)
                if default is not None and first_str_ref is not None:
                    values[first_str_ref] = default
                continue
            for ref in refs:
                if i >= len(args) or _terminator(args[i]):
                    break  # operand missing -> the slot's default_value applies
                if ref in values:
                    raise NetlistError(f"Specifier {openers[low]} given twice for prefix '{prefix}'")
                values[ref] = args[i]
                i += 1
        elif low in foreign:
            # a specifier declared only by ANOTHER spec of this prefix: this
            # spec cannot own the line — fail the candidate so the spec that
            # declares it can win
            return None
        else:
            remaining.append(tok)
            i += 1

    # transient function (SIN(...), PULSE(...), ...): ngspice keeps it as a
    # value of its own next to the DC value (V1 a 0 DC 2 SIN(1 0.5 1k)).
    # Written alone it stays the source's value, as before.
    fn_slot = next(
        (key for key, entry in component_spec.get("args_to_values", {}).items() if entry.get("functions")),
        None,
    )
    if fn_slot is not None:
        functions = component_spec["args_to_values"][fn_slot]["functions"]
        fn_positions = [
            j
            for j, tok in enumerate(remaining)
            if "=" not in tok and any(tok.lower().startswith(f"{name}(") for name in functions)
        ]
        if len(fn_positions) > 1:
            raise NetlistError(
                f"More than one transient function ({', '.join(remaining[j] for j in fn_positions)}) "
                f"for prefix '{prefix}'; ngspice uses only one."
            )
        if fn_positions:
            fn_pos = fn_positions[0]
            num_ports = len(component_spec.get("arg_to_ports", {}))
            bare_values = [
                j for j, tok in enumerate(remaining) if j >= num_ports and j != fn_pos and "=" not in tok
            ]
            if any(j > fn_pos for j in bare_values):
                # ngspice silently drops a bare value written after the function
                raise NetlistError(
                    f"The DC value must come before {remaining[fn_pos]} for prefix '{prefix}' "
                    f"(or be written as DC <value>)."
                )
            # a DC value: bare, from the DC specifier, or as its key=value (dc=2)
            dc_keys = {
                alias.lower()
                for key, entry in component_spec.get("args_to_values", {}).items()
                if isinstance(key, int)
                for alias in entry.get("alias") or []
            }
            dc_keyword = any("=" in tok and tok.split("=", 1)[0].lower() in dc_keys for tok in remaining)
            dc_given = bool(bare_values) or dc_keyword or any(isinstance(ref, int) for ref in values)
            if dc_given:
                values[fn_slot] = remaining.pop(fn_pos)
    return remaining, values


def _get_prefix(component_name: str) -> str | None:
    prefix_list = [str(p) for p in macro.TO_SKIN_CONFIG.keys()]
    # Sort by length descending
    prefix_list.sort(key=lambda x: len(x), reverse=True)

    for p in prefix_list:
        if component_name.lower().startswith(p.lower()):
            return p.lower()

    for p in macro.EXPLICIT_SPICE_GENERICS:
        if component_name.lower().startswith(p.lower()):
            return p.lower()

    logger.warning(f"Unknown component prefix for {component_name}, using generic skin cell.")

    return None


def _filter_spice_model_args(prefix, spec_idx, args, model_types, model_table):
    resolved_model_name = None
    resolved_model_types = [
        (model_table[tok.lower()]["model_type"], idx)
        for idx, tok in enumerate(args)
        if tok.lower() in model_table
    ]

    # a SPICE component references exactly ONE model
    if len(resolved_model_types) > 1:
        raise NetlistError(
            f"Multiple model types found in args for prefix '{prefix}' "
            f"spec index {spec_idx}: {resolved_model_types}. "
            f"A SPICE component references exactly one model."
        )

    # if a model name is defined in the args, but the spec does NOT declare any kind
    if len(resolved_model_types) == 1 and len(model_types) == 0:
        raise SpecError(
            f"Resolved model type found in args for prefix '{prefix}' spec index {spec_idx}, "
            f"but spec does not declare any kind. There must be at least one "
            f"kind keyword to match the model type."
        )

    # if a model name is NOT defined in the args, yet the spec declares a kind,
    # the spec cannot be verified — reject it here (every model name needs an
    # in-file .model card).
    if len(resolved_model_types) == 0 and len(model_types) > 0:
        raise NetlistError(
            f"No model type found in args for prefix '{prefix}' spec index {spec_idx}, "
            f"but spec requires kind {model_types}. (no matching .model directive)"
        )

    if len(resolved_model_types) == 1:
        resolved_model_type, resolved_model_arg_idx = resolved_model_types[0]
        # the resolved model type must match this spec's kind
        if model_types and resolved_model_type not in [k for k in model_types]:
            raise NetlistError(
                f"Resolved model type '{resolved_model_type}' does not match "
                f"spec kind {model_types} for prefix '{prefix}' spec index {spec_idx} "
            )
        non_model_args = copy.copy(args)
        resolved_model_name = args[resolved_model_arg_idx]
        non_model_args.pop(resolved_model_arg_idx)
    else:
        non_model_args = args

    return non_model_args, resolved_model_name


def _model_order_constraints(model_types, model_name, model_table, filtered_args) -> list[dict] | None:
    """Where the model name must sit relative to a value argument, as a list
    of constraints for the strict model-order check. Each constraint is
    {"before", "ref", "ref_span"}: the model name must come before (True) or
    after (False) the argument `ref`, whose (start, end) span in the line is
    `ref_span`.

    A constraint comes from a spec whose model_type is a {TYPE: {"ref_arg",
    "before_arg"}} dict with a rule for the line's model TYPE, when argument
    `ref_arg` is on the line: C, L before the value, W after Vcontrol. An
    optional argument left out adds no constraint.

    "After all nodes" is not a constraint here: _match_spec already rejects a
    spec whose nodes do not all come before the model name, so a matched
    line always satisfies it.

    Returns None when the line has no model name or the spec no model_type,
    else the (possibly empty) list. Raises SpecError for a ref_arg that is not
    a positional index.
    """
    if not model_name or not model_types:
        return None

    constraints: list[dict] = []
    if not isinstance(model_types, dict):
        return constraints

    card = (model_table or {}).get(str(model_name).lower())
    if card is None:
        return constraints
    # a line references one model name, so one model type: the first rule
    # for that type is the only one that can apply
    rule = next((r for t, r in model_types.items() if t == card["model_type"]), None)
    if not rule or rule.get("ref_arg") is None:
        return constraints
    ref_arg = rule["ref_arg"]
    if not isinstance(ref_arg, int) or ref_arg < 0:
        raise SpecError(
            f"Unsupported ref_arg {ref_arg} in model_type rule for model type {card['model_type']}. "
            f"Expected a non-negative integer. Keyword only arguments are not supported as a ref_arg."
        )
    if ref_arg >= len(filtered_args) or not isinstance(filtered_args[ref_arg], Token):
        return constraints

    ref = filtered_args[ref_arg]
    constraints.append({"before": bool(rule.get("before_arg", False)), "ref": str(ref), "ref_span": ref.span})
    return constraints
