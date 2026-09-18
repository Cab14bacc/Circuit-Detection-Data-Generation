"""Tests for the dual-dialect test harness (conftest swap mechanism).

Guards the fixture plumbing itself: the ``convert`` fixture must genuinely
reload the module per dialect, since a broken swap would silently leave the
lcapy assertions executing against the spice config (or vice versa).
"""

import circuit_data_gen.configs.config as config

from tests.conftest import NETLIST_FORMATS, _reload_convert


class TestConvertFixtureSwapsDialect:
    def test_reload_returns_consistent_module(self, convert):
        # the returned module's state must be internally consistent: IS_SPICE,
        # NETLIST_FORMAT and TO_SKIN_CONFIG all agree on the dialect
        expected_is_spice = convert.NETLIST_FORMAT == "spice"
        assert convert.IS_SPICE is expected_is_spice
        assert config.CONVERT_CONFIG["NETLIST_FORMAT"] == convert.NETLIST_FORMAT

    def test_parametrized_over_both_dialects(self, request):
        # the fixture is parametrized on every supported dialect (default run)
        mark = request.node.get_closest_marker("parametrize")
        if mark is None:
            # no marker: the env restricted the run to a single dialect
            assert config.CONVERT_CONFIG["NETLIST_FORMAT"] in NETLIST_FORMATS
        else:
            assert set(mark.args[1]) <= set(NETLIST_FORMATS)


class TestReloadMechanism:
    def test_explicit_reload_switches_dialect(self):
        lcapy = _reload_convert("lcapy")
        assert lcapy.NETLIST_FORMAT == "lcapy"
        assert lcapy.IS_SPICE is False
        # lcapy: 'dc' is a kind keyword, not part of the value
        _, elem = lcapy._parse_line("V1 N1 0 dc 5")
        assert elem["kind"] == ["dc"]

        spice = _reload_convert("spice")
        assert spice.NETLIST_FORMAT == "spice"
        assert spice.IS_SPICE is True
        # spice: the DC specifier is transparent — operand lands in value
        _, elem = spice._parse_line("V1 N1 0 dc 5")
        assert elem["values"]["value"]["value"] == "5"
        assert elem["kind"] == []

    def test_wire_prefix_differs_between_dialects(self):
        lcapy = _reload_convert("lcapy")
        # lcapy W is a structural wire (uniquified name, no skin)
        name, _ = lcapy._parse_line("W1 N1 N2")
        assert name != "W1"

        spice = _reload_convert("spice")
        # spice W is the current-controlled switch: N+ N- VNAM MODEL (5 tokens)
        netlist = "V1 N1 0 5\nW1 N1 N2 V1 SWM\n.model SWM CSW\n"
        parsed = spice._parse_netlist(netlist)
        assert parsed["W1"]["skin_alias"] == ["csw"]
        # the controlling source ref (VNAM) is consumed, not a node
        assert set(parsed["W1"]["connections"]) == {"+", "-"}
