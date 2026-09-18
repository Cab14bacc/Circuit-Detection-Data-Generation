from .convert import IS_SPICE, _merge_nodes, WIRE, GROUND_NAMES

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
    hanging_nodes_after_drop = {}
    for comp_name, comp_data in parsed_netlist.items():
        connections = comp_data["connections"]
        for pid, conn_data in connections.items():
            node_name = conn_data["node_name"]
            if conn_data.get("drop", False):
                # drop this connection when it is NOT supported in the corresponding skin.
                # when a connection is dropped, it may cause a node to become hanging
                # (i.e., connected to only one component).
                continue
            if node_name not in hanging_nodes_after_drop:
                hanging_nodes_after_drop[node_name] = 0
            hanging_nodes_after_drop[node_name] += 1
    # Ground Components are different as they are created as soon as a ground node name
    # (e.g., "0" or "GND") is used. So they could be referenced only once in the netlist,
    # but are implicitly referenced again by the creation of the ground component.
    hanging_nodes_after_drop = {
        node for node, count in hanging_nodes_after_drop.items() if count == 1 and node not in GROUND_NAMES
    }

    hanging_nodes_before_drop = {}
    for comp_name, comp_data in parsed_netlist.items():
        connections = comp_data["connections"]
        for pid, conn_data in connections.items():
            node_name = conn_data["node_name"]
            if node_name not in hanging_nodes_before_drop:
                hanging_nodes_before_drop[node_name] = 0
            hanging_nodes_before_drop[node_name] += 1
    hanging_nodes_before_drop = {
        node for node, count in hanging_nodes_before_drop.items() if count == 1 and node not in GROUND_NAMES
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
        if element["prefix"] == WIRE:
            continue
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
            f"{requirement.num_components}."
        )

    # ================================================================
    # check for undefined components
    # ================================================================
    undefined_comp_names = undefined_components(parsed_netlist)
    if undefined_comp_names:
        errors.append(f"Some components are not using valid prefixes: {undefined_comp_names}.")

    # ================================================================
    # check for required components
    # ================================================================
    types_in_netlist = {(data["prefix"], tuple(map(str.lower, data["kind"])), tuple(map(str.lower, data["specifiers"]))) 
                        for name, data in parsed_netlist.items()}
    comps_in_netlist = {
        (name, data["prefix"], tuple(map(str.lower, data["kind"])), tuple(map(str.lower, data["specifiers"]))) for name, data in parsed_netlist.items()
    }

    extra_components = [
        cmp_type for cmp_type in types_in_netlist if cmp_type not in requirement.component_subset
    ]

    if logger:
        logger.info(f"types_in_netlist: {types_in_netlist}")
        logger.info(f"components_in_netlist: {comps_in_netlist}")
    
    # ================================================================
    # check for required components that are missing from the netlist
    # ================================================================
    if len(extra_components) > 0:
        no_specifer_str = ", and without specifiers" if IS_SPICE else ""
        extra_components_str = ", ".join(
            (
                f"`{prefix}{' with kind keyword: ' + ", ".join(kind) if kind else ' without kind keyword.'}"
                f"{' with specifiers: ' + ", ".join(specifiers) if specifiers else no_specifer_str}`"
            )
            for prefix, kind, specifiers in extra_components
        )
        errors.append(f"Netlist can not contain components that are not in the specified subset, "
                      f"these components are not allowed: {extra_components_str}.")

    (hanging_nodes_before_drop, hanging_nodes_after_drop, hanging_nodes_from_dropping) = hanging_nodes(
        parsed_netlist
    )

    if logger:
        logger.info(f"hanging_nodes_before_drop: {hanging_nodes_before_drop}")
        logger.info(f"hanging_nodes_after_drop: {hanging_nodes_after_drop}")

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

    return errors