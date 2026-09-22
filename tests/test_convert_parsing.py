"""Tests for the netlist parser (convert.py parsing half).

Covers: tokenization, comment/continuation handling, prefix lookup, line
parsing into component dicts, duplicate detection, and value extraction.

Dialect-sensitive tests request the ``convert`` fixture, which reloads the
module per dialect so both lcapy and spice grammars are exercised. Those tests
read state as ``convert.IS_SPICE`` in the body (never a module-level import,
which would go stale across a reload).
"""

import pytest

from circuit_data_gen.parser.convert import (
    _get_prefix,
    _get_tokens,
    _parse_netlist,
    _preprocess_lines,
    _strip_hints,
)

from tests.conftest import CONNECTED_NETLIST


def test_preprocess_model_table_extracted(convert_spice):
    lines = [
        ".model QN3904 NPN (Is=1f)",
        "Q1 C B E QN3904",
        ".model DP D",
    ]
    out, model_table, subckt_table, _ = convert_spice._preprocess_lines(lines)
    # directives filtered out of the element lines
    assert out == ["Q1 C B E QN3904"]
    # name -> {name, model_type, args} mapping (keys and types upper-cased,
    # "name" keeps the card's spelling, level suffix stripped)
    assert model_table == {
        "QN3904": {"name": "QN3904", "model_type": "NPN", "args": ["Is=1f"]},
        "DP": {"name": "DP", "model_type": "D", "args": []},
    }
    assert subckt_table == {}


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
        out, model_table, subckt_table, _ = _preprocess_lines(lines)
        assert out == ["V1 N1 0 dc 5", "R1 N1 0 1k"]

    def test_continuation_lines_are_joined(self):
        lines = ["V1 N1 0 dc", "+ 5"]
        out, model_table, subckt_table, _ = _preprocess_lines(lines)
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

    def test_case_insensitive_fallback(self, convert):
        # prefix lookup is dialect-dependent: in the spice dialect 'r1' (damper)
        # is not a prefix, so the case-insensitive fallback maps it to 'R'
        if convert.IS_SPICE:
            assert _get_prefix("r1") == "R"
        else:
            # lcapy dialect has case-sensitive mechanical analogues k/m/r
            assert _get_prefix("r1") == "r"

    def test_wire(self):
        assert _get_prefix("W1") == "W"

    def test_unknown_is_generic(self, convert):
        # spice dialect: X is a real prefix (subcircuit invocation)
        if convert.IS_SPICE:
            assert _get_prefix("X1") == "X"
        else:
            assert _get_prefix("X1") is None


class TestParseLine:
    def test_resistor_parsed(self, convert):
        name, element = convert._parse_line("R1 N1 N2 1k")
        assert name == "R1"
        assert element["prefix"] == "R"
        assert not element["if_generic"]
        assert set(element["connections"].keys()) == {"+", "-"}
        assert element["connections"]["+"]["node_name"] == "N1"
        assert element["connections"]["-"]["node_name"] == "N2"
        # values are {value, alias} dicts; the resistor alias list differs
        # between dialects (spice: r/R, lcapy: Value/resistance/r)
        assert element["values"]["value"]["value"] == "1k"
        assert "resistance" in element["values"]["value"]["alias"]
        assert "r" in element["values"]["value"]["alias"]

    def test_vsource_kind_keyword(self, convert):
        # 'DC 5': in lcapy mode 'dc' is a kind keyword; in SPICE mode the
        # DC specifier is transparently expanded so the operand lands in the
        # value slot (same outcome, different mechanism).
        name, element = convert._parse_line("V1 N1 0 dc 5")
        assert element["prefix"] == "V"
        if convert.IS_SPICE:
            assert element["values"]["value"]["value"] == "5"
            assert element["kind"] == []
        else:
            assert element["kind"] == ["dc"]

    def test_wire_uniquified(self, convert):
        # W means different things per dialect: an lcapy structural wire
        # (2 tokens) vs a SPICE current-controlled switch (5 tokens:
        # N+ N- VNAM MODEL, resolved through the .model table).
        if convert.IS_SPICE:
            model_table = {"SWM": {"model_type": "CSW", "args": []}}
            name, element = convert._parse_line(
                "W1 N1 N2 V1 SWM", model_table=model_table
            )
            assert name == "W1"
            assert element["prefix"] == "W"
            # SPICE: W is the current-controlled switch — a normal component
            assert element["skin_alias"] == ["csw"]
        else:
            name, element = convert._parse_line("W1 N1 N2")
            assert element["prefix"] == "W"
            # lcapy: wires get a uuid suffix to stay unique
            assert name != "W1"
            assert name.startswith("W")

    def test_duplicate_component_raises(self, convert):
        with pytest.raises(convert.NetlistError, match="Duplicate"):
            text = "V1 N1 0 5\nV1 N2 0 3\n"
            convert._parse_netlist(text)


class TestSpecifiers:
    """The ngspice V/I mode-segment grammar: <DC VALUE> <AC [MAG [PHASE]]>.

    Specifiers are declaratively expanded into key=value tokens before spec
    matching; these tests cover every doc form plus the lexical glues.
    """

    def test_plain_value(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b 5")
        assert e["values"]["value"]["value"] == "5"

    def test_dc_transparent(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b DC 5")
        assert e["values"]["value"]["value"] == "5"
        assert not any(k.startswith("unknown") for k in e["values"])

    def test_ac_mag(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b AC 1")
        assert e["values"]["unknown_AC"]["value"] == "1"
        assert "value" not in e["values"]  # AC-only source: no DC value

    def test_ac_mag_phase(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b AC 1 90")
        assert e["values"]["unknown_AC"]["value"] == "1"
        assert e["values"]["unknown_phase"]["value"] == "90"

    def test_ac_bare_defaults_mag_1(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b AC")
        assert e["values"]["unknown_AC"]["value"] == "1"

    def test_value_then_ac(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b 5 AC 1")
        assert e["values"]["value"]["value"] == "5"
        assert e["values"]["unknown_AC"]["value"] == "1"

    def test_ac_case_insensitive(self, convert_spice):
        _, e = convert_spice._parse_line("V1 a b ac 1m")
        assert e["values"]["unknown_AC"]["value"] == "1m"

    def test_isource_same_grammar(self, convert_spice):
        _, e = convert_spice._parse_line("I1 x y 2m AC 1")
        assert e["values"]["value"]["value"] == "2m"
        assert e["values"]["unknown_AC"]["value"] == "1"

    def test_function_value_with_space_before_paren(self, convert_spice):
        # LTspice 'PWL (' form: space before the paren is legal
        _, e = convert_spice._parse_line("V1 a b PWL (0,0 10m,12)")
        assert e["values"]["value"]["value"] == "PWL(0,0 10m,12)"

    def test_comma_list_glues_to_one_keyword(self, convert_spice):
        # T's IC= list spanning whitespace -> one keyword token
        _, e = convert_spice._parse_line("T1 a b c d Z0=50 IC=1.2, 0.1, 0.05, 0.02")
        assert e["values"]["unknown_IC"]["value"] == "1.2,0.1,0.05,0.02"

    def test_dc_ac_source_split(self, convert_spice):
        # the DC/AC specifier split doubles as source-type discrimination
        # (mirrors the lcapy dialect's dc/ac kind split):
        #   no AC on the line  -> DC source (skin v / i)
        #   AC on the line     -> AC source (skin sv / si), even with a value
        _, e = convert_spice._parse_line("V1 a b 5")
        assert e["skin_alias"] == ["v"]
        _, e = convert_spice._parse_line("V1 a b DC 5")
        assert e["skin_alias"] == ["v"]
        _, e = convert_spice._parse_line("V1 a b 5 AC 1")
        assert e["skin_alias"] == ["sv"]
        _, e = convert_spice._parse_line("V1 a b AC")
        assert e["skin_alias"] == ["sv"]
        _, e = convert_spice._parse_line("V1 a b DC 5 AC 1")
        assert e["skin_alias"] == ["sv"]  # AC present -> AC source
        assert e["values"]["value"]["value"] == "5"  # DC operand kept
        # current sources mirror the split
        _, e = convert_spice._parse_line("I1 x y 2m")
        assert e["skin_alias"] == ["i"]
        _, e = convert_spice._parse_line("I1 x y AC 1")
        assert e["skin_alias"] == ["si"]

    def test_undeclared_bare_keyword_fails_loudly(self, convert_spice):
        # a bare token that is neither a declared specifier nor a kind
        # is not swallowed: it fails loudly in matching
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_line("V1 a b FOO 5")


class TestModelTable:
    """`.model` directives build a name->type table; specs marked is_model
    resolve their model token through it and use the resolved TYPE as the
    kind for spec selection. SPICE-only (skipped under lcapy)."""

    def test_diode_with_infile_model(self, convert_spice):
        parsed = convert_spice._parse_netlist("D1 N1 N2 1N4148\n.model 1N4148 D\n")
        assert parsed["D1"]["skin_alias"] == ["d_h"]
        assert parsed["D1"]["model_type"] == ["D"]
        # model token consumed — only the two nodes remain as connections
        assert set(parsed["D1"]["connections"]) == {"+", "-"}

    @pytest.mark.parametrize("strict", [False, True])
    def test_unknown_model_rejected(self, convert_spice, strict):
        # a model name without an in-file .model card (external .lib model)
        # cannot be resolved, so no spec matches — in both modes
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist("D1 N1 N2 1N4148\n", strict=strict)
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist("M1 D G S B IRFP240\n", strict=strict)

    @pytest.mark.parametrize("line", ["D1 N1 N2", "Q1 C B E", "M1 D G S B", "S1 a b c d", "O1 a 0 b 0"])
    def test_model_typed_element_needs_model_name(self, convert_spice, line):
        # model-typed elements have no model-less spec: ngspice needs the model
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist(line + "\n")

    def test_bjt_npn_pnp_selected_by_model(self, convert_spice):
        text = "Q1 C B E QN\nQ2 C2 B2 E2 QP\n.model QN NPN\n.model QP PNP\n"
        parsed = convert_spice._parse_netlist(text)
        assert parsed["Q1"]["skin_alias"] == ["q_npn"]
        assert parsed["Q2"]["skin_alias"] == ["q_pnp"]
        # nodes c/b/e only — model token consumed
        assert set(parsed["Q1"]["connections"]) == {"c", "b", "e"}
        assert parsed["Q1"]["connections"]["c"]["node_name"] == "C"

    def test_bjt_with_substrate_node(self, convert_spice):
        # 5-token form: substrate node dropped, model is last token
        text = "Q3 N9 N6 N5 0 MJE350\n.model MJE350 NPN\n"
        parsed = convert_spice._parse_netlist(text)
        assert parsed["Q3"]["skin_alias"] == ["q_npn"]
        # c/b/e connected; the substrate node is recorded but marked drop
        assert set(parsed["Q3"]["connections"]) == {"c", "b", "e", "ns"}
        assert parsed["Q3"]["connections"]["ns"]["drop"] is True
        assert parsed["Q3"]["connections"]["c"]["node_name"] == "N9"

    def test_mosfet_nmos_pmos_selected_by_model(self, convert_spice):
        text = "M1 D G S B MN\n.model MN NMOS\nM2 D2 G2 S2 B2 MP\n.model MP PMOS\n"
        parsed = convert_spice._parse_netlist(text)
        assert parsed["M1"]["skin_alias"] == ["mos_n"]
        assert parsed["M2"]["skin_alias"] == ["mos_p"]
        # bulk node recorded but marked drop (no skin pin)
        assert set(parsed["M1"]["connections"]) == {"d", "g", "s", "b"}
        assert parsed["M1"]["connections"]["b"]["drop"] is True

    def test_mismatched_model_type_raises(self, convert_spice):
        # an NMOS-type model cannot satisfy any Q spec (NPN/PNP/LPNP kinds)
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist("Q1 C B E QX\n.model QX NMOS\n")

    def test_wrong_prefix_model_type_raises(self, convert_spice):
        # a PNP-type model cannot satisfy the D spec (kind 'D')
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist("D1 N1 N2 QP\n.model QP PNP\n")

    def test_model_type_case_insensitive(self, convert_spice):
        parsed = convert_spice._parse_netlist("M1 D G S B mm\n.model mm pmos\n")
        assert parsed["M1"]["skin_alias"] == ["mos_p"]

    def test_model_name_matching_kind_keyword_not_eaten(self, convert_spice):
        # a model literally named 'NMOS' must resolve via the table, not be
        # consumed by inline kind filtering
        parsed = convert_spice._parse_netlist("M1 D G S B NMOS\n.model NMOS NMOS\n")
        assert parsed["M1"]["skin_alias"] == ["mos_n"]

    def test_single_model_arg_enforced(self, convert_spice):
        # a SPICE component references exactly ONE model: two model-name
        # tokens in one line is an error, regardless of spec matching
        with pytest.raises(ValueError, match="All specs failed"):
            convert_spice._parse_netlist("Q1 C B E QN QP\n.model QN NPN\n.model QP PNP\n")


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
