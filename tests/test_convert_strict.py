"""Tests for strict SPICE parsing (convert.py::_parse_netlist(strict=True)).

Strict parsing guarantees the netlist is proper ngspice input: no braced node
tokens, only allowlisted directives, no undefined symbols in expressions, no
dangling references. Lenient parsing (the default) must keep accepting all of
it so external netlists still render.

All tests request ``convert_spice`` (SPICE only; skipped for lcapy) and read
functions off the returned module, which is reloaded per dialect.
"""

import logging

import pytest

from tests.conftest import CONNECTED_NETLIST


def _strict_errors(convert, text: str, simulate: bool | None = False) -> str:
    """Parse strictly, return the violation message ("" when clean).
    `simulate` is pinned (False) unless given, so the config can't leak in."""
    try:
        convert._parse_netlist(text, strict=True, simulate=simulate)
    except convert.NetlistError as e:
        return str(e)
    return ""


class TestTokens:
    def test_tokens_keep_raw_text_and_span(self, convert_spice):
        line = "G1 n3 n4 {n1} {n2} 1m"
        tokens = convert_spice._get_tokens(line)
        assert tokens == ["G1", "n3", "n4", "n1", "n2", "1m"]
        braced = tokens[3]
        assert braced.raw == "{n1}" and braced.braced
        assert line[slice(*braced.span)] == "{n1}"
        assert not tokens[1].braced

    def test_connection_records_brace_and_span(self, convert_spice):
        line = "E1 a b {c} 0 2"
        _, element = convert_spice._parse_line(line)
        control = element["connections"]["c+"]
        assert control["node_name"] == "c"
        assert type(control["node_name"]) is str
        assert control["braced"] is True
        assert line[slice(*control["span"])] == "{c}"
        assert element["connections"]["+"]["braced"] is False


class TestBracedNodes:
    NETLIST = "V1 n1 0 5\nE1 n2 0 {n1} {0} 2\nR1 n1 n2 1k\n"

    def test_lenient_accepts_braced_nodes(self, convert_spice):
        parsed = convert_spice._parse_netlist(self.NETLIST, strict=False)
        assert parsed["E1"]["connections"]["c+"]["braced"] is True

    def test_strict_rejects_braced_nodes(self, convert_spice):
        errors = _strict_errors(convert_spice, self.NETLIST)
        assert "node {n1} is written in curly braces" in errors
        assert "node {0} is written in curly braces" in errors

    def test_braced_value_is_not_a_node_error(self, convert_spice):
        # braces around a VALUE are expressions, which ngspice accepts
        assert _strict_errors(convert_spice, "B1 n1 0 V={12}\nR1 n1 0 1k\n") == ""


class TestKindKeywordOrder:
    """ngspice requires off / ON / OFF AFTER the model name; lenient parsing
    accepts either order, strict parsing only ngspice's."""

    @pytest.mark.parametrize(
        "line, keyword, model",
        [
            ("D1 a b off Dm", "off", "Dm"),
            ("Q1 a b 0 0 off Qm", "off", "Qm"),
            ("S1 b 0 a 0 ON Sm", "ON", "Sm"),
        ],
    )
    def test_keyword_before_model(self, convert_spice, line, keyword, model):
        text = f"V1 a 0 5\nR1 a b 1k\n{line}\n.model Dm D\n.model Qm NPN\n.model Sm SW\n"
        convert_spice._parse_netlist(text, strict=False)  # lenient: any order
        errors = _strict_errors(convert_spice, text)
        assert f"'{keyword}' must come after the model name '{model}'" in errors

    @pytest.mark.parametrize("line", ["D1 a b Dm off", "Q1 a b 0 0 Qm off", "S1 b 0 a 0 Sm ON"])
    def test_keyword_after_model(self, convert_spice, line):
        text = f"V1 a 0 5\nR1 a b 1k\n{line}\n.model Dm D\n.model Qm NPN\n.model Sm SW\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_grammar_renders_keyword_after_model(self, convert_spice):
        grammar = convert_spice.config_to_grammar()
        assert "format:Dname N+ N- mname off" in grammar
        assert "format:Sname NP N NC+ NC- mname ON" in grammar
        assert not any("off mname" in line or "ON mname" in line for line in grammar)


class TestModelConfig:
    """MODEL_CONFIG: .model cards are matched against the configurations
    ngspice implements, like elements are matched against their specs."""

    LINE = "V1 a 0 5\nR1 a b 50\nO1 b 0 c 0 Om\nR2 c 0 50\n"

    def test_every_spec_model_type_is_declared(self, convert_spice):
        declared = set(convert_spice.MODEL_CONFIG)
        for prefix, specs in convert_spice.TO_SKIN_CONFIG.items():
            for spec in specs:
                for model_type in spec.get("model_type", []):
                    assert model_type.upper() in declared, (prefix, model_type)

    @pytest.mark.parametrize(
        "card",
        [
            ".model Om LTRA R=0.1 L=250n G=0 C=100p LEN=1",  # RLC
            ".model Om LTRA R=0.1 L=0 G=0 C=100p LEN=1",  # RC (L written as 0)
            ".model Om LTRA R=0 L=250n G=0 C=100p LEN=1",  # LC
            ".model Om LTRA(r=0.1 l=250n g=0 c=100p len=1)",  # paren form, lower case
        ],
    )
    def test_implemented_lines_pass(self, convert_spice, card):
        assert _strict_errors(convert_spice, self.LINE + card + "\n") == ""

    def test_param_valued_entry_is_resolved(self, convert_spice):
        # {gg} resolves to 0 through the .param table, so G is 0 as required
        text = ".param gg=0\n" + self.LINE + ".model Om LTRA R=0.1 L=250n G={gg} C=100p LEN=1\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_nonzero_conductance_rejected(self, convert_spice):
        # ngspice: "Nonzero G (except RG) line not supported yet"
        errors = _strict_errors(convert_spice, self.LINE + ".model Om LTRA R=0.1 L=250n G=1m C=100p LEN=1\n")
        assert ".model Om LTRA: G must be 0." in errors

    def test_missing_params_reported(self, convert_spice):
        errors = _strict_errors(convert_spice, self.LINE + ".model Om LTRA R=0.1 C=100p\n")
        assert ".model Om LTRA: L is missing, LEN is missing, G is missing." in errors

    def test_txl_needs_every_param_and_card_length(self, convert_spice):
        # ngspice 34 wants R/L/G/C present (0 is fine) and "length" in the
        # CARD: LEN= on the Y line only overrides it
        base = "V1 a 0 5\nR1 a b 50\nY1 b 0 c 0 Ym LEN=2\nR2 c 0 50\n"
        errors = _strict_errors(convert_spice, base + ".model Ym TXL R=12.45 C=0.468p\n")
        assert ".model Ym TXL: L is missing, G is missing, LENGTH is missing." in errors
        card = ".model Ym TXL R=12.45 L=8.972n G=0 C=0.468p length=16\n"
        assert _strict_errors(convert_spice, base + card) == ""

    def test_lenient_parsing_ignores_model_params(self, convert_spice):
        convert_spice._parse_netlist(self.LINE + ".model Om LTRA\n", strict=False)

    def test_types_without_declared_params_need_no_params(self, convert_spice):
        # bare cards work for D (ngspice defaults) and for types MODEL_CONFIG
        # does not know (e.g. XSPICE code models), which are left to ngspice
        text = "V1 a 0 1\nD1 a b Dm\nA1 b c gm\nR1 c 0 1k\n.model Dm D\n.model gm gain(gain=2)\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_model_grammar_lists_configurations(self, convert_spice):
        grammar = convert_spice.model_config_to_grammar()
        # an is_zero param renders as G=0, not as a <value> placeholder
        assert "format:.model mname LTRA R=<value> L=<value> C=<value> LEN=<value> G=0" in grammar
        assert "format:.model mname TXL R=<value> L=<value> G=<value> C=<value> LENGTH=<value>" in grammar
        # configurations without declared params are skipped by default
        assert not any(line.endswith(" D") for line in grammar)


class TestDirectives:
    @pytest.mark.parametrize(
        "directive", [".tran 1u 1m", ".ac dec 10 1 1k", ".op", ".lib x.lib", ".include a"]
    )
    def test_strict_rejects_directive(self, convert_spice, directive):
        errors = _strict_errors(convert_spice, CONNECTED_NETLIST + directive + "\n")
        assert f"Directive '{directive}' is not allowed" in errors

    def test_allowed_directives_pass(self, convert_spice):
        text = ".param R_1=1k\nV1 a 0 5\nR1 a 0 {R_1}\nD1 a 0 Dm\n.model Dm D\n.end\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_control_block_skipped_in_lenient_mode(self, convert_spice):
        # 'run' would otherwise parse as an R element
        text = CONNECTED_NETLIST + ".control\nrun\nshell echo hi\n.endc\n"
        parsed = convert_spice._parse_netlist(text, strict=False)
        assert set(parsed) == {"V1", "R1", "R2", "C1"}

    def test_strict_rejects_control_block(self, convert_spice):
        errors = _strict_errors(convert_spice, CONNECTED_NETLIST + ".control\nrun\n.endc\n")
        assert "A .control block is not allowed" in errors

    def test_subckt_body_lines_are_not_elements(self, convert_spice):
        text = "V1 a 0 5\nX1 a 0 amp\n.subckt amp in out\nR9 in out 1k\n.ends\n"
        parsed = convert_spice._parse_netlist(text, strict=True)
        assert set(parsed) == {"V1", "X1"}


class TestSymbols:
    def test_param_defined(self, convert_spice):
        assert _strict_errors(convert_spice, ".param Rb=1k\nV1 a 0 5\nR1 a 0 {2*Rb}\n") == ""

    def test_param_undefined(self, convert_spice):
        errors = _strict_errors(convert_spice, "V1 a 0 5\nR1 a 0 {R_1}\n")
        assert "undefined symbol(s) R_1" in errors

    def test_param_names_are_case_insensitive(self, convert_spice):
        assert _strict_errors(convert_spice, ".param rb=1k\nV1 a 0 5\nR1 a 0 {RB}\n") == ""

    def test_param_referencing_undefined_param(self, convert_spice):
        errors = _strict_errors(convert_spice, ".param a={2*b}\nV1 a1 0 5\nR1 a1 0 {a}\n")
        assert ".param A: value '2*b' uses undefined symbol(s) b" in errors

    @pytest.mark.parametrize("value", ["1k", "2.2u", "1e-6", "10Meg", "5V", "1uF", ".5"])
    def test_numbers_are_not_symbols(self, convert_spice, value):
        assert _strict_errors(convert_spice, f"V1 a 0 5\nR1 a 0 {value}\n") == ""

    def test_builtins_and_probes(self, convert_spice):
        text = "V1 a 0 5\nR1 a b 1k\nB1 b 0 V='2*sin(2*pi*time)+v(a)-v(a,b)+i(V1)'\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_function_names_are_not_checked(self, convert_spice):
        # unknown functions are ngspice's to report, not the parser's
        text = "V1 a 0 SINE(0 1 1k)\nV2 b 0 SIN(0 1 1k)\nR1 a b 1k\nB1 b 0 V={foo(1)}\n"
        assert _strict_errors(convert_spice, text) == ""

    def test_bad_probes(self, convert_spice):
        errors = _strict_errors(convert_spice, "V1 a 0 5\nR1 a 0 1k\nB1 a 0 V='v(nX)+i(R1)'\n")
        assert "v(nX) refers to node nX" in errors
        assert "i(R1) must name a voltage source" in errors


class TestReferences:
    def test_controlling_source_must_exist(self, convert_spice):
        errors = _strict_errors(convert_spice, "V1 a 0 5\nF1 a 0 V9 2\nR1 a 0 1k\n")
        assert "Component F1: 'V9' must name a V element" in errors
        assert _strict_errors(convert_spice, "V1 a 0 5\nF1 a 0 V1 2\nR1 a 0 1k\n") == ""

    def test_controlling_source_must_be_a_v_element(self, convert_spice):
        errors = _strict_errors(convert_spice, "V1 a 0 5\nH1 a 0 R1 2\nR1 a 0 1k\n")
        assert "Component H1: 'R1' must name a V element" in errors

    def test_coupled_inductors_must_exist(self, convert_spice):
        text = "V1 a 0 5\nL1 a 0 1u\nL2 a 0 1u\nK1 L1 L9 0.9\n"
        assert "inductor 'L9' is not an L element" in _strict_errors(convert_spice, text)
        assert _strict_errors(convert_spice, text.replace("L9", "L2")) == ""

    def test_subckt_must_be_defined(self, convert_spice):
        errors = _strict_errors(convert_spice, "V1 a 0 5\nX1 a 0 amp\n")
        assert "subcircuit 'amp' is not defined" in errors

    def test_subckt_name_is_label_not_pin(self, convert_spice):
        _, element = convert_spice._parse_line("X1 a 0 amp")
        assert [c["node_name"] for c in element["connections"].values()] == ["a", "0"]
        assert element["values"]["value"]["value"] == "amp"


class TestSimulationComponents:
    """With simulate on, strict parsing rejects what ngspice cannot simulate:
    specs marked "simulatable": False (N/P/A/U generics) and .model TYPEs
    whose configurations all are (LPNP)."""

    LPNP = "V1 c 0 5\nR1 c b 1k\nQ1 c b 0 QL\n.model QL LPNP\n"

    def test_unsimulatable_model_type_rejected(self, convert_spice):
        errors = _strict_errors(convert_spice, self.LPNP, simulate=True)
        assert "Component Q1: model QL has type LPNP, which cannot be simulated" in errors
        assert "use model type NPN or PNP instead" in errors

    def test_simulatable_model_type_accepted(self, convert_spice):
        text = self.LPNP.replace("LPNP", "NPN")
        assert _strict_errors(convert_spice, text, simulate=True) == ""

    @pytest.mark.parametrize("prefix", ["N", "P", "A", "U"])
    def test_unsimulatable_generic_rejected(self, convert_spice, prefix):
        text = f"V1 a 0 5\nR1 a b 1k\n{prefix}1 a b dev\n"
        errors = _strict_errors(convert_spice, text, simulate=True)
        assert f"Component {prefix}1: {prefix} elements cannot be simulated" in errors

    def test_subckt_instance_accepted(self, convert_spice):
        text = "V1 a 0 5\nR1 a b 1k\nX1 a b amp\n.subckt amp p n\nR9 p n 1k\n.ends\n"
        assert _strict_errors(convert_spice, text, simulate=True) == ""

    def test_strict_alone_does_not_check_simulation(self, convert_spice):
        # strict parsing does not imply simulation
        assert _strict_errors(convert_spice, self.LPNP, simulate=False) == ""

    def test_simulate_without_strict_does_nothing(self, convert_spice):
        # the check is part of strict parsing: lenient parsing never runs it
        parsed = convert_spice._parse_netlist(self.LPNP, strict=False, simulate=True)
        assert "Q1" in parsed

    def test_simulate_defaults_to_config(self, convert_spice, monkeypatch):
        monkeypatch.setattr(convert_spice, "SIMULATION_ENABLED", True)
        errors = _strict_errors(convert_spice, self.LPNP, simulate=None)
        assert "which cannot be simulated" in errors
        monkeypatch.setattr(convert_spice, "SIMULATION_ENABLED", False)
        assert _strict_errors(convert_spice, self.LPNP, simulate=None) == ""


class TestSimulatableGrammar:
    def test_grammar_can_leave_out_unsimulatable_specs(self, convert_spice):
        full = convert_spice.config_to_grammar()
        sim = convert_spice.config_to_grammar(simulatable_only=True)
        for prefix in "NPAU":
            assert f"format:{prefix}name" in "\n".join(full)
            assert not [line for line in sim if line.startswith(f"format:{prefix}name")]
        # the element lines do not name the model type, so count the Q specs:
        # the LPNP ones are gone, NPN/PNP stay
        q_specs = convert_spice.TO_SKIN_CONFIG["Q"]
        simulatable_q = [s for s in q_specs if s["model_type"] != ["LPNP"]]
        assert sum(line.startswith("format:Qname") for line in full) == len(q_specs)
        assert sum(line.startswith("format:Qname") for line in sim) == len(simulatable_q)
        assert "format:Xname" in "\n".join(sim)

    def test_model_grammar_can_leave_out_unsimulatable_cards(self, convert_spice):
        full = convert_spice.model_config_to_grammar(only_declared=False)
        sim = convert_spice.model_config_to_grammar(only_declared=False, simulatable_only=True)
        assert "format:.model mname LPNP" in full
        assert "format:.model mname LPNP" not in sim
        assert "format:.model mname NPN" in sim


class TestGrammarRendering:
    def test_grammar_never_braces_nodes(self, convert_spice):
        for mark_dropped in (False, True):
            for line in convert_spice.config_to_grammar(mark_dropped=mark_dropped):
                pattern = line.split(" , ")[0]
                assert "{" not in pattern and "}" not in pattern, line

    def test_hidden_nodes_listed_when_marked(self, convert_spice):
        spec = convert_spice.TO_SKIN_CONFIG["E"][0]
        assert convert_spice._render_spec_grammar("E", spec) == "Ename N+ N- NC+ NC- <voltage_gain>"
        marked = convert_spice._render_spec_grammar("E", spec, mark_dropped=True, show_skin=True)
        assert marked == "Ename N+ N- NC+ NC- <voltage_gain> , hidden:NC+ NC- , skin:cvs"


class TestExplicitGenerics:
    def test_n_is_an_explicit_generic(self, convert_spice):
        from circuit_data_gen.validate import undefined_components

        parsed = convert_spice._parse_netlist("V1 a 0 5\nN1 a 0 b osdimod\nR1 b 0 1k\n")
        assert parsed["N1"]["prefix"] == "N"
        assert parsed["N1"]["if_generic"] is True
        assert undefined_components(parsed) == []


class TestGenerationStrictness:
    def test_smoke_test_render_parses_strictly(self, convert_spice, monkeypatch, tmp_path):
        from circuit_data_gen.netlist_gen import setup
        from circuit_data_gen.netlist_gen.parallel import CircuitRequirements

        monkeypatch.setattr(setup, "GEN_STRICT", True)
        monkeypatch.setattr(setup, "GEN_SIMULATE", False)
        net_path = tmp_path / "test.net"
        net_path.write_text(CONNECTED_NETLIST + ".tran 1u 1m\n", encoding="utf-8")
        state = {
            "logger": logging.getLogger("test-strict"),
            "requirements": [
                CircuitRequirements(
                    num_components=4, component_subset=[("C", (), ()), ("R", (), ()), ("V", (), ("dc",))]
                )
            ],
        }
        valid, errors = setup.smoke_test_render(
            state, 0, net_path, tmp_path / "s.png", tmp_path / "a.json", tmp_path / "o.png"
        )
        assert not valid
        assert any("Directive '.tran 1u 1m' is not allowed" in e for e in errors), errors

    @pytest.mark.parametrize(
        "gen_strict, gen_simulate, key",
        [
            (False, False, "LLM_SYSTEM_PROMPT_PATH"),
            (True, False, "LLM_STRICT_SYSTEM_PROMPT_PATH"),
            (False, True, "LLM_SIMULATE_SYSTEM_PROMPT_PATH"),
            (True, True, "LLM_SIMULATE_SYSTEM_PROMPT_PATH"),
        ],
    )
    def test_system_prompt_follows_config(self, convert_spice, monkeypatch, gen_strict, gen_simulate, key):
        from circuit_data_gen.configs.config import get_config_path_value
        from circuit_data_gen.netlist_gen import setup

        monkeypatch.setattr(setup, "GEN_STRICT", gen_strict)
        monkeypatch.setattr(setup, "GEN_SIMULATE", gen_simulate)
        assert setup.system_prompt_key() == key
        prompt = get_config_path_value("netlist_gen", key).read_text(encoding="utf-8")
        # the strict and simulation prompts document the strict-parsing
        # errors; only the simulation prompt the DC operating-point rules
        assert ("Strict Parsing Errors" in prompt) is (gen_strict or gen_simulate)
        assert ("Electrical Validity" in prompt) is gen_simulate

    def test_explicit_simulate_overrides_config(self, convert_spice, monkeypatch):
        from circuit_data_gen.netlist_gen import setup

        monkeypatch.setattr(setup, "GEN_STRICT", False)
        monkeypatch.setattr(setup, "GEN_SIMULATE", False)
        assert setup.system_prompt_key(simulate=True) == "LLM_SIMULATE_SYSTEM_PROMPT_PATH"
        monkeypatch.setattr(setup, "GEN_SIMULATE", True)
        assert setup.system_prompt_key(simulate=False) == "LLM_SYSTEM_PROMPT_PATH"
        assert setup.system_prompt_key(strict=True, simulate=False) == "LLM_STRICT_SYSTEM_PROMPT_PATH"

    def test_simulate_implies_strict(self, convert_spice, monkeypatch):
        from circuit_data_gen.netlist_gen import setup

        monkeypatch.setattr(setup, "GEN_STRICT", False)
        monkeypatch.setattr(setup, "GEN_SIMULATE", False)
        assert setup._resolve_strict(None, simulate=True) is True
        # even an explicit strict=False: ngspice needs the strict guarantees
        assert setup._resolve_strict(False, simulate=True) is True
        assert setup._resolve_strict(None, simulate=False) is False

    @pytest.mark.parametrize("simulate, rejected", [(True, True), (False, False)])
    def test_smoke_test_render_forwards_simulate(
        self, convert_spice, monkeypatch, tmp_path, simulate, rejected
    ):
        from circuit_data_gen.netlist_gen import setup

        monkeypatch.setattr(setup, "full_validation", lambda *args, **kwargs: [])
        net_path = tmp_path / "test.net"
        net_path.write_text("V1 c 0 5\nR1 c b 1k\nQ1 c b 0 QL\n.model QL LPNP\n", encoding="utf-8")
        state = {"logger": logging.getLogger("test-simulate"), "requirements": [None]}
        valid, errors = setup.smoke_test_render(
            state,
            0,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
            strict=True,
            simulate=simulate,
        )
        assert valid is not rejected, errors
        assert any("type LPNP, which cannot be simulated" in e for e in errors) is rejected

    def test_gen_data_has_simulate_option(self):
        from typer.testing import CliRunner

        from circuit_data_gen.cli import app

        result = CliRunner().invoke(app, ["gen_data", "--help"], terminal_width=200)
        assert result.exit_code == 0
        assert "--simulate" in result.output and "--no-simulate" in result.output

    def test_explicit_strict_overrides_config(self, convert_spice, monkeypatch, tmp_path):
        from circuit_data_gen.netlist_gen import setup

        monkeypatch.setattr(setup, "GEN_SIMULATE", False)
        monkeypatch.setattr(setup, "GEN_STRICT", False)
        assert setup.system_prompt_key(True) == "LLM_STRICT_SYSTEM_PROMPT_PATH"
        monkeypatch.setattr(setup, "GEN_STRICT", True)
        assert setup.system_prompt_key(False) == "LLM_SYSTEM_PROMPT_PATH"

        # config says strict, the explicit False wins: the .tran line is ignored
        net_path = tmp_path / "test.net"
        net_path.write_text(CONNECTED_NETLIST + ".tran 1u 1m\n", encoding="utf-8")
        state = {"logger": logging.getLogger("test-strict"), "requirements": [None]}
        monkeypatch.setattr(setup, "full_validation", lambda *args, **kwargs: [])
        valid, errors = setup.smoke_test_render(
            state, 0, net_path, tmp_path / "s.png", tmp_path / "a.json", tmp_path / "o.png", strict=False
        )
        assert valid, errors

    def test_gen_data_has_strict_option(self):
        from typer.testing import CliRunner

        from circuit_data_gen.cli import app

        result = CliRunner().invoke(app, ["gen_data", "--help"], terminal_width=200)
        assert result.exit_code == 0
        assert "--strict" in result.output and "--no-strict" in result.output
