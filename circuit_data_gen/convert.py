"""SPICE → Yosys JSON converter for netlistsvg's lcapy skin.

Handles Lcapy SPICE netlists with or without layout hints. Maps component
prefixes to skin cells, merges nodes via W wires, emits a gnd cell for
ground-touching nets, and recognises Vcc/Vee rails.

Uses the custom lcapy.svg skin (co-located with this file) which mirrors
Lcapy's Circuitikz visual vocabulary.
"""

from __future__ import annotations
from .configs.config import get_config_value, get_logger
from functools import cache
from uuid import uuid4
import random
import string

logger = get_logger(__name__)

TO_SKIN_CONFIG = get_config_value("convert", "convert_config_path", "TO_SKIN_CONFIG")
WIRE = "W"
GROUND_NAMES = {"0", "GND"}


class NetlistError(Exception):
    """Raised when a netlist line is malformed or cannot be parsed into a component."""


class SpecError(Exception):
    """Raised when a skin spec in LCAPY_TO_SKIN is malformed (e.g. duplicate
    argument indices between arg_to_ports and args_to_values)."""


@cache
def _get_all_kind_keywords():
    """Return a set of all kind keywords across all component specs in TO_SKIN_CONFIG."""
    all_kinds = set()
    for prefix, component_specs in TO_SKIN_CONFIG.items():
        for spec in component_specs:
            kinds = spec.get("kind", [])
            all_kinds.update(kinds)
    return all_kinds


def _strip_hints(line: str) -> str | None:
    return line.split(";", 1)[0].split("#", 1)[0].strip()


def _get_prefix(component_name: str) -> str | None:
    # Mechanical analogues are case-sensitive single-letter (k, m, r)
    prefix_list = [str(p) for p in TO_SKIN_CONFIG.keys()]
    # Sort by length descending
    prefix_list.sort(key=lambda x: 10000 + len(x) if str().islower() else len(x), reverse=True)

    # look for exact match first, then case-insensitive match, then wire "W", else None (generic)
    for p in prefix_list:
        if component_name.startswith(p):
            return p

    for p in prefix_list:
        if component_name.upper().startswith(p.upper()):
            return p.upper()

    if component_name.upper().startswith(WIRE):
        return WIRE

    logger.warning(f"Unknown component prefix for {component_name}, using generic skin cell.")

    return None


def _preprocess_lines(text_lines: list[str]):
    """
    Merge continuation lines starting with '+' into the previous line.
    Remove comment and empty lines. Return a list of cleaned lines.
    """
    merged_stripped_lines = []
    current_line = ""
    for line in text_lines:
        stripped_line = _strip_hints(line)
        if not stripped_line:
            continue

        if stripped_line[0] in "*.#":
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

    return merged_stripped_lines


def _extract_connections(
    cur_elem: dict,
    non_kind_args: list[str],
    arg_to_ports: dict,
    component_spec: dict,
    prefix: str,
    spec_idx: int,
):

    # extract connections
    for arg_idx, arg_spec in arg_to_ports.items():
        arg_idx = int(arg_idx)
        node_name = non_kind_args[arg_idx]

        port_id = arg_spec.get("alias", None)
        if port_id is None:
            raise SpecError(
                f"Missing 'alias' for arg index {arg_idx} in "
                f"arg_to_ports for prefix '{prefix}' spec index {spec_idx}"
            )

        if_drop = arg_spec.get("drop", False)
        port_direction = component_spec.get("port_directions", {}).get(port_id, "input")
        cur_elem["connections"][port_id] = {
            "node_name": node_name,
            "port_direction": port_direction,
            "drop": if_drop,
        }


def _extract_values(
    cur_elem: dict,
    non_kind_args: list[str],
    args_to_values: dict,
    component_name: str,
    prefix: str,
    spec_idx: int,
):
    for arg_idx, arg_spec in args_to_values.items():
        arg_idx = int(arg_idx)

        # value spec must have alias
        value_alias = arg_spec.get("alias", None)
        if value_alias is None:
            raise SpecError(
                f"Missing 'alias' for arg index {arg_idx} in "
                f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
            )

        # value spec must have skin_label, unless it is optional, then it's dropped.
        is_optional = arg_spec.get("is_optional", True)
        skin_label = arg_spec.get("skin_label", None)
        if skin_label is None:
            if not is_optional:
                raise SpecError(
                    f"Missing 'skin_label' for arg index {arg_idx} in "
                    f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                )
            else:  # skip if there is no corresponding label in the skin
                continue

        # check for arguments specified with key=value format,
        # and if the key matches any of the value_alias, we use that value.
        for token in non_kind_args:
            if "=" in token:
                key, value = token.split("=", 1)
                if key in value_alias:
                    if skin_label in cur_elem["values"]:
                        raise SpecError(
                            f"Duplicate value for skin_label '{skin_label}' "
                            f"for prefix '{prefix}' spec index {spec_idx}"
                        )

                    cur_elem["values"][skin_label] = value
                    break

        # if the argument is specified as positional,
        # we take the value from that position in the non_kind_args list.
        if skin_label not in cur_elem["values"]:
            try:
                value_arg = non_kind_args[arg_idx]
            except IndexError:
                # only raise if it is not optional,
                # otherwise we attempt to use the default value or skip if no default value is specified.
                if not is_optional:
                    raise NetlistError(
                        f"Missing value for positional argument with alias: {value_alias} for "
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

            if key is not None and key not in value_alias:
                raise NetlistError(
                    f"Positional value argument expects aliases {value_alias}, but got '{key}' "
                    f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                )

            cur_elem["values"][skin_label] = value


def _get_tokens(line: str):
    """
    Split a line into tokens, separated by whitespaces, handling quoted strings and curly braces.
    Returns a list of tokens.
    """
    stack = []
    tokens = []
    current_token = ""
    for char in line:
        if char in string.whitespace and not stack:
            if current_token:
                tokens.append(current_token)
                current_token = ""
        else:
            if char == '"':
                if stack and stack[-1] == '"':
                    stack.pop()  # closing quote
                else:
                    stack.append('"')  # opening quote
                continue
            elif char == "'":
                if stack and stack[-1] == "'":
                    stack.pop()
                else:
                    stack.append("'")
                continue
            elif char == "{":
                stack.append("{")
                continue
            elif char == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
                continue

            current_token += char

    if current_token:
        tokens.append(current_token)
    return tokens


def _parse_line(line: str):
    """Parse a single line of the netlist into a component name and its tokens.

    Returns
    -------
    component_name: str
        The name of the component (e.g., R1, C1, etc.)
    element: dict
    """

    tokens = _get_tokens(line)
    args = tokens[1:]
    component_name = tokens[0]
    prefix = _get_prefix(component_name)
    # make sure wires are unique, they will be dropped aftering merging nodes anyways.
    component_name = tokens[0] + str(uuid4().hex[:4]) if prefix is WIRE else tokens[0]

    if prefix is not None:
        component_specs = TO_SKIN_CONFIG.get(prefix, [{}])
        elem = {
            "prefix": prefix,
            "kind": [],
            "if_generic": False,
            "connections": {},
            "values": {},
            "skin_alias": None,
        }

        # Try each spec, collect errors, pick the candidate with fewest errors.
        candidates: list[tuple[dict, list[str]]] = []

        for spec_idx, component_spec in enumerate(component_specs):
            errors: list[str] = []
            try:
                cur_elem = {"connections": {}, "values": {}, "kind": [], "skin_alias": None}
                skin_alias = component_spec.get("skin_alias", None)
                if skin_alias is None and prefix != WIRE:
                    raise SpecError(f"Missing 'skin_alias' for prefix '{prefix}' spec index {spec_idx}")
                cur_elem["skin_alias"] = skin_alias

                # if component is not of this kind
                kind = component_spec.get("kind", [])
                cur_elem["kind"] = kind
                non_kind_args = [t for t in args if t not in kind]

                if len(non_kind_args) == 0:
                    raise NetlistError(
                        f"No non-kind arguments found for component {component_name} "
                        f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )
                if len(non_kind_args) + len(kind) > len(args):
                    raise NetlistError(
                        f"Did not find kind keyword in arguments for component {component_name} "
                        f"in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )
                kind_keyword_matchings = [k in _get_all_kind_keywords() for k in non_kind_args]
                if any(kind_keyword_matchings):
                    kind_keyword_index = kind_keyword_matchings.index(True)
                    raise NetlistError(
                        f"Invalid kind keyword {non_kind_args[kind_keyword_index]} for "
                        f"component {component_name} in netlist for prefix '{prefix}' spec index {spec_idx}"
                    )
                if len(non_kind_args) > len(tokens) - len(kind) - 1:
                    raise NetlistError(
                        f"Too many arguments for component {component_name} in netlist "
                        f"for prefix '{prefix}' spec index {spec_idx}"
                    )

                # mapping from non kind argument index to port spec
                arg_to_ports = component_spec.get("arg_to_ports", {})
                # mapping from non kind argument index to value spec,
                args_to_values = component_spec.get("args_to_values", {})

                # check for duplicate keys in arg_to_ports and args_to_values
                # this ensures arguments are uniquely indexed.
                arg_indices_set = set(list(arg_to_ports.keys()))
                positional_arg_to_values = {
                    key for key, value in args_to_values.items() if value.get("is_positional", True)
                }
                arg_indices_set.update(positional_arg_to_values)
                if len(arg_indices_set) != len(arg_to_ports) + len(positional_arg_to_values):
                    raise SpecError(
                        f"Duplicate argument indices between arg_to_ports and "
                        f"args_to_values for prefix '{prefix}' spec index {spec_idx}"
                    )

                # extract connections
                _extract_connections(cur_elem, non_kind_args, arg_to_ports, component_spec, prefix, spec_idx)
                # extract values/label
                _extract_values(cur_elem, non_kind_args, args_to_values, component_name, prefix, spec_idx)

                candidates.append((cur_elem, errors))

            except (SpecError, NetlistError) as e:
                errors.append(str(e))
                candidates.append((None, errors))

        # Select the candidate with the least errors.
        best_elem, best_errors = min(candidates, key=lambda c: len(c[1]))

        if best_elem is not None:
            elem.update(best_elem)
        else:
            logger.error(
                f"All specs failed for prefix '{prefix}' on line '{line}'. Errors: {'; '.join(best_errors)}"
            )
            # All specs failed, raise the error from the best candidate.
            raise ValueError(
                f"All specs failed for prefix '{prefix}' on line '{line}'. Errors: {'; '.join(best_errors)}"
            )
    else:  # if generic
        # take the last arg and kwargs as value, and all else connections.
        elem = {
            "prefix": None,
            "kind": [],
            "if_generic": True,
            "connections": {},
            "values": {},
            "skin_alias": "generic",
        }

        for idx, token in enumerate(args):
            if "=" in token:
                key, value = token.split("=", 1)
                elem["values"][key] = value
            elif idx == len(args) - 1:
                elem["values"]["value"] = token
            else:
                # bare-number pin keys; the generic skin template draws these
                # verbatim next to each port, so keys == drawn pin text
                elem["connections"][f"{idx + 1}"] = {"node_name": token, "port_direction": "input"}

    return component_name, elem


def _parse_netlist(text: str):
    text_lines = text.splitlines()
    text_lines = _preprocess_lines(text_lines)

    parsed_netlist = {}
    # "component_name": {
    #     "prefix": prefix,
    #     "skin_alias": skin_alias,
    #     "if_generic": True/False,
    #     "connections": {
    #         "port id": {
    #             "node_name": node name,
    #             "port_direction": direction,
    #         },
    #     },
    #     "values": {
    #         "skin_label for value1": value1
    #         "skin_label for value2": value2
    #     }
    # }

    for line in text_lines:
        logger.debug(f"Parsing line: {line}")
        component_name, element = _parse_line(line)
        if component_name in parsed_netlist:
            raise NetlistError(f"Duplicate component name '{component_name}' found in netlist.")
        parsed_netlist[component_name] = element

    return parsed_netlist


def _merge_nodes(parsed_netlist):
    """Union-find over node names using the converter's merging semantics:
    W wires tie their nodes together and all ground aliases collapse into a
    single canonical ground node. Returns (find, union)."""
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
        if element["prefix"] == WIRE:
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


def to_yosys_json(netlist_text: str, module_name: str = "circuit") -> tuple[dict, dict]:
    parsed_netlist = _parse_netlist(netlist_text)
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

        if prefix == WIRE:
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
        attributes.update(element["values"])

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
