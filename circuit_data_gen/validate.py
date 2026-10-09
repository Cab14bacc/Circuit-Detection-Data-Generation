from .parser.helper import _merge_nodes
from .parser.macro import GROUND_NAMES
from .netlist_gen.sampling import samples_in


def undefined_components(parsed_netlist):
    undefined_comp_names = []
    for comp_name, comp_data in parsed_netlist.items():
        if_generic = comp_data.get("if_generic", False)
        prefix = comp_data.get("prefix", None)

        # check for if prefix exist because some generics are explicit generics like
        # "X" for subcircuits when dealing with spice netlists.
        if prefix is None and if_generic:
            undefined_comp_names.append(comp_name)

    return undefined_comp_names


def hanging_nodes(parsed_netlist):
    """Detect nodes that are not connected to any component
    This is used to detect hanging nodes, which are nodes that are connected to only one component.
    Some hanging nodes are created by dropping unsupported connections due to skin limits
    (e.g., a VCVS (Voltage Controlled Voltage Source) may take in control nodes in the netlist,
    but the corresponding skin is a bipole, supporting only input and output connections).

    Parameters
    ----------
    parsed_netlist : dict
        The parsed_netlist from convert.py, where keys are component names and values are component data.

    Returns
    -------
    hanging_nodes_before_drop : set
        A set of node names that are connected to only one component before dropping unsupported connections.
    hanging_nodes_after_drop : set
        A set of node names that are connected to only one component after dropping unsupported connections.
    hanging_nodes_from_dropping : set
        A set of node names that became hanging after dropping unsupported connections.
    """
    # nodes are counted ignoring letter case, as in ngspice (Out and out are
    # one net); the returned names keep the spelling first seen in the netlist
    spellings: dict[str, str] = {}
    hanging_nodes_after_drop = {}
    for comp_name, comp_data in parsed_netlist.items():
        connections = comp_data["connections"]
        for pid, conn_data in connections.items():
            node_key = conn_data["node_name"].lower()
            spellings.setdefault(node_key, conn_data["node_name"])
            if conn_data.get("drop", False):
                # drop this connection when it is NOT supported in the corresponding skin.
                # when a connection is dropped, it may cause a node to become hanging
                # (i.e., connected to only one component).
                continue

            hanging_nodes_after_drop[node_key] = hanging_nodes_after_drop.get(node_key, 0) + 1

    # Ground Components are different as they are created as soon as a ground node name
    # (e.g., "0" or "GND") is used. So they could be referenced only once in the netlist,
    # but are implicitly referenced again by the creation of the ground component.
    hanging_nodes_after_drop = {
        spellings[node]
        for node, count in hanging_nodes_after_drop.items()
        if count == 1 and node not in GROUND_NAMES
    }

    hanging_nodes_before_drop = {}
    for comp_name, comp_data in parsed_netlist.items():
        connections = comp_data["connections"]
        for pid, conn_data in connections.items():
            node_key = conn_data["node_name"].lower()
            hanging_nodes_before_drop[node_key] = hanging_nodes_before_drop.get(node_key, 0) + 1

    hanging_nodes_before_drop = {
        spellings[node]
        for node, count in hanging_nodes_before_drop.items()
        if count == 1 and node not in GROUND_NAMES
    }

    hanging_nodes_from_dropping = hanging_nodes_after_drop - hanging_nodes_before_drop

    return hanging_nodes_before_drop, hanging_nodes_after_drop, hanging_nodes_from_dropping


def connected_component_groups(parsed_netlist) -> tuple[bool, list[list[str]]]:
    """Group the non-wire components of a parsed netlist into electrically
    connected groups, ignoring connections flagged as dropped (they are not
    rendered, so they don't connect anything in the schematic).

    Returns one list of component names per group. A single group means the
    circuit is fully connected; multiple groups mean isolated subgraphs.
    Runs in O(connections) union-find passes.

    Parameters
    ----------
    parsed_netlist : dict
    The parsed_netlist from convert.py, where keys are component names and values are component data.

    Returns
    -------
    is_connected : bool
    True if the circuit is fully connected, False if there are isolated subgraphs.

    groups : list[list[str]]
    A list of groups, where each group is a list of component names that are electrically connected.
    """
    find, _ = _merge_nodes(parsed_netlist)

    comp_parent: dict[str, str] = {}

    def cfind(x):
        comp_parent.setdefault(x, x)
        while comp_parent[x] != x:
            comp_parent[x] = comp_parent[comp_parent[x]]
            x = comp_parent[x]
        return x

    def cunion(a, b):
        ra, rb = cfind(a), cfind(b)
        if ra != rb:
            comp_parent[rb] = ra

    # components sharing a canonical node belong to the same group
    node_to_comp: dict[str, str] = {}
    for component_name, element in parsed_netlist.items():
        cfind(component_name)
        for pid, connection in element["connections"].items():
            if connection.get("drop", False):
                continue
            canonical_node = find(connection["node_name"])
            first_comp = node_to_comp.get(canonical_node)
            if first_comp is None:
                node_to_comp[canonical_node] = component_name
            else:
                cunion(component_name, first_comp)

    groups: dict[str, list[str]] = {}
    for component_name in comp_parent:
        groups.setdefault(cfind(component_name), []).append(component_name)
    return len(groups) == 1, list(groups.values())


def full_validation(parsed_netlist, requirement, logger=None) -> dict:
    errors = []

    # ================================================================
    # check if the circuit contains the correct number of components
    # ================================================================
    if not len(parsed_netlist) == requirement.num_components:
        errors.append(
            f"Netlist has {len(parsed_netlist)} components, "
            f"which is not equal to the required number of "
            f"{requirement.num_components}. Only the element lines outside .subckt bodies count "
            f"(an X instance counts once; directives, K lines and the elements inside a "
            f".subckt ... .ends block do not)."
        )

    # ================================================================
    # check for undefined components
    # ================================================================
    undefined_comp_names = undefined_components(parsed_netlist)
    if undefined_comp_names:
        errors.append(f"Some components are not using valid prefixes: {undefined_comp_names}.")

    # ================================================================
    # check the component types: every required one must appear, and
    # none outside the allowed set may
    # ================================================================
    used = samples_in(parsed_netlist)
    used_keys = {sample.key() for sample in used}
    allowed_keys = {sample.key() for sample in requirement.allowed}

    missing = [sample for sample in requirement.required if sample.key() not in used_keys]
    if missing:
        errors.append(
            "Netlist is missing required components; use each of these at least once:\n"
            + "\n".join(f"- {sample.describe()}" for sample in missing)
        )

    extra = [sample for sample in used if sample.key() not in allowed_keys]
    if extra:
        errors.append(
            "Netlist uses components that are not allowed; remove them or replace them "
            "with allowed ones:\n" + "\n".join(f"- {sample.describe()}" for sample in extra)
        )

    (hanging_nodes_before_drop, hanging_nodes_after_drop, hanging_nodes_from_dropping) = hanging_nodes(
        parsed_netlist
    )

    # ================================================================
    # check for hanging nodes after dropping unsupported connections due to skin limits
    # realize dropping connections can also make a originally hanging node to become non-hanging
    # by removing all connections to that node.
    # ================================================================
    if len(hanging_nodes_after_drop) > 0:
        msg = (
            f"Netlist has {len(hanging_nodes_after_drop)} "
            f"hanging nodes: {', '.join(hanging_nodes_after_drop)}.\n"
        )
        hanging_nodes_from_dropping = hanging_nodes_after_drop - hanging_nodes_before_drop
        if len(hanging_nodes_from_dropping) > 0:
            msg += "Nodes that became hanging due to dropping (skin doesn't support this port): "
            msg += f"{', '.join(hanging_nodes_from_dropping)}."
        errors.append(msg)

    # ================================================================
    # the reverse case: a node whose ONLY connection is a hidden (dropped)
    # port, e.g. an E/G/S control pin wired to nothing else. After the drop
    # it has no connection at all, so the check above misses it, but it is
    # still a floating pin of the netlist (and a singular matrix in ngspice).
    # ================================================================
    hidden_only = hanging_nodes_before_drop - hanging_nodes_after_drop
    if hidden_only:
        pins = [
            f"{node} (hidden pin {pid} of {comp_name})"
            for node in sorted(hidden_only)
            for comp_name, comp_data in parsed_netlist.items()
            for pid, conn in comp_data["connections"].items()
            if conn["node_name"].lower() == node.lower()
        ]
        errors.append(
            f"Netlist has {len(hidden_only)} node(s) connected only to a hidden pin: {', '.join(pins)}. "
            "Hidden pins are not drawn but are still connected: connect each of these nodes to "
            "another component too."
        )

    # ================================================================
    # check if circuit is a connected graph (i.e., no isolated subgraphs).
    # ================================================================
    is_connected, comp_groups = connected_component_groups(parsed_netlist)
    if not is_connected:
        subgraphs_str = "],[".join(", ".join(sorted(group)) for group in comp_groups)
        errors.append(
            f"Netlist is not a connected graph: it has {len(comp_groups)} "
            f"isolated subgraphs: [{subgraphs_str}]. "
            "Connect all components electrically."
        )

    if logger:
        logger.info(
            f"component types in netlist: {[sample.key() for sample in used]}\n"
            f"hanging_nodes_before_drop: {hanging_nodes_before_drop}\n"
            f"hanging_nodes_after_drop: {hanging_nodes_after_drop}\n"
        )

    return errors
