"""SPICE (ngspice) netlist → Yosys JSON converter for netlistsvg.

Each element line is matched against the specs of its letter in
TO_SKIN_CONFIG (configs/convert/spice_convert_config.py, described in
docs/CONVERT_CONFIG.md), which map it to a skin cell of the skin built by
build_skin.py. Ground nodes (0, GND, gnd) are merged into one net, which gets
a gnd cell per connection. Strict parsing (strict.py) additionally checks
that the netlist is proper ngspice input.

This module holds the parsing pipeline (preprocessing, spec matching, line
and netlist parsing, yosys JSON). Its helpers live next to it:
  - macro.py   : the loaded config tables (lower-cased) and shared constants
  - helper.py  : node merging, specifier reading, model-order rules
  - errors.py  : NetlistError, SpecError
  - tokens.py  : Token, _get_tokens and the lexical repairs of SPICE args
  - strict.py  : strict SPICE parsing checks (_strict_violations)
  - grammar.py : TO_SKIN_CONFIG -> grammar listing (config_to_grammar)
The names below are re-exported, so `convert.X` keeps working.
"""

from ..configs.config import get_logger
import random
import re

from .macro import (  # noqa: F401 (re-exported)
    COUPLED_INDUCTOR_PREFIXES,
    EXPLICIT_SPICE_GENERICS,
    EXPRESSION_CONSTANTS,
    GROUND_NAMES,
    MODEL_CONFIG,
    STRICT_PARSING,
    SUBCKT_PREFIX,
    TO_SKIN_CONFIG,
    _get_all_kind_keywords,
    _get_spice_directive_prefixes,
)
from .errors import NetlistError, SpecError
from .grammar import (  # noqa: F401 (re-exported)
    _render_spec_grammar,
    config_to_grammar,
    model_config_to_grammar,
)
from .helper import (  # noqa: F401 (re-exported)
    _get_prefix,
    _filter_spice_model_args,
    _assign_net_ids,
    _merge_nodes,
    _read_specifiers,
    _model_order_constraints,
    _format_line_errors,
)
from .strict import STRICT_ALLOWED_DIRECTIVES, _strict_violations  # noqa: F401 (re-exported)
from .tokens import (  # noqa: F401 (re-exported)
    Token,
    _get_tokens,
    _glue_comma_values,
    _glue_paren_values,
    _strip_hints,
    _token_origin,
)

logger = get_logger(__name__)


# --- SPICE arg normalization (specifier expansion; lexical repair: tokens.py) --
def _preprocess_lines(text_lines: list[str]):
    """
    Merge continuation lines starting with '+' into the previous line.
    Remove comment and empty lines. Return a list of cleaned lines.

    Dot-directives (.model, .param, .subckt, .lib, .tran, .end, ...) are
    not element lines: .model cards go into the model table (the card's TYPE
    is what a spec's `model_type` matches), and the rest is recorded in meta
    for strict parsing, which rejects what ngspice would not accept.
    The bodies of .subckt/.ends and .control/.endc blocks are not parsed as
    elements; subcircuit bodies are kept in meta for strict parsing.

    Returns
    -------
    merged_stripped_lines : list[str]
        The element lines, continuation lines merged.
    model_table : dict[str, dict]
        Model name (lower) -> {"name" (as written), "model_type" (lower),
        "args"}.
    subckt_table : dict[str, list[str]]
        Subckt name (lower) -> list of port names.
    meta : dict
        SPICE netlist-level information used by strict parsing:
        "params" (.param name (lower) -> value text), "directives" (list of
        (directive, line) for every top-level dot-directive), "coupling"
        (the K coupled-inductor lines, which are not parsed as elements) and
        "subckts" (subckt name (lower) -> {"name", "ports", "params",
        "lines"}: the header's spelling, port names, header parameters and
        the raw body lines, which strict parsing checks).
    """
    merged_stripped_lines = []
    current_line = ""
    model_table: dict[str, dict] = {}  # model name (lower) -> model type (lower)
    subckt_table: dict[str, list[str]] = {}  # subckt name (lower) -> list of port names
    meta: dict = {"params": {}, "directives": [], "coupling": [], "subckts": {}}

    # .subckt blocks may nest: only the matching .ends closes the outer one
    in_subckt = False
    subckt_depth = 0
    current_subckt: dict | None = None

    # .control/.endc blocks hold ngspice scripts (run, plot, shell ...),
    # not elements, so their lines are skipped as well.
    in_control = False

    for line in text_lines:
        stripped_line = _strip_hints(line)
        if not stripped_line:
            continue

        if stripped_line[0] in "*#":
            continue

        # ========================================================================
        # subckt are kept for later inspection, and control blocks are skipped
        # ========================================================================
        if in_subckt or in_control:
            # lines inside a .subckt or .control block are not elements of
            # the netlist; subcircuit bodies are kept for strict parsing
            directive = stripped_line.split()[0].lower()
            if in_subckt:
                if directive == ".subckt":
                    subckt_depth += 1
                elif directive == ".ends":
                    subckt_depth -= 1
                if subckt_depth == 0:
                    in_subckt = False
                else:
                    current_subckt["lines"].append(stripped_line)
            if in_control and directive == ".endc":
                in_control = False
            continue

        # ========================================================================
        # dot directives
        # .model lines populate the model table (lowered name -> type),
        # .param lines the parameter table
        # .subckt lines populate the subckt table (lowered name -> subckt info)
        # ========================================================================
        if stripped_line[0] == ".":
            directive = stripped_line.split()[0].lower()
            meta["directives"].append((directive, stripped_line))

            if directive == ".control":
                in_control = True
                continue

            elif directive in (".param", ".params"):
                # .param a=1k b={2*a}; spaces around '=' are tolerated here
                assignments = re.sub(r"\s*=\s*", "=", stripped_line[len(directive) :])
                for tok in _get_tokens(assignments):
                    if "=" in tok:
                        key, value = tok.split("=", 1)
                        meta["params"][key.lower()] = value
                    else:
                        raise NetlistError(
                            f"Malformed .param directive: {stripped_line}, "
                            f"should follow the '.param <name>=<value> ...' format"
                        )
                continue

            elif directive == ".model":
                parts = stripped_line.split()
                # .model <name> <type>(pname1=pval1 pname2=pval2 ... )
                # .model <name> <type> (pname1=pval1 pname2=pval2 ... )
                # both are correct, parse for both
                if len(parts) >= 3:
                    # "name" keeps the spelling of the card, for messages
                    model_table[parts[1].lower()] = {"name": parts[1]}
                    model_type_and_args = parts[2].lower().split("(")
                    model_table[parts[1].lower()]["model_type"] = model_type_and_args[0]
                    model_args = parts[2].lower().split("(")[1] if len(model_type_and_args) > 1 else ""
                    # strip the enclosing parens BEFORE tokenizing: with the
                    # parens present, _get_tokens paren-joins the entire arg
                    # list into a single token (SPICE function-value joining)
                    joined = (model_args + " " + " ".join(parts[3:])).strip().strip("()").strip()
                    model_table[parts[1].lower()]["args"] = [tok for tok in _get_tokens(joined) if tok]
                else:
                    raise NetlistError(
                        f"Malformed .model directive: {stripped_line}, "
                        f"should follow the '.model <name> <type>' format"
                    )

            elif directive == ".subckt":
                parts = stripped_line.split()
                if len(parts) >= 3:
                    in_subckt = True
                    subckt_depth = 1
                    subckt_port_names = []
                    for arg in parts[2:]:
                        if "params:" in arg.lower():
                            break
                        if "=" in arg:
                            break
                        subckt_port_names.append(arg)
                    subckt_table[parts[1].lower()] = subckt_port_names
                    # header parameters (".subckt amp a b params: gain=2"),
                    # which the body may use in expressions
                    header = " ".join(parts[2 + len(subckt_port_names) :])
                    header = re.sub(r"(?i)params:", " ", header)
                    header_params = {}
                    for tok in _get_tokens(re.sub(r"\s*=\s*", "=", header)):
                        if "=" in tok:
                            key, value = tok.split("=", 1)
                            header_params[key.lower()] = value
                    current_subckt = {
                        "name": parts[1],
                        "ports": subckt_port_names,
                        "params": header_params,
                        "lines": [],
                    }
                    meta["subckts"][parts[1].lower()] = current_subckt
                else:
                    raise NetlistError(
                        f"Malformed .subckt directive: {stripped_line}, should follow the "
                        f"'.subckt <name> <node_1>...<node_n> [param1=val1] ...' format, "
                        f"and at least have one node."
                    )

            if directive in _get_spice_directive_prefixes():
                continue

            logger.debug(f"Ignoring unrecognized SPICE directive: {stripped_line[:60]}")
            continue

        # ========================================================================
        # Skip coupled-inductor lines (K...)
        # their args are inductor references, not nodes,
        # so there is nothing to draw, but will be inspected for strict parsing
        # ========================================================================
        if stripped_line[0].lower() in COUPLED_INDUCTOR_PREFIXES:
            logger.debug(f"Dropping coupled-inductor line (no visual form, no nodes): {stripped_line[:60]}")
            meta["coupling"].append(stripped_line)
            continue

        # ========================================================================
        # Merge continuation lines
        # ========================================================================
        if stripped_line.startswith("+"):
            # Continuation of the previous line.
            cont = stripped_line[1:].strip()
            if current_line:
                current_line += " " + cont if cont else current_line
            elif merged_stripped_lines:
                merged_stripped_lines[-1] += " " + cont
        else:
            # Flush the previous line, start a new one.
            if current_line:
                merged_stripped_lines.append(current_line)
            current_line = stripped_line

    if current_line:
        merged_stripped_lines.append(current_line)

    return merged_stripped_lines, model_table, subckt_table, meta


def _extract_connections(
    cur_elem: dict,
    filtered_args: list[str],
    arg_to_ports: dict,
    component_spec: dict,
    prefix: str,
    spec_idx: int,
):

    # extract connections
    for arg_idx, arg_spec in arg_to_ports.items():
        arg_idx = int(arg_idx)
        try:
            node_name = filtered_args[arg_idx]
        except IndexError:
            # this spec needs more tokens than the line provides — fail the
            # candidate cleanly (other spec variants may still match)
            raise NetlistError(
                f"Missing node argument at index {arg_idx} for component in netlist "
                f"for prefix '{prefix}' spec index {spec_idx}. "
            )

        port_id = arg_spec.get("alias", None)
        if port_id is None:
            raise SpecError(
                f"Missing 'alias' for arg index {arg_idx} in "
                f"arg_to_ports for prefix '{prefix}' spec index {spec_idx}"
            )

        # a keyword token (key=value) can never be a node name: if one landed
        # here, this spec bound a value keyword as a port (e.g. the linear E
        # spec swallowing `vol=...` as a control node). Reject so the correct
        # spec variant can win.
        if "=" in node_name:
            raise NetlistError(
                f"Keyword argument '{node_name}' at index {arg_idx} was bound as a "
                f"port in netlist for prefix '{prefix}' spec index {spec_idx}; "
                f"a node name cannot contain '='."
            )

        if_drop = arg_spec.get("drop", False)
        port_direction = component_spec.get("port_directions", {}).get(port_id, "input")
        cur_elem["connections"][port_id] = {
            "node_name": str(node_name),
            "port_direction": port_direction,
            "drop": if_drop,
            **_token_origin(node_name),
        }


def _extract_values(
    cur_elem: dict,
    filtered_args: list[str],
    args_to_values: dict,
    component_name: str,
    prefix: str,
    spec_idx: int,
    model_table: dict[str, dict],
    resolved_model_token: Token | None,
    warnings: list[str] | None,
    specifier_values: dict | None = None,
):

    for arg_idx, arg_spec in args_to_values.items():
        # value spec must have alias
        value_alias = arg_spec.get("alias", None)
        display_alias = arg_spec.get("display_alias", None)
        is_optional = arg_spec.get("is_optional", True)
        is_positional = arg_spec.get("is_positional", "positional")
        is_expression = arg_spec.get("is_expression", False)
        positional_only = is_positional == "positional_only"
        positional_or_keyword = is_positional == "positional_or_keyword"
        # at its index, and only in its key=value spelling (B's V=, E's vol=)
        positional_with_key = is_positional == "positional_with_key"

        if value_alias is None and not positional_only:
            raise SpecError(
                f"Missing 'alias' for arg index {arg_idx} in "
                f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
            )

        if value_alias is None and display_alias is None:
            raise SpecError(
                f"Need 'display_alias' when 'alias' is also missing "
                f"for arg index {arg_idx} in args_to_values for prefix '{prefix}' "
                f"spec index {spec_idx}"
            )

        if display_alias is None:
            display_alias = value_alias[0]

        spec_skin_label = arg_spec.get("skin_label", None)
        fallback_skin_label = f"unknown_{value_alias[0]}" if value_alias else f"unknown_{display_alias}"

        # a value given by a specifier segment (DC 5, AC 1 90): read by
        # _read_specifiers, so it goes straight into its slot. Writing the
        # same value again as a bare value or key=value is ambiguous.
        if specifier_values and arg_idx in specifier_values:
            operand = specifier_values[arg_idx]
            if isinstance(arg_idx, int) and int(arg_idx) < len(filtered_args):
                raise NetlistError(
                    f"Value given twice for component {component_name}: '{filtered_args[int(arg_idx)]}' "
                    f"and a specifier operand '{operand}' for prefix '{prefix}' spec index {spec_idx}"
                )
            keys = [tok.split("=", 1)[0].lower() for tok in filtered_args if "=" in tok]
            if any(key in (value_alias or []) for key in keys):
                raise NetlistError(
                    f"Value given twice for component {component_name}: as a key=value argument and as "
                    f"a specifier operand '{operand}' for prefix '{prefix}' spec index {spec_idx}"
                )
            skin_label = spec_skin_label if spec_skin_label is not None else fallback_skin_label
            cur_elem["values"][skin_label] = {
                "value": str(operand),
                "alias": value_alias,
                "raw_token": operand.raw if isinstance(operand, Token) else str(operand),
            }
            continue
        # value spec must have skin_label, unless it is optional, then it's dropped.
        # if skin_label is None:
        # if not is_optional:
        #     raise SpecError(
        #         f"Missing 'skin_label' for arg index {arg_idx} in "
        #         f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
        #     )

        kw_found_arg = False
        if is_positional == "keyword" or positional_or_keyword:
            # check for arguments specified with key=value format,
            # and if the key matches any of the value_alias, we use that value.
            # Alias matching is case-insensitive (SPICE is case-insensitive).
            for token in filtered_args:
                if "=" in token:
                    key, value = token.split("=", 1)
                    if key.lower() in value_alias:
                        skin_label = spec_skin_label if spec_skin_label is not None else f"unknown_{key}"
                        if skin_label in cur_elem["values"]:
                            raise NetlistError(
                                f"Duplicate value for skin_label '{skin_label}' "
                                f"for prefix '{prefix}' spec index {spec_idx}"
                            )

                        cur_elem["values"][skin_label] = {
                            "value": value,
                            "alias": value_alias,
                            "raw_token": token.raw if isinstance(token, Token) else token,
                        }
                        kw_found_arg = True
                        # if the value is an expression, it must be wrapped in curly braces
                        if is_expression and isinstance(token, Token):
                            _, has_key, after = token.raw.partition("=")
                            raw_value = (after if has_key else token.raw).strip()
                            wrapped = (raw_value.startswith("{") and raw_value.endswith("}")) or (
                                raw_value.startswith("'") and raw_value.endswith("'")
                            )
                            if not wrapped:
                                cur_elem["values"][skin_label]["unwrapped_expression"] = True
                        break

            if not kw_found_arg and not positional_or_keyword:
                default_value = arg_spec.get("default_value", None)
                if not is_optional:
                    raise NetlistError(
                        f"Missing required value for {is_positional} argument with alias: "
                        f"{display_alias} for component {component_name} in netlist for "
                        f"prefix '{prefix}' spec index {spec_idx}"
                    )
                elif default_value is not None:
                    skin_label = spec_skin_label if spec_skin_label is not None else fallback_skin_label
                    cur_elem["values"][skin_label] = {"value": default_value, "alias": value_alias}

        if (
            is_positional == "positional"
            or positional_only
            or positional_with_key
            or (positional_or_keyword and not kw_found_arg)
        ):
            # if the argument is specified as positional, it can only be provided in that position.
            # we take the value from that position in the non_kind_args list.
            arg_idx = int(arg_idx)
            try:
                token = filtered_args[arg_idx]
            except IndexError:
                # only raise if it is not optional,
                # otherwise we attempt to use the default value or skip if no default value is specified.
                if not is_optional:
                    raise NetlistError(
                        f"Missing required value for {is_positional} argument with alias: "
                        f"{display_alias} for component {component_name} in netlist "
                        f"for prefix '{prefix}' spec index {spec_idx}"
                    )
                else:
                    token = None

            if token is None:
                key = None
                value = arg_spec.get("default_value", None)
                if value is None:
                    continue
            elif "=" in token:
                key, value = token.split("=", 1)
            else:
                key, value = None, token

            # the key is mandatory: a bare value does not say which source it
            # is (B V= vs I=), and ngspice rejects it ("mal formed ... instance")
            if positional_with_key and token is not None and key is None:
                raise NetlistError(
                    f"Value '{token}' for component {component_name} must be written as "
                    f"{value_alias[0]}=<value> for prefix '{prefix}' spec index {spec_idx}"
                )

            alias = None
            skin_label = None
            if key is not None:
                # if positional_only is True, then the key should not be present
                if positional_only:
                    raise NetlistError(
                        f"Positional-only value argument expects no key, but got '{key}' "
                        f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )

                # if the key is not in the value_alias, then we check if it is optional or not
                # if optional, we record the value with a fallback key, otherwise we raise an error
                if key.lower() not in value_alias:
                    if not is_optional:
                        raise NetlistError(
                            f"Positional value argument expects aliases {display_alias}, but got '{key}' "
                            f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                        )
                    # another key=value at this index (an instance parameter such as
                    # ac=1 or m=2): the optional value was left out. Apply its default;
                    # the token itself is recorded by the keyword-argument loop below.
                    default_value = arg_spec.get("default_value", None)
                    if default_value is not None:
                        skin_label = spec_skin_label if spec_skin_label is not None else fallback_skin_label
                        cur_elem["values"][skin_label] = {"value": str(default_value), "alias": value_alias}
                    continue
                # if the key is in the value_alias, we record the value with the skin_label
                else:
                    skin_label = spec_skin_label if spec_skin_label is not None else fallback_skin_label
                    alias = value_alias
            # if key is None, we record the value for the current spec
            else:
                skin_label = spec_skin_label if spec_skin_label is not None else fallback_skin_label
                alias = value_alias

            if skin_label in cur_elem["values"]:
                raise NetlistError(
                    f"Duplicate value for skin_label '{skin_label}' "
                    f"for prefix '{prefix}' spec index {spec_idx}"
                )

            cur_elem["values"][skin_label] = {
                "value": str(value),
                "alias": alias,
                "raw_token": token.raw if isinstance(token, Token) else token,
            }

            if is_expression and isinstance(token, Token):
                _, has_key, after = token.raw.partition("=")
                raw_value = (after if has_key else token.raw).strip()
                wrapped = (raw_value.startswith("{") and raw_value.endswith("}")) or (
                    raw_value.startswith("'") and raw_value.endswith("'")
                )
                if not wrapped:
                    cur_elem["values"][skin_label]["unwrapped_expression"] = True

            # for value args that reference another element (e.g. F/H/W's controlling V source)
            if arg_spec.get("is_reference"):
                # names another element (e.g. F/H/W's controlling V source);
                # strict parsing checks that it exists
                cur_elem["values"][skin_label]["reference"] = arg_spec["is_reference"].lower()

    # add in arguments that are not specified in the spec but are keyword args
    for token in filtered_args:
        if "=" in token:
            key, value = token.split("=", 1)
            skin_label = None
            found_value_alias = None
            for arg_idx, arg_spec in args_to_values.items():
                value_alias = arg_spec.get("alias", None)
                if value_alias is None:
                    continue
                if key.lower() in value_alias:
                    skin_label = arg_spec.get("skin_label", None)
                    skin_label = skin_label if skin_label is not None else f"unknown_{key}"
                    found_value_alias = value_alias
                    break

            if found_value_alias is None and warnings is not None:
                # a key this spec does not declare (an instance parameter such as
                # m=2): tolerated, but a spec that declares it wins a tie
                warnings.append(f"Undeclared parameter '{key}' for prefix '{prefix}' spec {spec_idx}")
            found_value_alias = found_value_alias if found_value_alias is not None else [key]
            skin_label = skin_label if skin_label is not None else f"unknown_{key}"
            display_alias = found_value_alias[0]

            if skin_label not in cur_elem["values"]:
                cur_elem["values"][skin_label] = {
                    "value": value,
                    "alias": found_value_alias,
                    "raw_token": token.raw if isinstance(token, Token) else token,
                }

    # add in arguments specified as args to the model directive (.model)
    if resolved_model_token is not None and resolved_model_token.lower() in model_table:
        model_args = model_table[resolved_model_token.lower()].get("args", [])
        for idx, token in enumerate(model_args):
            if "=" not in token:
                raise NetlistError(
                    f"Malformed model argument '{token}' for model '{resolved_model_token}' "
                    f"in netlist for prefix '{prefix}' spec index {spec_idx}. "
                    f"Model arguments must be in key=value format."
                )

            key, value = token.split("=", 1)

            # look if this model arg is defined
            skin_label = None
            found_value_alias = None
            for arg_idx, arg_spec in args_to_values.items():
                value_alias = arg_spec.get("alias", None)
                if value_alias is None:
                    continue
                if key.lower() in value_alias:
                    skin_label = arg_spec.get("skin_label", None)
                    skin_label = skin_label if skin_label is not None else f"unknown_{key}"
                    found_value_alias = value_alias
                    break

            found_value_alias = found_value_alias if found_value_alias is not None else [key]
            skin_label = skin_label if skin_label is not None else f"unknown_{key}"

            # instance args take precedence over model args
            if skin_label not in cur_elem["values"]:
                cur_elem["values"][skin_label] = {
                    "value": value,
                    "alias": found_value_alias,
                    "raw_token": token.raw if isinstance(token, Token) else token,
                }


def _match_spec(
    spec_idx: int,
    prefix: str,
    component_name: str,
    args: list[Token],
    component_spec,
    model_table,
):
    """
    Matches a component spec.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        cur_elem = {"connections": {}, "values": {}, "kind": [], "specifiers": [], "skin_alias": None}
        # components recognised by the config but with no skin yet
        skin_alias = component_spec.get("skin_alias", None)
        if skin_alias is None:
            raise SpecError(f"Missing 'skin_alias' for prefix '{prefix}' spec index {spec_idx}")

        cur_elem["skin_alias"] = skin_alias
        # which of the prefix's specs matched (error messages name its form)
        cur_elem["spec_index"] = spec_idx

        # if component is not of this kind
        kind = component_spec.get("kind", [])
        cur_elem["kind"] = kind
        # model types drive SPICE model-table matching (independent of kind)
        # a list of TYPEs, or {TYPE: {"ref_arg", "before_arg"}} when ngspice
        # reads the model name only on one side of an argument
        model_types = component_spec.get("model_type", [])
        cur_elem["model_type"] = model_types

        # ============================================================================
        # Check for duplicate argument indices and ensure consecutive indices in
        # arg_to_ports and args_to_values (other than keyword args)
        # ============================================================================
        # mapping from non kind and model name argument index to port spec
        arg_to_ports = component_spec.get("arg_to_ports", {})
        # mapping from non kind and model name argument index to value spec,
        args_to_values = component_spec.get("args_to_values", {})
        args_to_values_positional = [
            key
            for key, value in args_to_values.items()
            if value.get("is_positional", "positional") != "keyword"
        ]
        arg_indices_set = {key for key, value in arg_to_ports.items()}
        arg_indices_set.update(args_to_values_positional)

        if len(arg_indices_set) != len(arg_to_ports) + len(args_to_values_positional):
            raise SpecError(
                f"Duplicate argument indices between or within arg_to_ports and "
                f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
            )

        if list(arg_indices_set) != list(range(len(arg_indices_set))):
            raise SpecError(
                f"Argument indices in arg_to_ports and args_to_values must be "
                f"consecutive starting from 0 for prefix '{prefix}' spec index {spec_idx}"
            )

        # ============================================================================
        # resolve model names to model types
        # ============================================================================
        non_model_args: list[Token] = args
        resolved_model_token = None
        non_model_args, resolved_model_token = _filter_spice_model_args(
            prefix, spec_idx, args, model_types, model_table
        )
        cur_elem["model_name"] = resolved_model_token.lower() if resolved_model_token is not None else None
        # ngspice reads every node token before the model name: a spec with
        # more nodes than tokens before the model cannot own the line
        # (Q1 a b c QN 2 is 3 nodes + area for ngspice, not 4 nodes)
        if resolved_model_token is not None:
            model_pos = next(i for i, tok in enumerate(args) if tok is resolved_model_token)
            if model_pos < len(arg_to_ports):
                raise NetlistError(
                    f"The model name '{resolved_model_token}' must come after all {len(arg_to_ports)} "
                    f"nodes for prefix '{prefix}' spec index {spec_idx}"
                )

        cur_elem["model_span"] = getattr(resolved_model_token, "span", None)

        # ============================================================================
        # resolve specifiers and kind keywords
        # ============================================================================
        # declared specifiers must be present (unless optional). Only the
        # bare keyword proves presence: the doc grammar has no key=value
        # form for specifiers (<AC <ACMAG <ACPHASE>> is a bare keyword
        # followed by operands), the corpus confirms zero ac=/dc= lines,
        # and the bare-word test shares the same definition of "specifier
        # exists" that _read_specifiers uses to open segments.
        for sp_key, sp_def in component_spec.get("specifiers", {}).items():
            if sp_def.get("is_optional", False):
                continue

            if not any(t.lower() == sp_key for t in non_model_args):
                raise NetlistError(
                    f"Did not find specifier {sp_key} for component "
                    f"{component_name} in netlist for prefix '{prefix}' "
                    f"spec index {spec_idx}"
                )
        cur_elem["specifiers"] = list(component_spec.get("specifiers", {}).keys())

        # normalize: lexical repair, then per-spec specifier expansion
        glued = _glue_comma_values(_glue_paren_values(non_model_args))
        read = _read_specifiers(glued, component_spec, prefix)
        if read is None:
            # a foreign specifier keyword appeared: this spec cannot own
            # the line; another spec variant may.
            raise NetlistError(
                f"Undeclared specifier found for component {component_name} "
                f"in netlist for prefix '{prefix}' spec index {spec_idx}"
            )

        # the specifier operands go straight into their slots (_extract_values);
        # the rest of the line is matched as usual
        non_model_args, specifier_values = read

        # after filtering model name, we filter kind keywords.
        # kind keywords are matched case-insensitively: SPICE is
        # case-insensitive, so ON/On/on/OFF/off all match.
        filtered_args = [s for s in non_model_args if s.lower() not in kind]
        cur_elem["kind_spans"] = {
            str(t): t.span for t in non_model_args if isinstance(t, Token) and t.lower() in kind
        }
        # the argument the model name must precede / follow, for the strict
        # model order check (absent when the argument is left out)
        cur_elem["model_order"] = _model_order_constraints(
            model_types, resolved_model_token, model_table, filtered_args
        )

        # key=value arguments and their positions: ngspice reads everything
        # after the first one as further parameters, so a model name there
        # fails ("unknown parameter"); strict parsing checks it
        cur_elem["keyword_arg_spans"] = {
            str(t): t.span for t in filtered_args if isinstance(t, Token) and "=" in t and t.span is not None
        }

        if len(filtered_args) == 0:
            raise NetlistError(
                f"No non-keyword arguments found for component {component_name} "
                f"in netlist for prefix '{prefix}' spec index {spec_idx}"
            )

        # checks for kind keywords that are not in the spec's kind
        # (case-insensitive: SPICE kind keywords may be written in any case)
        kind_keyword_matchings = [k.lower() in _get_all_kind_keywords() for k in filtered_args]
        if any(kind_keyword_matchings):
            kind_keyword_index = kind_keyword_matchings.index(True)
            bad_keyword = filtered_args[kind_keyword_index]
            raise NetlistError(
                f"Invalid kind keyword {bad_keyword} for "
                f"component {component_name} in netlist for prefix '{prefix}' "
                f"spec index {spec_idx}"
            )

        if len(kind) > 0 and len(filtered_args) == len(non_model_args):
            raise NetlistError(
                f"Did not find kind keyword in arguments for component {component_name} "
                f"in netlist for prefix '{prefix}' spec index {spec_idx}"
            )

        # ============================================================================
        # Check for:
        # 1. positional arguments after keyword arguments.
        # 2. more positional arguments than the spec declares.
        # ============================================================================
        # preliminary check for positional arguments after keyword arguments
        # positional_arg_end is the length of the positional args, note that we allow
        # positional args that are specified in a keyword arg form as long as they
        # are in the correct position.
        positional_arg_end = len(filtered_args)
        for arg_idx, arg in enumerate(filtered_args):
            if "=" in arg:
                key, value = arg.split("=", 1)
                arg_spec = args_to_values.get(arg_idx, None)

                if arg_spec is None:
                    # undeclared keyword (instance param) — positional section
                    # ends here (exclusive). Keywords at/after that point are
                    # simply tolerated (already beyond the positional section).
                    if arg_idx < positional_arg_end:
                        positional_arg_end = arg_idx

                elif arg_spec.get("is_positional", "positional") == "positional_only":
                    display_alias = arg_spec.get("display_alias", None)
                    if display_alias is None:
                        raise SpecError(
                            f"Missing 'display_alias' for positional_only arg index {arg_idx} in "
                            f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                        )

                    raise SpecError(
                        f"Positional-only argument specified as keyword argument with key '{key}', "
                        f"spec in args_to_values with display alias {display_alias}, "
                        f"for prefix '{prefix}' spec index {spec_idx}"
                    )
                # could be positional arg specified as keyword arg in the correct position
                # check for it
                elif arg_spec.get("is_positional", "positional") != "keyword":
                    value_alias = arg_spec.get("alias", None)
                    if value_alias is None:
                        raise SpecError(
                            f"Missing 'alias' for arg index {arg_idx} in "
                            f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                        )
                    if key.lower() not in value_alias and arg_idx < positional_arg_end:
                        positional_arg_end = arg_idx
                # now definitely a keyword arg
                elif arg_idx < positional_arg_end:
                    positional_arg_end = arg_idx

            elif arg_idx > positional_arg_end:
                raise NetlistError(
                    f"Positional argument after keyword argument in netlist "
                    f"for prefix '{prefix}' spec index {spec_idx}"
                )

        # overflow: more positional args than the spec declares. This is the
        # subset-protection check — without it, a line carrying extra tokens
        # (e.g. a kind keyword or model name this spec doesn't declare) would
        # be silently absorbed by a more permissive spec variant.
        if positional_arg_end > len(arg_indices_set):
            raise NetlistError(
                f"There exists more positional arguments than defined in the spec "
                f"for component {component_name} in netlist for prefix '{prefix}' "
                f"spec index {spec_idx}"
            )

        # ============================================================================
        # Extract node connections and values
        # ============================================================================
        _extract_connections(cur_elem, filtered_args, arg_to_ports, component_spec, prefix, spec_idx)
        _extract_values(
            cur_elem,
            filtered_args,
            args_to_values,
            component_name,
            prefix,
            spec_idx,
            model_table,
            resolved_model_token,
            warnings,
            specifier_values,
        )

        return cur_elem, errors, warnings
    except (SpecError, NetlistError, IndexError) as e:
        errors.append(str(e))
        return None, errors, warnings


def _parse_line(
    line: str,
    model_table: dict[str, dict] | None = None,
    subckt_table: dict[str, list[str]] | None = None,
):
    """Parse a single line of the netlist into a component name and its tokens.

    Parameters
    ----------
    line : str
        The (preprocessed) netlist line.
    model_table : dict[str, dict] | None
        The model table of _preprocess_lines (model name, lower case ->
        {"name", "model_type", "args"}). A token found in it is the line's
        model name: it is removed from the line, and its TYPE must be in the
        spec's `model_type` (e.g. Q with a PNP card matches the PNP specs).
    subckt_table : dict[str, list[str]] | None
        The subckt table of _preprocess_lines (subckt name, lower case ->
        port names). An X line names its pins after the ports of its
        definition (numbered 1, 2, ... when it is not in the table).


    Returns
    -------
    component_name: str
        The name of the component (e.g., R1, C1, etc.)
    element: dict
    """

    tokens = _get_tokens(line)
    args = tokens[1:]
    component_name = str(tokens[0])
    prefix = _get_prefix(component_name)
    model_table = model_table or {}
    subckt_table = subckt_table or {}
    # if not generic
    if prefix is not None and prefix not in EXPLICIT_SPICE_GENERICS:
        component_specs = TO_SKIN_CONFIG.get(prefix, [{}])
        elem = {
            "prefix": prefix,
            "kind": [],
            "specifiers": [],
            "if_generic": False,
            "connections": {},
            "values": {},
            "skin_alias": None,
        }

        # ============================================================================
        # Try each spec, collect errors, pick the candidate with fewest errors.
        # ============================================================================
        candidates: list[tuple[dict, list[str], list[str]]] = []
        for spec_idx, component_spec in enumerate(component_specs):
            cur_elem, errors, warnings = _match_spec(
                spec_idx, prefix, component_name, args, component_spec, model_table
            )
            candidates.append((cur_elem, errors, warnings))

        # ============================================================================
        # Select the candidate with the least errors and warnings.
        # Errors take precedence over warnings, though if errors, candidate is None.
        # ============================================================================
        best_elem, _, _ = min(candidates, key=lambda c: (len(c[1]), len(c[2])))
        if best_elem is not None:
            elem.update(best_elem)
        else:
            # render the spec grammar for each candidate to show what was expected
            patterns = [
                _render_spec_grammar(prefix, component_specs[spec_idx], show_skin=False)
                for spec_idx in range(len(candidates))
            ]
            msg = [
                (
                    f"Spec {spec_idx}: {patterns[spec_idx]}\n- Errors: {'; '.join(errs)}\n"
                    if errs
                    else f"- Warnings: {'; '.join(warns)}\n"
                    if warns
                    else ""
                )
                for spec_idx, (elem, errs, warns) in enumerate(candidates)
                if errs
            ]

            logger.error(f"All specs failed for prefix '{prefix}' on line '{line}'\n{''.join(msg)}")
            raise ValueError(f"All specs failed for prefix '{prefix}' on line '{line}'\n{''.join(msg)}")

    else:  # if generic
        # take the last arg and kwargs as value, and all else connections.
        elem = {
            "prefix": prefix,
            "kind": [],
            "specifiers": [],
            "if_generic": True,
            "connections": {},
            "values": {},
            "skin_alias": "generic",
        }

        # ============================================================================
        # Parse subckt statement (i.e. prefix X)
        # ============================================================================
        if prefix == SUBCKT_PREFIX:
            # ============================================================================
            # Find the most likely subckt name in the args,
            # which is the last token that is not a key=value pair or a param keyword.
            # ============================================================================
            param_idx = None
            for idx in range(len(args) - 1, -1, -1):
                # subckt allow instance parameters (e.g. X1 a b SUB params: rv=2k)
                if args[idx].lower().startswith("params:"):
                    # if the token is params:rv=2k, we split it into two tokens: params: and rv=2k
                    if args[idx].lower().strip() != "params:":
                        second_token = args[idx].lower().split(":")[1]
                        args = args[:idx] + ["params:", second_token] + args[idx + 1 :]

                    param_idx = idx
                    break

            args_before_param = args[:param_idx] if param_idx is not None else args
            args_after_param = args[param_idx + 1 :] if param_idx is not None else []

            if param_idx is not None:
                for idx in range(len(args_after_param) - 1, -1, -1):
                    if "=" in args_after_param[idx]:
                        continue

                    raise NetlistError(
                        f"Subckt instance {component_name} defines instance parameters not "
                        f"in key=value format: {args_after_param[idx]}"
                    )

            first_kw_arg_idx = None
            for idx in range(len(args_before_param) - 1, -1, -1):
                if "=" in args_before_param[idx]:
                    first_kw_arg_idx = idx
                    continue
                break

            if first_kw_arg_idx is not None:
                args_connections = args_before_param[:first_kw_arg_idx]
                args_non_param_kw_args = args_before_param[first_kw_arg_idx:]
            else:
                args_connections = args_before_param
                args_non_param_kw_args = []

            if len(args_connections) < 1:
                raise NetlistError(
                    f"Subckt instance {component_name} has no connections before the subckt name"
                )
            subckt_name = args_connections.pop()
            subckt_def = subckt_table.get(subckt_name.lower(), None)
            # without a matching definition (undefined, or another port count)
            # the pins are numbered, like any generic box; strict parsing
            # rejects both cases
            if subckt_def is None or len(args_connections) != len(subckt_def):
                subckt_def = [f"{idx + 1}" for idx in range(len(args_connections))]
            elem["subckt"] = subckt_name
            # ============================================================================
            # extract the subckt connections and values.
            # ============================================================================

            # extract connections
            # exclude subckt name
            for idx, token in enumerate(args_connections):
                port_name = subckt_def[idx]
                elem["connections"][port_name] = {
                    "node_name": str(token),
                    "port_direction": "input",
                    **_token_origin(token),
                }

            # extract values
            for idx, token in enumerate(args_non_param_kw_args + args_after_param):
                key, value = token.split("=", 1)
                elem["values"][f"unknown_{key}"] = {
                    "value": value,
                    "alias": [key],
                    "raw_token": token.raw if isinstance(token, Token) else token,
                }

            # make the value label on the skin the subckt name
            elem["values"]["value"] = {
                "value": subckt_name,
                "alias": ["value"],
                "raw_token": subckt_name.raw if isinstance(subckt_name, Token) else subckt_name,
            }

        else:
            # ============================================================================
            # extract the connections and values for a non-subckt generic component
            # ============================================================================
            for idx in range(len(args) - 1, -1, -1):
                if "=" in args[idx]:
                    continue
                # subckt allow instance parameters (e.g. X1 a b SUB params: rv=2k)
                if args[idx].lower().startswith("param"):
                    continue

                break

            for idx, token in enumerate(args):
                if "=" in token:
                    key, value = token.split("=", 1)
                    elem["values"][key] = {"value": value, "alias": [key]}
                elif idx == len(args) - 1:
                    elem["values"]["value"] = {"value": str(token), "alias": ["value"]}
                else:  # node
                    # bare-number pin keys; the generic skin template draws these
                    # verbatim next to each port, so keys == drawn pin text
                    elem["connections"][f"{idx + 1}"] = {
                        "node_name": str(token),
                        "port_direction": "input",
                        **_token_origin(token),
                    }

    return component_name, elem


def _parse_netlist(text: str, strict: bool | None = None):
    """Parse netlist text into {component_name: element}.

    Every line is parsed before failing: all lines that match no spec (and
    duplicate names) are raised together as one NetlistError. Strict checks
    run only once every line parsed.

    Parameters
    ----------
    text : str
        The netlist text.
    strict : bool | None
        SPICE only: strict parsing (see _strict_violations). All violations
        are collected and raised together as one NetlistError, one per line,
        so they can be fed back to the LLM in a single round. None uses the
        STRICT_PARSING config default.
    """
    strict = STRICT_PARSING if strict is None else strict
    text_lines, model_table, subckt_table, meta = _preprocess_lines(text.splitlines())

    parsed_netlist = {}
    # "component_name": {
    #     "prefix": prefix,
    #     "skin_alias": skin_alias,
    #     "if_generic": True/False,
    #     "connections": {
    #         "port id": {
    #             "node_name": node name,
    #             "port_direction": direction,
    #             "braced": written as {node},
    #             "span": (start, end) of the token in its element line,
    #         },
    #     },
    #     "values": {
    #         "skin_label for value1": value1
    #         "skin_label for value2": value2
    #     }
    # }

    # every line is parsed before failing, so all bad lines are reported in
    # one error (fed back to the LLM in a single round). SpecError (a broken
    # config, not a bad netlist) is not collected.
    line_errors: list[str] = []
    for line in text_lines:
        logger.debug(f"Parsing line: {line}")
        try:
            component_name, element = _parse_line(line, model_table=model_table, subckt_table=subckt_table)
        except (NetlistError, ValueError) as e:
            line_errors.append(str(e))
            continue

        if component_name in parsed_netlist:
            line_errors.append(f"Exact duplicate component name found in netlist: {component_name}")
            continue

        parsed_netlist[component_name] = element

    # ========================================================================
    # Check for duplicate component names (case-insensitive)
    # ========================================================================
    seen_names: dict[str, list[str]] = {}
    for component_name in parsed_netlist.keys():
        seen_names.setdefault(component_name.lower(), [])
        seen_names[component_name.lower()].append(component_name)

    for component_name, duplicates in seen_names.items():
        if len(duplicates) > 1:
            line_errors.append(
                f"Duplicate component names found in netlist (spice is case-insensitive): "
                f"{', '.join(duplicates)}"
            )

    if line_errors:
        # strict checks are skipped: on an incomplete parse they would report
        # follow-on problems (references to the failed lines, missing ground)
        raise ValueError(_format_line_errors(line_errors))

    if strict:
        violations = _strict_violations(
            parsed_netlist,
            subckt_table,
            meta,
            model_table,
        )

        if violations:
            raise NetlistError(
                f"Strict parsing found {len(violations)} problem(s):\n"
                + "\n".join(f"- {v}" for v in violations)
            )

    return parsed_netlist


def to_yosys_json(
    netlist_text: str,
    module_name: str = "circuit",
    strict: bool | None = None,
) -> tuple[dict, dict]:
    """Parse a netlist and convert it to yosys JSON for netlistsvg.

    `strict` is forwarded to _parse_netlist (None = the STRICT_PARSING
    config default).
    Returns (yosys_json, parsed_netlist).
    """
    parsed_netlist = _parse_netlist(netlist_text, strict=strict)
    id_map, find, ground_net_id = _assign_net_ids(parsed_netlist)

    cells = {}

    if ground_net_id is not None:
        cells["gnd"] = {
            "type": "gnd",
            "port_directions": {"A": "input"},
            "connections": {"A": [ground_net_id]},
            "attributes": {"name": "GND"},
        }

    for component_name, element in parsed_netlist.items():
        if component_name in cells:
            raise ValueError(f"Duplicate component name: {component_name}")

        element_connections = element["connections"]
        nodes = list(element_connections.keys())
        if not nodes:
            continue

        if element["if_generic"]:
            # Assign half of the connections as input and half as output,
            # in order of appearance.
            half = (len(nodes) + 1) // 2
            element_connections = {
                pid: {
                    "node_name": conn["node_name"],
                    "port_direction": "input" if i < half else "output",
                }
                for i, (pid, conn) in enumerate(element_connections.items())
            }

        connections = {}
        port_directions = {}
        for pid, connection in element_connections.items():
            node_name = connection["node_name"]
            if connection.get("drop", False):
                # drop this connection when it is NOT supported in the corresponding skin.
                # we drop here instead of in _parse_netlist because we would like to
                # separate the skin logic from the parsing logic.
                continue
            connections[pid] = [id_map.get(find(node_name), 0)]
            port_directions[pid] = connection["port_direction"]

        if not connections:
            continue

        # skin_alias is a list in spec path, a string in generic path.
        # Normalise to a string for the cell type.
        skin_alias = element["skin_alias"]
        if skin_alias:
            if isinstance(skin_alias, list):
                skin_alias = random.choice(skin_alias)
        else:
            skin_alias = "generic"

        attributes = {"ref": component_name}
        elem_values = element["values"]
        values = {k: v["value"] for k, v in elem_values.items()}
        attributes.update(values)

        cells[component_name] = {
            "type": skin_alias,
            "port_directions": port_directions,
            "connections": connections,
            "attributes": attributes,
        }

    return ({"modules": {module_name: {"cells": cells}}}, parsed_netlist)


if __name__ == "__main__":
    import sys
    # input from stdin

    netlist_text = sys.stdin.read()
    yosys_json = to_yosys_json(netlist_text)
    import json

    print(json.dumps(yosys_json, indent=2))
