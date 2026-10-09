"""Tests for hanging-node detection (validate.py).

Hanging nodes = nets touching only one component. Some appear from skin
port drops, some are genuine netlist mistakes.
"""

from circuit_data_gen.parser.convert import _parse_netlist
from circuit_data_gen.validate import full_validation, hanging_nodes

from tests.conftest import CONNECTED_NETLIST, requirements_for


class TestHangingNodes:
    def test_clean_netlist_has_no_hanging_nodes(self):
        parsed = _parse_netlist(CONNECTED_NETLIST)
        before, after, from_drop = hanging_nodes(parsed)
        assert before == set()
        assert after == set()
        assert from_drop == set()

    def test_floating_node_is_hanging(self):
        # N2 touches only R1
        parsed = _parse_netlist("V1 N1 0 5\nR1 N1 N2 1k\n")
        before, after, _ = hanging_nodes(parsed)
        assert "N2" in before
        assert "N2" in after

    def test_drop_can_create_hanging_node(self):
        # G (VCCS): control ports c+/c- are dropped by the skin. With a
        # single G, M1 touches only the dropped c+ port, so it's hanging
        # before AND after; the drop-delta is empty. A fully-dropped node
        # vanishes from the post-drop count (zero connections) rather than
        # staying 'hanging'.
        parsed = _parse_netlist("V1 N1 0 5\nG1 N1 0 M1 0 1m\n")
        before, after, from_drop = hanging_nodes(parsed)
        assert "M1" in before
        assert "M1" not in after
        assert from_drop == set()

    def test_node_not_hanging_before_drop_becomes_hanging_after(self):
        # G1 taps M1 via dropped c+ AND G2's kept '+' port lands on M1:
        # pre-drop M1 has 3 connections (G1.c+, G2.+, G2.c+) -> not hanging.
        # Post-drop: only G2.+ remains (1 connection) -> M1 IS hanging, and
        # the drop caused the count to fall from 3 to 1, so it's in the
        # drop-caused delta.
        parsed = _parse_netlist("V1 N1 0 5\nG1 N1 0 M1 0 1m\nG2 M1 0 M1 0 1m\n")
        before, after, from_drop = hanging_nodes(parsed)
        assert "M1" not in before  # 3 connections pre-drop
        assert "M1" in after  # 1 kept connection (G2.+)
        assert "M1" in from_drop  # drops pushed 3 -> 1


class TestHiddenPinOnlyNodes:
    """A node whose only connection is a hidden (dropped) port has zero
    connections after the drop, so it is not "hanging" in the rendered
    sense, but it is still a floating pin of the netlist."""

    def test_node_on_hidden_pin_only_is_reported(self, convert):
        # G1's control pin c+ is on M1, which nothing else touches
        text = "V1 N1 0 5\nR1 N1 0 1k\nG1 N1 0 M1 0 1m\n"
        errors = full_validation(convert._parse_netlist(text), requirements_for(text))
        hidden = [e for e in errors if "connected only to a hidden pin" in e]
        assert len(hidden) == 1, errors
        assert "M1 (hidden pin c+ of G1)" in hidden[0]

    def test_hidden_pin_wired_elsewhere_is_fine(self, convert):
        # control pin on N1, which V1 and R1 also use
        text = "V1 N1 0 5\nR1 N1 N2 1k\nG1 N2 0 N1 0 1m\n"
        errors = full_validation(convert._parse_netlist(text), requirements_for(text))
        assert not any("hidden pin" in e for e in errors), errors
