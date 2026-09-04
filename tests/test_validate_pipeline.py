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


@pytest.fixture
def state():
    logging.basicConfig(level=logging.CRITICAL)
    return {
        "logger": logging.getLogger("test-validate"),
        "index": 0,
        "requirements": CircuitRequirements(min_components=1),
    }


def _run(state, netlist_text: str, tmp_path: Path, net_name: str = "test.net"):
    net_path = tmp_path / net_name
    net_path.write_text(netlist_text)
    return smoke_test_render(
        state,
        net_path,
        tmp_path / "schematic.png",
        tmp_path / "annotation.json",
        tmp_path / "overlay.png",
    )


class TestSmokeTestRenderValid:
    def test_clean_connected_netlist_passes(self, state, tmp_netlist, tmp_path):
        net_path = tmp_netlist(CONNECTED_NETLIST)
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert valid, errors
        assert errors == []
        assert (tmp_path / "s.png").exists()

    def test_persisted_outputs_are_created(self, state, tmp_netlist, tmp_path):
        net_path = tmp_netlist(CONNECTED_NETLIST)
        # smoke_test_render renders into the given temp paths; the caller
        # (validate_circuit) does the persisting, so here we just confirm
        # render products exist after a valid run
        valid, _ = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert (tmp_path / "s.png").stat().st_size > 0


class TestSmokeTestRenderErrors:
    def test_isolated_subgraphs_rejected(self, state, tmp_netlist, tmp_path):
        net_path = tmp_netlist(ISOLATED_NETLIST)
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        assert any("not a connected graph" in e and "isolated subgraphs" in e for e in errors), errors

    def test_min_components_enforced(self, state, tmp_netlist, tmp_path):
        state["requirements"] = CircuitRequirements(min_components=10)
        net_path = tmp_netlist(CONNECTED_NETLIST)
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        assert any("less than the required minimum" in e for e in errors)

    def test_missing_required_component_rejected(self, state, tmp_netlist, tmp_path):
        state["requirements"] = CircuitRequirements(
            min_components=1,
            required_components=[("L", "")],  # inductor not in CONNECTED_NETLIST
        )
        net_path = tmp_netlist(CONNECTED_NETLIST)
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        assert any("missing required components" in e for e in errors)

    def test_hanging_node_rejected(self, state, tmp_netlist, tmp_path):
        # N2 touches only R1 -> hanging
        net_path = tmp_netlist("V1 N1 0 dc 5\nR1 N1 N2 1k\n")
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        assert any("hanging" in e for e in errors), errors

    def test_unparseable_netlist_reports_error_string(self, state, tmp_netlist, tmp_path):
        net_path = tmp_netlist("R1 N1 N2 1k\nTHIS IS NOT SPICE @@@\n")
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        assert len(errors) >= 1

    def test_multiple_errors_reported_together(self, state, tmp_netlist, tmp_path):
        # isolated AND below min components AND missing a required kind
        state["requirements"] = CircuitRequirements(
            min_components=20,
            required_components=[("L", "")],
        )
        net_path = tmp_netlist(ISOLATED_NETLIST)
        valid, errors = smoke_test_render(
            state,
            net_path,
            tmp_path / "s.png",
            tmp_path / "a.json",
            tmp_path / "o.png",
        )
        assert not valid
        joined = "\n".join(errors)
        assert "not a connected graph" in joined
        assert "required minimum" in joined
        assert "missing required components" in joined
