"""Tests for the ngspice deck exporter (parser/export.py). No ngspice needed:
the deck is plain text."""

import pytest

from circuit_data_gen.parser.export import DEFAULT_TITLE, SOLVER_OPTIONS, to_ngspice_netlist

NETLIST = "V1 in 0 5\nR1 in out 1k\nR2 out 0 2k\n.model Dm D\n"


class TestDeckFrame:
    def test_title_first_then_body_then_options_analysis_end(self):
        lines = to_ngspice_netlist(NETLIST).splitlines()
        assert lines[0] == DEFAULT_TITLE
        assert lines[1:5] == NETLIST.splitlines()
        assert lines[5:] == [SOLVER_OPTIONS, ".op", ".end"]

    def test_convergence_aids_are_off(self):
        assert "gminsteps=0" in SOLVER_OPTIONS and "srcsteps=0" in SOLVER_OPTIONS

    def test_first_netlist_line_is_not_the_title(self):
        # ngspice reads line 1 as the title: the element must move down
        deck = to_ngspice_netlist("V1 in 0 5\nR1 in 0 1k\n")
        assert deck.splitlines()[0] == DEFAULT_TITLE
        assert deck.splitlines()[1] == "V1 in 0 5"

    def test_custom_analysis_and_title(self):
        deck = to_ngspice_netlist(NETLIST, analysis=".tran 1u 1m", title="* sample 7")
        lines = deck.splitlines()
        assert lines[0] == "* sample 7"
        assert lines[-2:] == [".tran 1u 1m", ".end"]

    def test_title_must_be_one_line(self):
        with pytest.raises(ValueError, match="single line"):
            to_ngspice_netlist(NETLIST, title="* a\n* b")


class TestBody:
    @pytest.mark.parametrize("end", [".end", ".END", "  .end  "])
    def test_netlist_end_is_dropped(self, end):
        # ngspice stops at the first .end, which would hide the analysis
        deck = to_ngspice_netlist(f"V1 in 0 5\n{end}\nR1 in 0 1k\n")
        assert [line for line in deck.splitlines() if line.strip().lower() == ".end"] == [".end"]
        assert deck.splitlines()[-1] == ".end"
        assert "R1 in 0 1k" in deck

    def test_ends_is_kept(self):
        text = "X1 a 0 amp\n.subckt amp p n\nR1 p n 1k\n.ends\n"
        assert ".ends" in to_ngspice_netlist(text).splitlines()

    def test_body_is_otherwise_untouched(self):
        # .param, K lines, continuations and comments reach ngspice as written
        text = ".param R0=1k\nL1 a 0 1u\nL2 b 0 1u\nK1 L1 L2 0.9\nR1 a b\n+ {R0}\n* note\n"
        lines = to_ngspice_netlist(text).splitlines()
        assert lines[1:-3] == text.splitlines()

    def test_surrounding_blank_lines_trimmed(self):
        lines = to_ngspice_netlist("\n\nV1 in 0 5\n\nR1 in 0 1k\n\n").splitlines()
        assert lines[1:4] == ["V1 in 0 5", "", "R1 in 0 1k"]
        assert lines[4] == SOLVER_OPTIONS
