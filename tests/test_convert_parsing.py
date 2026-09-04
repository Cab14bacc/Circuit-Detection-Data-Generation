"""Tests for the netlist parser (convert.py parsing half).

Covers: tokenization, comment/continuation handling, prefix lookup, line
parsing into component dicts, duplicate detection, and value extraction.
"""

import pytest

from circuit_data_gen.convert import (
    NetlistError,
    _get_prefix,
    _get_tokens,
    _parse_line,
    _parse_netlist,
    _preprocess_lines,
    _strip_hints,
)

from tests.conftest import CONNECTED_NETLIST


class TestStripHints:
    def test_semicolon_comment(self):
        assert _strip_hints("V1 N1 0 dc 5 ; supply") == "V1 N1 0 dc 5"

    def test_hash_comment(self):
        assert _strip_hints("V1 N1 0 dc 5 # supply") == "V1 N1 0 dc 5"

    def test_no_comment(self):
        assert _strip_hints("V1 N1 0 dc 5") == "V1 N1 0 dc 5"

    def test_blank_returns_empty(self):
        assert _strip_hints("   ") == ""


class TestPreprocessLines:
    def test_drops_blank_and_comment_lines(self):
        lines = ["", "V1 N1 0 dc 5", "* full comment", "  ", "R1 N1 0 1k"]
        out = _preprocess_lines(lines)
        assert out == ["V1 N1 0 dc 5", "R1 N1 0 1k"]

    def test_continuation_lines_are_joined(self):
        lines = ["V1 N1 0 dc", "+ 5"]
        out = _preprocess_lines(lines)
        assert len(out) == 1
        assert "5" in out[0]


class TestGetTokens:
    def test_basic_split(self):
        assert _get_tokens("V1 N1 0 dc 5") == ["V1", "N1", "0", "dc", "5"]

    def test_quoted_string_stays_one_token(self):
        # quotes group the text into one token; the delimiters themselves are
        # stripped
        tokens = _get_tokens('R1 N1 N2 "my model"')
        assert tokens[3] == "my model"

    def test_braces_stay_one_token(self):
        tokens = _get_tokens("G1 N1 N2 N3 N4 {2m}")
        assert tokens[-1] == "2m"


class TestGetPrefix:
    def test_exact_prefix(self):
        assert _get_prefix("R1") == "R"

    def test_case_insensitive_fallback(self):
        # lowercase names keep their prefix case (mechanical analogues k/m/r
        # are case-sensitive in SPICE; the matcher returns the raw prefix)
        assert _get_prefix("r1") == "r"

    def test_wire(self):
        assert _get_prefix("W1") == "W"

    def test_unknown_is_generic(self):
        assert _get_prefix("X1") is None


class TestParseLine:
    def test_resistor_parsed(self):
        name, element = _parse_line("R1 N1 N2 1k")
        assert name == "R1"
        assert element["prefix"] == "R"
        assert not element["if_generic"]
        assert set(element["connections"].keys()) == {"+", "-"}
        assert element["connections"]["+"]["node_name"] == "N1"
        assert element["connections"]["-"]["node_name"] == "N2"
        assert "1k" in element["values"].values()

    def test_vsource_kind_keyword(self):
        name, element = _parse_line("V1 N1 0 dc 5")
        assert element["prefix"] == "V"
        assert element["kind"] == ["dc"]

    def test_wire_uniquified(self):
        name, element = _parse_line("W1 N1 N2")
        assert element["prefix"] == "W"
        assert name != "W1"  # wires get a uuid suffix to stay unique
        assert name.startswith("W")

    def test_duplicate_component_raises(self):
        with pytest.raises(NetlistError, match="Duplicate"):
            text = "V1 N1 0 dc 5\nV1 N2 0 dc 3\n"
            _parse_netlist(text)


class TestParseNetlist:
    def test_full_connected_netlist(self):
        parsed = _parse_netlist(CONNECTED_NETLIST)
        assert set(parsed.keys()) == {"V1", "R1", "R2", "C1"}
        assert all("connections" in el and "values" in el for el in parsed.values())

    def test_missing_component_raises(self):
        # Z is not a known prefix -> generic element with unknown ports;
        # a truly malformed line (no tokens) must raise
        with pytest.raises(Exception):
            _parse_netlist("R1")


@pytest.fixture
def connected_netlist():
    return CONNECTED_NETLIST
