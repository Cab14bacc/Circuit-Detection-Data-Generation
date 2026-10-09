"""Tests for the generation langchain graph wiring (netlist_gen/setup.py).

Exercises validate_circuit's netlist-tag handling directly (the node function
is pulled off the compiled graph), the regenerate loop's max_attempts budget
end to end with a fake LLM, so no API call is made, and (when ngspice is
available) the ngspice smoke test of strict generation.
"""

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
# passes the strict parser, but ngspice has no `v` instance parameter
NGSPICE_REJECTED_NETLIST = "V1 N1 0 v=5\nR1 N1 N2 1k\nR2 N2 0 1k\n"
NGSPICE_REJECTED_TAGGED = "<netlist0>\n" + NGSPICE_REJECTED_NETLIST + "</netlist0>"


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
    }


@pytest.fixture
def validate_node(tmp_path):
    netlist_dir = tmp_path / "netlists"
    schematic_dir = tmp_path / "schematics"
    annotation_dir = tmp_path / "annotations"
    graph = netlist_gen_setup(netlist_dir, schematic_dir, annotation_dir, temperature=0.0, strict=False)
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
        setup_kwargs.setdefault("strict", False)
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


class TestNgspiceStep:
    async def test_ngspice_errors_are_fed_back(
        self, convert_spice, ngspice, fake_graph, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(gen_setup, "find_ngspice", lambda: ngspice)
        graph, llm = fake_graph(NGSPICE_REJECTED_TAGGED, strict=True)
        final = await graph.ainvoke(
            {**_state("", max_attempts=1, netlist=NGSPICE_REJECTED_NETLIST), "messages": []}
        )
        assert final["circuits_valid"] == [False]
        assert "ngspice rejected the netlist" in final["messages"][-1].content
        assert not any((tmp_path / "netlists").glob("*.net"))

    async def test_valid_circuit_passes_ngspice(self, convert_spice, ngspice, fake_graph, monkeypatch):
        monkeypatch.setattr(gen_setup, "find_ngspice", lambda: ngspice)
        graph, llm = fake_graph(WELL_FORMED_TAGGED, strict=True)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert final["circuits_valid"] == [True]

    async def test_without_ngspice_the_step_is_skipped(self, convert_spice, fake_graph, monkeypatch):
        monkeypatch.setattr(gen_setup, "find_ngspice", lambda: None)
        graph, llm = fake_graph(NGSPICE_REJECTED_TAGGED, strict=True)
        final = await graph.ainvoke(
            {**_state("", max_attempts=1, netlist=NGSPICE_REJECTED_NETLIST), "messages": []}
        )
        assert final["circuits_valid"] == [True]

    async def test_lenient_generation_never_runs_ngspice(self, convert_spice, fake_graph, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("ngspice ran")

        monkeypatch.setattr(gen_setup, "ngspice_smoke_test", fail)
        graph, llm = fake_graph(WELL_FORMED_TAGGED, strict=False)
        final = await graph.ainvoke({**_state(""), "messages": []})
        assert final["circuits_valid"] == [True]


class TestNgspiceVersion:
    def test_installed_version_is_read(self, ngspice):
        from circuit_data_gen.ngspice_check import ngspice_version  # noqa: PLC0415

        assert isinstance(ngspice_version(ngspice), int)

    def test_unreadable_version_is_none(self, tmp_path):
        from circuit_data_gen.ngspice_check import ngspice_version  # noqa: PLC0415

        assert ngspice_version(str(tmp_path / "missing.exe")) is None

    @pytest.mark.parametrize("version, warned", [(47, False), (32, True), (None, True)])
    def test_warns_unless_tested_version(self, monkeypatch, caplog, version, warned):
        from circuit_data_gen import ngspice_check  # noqa: PLC0415

        monkeypatch.setattr(ngspice_check, "ngspice_version", lambda exe: version)
        with caplog.at_level(logging.WARNING):
            ngspice_check.warn_if_untested_version("ngspice")
        assert ("checked against ngspice 47" in caplog.text) is warned

    def test_strict_setup_checks_the_version(self, fake_graph, monkeypatch):
        checked = []
        monkeypatch.setattr(gen_setup, "find_ngspice", lambda: "ngspice")
        monkeypatch.setattr(gen_setup, "warn_if_untested_version", checked.append)
        fake_graph(WELL_FORMED_TAGGED, strict=True)
        fake_graph(WELL_FORMED_TAGGED, strict=False)
        assert checked == ["ngspice"]


class TestTimings:
    def test_session_llm_time_is_counted_once(self):
        timings = {0: {"llm": 10.0, "smoke": 1.0}, 1: {"llm": 10.0, "smoke": 2.0, "ngspice": 0.5}}
        assert gen_setup.stage_timings(timings) == {"llm": 10.0, "smoke": 3.0, "ngspice": 0.5}

    @pytest.mark.parametrize("content, valid", [(WELL_FORMED_TAGGED, 2), (ISOLATED_TAGGED, 0)])
    async def test_batch_summary(self, tmp_path, monkeypatch, caplog, content, valid):
        # a whole batch through scale_generation: fake LLM, fixed requirements;
        # the summary at the end must not fail, also when nothing is valid
        from circuit_data_gen.netlist_gen import parallel  # noqa: PLC0415

        monkeypatch.setattr(gen_setup, "ChatOpenAI", _FakeLLM(content))
        fixed = requirements_for(CONNECTED_NETLIST)
        monkeypatch.setattr(parallel, "sample_requirements", lambda *args, **kwargs: fixed)
        with caplog.at_level(logging.INFO):
            results = await scale_generation(
                tmp_path / "project",
                num_netlists=2,
                concurrency=2,
                max_attempts=1,
                gen_seed="batch",
                project_name="pytest_batch",
                strict=False,
            )
        assert sum(r.ok for r in results) == valid
        assert f"Batch generation complete: {valid}/2 valid schematics" in caplog.text
        assert ("average time per schematic: n/a" in caplog.text) is (valid == 0)
