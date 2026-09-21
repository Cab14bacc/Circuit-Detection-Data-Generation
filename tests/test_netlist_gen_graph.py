"""Tests for the generation langchain graph wiring (netlist_gen/setup.py).

Exercises validate_circuit's netlist-tag handling and the regenerate-loop
routing directly (the node function is pulled off the compiled graph), so no
LLM call is made — generate_circuit is the only node that hits the API.
"""

import logging

import pytest
from langchain_core.messages import AIMessage

from circuit_data_gen.netlist_gen.parallel import CircuitRequirements
from circuit_data_gen.netlist_gen.setup import netlist_gen_setup

from tests.conftest import (
    CONNECTED_NETLIST,
    ISOLATED_NETLIST,
    NETLIST_MISSING_TAG,
)


# a valid netlist wrapped in the tags the LLM is asked to emit
# (gen idx 0 of a gen_count_per_session=1 session)
WELL_FORMED_TAGGED = "<netlist0>\n" + CONNECTED_NETLIST + "</netlist0>"
ISOLATED_TAGGED = "<netlist0>\n" + ISOLATED_NETLIST + "</netlist0>"

# a per-index requirement matching CONNECTED_NETLIST's 4 components
# (V, R, R, C) and its exact type tuples
_CONNECTED_TYPES = [("C", (), ()), ("R", (), ()), ("V", (), ("dc",))]


def _reqs(count: int = 1):
    return [
        CircuitRequirements(num_components=4, component_subset=_CONNECTED_TYPES)
        for _ in range(count)
    ]


@pytest.fixture
def validate_node(tmp_path):
    netlist_dir = tmp_path / "netlists"
    schematic_dir = tmp_path / "schematics"
    annotation_dir = tmp_path / "annotations"
    graph = netlist_gen_setup(netlist_dir, schematic_dir, annotation_dir, temperature=0.0)
    # langgraph wraps node fns in a Runnable; .invoke() calls it
    node = graph.nodes["validate_circuit"]
    return node, netlist_dir, schematic_dir, annotation_dir


def _state(content: str, index: int = 0):
    # NOTE: output_* arrays are normally seeded by the graph's initialize()
    # node, which this test bypasses by invoking validate_circuit directly.
    return {
        "messages": [AIMessage(content=content)],
        "circuit_valid": False,
        "logger": logging.getLogger("test-graph"),
        "index": index,
        "gen_count_per_session": 1,
        "circuits_valid": [False],
        "requirements": _reqs(1),
        "output_netlists": [""],
        "output_schematics": [""],
        "output_annotations": [""],
        "output_overlays": [""],
        "output_yosys": [""],
    }


class TestValidateCircuitTags:
    async def test_missing_netlist_tag_asks_for_retry(self, validate_node):
        node, netlist_dir, _, _ = validate_node
        result = node.invoke(_state(NETLIST_MISSING_TAG))
        assert result["circuit_valid"] is False
        # retry message asks for <netlist0> tags
        assert "<netlist0>" in result["messages"][-1].content

    async def test_valid_tagged_netlist_passes_and_persists(self, validate_node):
        node, netlist_dir, schematic_dir, annotation_dir = validate_node
        result = node.invoke(_state(WELL_FORMED_TAGGED))
        assert result["circuits_valid"] == [True]
        # filenames are {index}_{idx}: netlist_0_0.net for index=0, idx=0
        assert (netlist_dir / "netlist_0_0.net").exists()
        assert (schematic_dir / "schematic_0_0.png").exists()
        assert (annotation_dir / "annotation_0_0.json").exists()
        assert (annotation_dir / "overlay_0_0.png").exists()

    async def test_isolated_netlist_feeds_errors_back(self, validate_node):
        node, netlist_dir, _, _ = validate_node
        result = node.invoke(_state(ISOLATED_TAGGED))
        assert result["circuits_valid"] == [False]
        # nothing persisted for invalid circuits
        assert not (netlist_dir / "netlist_0_0.net").exists()
        # the error message is fed back for regeneration
        assert "not a connected graph" in result["messages"][-1].content

    async def test_index_respected_when_persisting(self, validate_node):
        node, netlist_dir, schematic_dir, _ = validate_node
        result = node.invoke(_state(WELL_FORMED_TAGGED, index=3))
        assert result["circuits_valid"] == [True]
        assert (netlist_dir / "netlist_3_0.net").exists()
        assert (schematic_dir / "schematic_3_0.png").exists()
