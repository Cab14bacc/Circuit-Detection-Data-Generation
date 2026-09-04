"""Tests for net->yosys conversion and connectivity analysis.

Covers: yosys JSON structure, net-id assignment (ground unification, wire
merging), hanging-node detection, and connected-component grouping — the
same functions the generation pipeline's validator uses.
"""

from circuit_data_gen.convert import (
    GROUND_NAMES,
    _assign_net_ids,
    _merge_nodes,
    _parse_netlist,
    to_yosys_json,
)
from circuit_data_gen.validate import connected_component_groups

from tests.conftest import (
    CONNECTED_NETLIST,
    ISOLATED_NETLIST,
    WIRE_BRIDGED_NETLIST,
)


class TestToYosysJson:
    def test_modules_and_cells_present(self):
        yosys, parsed = to_yosys_json(CONNECTED_NETLIST)
        assert "modules" in yosys
        cells = yosys["modules"]["circuit"]["cells"]
        assert set(cells.keys()) == {"gnd", "V1", "R1", "R2", "C1"}

    def test_ground_cell_emitted(self):
        yosys, _ = to_yosys_json(CONNECTED_NETLIST)
        cells = yosys["modules"]["circuit"]["cells"]
        assert cells["gnd"]["type"] == "gnd"
        assert cells["gnd"]["attributes"]["name"] == "GND"

    def test_cell_attributes_carry_ref_and_value(self):
        yosys, _ = to_yosys_json(CONNECTED_NETLIST)
        r1 = yosys["modules"]["circuit"]["cells"]["R1"]
        assert r1["attributes"]["ref"] == "R1"
        assert r1["attributes"]["value"] == "1k"

    def test_net_ids_are_positive_ints(self):
        yosys, _ = to_yosys_json(CONNECTED_NETLIST)
        for cell in yosys["modules"]["circuit"]["cells"].values():
            for net in cell["connections"].values():
                assert all(isinstance(b, int) and b >= 2 for b in net)

    def test_shared_nets_use_same_id(self):
        yosys, _ = to_yosys_json(CONNECTED_NETLIST)
        cells = yosys["modules"]["circuit"]["cells"]
        # R1.(-) and R2.(+) and C1.(+) all touch N2 -> same net id
        ids = {
            cells["R1"]["connections"]["-"][0],
            cells["R2"]["connections"]["+"][0],
            cells["C1"]["connections"]["+"][0],
        }
        assert len(ids) == 1


class TestMergeNodes:
    def test_ground_aliases_unify(self):
        parsed = _parse_netlist("V1 A 0 dc 5\nR1 B GND 1k\n")
        find, _ = _merge_nodes(parsed)
        assert find("0") == find("GND")

    def test_wire_merges_nodes(self):
        parsed = _parse_netlist("V1 A 0 dc 5\nW1 A B\nR1 B 0 1k\n")
        find, _ = _merge_nodes(parsed)
        assert find("A") == find("B")


class TestAssignNetIds:
    def test_ground_net_id_found(self):
        parsed = _parse_netlist(CONNECTED_NETLIST)
        id_map, find, ground_net_id = _assign_net_ids(parsed)
        assert ground_net_id is not None
        assert ground_net_id == id_map[find(list(GROUND_NAMES)[0])]

    def test_no_ground_returns_none(self):
        # two floating components, no ground at all
        parsed = _parse_netlist("R1 A B 1k\nC1 B A 1u\n")
        _, _, ground_net_id = _assign_net_ids(parsed)
        assert ground_net_id is None


class TestConnectedComponentGroups:
    def test_connected_netlist_is_one_group(self):
        parsed = _parse_netlist(CONNECTED_NETLIST)
        is_connected, groups = connected_component_groups(parsed)
        assert is_connected
        assert len(groups) == 1
        assert sorted(groups[0]) == ["C1", "R1", "R2", "V1"]

    def test_isolated_netlist_splits(self):
        parsed = _parse_netlist(ISOLATED_NETLIST)
        is_connected, groups = connected_component_groups(parsed)
        assert not is_connected
        assert len(groups) == 2
        sizes = sorted(len(g) for g in groups)
        assert sizes == [2, 2]

    def test_wire_bridge_connects(self):
        parsed = _parse_netlist(WIRE_BRIDGED_NETLIST)
        is_connected, groups = connected_component_groups(parsed)
        assert is_connected, groups
        assert len(groups) == 1

    def test_wires_excluded_from_groups(self):
        parsed = _parse_netlist(WIRE_BRIDGED_NETLIST)
        _, groups = connected_component_groups(parsed)
        names = [n for g in groups for n in g]
        assert not any(n.startswith("W") for n in names)

    def test_dropped_connections_do_not_connect(self):
        # G (VCCS) has ports 2/3 dropped by the skin. Craft a netlist where
        # G's only tie to the second loop is via a dropped port pair.
        netlist = "V1 N1 0 dc 5\nR1 N1 0 1k\nG1 N1 0 M1 0 1m\n"
        parsed = _parse_netlist(netlist)
        # sanity: some G ports are dropped
        g = parsed["G1"]
        assert any(c.get("drop") for c in g["connections"].values())
        # G1 still shares N1 with the loop, so it must remain connected
        is_connected, groups = connected_component_groups(parsed)
        assert is_connected
        assert len(groups) == 1
