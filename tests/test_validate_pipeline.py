"""Tests for the generation pipeline's circuit validator
(circuit_data_gen/netlist_gen/setup.py::smoke_test_render).

Each test feeds a crafted netlist through the real render + validation path
and asserts which errors fire. The LLM itself is not involved: only the
validation half of the LangGraph loop is tested.
"""

import logging
from pathlib import Path

import pytest

from circuit_data_gen.netlist_gen.setup import smoke_test_render

from tests.conftest import (
    CONNECTED_NETLIST,
    ISOLATED_NETLIST,
    requirements_for,
)

# a one-line netlist whose component type (a plain inductor) the other
# fixtures do not use
INDUCTOR_NETLIST = "L1 N1 0 1u\n"


@pytest.fixture
def state():
    logging.basicConfig(level=logging.CRITICAL)
    return {
        "logger": logging.getLogger("test-validate"),
        "index": 0,
        "gen_count_per_session": 1,
        "circuits_valid": [False],
        # CONNECTED_NETLIST's own 4 components and types, all required
        "requirements": [requirements_for(CONNECTED_NETLIST)],
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
        state["requirements"] = [requirements_for(ISOLATED_NETLIST)]
        valid, errors = _run(state, ISOLATED_NETLIST, tmp_path)
        assert not valid
        assert any("not a connected graph" in e and "isolated subgraphs" in e for e in errors), errors

    def test_wrong_component_count_rejected(self, state, tmp_path):
        # validation now demands an EXACT component count, not a minimum
        state["requirements"] = [requirements_for(CONNECTED_NETLIST, num_components=10)]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert not valid
        assert any("not equal to the required number" in e for e in errors), errors

    def test_disallowed_components_rejected(self, state, tmp_path):
        # the netlist uses R/V/C but only L is allowed (and none required)
        state["requirements"] = [requirements_for(INDUCTOR_NETLIST, num_components=4, required=False)]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert not valid
        assert any("uses components that are not allowed" in e for e in errors), errors
        assert not any("missing required components" in e for e in errors), errors

    def test_missing_required_component_rejected(self, state, tmp_path):
        # L is required on top of CONNECTED_NETLIST's own types
        reqs = requirements_for(CONNECTED_NETLIST + INDUCTOR_NETLIST, num_components=4)
        state["requirements"] = [reqs]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert not valid
        missing = [e for e in errors if "missing required components" in e]
        assert len(missing) == 1 and "`L`" in missing[0], errors
        assert not any("not allowed" in e for e in errors), errors

    def test_optional_component_may_be_left_out(self, state, tmp_path):
        # L allowed but not required: leaving it out is fine
        reqs = requirements_for(CONNECTED_NETLIST, num_components=4)
        reqs.allowed = reqs.allowed + requirements_for(INDUCTOR_NETLIST).allowed
        state["requirements"] = [reqs]
        valid, errors = _run(state, CONNECTED_NETLIST, tmp_path)
        assert valid, errors

    def test_hanging_node_rejected(self, state, tmp_path):
        # N2 touches only R1 -> hanging
        hanging = "V1 N1 0 5\nR1 N1 N2 1k\n"
        state["requirements"] = [requirements_for(hanging)]
        valid, errors = _run(state, hanging, tmp_path)
        assert not valid
        assert any("hanging" in e for e in errors), errors

    def test_unparseable_netlist_reports_error_string(self, state, tmp_path):
        valid, errors = _run(state, "R1 N1 N2 1k\nTHIS IS NOT SPICE @@@\n", tmp_path)
        assert not valid
        assert len(errors) >= 1

    def test_multiple_errors_reported_together(self, state, tmp_path):
        # wrong count AND subset violation AND isolated subgraphs
        state["requirements"] = [requirements_for(INDUCTOR_NETLIST, num_components=20, required=False)]
        valid, errors = _run(state, ISOLATED_NETLIST, tmp_path)
        assert not valid
        joined = "\n".join(errors)
        assert "not a connected graph" in joined
        assert "not equal to the required number" in joined
        assert "uses components that are not allowed" in joined
