"""Tests for the generation langchain graph wiring (netlist_gen/setup.py).

Exercises validate_circuit's netlist-tag handling directly (the node function
is pulled off the compiled graph), the regenerate loop's max_attempts budget
end to end with a fake LLM, so no API call is made, and (when ngspice is
available) the simulation step of validate_circuit.
"""

import json
import logging

import pytest
from langchain_core.messages import AIMessage

from circuit_data_gen.netlist_gen import setup as gen_setup
from circuit_data_gen.netlist_gen.parallel import run_pipeline_worker, scale_generation
from circuit_data_gen.netlist_gen.setup import DEFAULT_MAX_ATTEMPTS, llm_seed, netlist_gen_setup

from tests.conftest import (
    CONNECTED_NETLIST,
    ISOLATED_NETLIST,
    NETLIST_MISSING_TAG,
    requirements_for,
)


# a valid netlist wrapped in the tags the LLM is asked to emit
# (gen idx 0 of a gen_count_per_session=1 session)
WELL_FORMED_TAGGED = "<netlist0>\n" + CONNECTED_NETLIST + "</netlist0>"
ISOLATED_TAGGED = "<netlist0>\n" + ISOLATED_NETLIST + "</netlist0>"
# structurally fine (every node has two connections, one ground), but the
# node between the capacitors has no DC path: only simulation rejects it
FLOATING_CAP_NETLIST = "V1 N1 0 5\nC1 N1 N2 1u\nC2 N2 0 1u\n"
FLOATING_CAP_TAGGED = "<netlist0>\n" + FLOATING_CAP_NETLIST + "</netlist0>"


def _state(
    content: str, worker_id: int = 0, max_attempts: int = DEFAULT_MAX_ATTEMPTS, netlist=CONNECTED_NETLIST
):
    # NOTE: output_* arrays are normally seeded by the graph's initialize()
    # node, which this test bypasses by invoking validate_circuit directly.
    return {
        "messages": [AIMessage(content=content)],
        "logger": logging.getLogger("test-graph"),
        "worker_id": worker_id,
        # as run_pipeline_worker names them: {gen_seed}_{worker_id}[_{gen_id}]
        "rng_base_seed": f"test_{worker_id}",
        "rng_seeds": [f"test_{worker_id}_0"],
        "max_attempts": max_attempts,
        "gen_count_per_session": 1,
        "circuits_valid": [False],
        # the netlist's own component count and types
        "requirements": [requirements_for(netlist)],
        "output_netlists": [""],
        "output_schematics": [""],
        "output_annotations": [""],
        "output_overlays": [""],
        "output_yosys": [""],
        "output_simulations": [""],
    }


@pytest.fixture
def validate_node(tmp_path):
    netlist_dir = tmp_path / "netlists"
    schematic_dir = tmp_path / "schematics"
    annotation_dir = tmp_path / "annotations"
    graph = netlist_gen_setup(netlist_dir, schematic_dir, annotation_dir, temperature=0.0, simulate=False)
    # langgraph wraps node fns in a Runnable; validate_circuit is async
    node = graph.nodes["validate_circuit"]
    return node, netlist_dir, schematic_dir, annotation_dir


class TestValidateCircuitTags:
    async def test_missing_netlist_tag_asks_for_retry(self, validate_node):
        node, netlist_dir, _, _ = validate_node
        result = await node.ainvoke(_state(NETLIST_MISSING_TAG))
        assert result["circuit_valid"] is False
        # retry message asks for <netlist0> tags
        assert "<netlist0>" in result["messages"][-1].content
        # a response without its tags still uses up an attempt
        assert result["attempts"] == 1

    async def test_valid_tagged_netlist_passes_and_persists(self, validate_node):
        node, netlist_dir, schematic_dir, annotation_dir = validate_node
        result = await node.ainvoke(_state(WELL_FORMED_TAGGED))
        assert result["circuits_valid"] == [True]
        # files are named after the netlist's seed: netlist_{rng_seed}.net
        assert (netlist_dir / "netlist_test_0_0.net").exists()
        assert (schematic_dir / "schematic_test_0_0.png").exists()
        assert (schematic_dir / "yosys_test_0_0.json").exists()
        assert (annotation_dir / "annotation_test_0_0.json").exists()
        assert (annotation_dir / "overlay_test_0_0.png").exists()
        # no simulation, no operating point
        assert result["output_simulations"] == [""]

    async def test_isolated_netlist_feeds_errors_back(self, validate_node):
        node, netlist_dir, _, _ = validate_node
        result = await node.ainvoke(_state(ISOLATED_TAGGED, netlist=ISOLATED_NETLIST))
        assert result["circuits_valid"] == [False]
        # nothing persisted for invalid circuits
        assert not any(netlist_dir.glob("*.net"))
        # the error message is fed back for regeneration
        assert "not a connected graph" in result["messages"][-1].content

    async def test_netlist_seed_names_the_files(self, validate_node):
        node, netlist_dir, schematic_dir, _ = validate_node
        result = await node.ainvoke(_state(WELL_FORMED_TAGGED, worker_id=3))
        assert result["circuits_valid"] == [True]
        assert (netlist_dir / "netlist_test_3_0.net").exists()
        assert (schematic_dir / "schematic_test_3_0.png").exists()


class _FakeLLM:
    """Stands in for ChatOpenAI: answers every call with the same content
    and counts the calls."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.bound: list[dict] = []  # the kwargs of every bind() call

    def __call__(self, **kwargs):
        return self  # ChatOpenAI(...) in netlist_gen_setup

    def bind(self, **kwargs):
        self.bound.append(kwargs)
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
        setup_kwargs.setdefault("simulate", False)
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
        graph, llm = fake_graph(ISOLATED_TAGGED)
        final = await graph.ainvoke({**_state("", max_attempts=2, netlist=ISOLATED_NETLIST), "messages": []})
        assert llm.calls == 2
        assert final["attempts"] == 2
        assert final["circuits_valid"] == [False]
        # the last message is the error feedback of the final attempt
        assert "not a connected graph" in final["messages"][-1].content

    async def test_stops_at_first_valid_attempt(self, fake_graph):
        graph, llm = fake_graph(WELL_FORMED_TAGGED)
        final = await graph.ainvoke({**_state("", max_attempts=5), "messages": []})
        assert llm.calls == 1
        assert final["attempts"] == 1
        assert final["circuits_valid"] == [True]

    async def test_default_budget(self, fake_graph):
        graph, llm = fake_graph(NETLIST_MISSING_TAG)
        state = {**_state(""), "messages": []}
        del state["max_attempts"]  # initialize() fills in the default
        final = await graph.ainvoke(state)
        assert llm.calls == DEFAULT_MAX_ATTEMPTS
        assert final["attempts"] == DEFAULT_MAX_ATTEMPTS

    async def test_worker_reports_exhausted_budget(self, fake_graph, tmp_path):
        # running out of attempts is a plain failure, not an exception
        graph, llm = fake_graph(ISOLATED_TAGGED)
        results = await run_pipeline_worker(
            graph,
            pool=requirements_for(CONNECTED_NETLIST).allowed,
            num_components_range=(4, 4),
            base_seed="attempts",
            log_dir=tmp_path / "logs",
            max_attempts=15,
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


class TestLLMSeed:
    def test_seed_is_a_stable_non_negative_int32(self):
        assert llm_seed("myseed_0") == llm_seed("myseed_0")
        assert llm_seed("myseed_0") != llm_seed("myseed_1")
        assert all(0 <= llm_seed(f"s{i}") < 2**31 for i in range(200))

    async def test_every_call_is_seeded_with_the_session_seed(self, fake_graph):
        graph, llm = fake_graph(ISOLATED_TAGGED)
        await graph.ainvoke({**_state("", max_attempts=2, netlist=ISOLATED_NETLIST), "messages": []})
        # _state's rng_base_seed is "test_0" (worker 0); one bind per LLM call
        assert llm.bound == [{"seed": llm_seed("test_0")}] * 2


class TestSimulationStep:
    def test_simulate_without_ngspice_fails_early(self, fake_graph, monkeypatch):
        monkeypatch.setattr(gen_setup, "ngspice_available", lambda: False)
        with pytest.raises(RuntimeError, match=r"pip install -e \.\[sim\]"):
            fake_graph(WELL_FORMED_TAGGED, simulate=True)

    async def test_valid_circuit_is_simulated_and_saved(self, convert_spice, ngspice, fake_graph, tmp_path):
        graph, llm = fake_graph(WELL_FORMED_TAGGED, simulate=True)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert final["circuits_valid"] == [True]
        saved = tmp_path / "simulations" / "simulation_test_0_0.json"
        assert final["output_simulations"] == [str(saved.resolve())]
        op = json.loads(saved.read_text(encoding="utf-8"))
        # divider: N1 = 5 V, N2 = 2.5 V (the capacitor is open at DC)
        assert op["node_voltages"]["N1"] == pytest.approx(5.0)
        assert op["node_voltages"]["N2"] == pytest.approx(2.5)

    async def test_simulation_errors_are_fed_back(self, convert_spice, ngspice, fake_graph, tmp_path):
        graph, llm = fake_graph(FLOATING_CAP_TAGGED, simulate=True)
        final = await graph.ainvoke(
            {**_state("", max_attempts=1, netlist=FLOATING_CAP_NETLIST), "messages": []}
        )
        assert final["circuits_valid"] == [False]
        assert "Node N2 has no DC path to ground" in final["messages"][-1].content
        assert not any((tmp_path / "netlists").glob("*.net"))
        assert not (tmp_path / "simulations").exists()

    @pytest.mark.parametrize("rename", [True, False])
    async def test_batch_saves_operating_points(self, convert_spice, ngspice, tmp_path, monkeypatch, rename):
        # a whole batch through scale_generation: fake LLM, fixed requirements
        from circuit_data_gen.netlist_gen import parallel  # noqa: PLC0415

        monkeypatch.setattr(gen_setup, "ChatOpenAI", _FakeLLM(WELL_FORMED_TAGGED))
        fixed = requirements_for(CONNECTED_NETLIST)
        monkeypatch.setattr(parallel, "sample_requirements", lambda *args, **kwargs: fixed)
        out = tmp_path / "project"
        results = await scale_generation(
            out,
            num_netlists=2,
            concurrency=2,
            simulate=True,
            gen_seed="e2e",
            project_name="pytest_sim",
            rename=rename,
        )
        assert [r.ok for r in results] == [True, True]
        saved = sorted(p.name for p in (out / "simulations").glob("*.json"))
        if rename:
            # simulation_{global_idx}_{rng_seed}.json; the global index follows
            # completion order, so only the set of indices is fixed
            assert {name.split("_")[1] for name in saved} == {"0", "1"}
            assert sorted(name.split("_", 2)[2] for name in saved) == ["e2e_0_0.json", "e2e_1_0.json"]
        else:
            assert saved == ["simulation_e2e_0_0.json", "simulation_e2e_1_0.json"]
        assert len(list((out / "netlists").glob("*.net"))) == 2
        op = json.loads((out / "simulations" / saved[0]).read_text(encoding="utf-8"))
        assert op["node_voltages"]["N2"] == pytest.approx(2.5)
