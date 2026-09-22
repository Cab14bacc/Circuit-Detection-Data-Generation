"""SPICE → Yosys JSON converter for netlistsvg's lcapy skin.

Handles Lcapy SPICE netlists with or without layout hints. Maps component
prefixes to skin cells, merges nodes via W wires, emits a gnd cell for
ground-touching nets, and recognises Vcc/Vee rails.

Uses the custom lcapy.svg skin (co-located with this file) which mirrors
Lcapy's Circuitikz visual vocabulary.

Two netlist dialects are supported, selected by CONVERT_CONFIG.NETLIST_FORMAT
("lcapy" | "spice") in config.py:
  - "lcapy": simplified lcapy grammar (default_convert_config.TO_SKIN_CONFIG).
  - "spice": SPICE/ngspice/LTspice grammar (spice_convert_config.TO_SKIN_CONFIG).
    SPICE-specific logic in this module (directive filtering, paren-joined
    function values like SINE(...), model-name args) is guarded by IS_SPICE;
    the shared parsing skeleton is dialect-agnostic.

This module holds the parsing pipeline (preprocessing, spec matching, line
and netlist parsing, net merging, yosys JSON). Its helpers live next to it:
  - dialect.py : dialect selection (IS_SPICE, TO_SKIN_CONFIG, ...) and constants
  - errors.py  : NetlistError, SpecError
  - tokens.py  : Token, _get_tokens and the lexical repairs of SPICE args
  - strict.py  : strict SPICE parsing checks (_strict_violations)
  - grammar.py : TO_SKIN_CONFIG -> grammar listing (config_to_grammar)
The names below are re-exported, so `convert.X` keeps working.
"""

from __future__ import annotations
from ..configs.config import get_logger
from functools import cache
from uuid import uuid4
import random
import re
import copy

# dialect state is bound here at import: reload dialect, then this module, to
# switch dialect (see tests/conftest.py::_reload_convert)
from .dialect import (  # noqa: F401 (re-exported)
    COUPLED_INDUCTOR_PREFIXES,
    EXPLICIT_SPICE_GENERICS,
    EXPRESSION_CONSTANTS,
    GROUND_NAMES,
    IS_SPICE,
    MODEL_CONFIG,
    NETLIST_FORMAT,
    SIMULATION_ENABLED,
    STRICT_PARSING,
    SUBCKT_PREFIX,
    TO_SKIN_CONFIG,
    WIRE,
)
from .errors import NetlistError, SpecError
from .grammar import (  # noqa: F401 (re-exported)
    _render_spec_grammar,
    config_to_grammar,
    model_config_to_grammar,
)
from .strict import STRICT_ALLOWED_DIRECTIVES, _strict_violations  # noqa: F401 (re-exported)
from .tokens import (
    Token,  # noqa: F401 (re-exported)
    _get_tokens,
    _glue_comma_values,
    _glue_paren_values,
    _strip_hints,
    _token_origin,
)

logger = get_logger(__name__)


@cache
def _get_all_kind_keywords():
    """Return a set of all kind keywords across all component specs in TO_SKIN_CONFIG."""
    all_kinds = set()
    for prefix, component_specs in TO_SKIN_CONFIG.items():
        for spec in component_specs:
            kinds = spec.get("kind", [])
            all_kinds.update(kinds)
    return all_kinds


@cache
def _get_spice_directive_prefixes():
    """SPICE dot-directive prefixes that are filtered before parsing.
    Element lines never start with '.'."""
    return {
        ".model",
        ".lib",
        ".include",
        ".inc",
        ".tran",
        ".ac",
        ".dc",
        ".op",
        ".four",
        ".fourier",
        ".param",
        ".params",
        ".step",
        ".temp",
        ".ic",
        ".nodeset",
        ".measure",
        ".meas",
        ".noise",
        ".save",
        ".probe",
        ".options",
        ".option",
        ".plot",
        ".print",
        ".pz",
        ".sens",
        ".tf",
        ".disto",
        ".global",
        ".func",
        ".if",
        ".elseif",
        ".else",
        ".endif",
        ".endc",
        ".backanno",
        ".end",
        ".wave",
        ".net",
        ".title",
        ".width",
    }


# --- SPICE arg normalization (specifier expansion; lexical repair: tokens.py) --
def _expand_specifiers(args: list[str], component_spec: dict, prefix: str) -> list[str] | None:
    """SPICE only: rewrite V/I mode-segment streams into the uniform
    key=value token form that args_to_values slots bind.

    The ngspice source-tail grammar is a sequence of mode segments, each
    opened by a bare specifier keyword (DC/AC/...) with a fixed operand
    arity given by the referenced refs:

        V1 a b DC 5 AC 1 90   ->   [a, b, 5, ac=1, phase=90]

    Each ref resolves against args_to_values:
      - int ref  (positional slot): operand emitted BARE (keyword transparent)
        -> lands in that positional slot.
      - str ref  (kw-slot): operand emitted as <first-alias>=<operand>.
    A missing operand (segment opened at end of line) emits nothing; the
    slot's default_value applies at matching time (bare "AC" == AC 1).

    The specifier keyword itself is CONSUMED (never appended). Segments
    terminate on: a '='-token, a kind keyword, or a bare keyword that is a
    declared specifier of any spec of this prefix. A terminator that is a
    FOREIGN specifier (declared only by another spec of the same prefix)
    means this spec cannot own the line: return None so the candidate fails.

    Returns the rewritten arg list, or None on a foreign specifier.
    """
    specifiers: dict = component_spec.get("specifiers", {})
    if not specifiers:
        return args  # nothing declared -> nothing to do

    # segment openers declared by THIS spec
    openers = {k.upper(): k for k in specifiers}
    # foreign openers: declared by other specs of the same prefix
    foreign: set[str] = set()
    for other in TO_SKIN_CONFIG.get(prefix, []):
        if other is component_spec:
            continue
        foreign.update(k.upper() for k in other.get("specifiers", {}))
    foreign -= set(openers)
    kinds = {k.upper() for k in _get_all_kind_keywords()}

    def _terminator(tok: str) -> bool:
        return "=" in tok or tok.upper() in kinds or tok.upper() in foreign

    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if "=" in tok:
            out.append(tok)
            i += 1
            continue
        up = tok.upper()
        if up in openers:
            entry = specifiers[openers[up]]  # {"refs": [...], "is_optional":, "default":}
            refs = entry.get("refs", [])
            i += 1  # consume the specifier keyword itself
            if i >= len(args) or _terminator(args[i]):
                # bare specifier (e.g. 'AC' with no operands): the specifier's
                # own "default" encodes the documented default (AC == AC 1),
                # emitted in the first str ref's key=value form
                for ref in refs:
                    if isinstance(ref, str) and entry.get("default", None) is not None:
                        out.append(f"{ref}={entry['default']}")
                        break
                continue  # nothing else to consume for this segment
            for ref in refs:
                if i >= len(args):
                    break  # operand missing -> default_value applies at match
                nxt = args[i]
                if _terminator(nxt):
                    break
                if isinstance(ref, int):
                    out.append(nxt)  # bare operand -> positional slot
                else:
                    out.append(f"{ref}={nxt}")  # slot key = operand -> kw-slot
                i += 1
            # next iteration may open the following segment
        elif up in foreign:
            # a specifier declared only by ANOTHER spec of this prefix: this
            # spec cannot own the line — fail the candidate so the spec that
            # declares it can win (this is what routes V lines into the
            # DC-source vs AC-source spec split).
            return None
        else:
            out.append(tok)
            i += 1
            continue
    return out


def _get_prefix(component_name: str) -> str | None:
    # Mechanical analogues are case-sensitive single-letter (k, m, r)
    prefix_list = [str(p) for p in TO_SKIN_CONFIG.keys()]
    # Sort by length descending
    prefix_list.sort(key=lambda x: 10000 + len(x) if str().islower() else len(x), reverse=True)

    # look for exact match first, then case-insensitive match, then wire "W" (lcapy only), else None (generic)
    for p in prefix_list:
        if component_name.startswith(p):
            return p

    for p in prefix_list:
        if component_name.upper().startswith(p.upper()):
            return p.upper()

    # lcapy only: W is the structural wire. SPICE has no wire element (W is
    # the current-controlled switch there), so this fallback is lcapy-guarded.
    if not IS_SPICE and component_name.upper().startswith(WIRE):
        return WIRE

    logger.warning(f"Unknown component prefix for {component_name}, using generic skin cell.")

    return None


def _preprocess_lines(text_lines: list[str]):
    """
    Merge continuation lines starting with '+' into the previous line.
    Remove comment and empty lines. Return a list of cleaned lines.

    SPICE (IS_SPICE): additionally filters dot-directives (.model, .lib,
    .tran, .backanno, .end, ...) which carry simulation/model information
    that the schematic render does not use. The `.model mname type` mappings
    are extracted into a model table FIRST (returned alongside the lines) —
    the model type is the specialization that spec `kind` matching uses.
    The bodies of .subckt/.ends and .control/.endc blocks are skipped.

    Returns
    -------
    merged_stripped_lines : list[str]
        The element lines, continuation lines merged.
    model_table : dict[str, dict]
        SPICE: model name (upper) -> {"model_type", "args"}.
    subckt_table : dict[str, list[str]]
        SPICE: subckt name (upper) -> list of port names.
    meta : dict
        SPICE netlist-level information used by strict parsing:
        "params" (.param name (upper) -> value text), "directives" (list of
        (directive, line) for every top-level dot-directive) and "coupling"
        (the K coupled-inductor lines, which are not parsed as elements).
    """
    merged_stripped_lines = []
    current_line = ""
    model_table: dict[str, dict] = {}  # model name (upper) -> model type (upper)
    # SPICE: ignore lines inside .subckt/.ends blocks (we treat subckt as generics, only nodes are parsed)
    subckt_table: dict[str, list[str]] = {}  # subckt name (upper) -> list of port names
    meta: dict = {"params": {}, "directives": [], "coupling": []}
    in_subckt = False
    # SPICE: .control/.endc blocks hold ngspice scripts (run, plot, shell ...),
    # not elements, so their lines are skipped as well.
    in_control = False
    for line in text_lines:
        stripped_line = _strip_hints(line)
        if not stripped_line:
            continue

        if stripped_line[0] in "*#":
            continue

        if IS_SPICE and (in_subckt or in_control):
            # ignore all lines inside a .subckt or .control block
            directive = stripped_line.split()[0].lower()
            if in_subckt and directive == ".ends":
                in_subckt = False
            if in_control and directive == ".endc":
                in_control = False
            continue

        if IS_SPICE and stripped_line[0] == ".":
            # SPICE dot-directives: .model lines populate the model table
            # (name -> type), .param lines the parameter table, the rest are
            # simulation info the schematic render does not use.
            # (.subckt expansion not supported yet.)
            directive = stripped_line.split()[0].lower()
            meta["directives"].append((directive, stripped_line))

            if directive == ".control":
                in_control = True
                continue

            if directive in (".param", ".params"):
                # .param a=1k b={2*a}; spaces around '=' are tolerated here
                assignments = re.sub(r"\s*=\s*", "=", stripped_line[len(directive) :])
                for tok in _get_tokens(assignments):
                    if "=" in tok:
                        key, value = tok.split("=", 1)
                        meta["params"][key.upper()] = value
                    else:
                        raise NetlistError(
                            f"Malformed .param directive: {stripped_line}, "
                            f"should follow the '.param <name>=<value> ...' format"
                        )
                continue

            if directive == ".model":
                parts = stripped_line.split()
                # .model <name> <type>(pname1=pval1 pname2=pval2 ... )
                # .model <name> <type> (pname1=pval1 pname2=pval2 ... )
                # both are correct, parse for both
                if len(parts) >= 3:
                    # "name" keeps the spelling of the card, for messages
                    model_table[parts[1].upper()] = {"name": parts[1]}
                    model_type_and_args = parts[2].upper().split("(")
                    model_table[parts[1].upper()]["model_type"] = model_type_and_args[0]
                    model_args = parts[2].upper().split("(")[1] if len(model_type_and_args) > 1 else ""
                    # strip the enclosing parens BEFORE tokenizing: with the
                    # parens present, _get_tokens paren-joins the entire arg
                    # list into a single token (SPICE function-value joining)
                    joined = (model_args + " " + " ".join(parts[3:])).strip().strip("()").strip()
                    model_table[parts[1].upper()]["args"] = [tok for tok in _get_tokens(joined) if tok]
                else:
                    raise NetlistError(
                        f"Malformed .model directive: {stripped_line}, "
                        f"should follow the '.model <name> <type>' format"
                    )

            if directive == ".subckt":
                parts = stripped_line.split()
                if len(parts) >= 3:
                    in_subckt = True
                    subckt_port_names = []
                    for arg in parts[2:]:
                        if "PARAMS:" in arg.upper():
                            break
                        if "=" in arg:
                            break
                        subckt_port_names.append(arg)
                    subckt_table[parts[1].upper()] = subckt_port_names
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

        if IS_SPICE and stripped_line[0] in COUPLED_INDUCTOR_PREFIXES:
            # SPICE coupled inductors (Kname Lname1 Lname2 ... k): args are
            # INDUCTOR REFERENCES, not nodes — the coupling has no visual
            # form for now, therefore the line is dropped entirely.
            # The skin has no cell for it, and
            # A generic cell would fabricate fake nodes named "L1", "L2",
            # so the line is dropped entirely here. It is kept in meta so
            # strict parsing can check the inductor references.
            logger.debug(f"Dropping coupled-inductor line (no visual form, no nodes): {stripped_line[:60]}")
            meta["coupling"].append(stripped_line)
            continue

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
    resolved_model_name: str | None,
):

    for arg_idx, arg_spec in args_to_values.items():
        # value spec must have alias
        value_alias = arg_spec.get("alias", None)
        is_optional = arg_spec.get("is_optional", True)
        is_positional = arg_spec.get("is_positional", True)

        if value_alias is None and not is_positional:
            if not is_optional:
                raise SpecError(
                    f"Missing 'alias' for arg index {arg_idx} in "
                    f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                )
            # skip if there is no corresponding alias in the spec
            continue

        skin_label = arg_spec.get("skin_label", None)
        # value spec must have skin_label, unless it is optional, then it's dropped.
        # if skin_label is None:
        # if not is_optional:
        #     raise SpecError(
        #         f"Missing 'skin_label' for arg index {arg_idx} in "
        #         f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
        #     )

        if not is_positional:
            # check for arguments specified with key=value format,
            # and if the key matches any of the value_alias, we use that value.
            # Alias matching is case-insensitive (SPICE is case-insensitive).
            value_alias_l = [a.lower() for a in value_alias]
            found_arg = False
            for token in filtered_args:
                if "=" in token:
                    key, value = token.split("=", 1)
                    if key.lower() in value_alias_l:
                        skin_label = skin_label if skin_label is not None else f"unknown_{key}"
                        if skin_label in cur_elem["values"]:
                            raise SpecError(
                                f"Duplicate value for skin_label '{skin_label}' "
                                f"for prefix '{prefix}' spec index {spec_idx}"
                            )

                        cur_elem["values"][skin_label] = {"value": value, "alias": value_alias}
                        found_arg = True
                        break

            if not found_arg:
                default_value = arg_spec.get("default_value", None)
                if not is_optional:
                    raise NetlistError(
                        f"Missing required value for non-positional argument with alias: {value_alias} for "
                        f"component {component_name} in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )
                elif default_value is not None:
                    skin_label = skin_label if skin_label is not None else f"unknown_{value_alias[0]}"
                    cur_elem["values"][skin_label] = {"value": default_value, "alias": value_alias}
        else:
            # if the argument is specified as positional, it can only be provided in that position.
            # we take the value from that position in the non_kind_args list.
            arg_idx = int(arg_idx)
            try:
                value_arg = filtered_args[arg_idx]
            except IndexError:
                # only raise if it is not optional,
                # otherwise we attempt to use the default value or skip if no default value is specified.
                if not is_optional:
                    raise NetlistError(
                        f"Missing required value for positional argument with alias: {value_alias} for "
                        f"component {component_name} in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )
                else:
                    value_arg = None

            if value_arg is None:
                key = None
                value = arg_spec.get("default_value", None)
                if value is None:
                    continue
            elif "=" in value_arg:
                key, value = value_arg.split("=", 1)
            else:
                key, value = None, value_arg

            if key is not None and key.lower() not in [a.lower() for a in value_alias]:
                if not is_optional:
                    raise NetlistError(
                        f"Positional value argument expects aliases {value_alias}, but got '{key}' "
                        f"in netlist for prefix '{prefix}'d spec index {spec_idx}"
                    )
            else:
                fallback_key = key if key is not None else value_alias[0]
                skin_label = skin_label if skin_label is not None else f"unknown_{fallback_key}"
                cur_elem["values"][skin_label] = {"value": str(value), "alias": value_alias}
                # for value args that reference another element (e.g. F/H/W's controlling V source)
                if arg_spec.get("is_reference"):
                    # names another element (e.g. F/H/W's controlling V source);
                    # strict parsing checks that it exists
                    cur_elem["values"][skin_label]["reference"] = arg_spec["is_reference"]

    # add in arguments that are not specified in the spec but are keyword args
    for arg in filtered_args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            skin_label = None
            found_value_alias = None
            for arg_idx, arg_spec in args_to_values.items():
                value_alias = arg_spec.get("alias", None)
                if value_alias is None:
                    continue
                value_alias_l = [a.lower() for a in value_alias]
                if key.lower() in value_alias_l:
                    skin_label = arg_spec.get("skin_label", None)
                    skin_label = skin_label if skin_label is not None else f"unknown_{key}"
                    found_value_alias = value_alias
                    break

            found_value_alias = found_value_alias if found_value_alias is not None else [key]
            skin_label = skin_label if skin_label is not None else f"unknown_{key}"

            if skin_label not in cur_elem["values"]:
                cur_elem["values"][skin_label] = {"value": value, "alias": found_value_alias}

    # add in arguments specified as args to the model directive (.model)
    if resolved_model_name is not None and resolved_model_name in model_table:
        model_args = model_table[resolved_model_name].get("args", [])
        for idx, arg in enumerate(model_args):
            if "=" not in arg:
                raise NetlistError(
                    f"Malformed model argument '{arg}' for model '{resolved_model_name}' "
                    f"in netlist for prefix '{prefix}' spec index {spec_idx}. "
                    f"Model arguments must be in key=value format."
                )

            key, value = arg.split("=", 1)

            # look if this model arg is defined
            skin_label = None
            found_value_alias = None
            for arg_idx, arg_spec in args_to_values.items():
                value_alias = arg_spec.get("alias", None)
                if value_alias is None:
                    continue
                value_alias_l = [a.lower() for a in value_alias]
                if key.lower() in value_alias_l:
                    skin_label = arg_spec.get("skin_label", None)
                    skin_label = skin_label if skin_label is not None else f"unknown_{key}"
                    found_value_alias = value_alias
                    break

            found_value_alias = found_value_alias if found_value_alias is not None else [key]
            skin_label = skin_label if skin_label is not None else f"unknown_{key}"

            # instance args take precedence over model args
            if skin_label not in cur_elem["values"]:
                cur_elem["values"][skin_label] = {"value": value, "alias": found_value_alias}


def _filter_spice_model_args(prefix, spec_idx, args, model_types, model_table):
    resolved_model_name = None
    resolved_model_types = [
        (model_table[tok.upper()]["model_type"], idx)
        for idx, tok in enumerate(args)
        if tok.upper() in model_table
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
        if model_types and resolved_model_type.lower() not in [k.lower() for k in model_types]:
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


def _match_spec(
    spec_idx,
    prefix,
    component_name,
    args,
    component_spec,
    model_table,
):
    errors: list[str] = []
    warnings: list[str] = []

    try:
        cur_elem = {"connections": {}, "values": {}, "kind": [], "specifiers": [], "skin_alias": None}
        skin_alias = component_spec.get("skin_alias", None)
        # components recognised by the config but with no skin yet
        # (lcapy wire: skin_alias None is its normal state)
        parse_only = component_spec.get("parse_only", False)
        if skin_alias is None and prefix != WIRE and not parse_only:
            raise SpecError(f"Missing 'skin_alias' for prefix '{prefix}' spec index {spec_idx}")
        cur_elem["skin_alias"] = skin_alias
        if parse_only:
            cur_elem["parse_only"] = True
        # False when ngspice cannot simulate this spec (strict + simulate rejects it)
        cur_elem["simulatable"] = component_spec.get("simulatable", True)

        # if component is not of this kind
        kind = component_spec.get("kind", [])
        cur_elem["kind"] = kind
        # model types drive SPICE model-table matching (independent of kind)
        model_types = component_spec.get("model_type", [])
        cur_elem["model_type"] = model_types

        # mapping from non kind and model name argument index to port spec
        arg_to_ports = component_spec.get("arg_to_ports", {})
        # mapping from non kind and model name argument index to value spec,
        args_to_values = component_spec.get("args_to_values", {})
        args_to_values_positional = [
            key for key, value in args_to_values.items() if value.get("is_positional", True)
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

        # SPICE: resolve model names to model types
        non_model_args = args
        resolved_model_name = None
        if IS_SPICE:
            non_model_args, resolved_model_name = _filter_spice_model_args(
                prefix, spec_idx, args, model_types, model_table
            )
            cur_elem["model_name"] = str(resolved_model_name) if resolved_model_name is not None else None
            cur_elem["model_span"] = getattr(resolved_model_name, "span", None)

            # declared specifiers must be present (unless optional). Only the
            # bare keyword proves presence: the doc grammar has no key=value
            # form for specifiers (<AC <ACMAG <ACPHASE>> is a bare keyword
            # followed by operands), the corpus confirms zero ac=/dc= lines,
            # and the bare-word test shares the same definition of "specifier
            # exists" that _expand_specifiers uses to open segments.
            for sp_key, sp_def in component_spec.get("specifiers", {}).items():
                if sp_def.get("is_optional", False):
                    continue

                if not any(t.lower() == sp_key.lower() for t in non_model_args):
                    raise NetlistError(
                        f"Did not find specifier {sp_key} for component "
                        f"{component_name} in netlist for prefix '{prefix}' "
                        f"spec index {spec_idx}"
                    )
            cur_elem["specifiers"] = list(component_spec.get("specifiers", {}).keys())

            # normalize: lexical repair, then per-spec specifier expansion
            glued = _glue_comma_values(_glue_paren_values(non_model_args))
            expanded = _expand_specifiers(glued, component_spec, prefix)
            if expanded is None:
                # a foreign specifier keyword appeared: this spec cannot own
                # the line; another spec variant may.
                raise NetlistError(
                    f"Undeclared specifier found for component {component_name} "
                    f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                )
            non_model_args = expanded

        # after filtering model name, we filter kind keywords.
        # kind keywords are matched case-insensitively: SPICE is
        # case-insensitive, so ON/On/on/OFF/off all match.
        kind_l = [k.lower() for k in kind]
        filtered_args = [t for t in non_model_args if t.lower() not in kind_l]
        cur_elem["kind_spans"] = {
            str(t): t.span for t in non_model_args if t.lower() in kind_l and isinstance(t, Token)
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

                # could be positional arg specified as keyword arg in the correct position
                # check for it
                elif arg_spec.get("is_positional", True):
                    value_alias = arg_spec.get("alias", None)
                    if value_alias is None:
                        raise SpecError(
                            f"Missing 'alias' for arg index {arg_idx} in "
                            f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                        )
                    if key.lower() not in [a.lower() for a in value_alias] and arg_idx < positional_arg_end:
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

        # extract connections
        _extract_connections(cur_elem, filtered_args, arg_to_ports, component_spec, prefix, spec_idx)
        # extract values/label
        _extract_values(
            cur_elem,
            filtered_args,
            args_to_values,
            component_name,
            prefix,
            spec_idx,
            model_table,
            resolved_model_name,
        )

        return cur_elem, errors, warnings
    except (SpecError, NetlistError, IndexError) as e:
        errors.append(str(e))
        return None, errors, warnings


def _parse_line(
    line: str,
    model_table: dict[str, str] | None = None,
    subckt_table: dict[str, list[str]] | None = None,
):
    """Parse a single line of the netlist into a component name and its tokens.

    Parameters
    ----------
    line : str
        The (preprocessed) netlist line.
    model_table : dict[str, str] | None
        SPICE only: mapping of model name -> model type from `.model`
        directives (upper-cased). Used to resolve args marked `is_model` in
        the spec: the model name token is looked up to obtain its TYPE, which
        must match the spec's `kind` — so `kind` drives skin selection for
        model-specialized components (e.g. Q with a PNP-type model).
    subckt_table : dict[str, list[str]] | None
        SPICE only: mapping of subckt name -> list of port names from `.subckt`
        directives (upper-cased). Used to resolve args marked `is_subckt` in
        the spec: the subckt name token is looked up to obtain its port names,
        which are used to populate the component's connections.


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
    # lcapy only: make sure wires are unique, they will be dropped after merging
    # nodes anyways. SPICE has no wire element (W is a switch there).
    if prefix is WIRE and not IS_SPICE:
        component_name = tokens[0] + str(uuid4().hex[:4])
    model_table = model_table or {}
    subckt_table = subckt_table or {}
    # if not generic
    if prefix is not None and (not IS_SPICE or prefix not in EXPLICIT_SPICE_GENERICS):
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

        # Try each spec, collect errors, pick the candidate with fewest errors.
        candidates: list[tuple[dict, list[str], list[str]]] = []

        for spec_idx, component_spec in enumerate(component_specs):
            cur_elem, errors, warnings = _match_spec(
                spec_idx, prefix, component_name, args, component_spec, model_table
            )
            candidates.append((cur_elem, errors, warnings))

        # Select the candidate with the least errors and warnings, give errors more weight.
        best_elem, _, _ = min(candidates, key=lambda c: len(c[1]) * 2 + len(c[2]))

        if best_elem is not None:
            elem.update(best_elem)
        else:
            # dropped ports are NOT marked ({NC+}) here: this message is fed
            # back to the LLM, which must write plain node names
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
            # All specs failed, raise the error from the best candidate.
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
            # an explicit generic has a single spec (X/A/U/P/N in SPICE)
            "simulatable": TO_SKIN_CONFIG.get(prefix, [{}])[0].get("simulatable", True),
        }

        if prefix == SUBCKT_PREFIX and IS_SPICE:
            # assume last non keyword arg is the subckt name
            subckt_idx = len(args) - 1
            for idx in range(len(args) - 1, -1, -1):
                if "=" in args[idx]:
                    continue
                subckt_idx = idx
                break
            subckt_name = str(args[subckt_idx])
            subckt_def = subckt_table.get(subckt_name.upper(), None)
            elem["subckt"] = subckt_name

            for idx, token in enumerate(args):
                if "=" in token:
                    key, value = token.split("=", 1)
                    elem["values"][key] = {"value": value, "alias": [key]}
                elif idx == subckt_idx:
                    # the subckt name is the cell's label, not a pin
                    elem["values"]["value"] = {"value": subckt_name, "alias": ["value"]}
                else:  # node
                    if subckt_def is not None and idx < len(subckt_def):
                        port_name = subckt_def[idx]
                    else:
                        # bare-number pin keys; the generic skin template draws these
                        # verbatim next to each port, so keys == drawn pin text
                        port_name = f"{idx + 1}"
                    elem["connections"][port_name] = {
                        "node_name": str(token),
                        "port_direction": "input",
                        **_token_origin(token),
                    }
        else:
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


def _parse_netlist(text: str, strict: bool | None = None, simulate: bool | None = None):
    """Parse netlist text into {component_name: element}.

    Parameters
    ----------
    text : str
        The netlist text.
    strict : bool | None
        SPICE only: strict parsing (see _strict_violations). All violations
        are collected and raised together as one NetlistError, one per line,
        so they can be fed back to the LLM in a single round. None uses the
        STRICT_PARSING config default.
    simulate : bool | None
        SPICE only: the netlist will be simulated, so strict parsing also
        rejects components ngspice cannot simulate. Only checked when
        `strict` is on. None uses the simulation.enabled config default.
    """
    strict = (STRICT_PARSING if strict is None else strict) and IS_SPICE
    simulate = SIMULATION_ENABLED if simulate is None else simulate
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

    for line in text_lines:
        logger.debug(f"Parsing line: {line}")
        component_name, element = _parse_line(line, model_table=model_table, subckt_table=subckt_table)
        if component_name in parsed_netlist:
            raise NetlistError(f"Duplicate component name '{component_name}' found in netlist.")
        parsed_netlist[component_name] = element

    if strict:
        violations = _strict_violations(parsed_netlist, subckt_table, meta, model_table, simulate=simulate)
        if violations:
            raise NetlistError(
                f"Strict parsing found {len(violations)} problem(s):\n"
                + "\n".join(f"- {v}" for v in violations)
            )

    return parsed_netlist


def _merge_nodes(parsed_netlist):
    """Union-find over node names using the converter's merging semantics:
    lcapy W wires tie their nodes together and all ground aliases collapse
    into a single canonical ground node. Returns (find, union)."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for component_name, element in parsed_netlist.items():
        if element["prefix"] == WIRE and not IS_SPICE:
            nodes = [connection["node_name"] for pid, connection in element["connections"].items()]
            for n in nodes[1:]:
                union(nodes[0], n)

    # Merge all ground nodes into a single canonical node
    for g in GROUND_NAMES:
        union(list(GROUND_NAMES)[0], g)

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
            if n in GROUND_NAMES:
                canonicals[canonical_node].add("GND")
            else:
                canonicals[canonical_node].add(component_name)

    canonicals = dict(sorted(canonicals.items()))
    id_map = {node: i + 2 for i, node in enumerate(canonicals.keys())}

    ground_net_id = None
    if find(list(GROUND_NAMES)[0]) in id_map:
        ground_net_id = id_map[find(list(GROUND_NAMES)[0])]

    return id_map, find, ground_net_id


def to_yosys_json(
    netlist_text: str,
    module_name: str = "circuit",
    strict: bool | None = None,
    simulate: bool | None = None,
) -> tuple[dict, dict]:
    """Parse a netlist and convert it to yosys JSON for netlistsvg.

    `strict` and `simulate` are forwarded to _parse_netlist (None = the
    STRICT_PARSING / SIMULATION_ENABLED config defaults).
    Returns (yosys_json, parsed_netlist).
    """
    parsed_netlist = _parse_netlist(netlist_text, strict=strict, simulate=simulate)
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
        prefix = element["prefix"]

        if prefix == WIRE and not IS_SPICE:
            continue

        if element.get("parse_only", False):
            continue

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
