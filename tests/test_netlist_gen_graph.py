"""Tests for the generation langchain graph wiring (netlist_gen/setup.py).

Exercises validate_circuit's netlist-tag handling directly (the node function
is pulled off the compiled graph), and the regenerate loop's max_attempts
budget end to end with a fake LLM, so no API call is made.
"""

import logging

import pytest
from langchain_core.messages import AIMessage

from circuit_data_gen.netlist_gen import setup as gen_setup
from circuit_data_gen.netlist_gen.parallel import CircuitRequirements, run_pipeline_worker, scale_generation
from circuit_data_gen.netlist_gen.setup import DEFAULT_MAX_ATTEMPTS, netlist_gen_setup

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
        # a response without its tags still uses up an attempt
        assert result["attempts"] == 1

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


class _FakeLLM:
    """Stands in for ChatOpenAI: answers every call with the same content
    and counts the calls."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def __call__(self, **kwargs):
        return self  # ChatOpenAI(...) in netlist_gen_setup

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.content)


@pytest.fixture
def fake_graph(tmp_path, monkeypatch):
    """Build the graph around a fake LLM answering with `content`."""

    def build(content: str, **setup_kwargs):
        llm = _FakeLLM(content)
        monkeypatch.setattr(gen_setup, "ChatOpenAI", llm)
        graph = netlist_gen_setup(
            tmp_path / "netlists",
            tmp_path / "schematics",
            tmp_path / "annotations",
            temperature=0.0,
            **setup_kwargs,
        )
        return graph, llm

    return build


class TestMaxAttempts:
    async def test_gives_up_after_max_attempts(self, fake_graph):
        graph, llm = fake_graph(ISOLATED_TAGGED, max_attempts=2)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert llm.calls == 2
        assert final["attempts"] == 2
        assert final["circuits_valid"] == [False]
        # the last message is the error feedback of the final attempt
        assert "not a connected graph" in final["messages"][-1].content

    async def test_stops_at_first_valid_attempt(self, fake_graph):
        graph, llm = fake_graph(WELL_FORMED_TAGGED, max_attempts=5)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert llm.calls == 1
        assert final["attempts"] == 1
        assert final["circuits_valid"] == [True]

    async def test_default_budget(self, fake_graph):
        graph, llm = fake_graph(NETLIST_MISSING_TAG)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert llm.calls == DEFAULT_MAX_ATTEMPTS
        assert final["attempts"] == DEFAULT_MAX_ATTEMPTS

    async def test_worker_reports_exhausted_budget(self, fake_graph, tmp_path):
        # running out of attempts is a plain failure, not an exception
        graph, llm = fake_graph(ISOLATED_TAGGED, max_attempts=15)
        results = await run_pipeline_worker(
            graph,
            all_components=_CONNECTED_TYPES,
            num_components_range=(4, 4),
            gen_seed="attempts",
            log_dir=tmp_path / "logs",
        )
        assert llm.calls == 15
        assert [r.ok for r in results] == [False]
        assert results[0].exception is None
        assert results[0].attempts == 15

    async def test_scale_generation_rejects_zero_attempts(self, tmp_path):
        with pytest.raises(ValueError, match="max_attempts"):
            await scale_generation(tmp_path, num_netlists=1, max_attempts=0)

    def test_gen_data_has_max_attempts_option(self):
        from typer.testing import CliRunner  # noqa: PLC0415

        from circuit_data_gen.cli import app  # noqa: PLC0415

        result = CliRunner().invoke(app, ["gen_data", "--help"], terminal_width=200)
        assert result.exit_code == 0
        assert "--max-attempts" in result.output
