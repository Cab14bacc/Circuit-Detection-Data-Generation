"""Tests for the generation pipeline's circuit validator
(circuit_data_gen/netlist_gen/setup.py::smoke_test_render).

Each test feeds a crafted netlist through the real render + validation path
and asserts which errors fire. The LLM itself is not involved: only the
validation half of the LangGraph loop is tested.
"""

import logging
from pathlib import Path

import pytest

from circuit_data_gen.netlist_gen.parallel import CircuitRequirements
from circuit_data_gen.netlist_gen.setup import smoke_test_render

from tests.conftest import (
    CONNECTED_NETLIST,
    ISOLATED_NETLIST,
)


# exact component-type tuples as a parsed netlist reports them —
# (prefix, lowercased kinds, lowercased specifiers). The subset allow-list
# check compares against these verbatim, so they must match exactly.
_CONNECTED_TYPES = [("C", (), ()), ("R", (), ()), ("V", (), ("dc",))]
_ISOLATED_TYPES = [("R", (), ()), ("V", (), ("dc",))]

# CONNECTED_NETLIST holds 4 elements (V, R, R, C)
CONNECTED_REQS = CircuitRequirements(num_components=4, component_subset=_CONNECTED_TYPES)
# ISOLATED_NETLIST holds 4 elements (V, R, V, R)
ISOLATED_REQS = CircuitRequirements(num_components=4, component_subset=_ISOLATED_TYPES)


@pytest.fixture
def state():
    logging.basicConfig(level=logging.CRITICAL)
    return {
        "logger": logging.getLogger("test-validate"),
        "index": 0,
        "gen_count_per_session": 1,
        "circuits_valid": [False],
        "requirements": [CONNECTED_REQS],
    }


def _run(state, netlist_text: str, tmp_path: Path, gen_idx: int = 0, net_name: str = "test.net"):
    """Write a netlist to a temp file and run the real smoke test on it."""
    net_path = tmp_path / net_name
    net_path.write_text(netlist_text, encoding="utf-8")
    return smoke_test_render(
        state,
        gen_idx,
        net_path,
        tmp_path / "schematic.png",
        tmp_path / "annotation.json",
        tmp_path / "overlay.png",
    )


class TestSmokeTestRenderValid:
    def test_clean_connected_netlist_passes(self, state, tmp_path):
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert valid, errors
        assert errors == []
        assert (tmp_path / "schematic.png").exists()

    def test_render_artifacts_are_created(self, state, tmp_path):
        # smoke_test_render renders into the given temp paths; the caller
        # (validate_circuit) does the persisting, so here we just confirm
        # render products exist after a valid run
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert valid, errors
        assert (tmp_path / "schematic.png").stat().st_size > 0
        assert (tmp_path / "annotation.json").exists()
        assert (tmp_path / "overlay.png").exists()


class TestSmokeTestRenderErrors:
    def test_isolated_subgraphs_rejected(self, state, tmp_path):
        state["requirements"] = [ISOLATED_REQS]
        valid, errors = _run(state, ISOLATED_NETLIST, tmp_path)
        assert not valid
        assert any("not a connected graph" in e and "isolated subgraphs" in e for e in errors), errors

    def test_wrong_component_count_rejected(self, state, tmp_path):
        # validation now demands an EXACT component count, not a minimum
        state["requirements"] = [
            CircuitRequirements(num_components=10, component_subset=_CONNECTED_TYPES)
        ]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert not valid
        assert any("not equal to the required number" in e for e in errors), errors

    def test_subset_violation_rejected(self, state, tmp_path):
        # the netlist uses R/V/C but the allowed subset only permits L
        state["requirements"] = [
            CircuitRequirements(num_components=4, component_subset=[("L", (), ())])
        ]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert not valid
        assert any("not in the specified subset" in e for e in errors), errors

    def test_hanging_node_rejected(self, state, tmp_path):
        # N2 touches only R1 -> hanging
        state["requirements"] = [
            CircuitRequirements(
                num_components=2,
                component_subset=[("R", (), ()), ("V", (), ("dc",))],
            )
        ]
        valid, errors = _run(state, "V1 N1 0 5\nR1 N1 N2 1k\n", tmp_path)
        assert not valid
        assert any("hanging" in e for e in errors), errors

    def test_unparseable_netlist_reports_error_string(self, state, tmp_path):
        valid, errors = _run(state, "R1 N1 N2 1k\nTHIS IS NOT SPICE @@@\n", tmp_path)
        assert not valid
        assert len(errors) >= 1

    def test_multiple_errors_reported_together(self, state, tmp_path):
        # wrong count AND subset violation AND isolated subgraphs
        state["requirements"] = [
            CircuitRequirements(num_components=20, component_subset=[("L", (), ())])
        ]
        valid, errors = _run(state, ISOLATED_NETLIST, tmp_path)
        assert not valid
        joined = "\n".join(errors)
        assert "not a connected graph" in joined
        assert "not equal to the required number" in joined
        assert "not in the specified subset" in joined
