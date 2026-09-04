from .convert import _merge_nodes, WIRE, GROUND_NAMES


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
