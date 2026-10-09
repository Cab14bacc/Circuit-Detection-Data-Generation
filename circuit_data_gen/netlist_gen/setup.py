import asyncio
import copy
import hashlib
import json
import os
import uuid
import re
import logging
import time

from dotenv import load_dotenv
from typing import Annotated, TypedDict
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AnyMessage

from langgraph.graph import add_messages
from langgraph.graph import StateGraph, START, END

from ..configs.config import get_logger, get_config_path_value, get_config_value
from ..validate import full_validation
from ..render_netlist import render_netlist
from ..parser.export import to_ngspice_netlist
from ..ngspice_check import find_ngspice, ngspice_smoke_test, warn_if_untested_version
from .sampling import CircuitRequirements


SKIN_PATH = get_config_path_value("netlistsvg", "skin_path").resolve()
# config default of the generation flag --strict/--no-strict
GEN_STRICT = bool(get_config_value("convert", "strict_parsing"))
# LLM generations per session (the first one included) before giving up
DEFAULT_MAX_ATTEMPTS = 3


class CircuitState(TypedDict):
    # llm messages and error messages from validation step
    messages: Annotated[list[AnyMessage], add_messages]

    rng_base_seed: str
    rng_seeds: list[str]
    logger: logging.Logger
    worker_id: int
    max_attempts: int
    attempts: int
    # timing diagnostics: time.perf_counter() when the session started, and
    # the seconds spent per stage ("llm", "smoke_test", "ngspice") summed
    # over all attempts, summarized when the session ends
    started_at: float
    timings: dict[str, float]
    gen_count_per_session: int
    circuits_valid: list[bool]
    requirements: list[CircuitRequirements]

    output_netlists: list[Path]
    output_schematics: list[Path]
    output_annotations: list[Path]
    output_overlays: list[Path]
    output_yosys: list[Path]


def llm_seed(seed: str) -> int:
    """The LLM sampling seed for a string seed: the API takes an integer. A
    stable hash (builtin hash() is salted per process), kept within a
    non-negative signed 32-bit range, which OpenAI-compatible backends
    accept. Seeded sampling is best effort on the backend's side."""
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def _elapsed(state: CircuitState) -> float:
    """Seconds since the session started (set by initialize; 0 when a node
    runs on its own, outside the graph)."""
    now = time.perf_counter()
    return now - state.get("started_at", now)


def stage_timings(timings) -> dict[str, float]:
    stage_timings = {}
    for circuit_idx, timing in timings.items():
        for stage, spent in timing.items():
            if stage == "llm":
                # the LLM stage is shared by all circuits of the session
                stage_timings[stage] = max(stage_timings.get(stage, 0.0), spent)
            else:
                stage_timings[stage] = stage_timings.get(stage, 0.0) + spent
    return stage_timings


def _add_timings(state: CircuitState, circuit_idx: int, **seconds: float) -> dict[str, float]:
    """The session's stage timings with `seconds` added (a new dict, to
    return as the node's state update)."""
    timings = dict(state.get("timings") or {})
    timing = timings.get(circuit_idx, {})
    for stage, spent in seconds.items():
        timing[stage] = timing.get(stage, 0.0) + spent
    timings[circuit_idx] = timing
    return timings


def _resolve_strict(strict: bool | None) -> bool:
    """Generation strictness: the explicit value (e.g. `cirdg gen_data
    --strict/--no-strict`), else GEN_STRICT."""
    return GEN_STRICT if strict is None else strict


def smoke_test_render(
    state: CircuitState,
    gen_idx: int,
    temp_netlist_path: Path,
    temp_schematic_path: Path,
    temp_annotation_path: Path,
    temp_overlay_path: Path,
    strict: bool | None = None,
    elk_seed: str | int | None = None,
) -> tuple[bool, list[str]]:
    """Attempt to render the netlist and return (valid, errors).

    `strict` selects strict SPICE parsing (None = GEN_STRICT).
    `elk_seed` fixes the ELK randomization seed for the render; None keeps
    the skin's seed. Callers pass the worker's rng_base_seed so the smoke-test
    layout matches the persisted render layout for the same sample.
    """
    logger = state["logger"]
    if_valid = False
    errors: list[str] = []

    try:
        _, parsed_netlist = render_netlist(
            temp_netlist_path,
            SKIN_PATH,
            temp_schematic_path,
            temp_annotation_path,
            format="png",
            debug_overlay=True,
            debug_overlay_path=temp_overlay_path,
            strict=_resolve_strict(strict),
            elk_seed=elk_seed,
        )

        # check if the rendered schematic file exists and is not empty
        if not (temp_schematic_path.exists() and temp_schematic_path.stat().st_size > 0):
            errors.append("Rendering produced an empty or missing SVG.")

        errors.extend(full_validation(parsed_netlist, state["requirements"][gen_idx], logger))

        if_valid = True if len(errors) == 0 else False
    except Exception as e:
        errors.append(str(e))

    return if_valid, errors


def netlist_gen_setup(
    netlist_dir: str | Path,
    schematic_dir: str | Path,
    annotation_dir: str | Path,
    temperature: float = 0.9,
    strict: bool | None = None,
):
    """Build the LangGraph state machine for netlist generation.

    Parameters
    ----------
    netlist_dir : str | Path
        Directory where validated .net files are written.
    schematic_dir : str | Path
        Directory where rendered .svg files are written.
    temperature : float
        LLM sampling temperature (passed to ChatOpenAI).
    annotation_dir : str | Path | None
        If given, annotations (component/label/pin boxes) are written
        here as <stem>.txt alongside a shared classes.txt registry.
    strict : bool | None
        SPICE strict parsing for the smoke test. None = GEN_STRICT
        (convert.strict_parsing). Netlists that pass the render smoke test
        are then loaded by ngspice too (ngspice_check.ngspice_smoke_test),
        when an ngspice executable is found; without one that step is
        skipped with a warning. Strict and lenient generation use the same
        system prompt.
    """
    strict = _resolve_strict(strict)
    ngspice_exe = find_ngspice() if strict else None
    if strict and ngspice_exe is None:
        get_logger(__name__).warning(
            "Strict generation without the ngspice smoke test: no ngspice executable found "
            "(set netlist_gen.ngspice_path, or put ngspice_con / ngspice on PATH)."
        )
    elif ngspice_exe is not None:
        warn_if_untested_version(ngspice_exe)

    load_dotenv()
    api_key = os.getenv("CIRDG_API_KEY")
    model_name = os.getenv("CIRDG_MODEL_NAME")
    base_url = os.getenv("CIRDG_BASE_URL")

    llm = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        reasoning_effort="high",
        temperature=temperature,
    )

    sys_prompt_path = get_config_path_value("netlist_gen", "llm_system_prompt_path").resolve()

    with open(sys_prompt_path, "r", encoding="utf-8") as f:
        sys_prompt = f.read()

    def initialize(state: CircuitState) -> CircuitState:
        """Ensure logger and rng_base_seed exist in state."""
        init_state: CircuitState = copy.copy(state)

        if not state.get("logger"):
            logger = get_logger(__name__)
            init_state["logger"] = logger

        if not state.get("rng_base_seed"):
            init_state["rng_base_seed"] = f"{uuid.uuid4().hex[:8]}"

        if not state.get("requirements"):
            init_state["requirements"] = [CircuitRequirements()]

        init_state["attempts"] = 0
        init_state["started_at"] = time.perf_counter()

        if not state.get("gen_count_per_session"):
            init_state["gen_count_per_session"] = 1 or len(state.get("requirements", ["dummy"]))

        if not state.get("circuits_valid"):
            count = 1 if state.get("gen_count_per_session") is None else init_state["gen_count_per_session"]
            init_state["circuits_valid"] = [False] * count

        if not state.get("rng_seeds"):
            init_state["rng_seeds"] = [
                f"{init_state['rng_base_seed']}_{idx}" for idx in range(init_state["gen_count_per_session"])
            ]

        if not state.get("max_attempts"):
            init_state["max_attempts"] = DEFAULT_MAX_ATTEMPTS

        init_state["timings"] = {
            circuit_idx: {} for circuit_idx in range(init_state["gen_count_per_session"])
        }

        init_state["output_netlists"] = ["" for _ in range(init_state["gen_count_per_session"])]
        init_state["output_schematics"] = ["" for _ in range(init_state["gen_count_per_session"])]
        init_state["output_annotations"] = ["" for _ in range(init_state["gen_count_per_session"])]
        init_state["output_overlays"] = ["" for _ in range(init_state["gen_count_per_session"])]
        init_state["output_yosys"] = ["" for _ in range(init_state["gen_count_per_session"])]

        return init_state

    async def generate_circuit(state: CircuitState) -> dict:
        logger = state["logger"]
        logger.info("Generating circuit...")

        system = SystemMessage(content=sys_prompt)
        # bound per call: the graph (and llm) is shared by every worker of the
        # batch, so the session's seed cannot live on the shared client
        seeded_llm = llm.bind(seed=llm_seed(state["rng_base_seed"]))
        llm_start = time.perf_counter()
        response = await seeded_llm.ainvoke([system] + state["messages"])
        llm_s = time.perf_counter() - llm_start
        timings = {}
        for i in range(state["gen_count_per_session"]):
            timings.update(_add_timings(state, i, llm=llm_s))
        logger.info(f"LLM response in {llm_s:.2f}s")

        return {"messages": [response], "timings": timings}

    # validate_circuit  (smoke test: attempt to render the netlist)
    async def validate_circuit(state: CircuitState) -> dict:
        logger = state["logger"]
        llm_response = state["messages"][-1]
        llm_msg = llm_response.content
        # each validated LLM response is one attempt of the session's budget
        attempts = state.get("attempts", 0) + 1

        logger.info(f"{type(llm_response).__name__}:\n {llm_response.content or llm_response.tool_calls}\n")
        logger.info(f"Validating circuit (smoke test, attempt {attempts}/{state['max_attempts']})...")

        netlists = [None for _ in range(state["gen_count_per_session"])]
        for idx in range(state["gen_count_per_session"]):
            if state["circuits_valid"][idx]:
                continue
            match = re.search(rf"<netlist{idx}>(.*?)</netlist{idx}>", llm_msg, re.DOTALL)

            if not match:
                message = HumanMessage(
                    content=(
                        f"No valid netlist found. Please ensure netlist {idx} is "
                        f"enclosed within <netlist{idx}> and </netlist{idx}> tags."
                    )
                )
                logger.info(f"{type(message).__name__}:\n {message.content}\n")
                return {"circuit_valid": False, "messages": [message], "attempts": attempts}

            netlist = match.group(1).strip()
            netlists[idx] = netlist

        # ---- smoke test: write temp netlist and try to render it ---- #
        list_of_errors: list[list[str]] = [[] for _ in range(state["gen_count_per_session"])]
        smoke_test_s = ngspice_s = 0.0
        timings = {}

        # ===========================================================
        # Iterate through each of the generated netlists, validate
        # and render them, and save the outputs
        # ===========================================================
        with TemporaryDirectory() as temp_dir:
            for idx, netlist in enumerate(netlists):
                if state["circuits_valid"][idx]:
                    continue

                if_valid = False
                errors: list[str] = []
                temp_netlist_path = Path(temp_dir) / "temp.net"
                temp_schematic_path = Path(temp_dir) / "temp_schematic.png"
                temp_yosys_json_path = temp_schematic_path.with_suffix(".json")
                temp_annotation_path = Path(temp_dir) / "temp_annotation.png"
                temp_overlay_path = Path(temp_dir) / "temp_overlay.png"
                temp_netlist_path.write_text(netlist, encoding="utf-8")

                # ===========================================================
                # smoke test: attempt to render the netlist and validate it
                # ===========================================================
                logger.info(f"smoke test for netlist {idx}:\n")
                smoke_start = time.perf_counter()
                try:
                    if_valid, errors = smoke_test_render(
                        state,
                        idx,
                        temp_netlist_path,
                        temp_schematic_path,
                        temp_annotation_path,
                        temp_overlay_path,
                        strict=strict,
                        # the circuits's rng_base_seed drives the ELK layout seed
                        elk_seed=state["rng_seeds"][idx],
                    )
                except Exception as e:
                    errors.append(str(e))
                smoke_s = time.perf_counter() - smoke_start
                smoke_test_s += smoke_s
                timings.update(_add_timings(state, circuit_idx=idx, smoke=smoke_s))
                logger.info(f"smoke test of netlist {idx} in {smoke_s:.2f}s")

                # ===========================================================
                # ngspice smoke test (strict, ngspice found): the netlist
                # must load into ngspice. Runs in a thread so the other
                # workers of the batch keep going while ngspice runs.
                # ===========================================================
                if if_valid and ngspice_exe is not None:
                    ngspice_start = time.perf_counter()
                    ngspice_errors = await asyncio.to_thread(ngspice_smoke_test, netlist, ngspice_exe)
                    spent = time.perf_counter() - ngspice_start
                    ngspice_s += spent
                    timings.update(_add_timings(state, circuit_idx=idx, ngspice=spent))
                    verdict = "ok" if not ngspice_errors else "rejected"
                    logger.info(f"ngspice smoke test of netlist {idx} in {spent:.2f}s: {verdict}")
                    if ngspice_errors:
                        if_valid, errors = False, ngspice_errors

                list_of_errors[idx] = errors
                state["circuits_valid"][idx] = if_valid

                # ===========================================================
                # Save to output directories if valid
                # ===========================================================
                if if_valid:
                    logger.info(
                        f"netlist {idx} ({state['rng_seeds'][idx]}) valid after "
                        f"{_elapsed(state):.2f}s, attempt {attempts}"
                    )
                    netlist_path = Path(netlist_dir) / f"netlist_{state['rng_seeds'][idx]}.net"
                    schematic_path = Path(schematic_dir) / f"schematic_{state['rng_seeds'][idx]}.png"
                    yosys_path = Path(schematic_dir) / f"yosys_{state['rng_seeds'][idx]}.json"
                    overlay_path = Path(annotation_dir) / f"overlay_{state['rng_seeds'][idx]}.png"
                    annotation_path = Path(annotation_dir) / f"annotation_{state['rng_seeds'][idx]}.json"

                    Path(netlist_dir).mkdir(parents=True, exist_ok=True)
                    Path(schematic_dir).mkdir(parents=True, exist_ok=True)
                    Path(annotation_dir).mkdir(parents=True, exist_ok=True)

                    netlist = to_ngspice_netlist(
                        netlist,
                        title=f"* netlist {state['rng_seeds'][idx]} generated by circuit_data_gen",
                        analysis=None,
                    )
                    netlist_path.write_text(netlist, encoding="utf-8")

                    schematic_bytes = temp_schematic_path.read_bytes()
                    schematic_path.write_bytes(schematic_bytes)

                    yosys_bytes = temp_yosys_json_path.read_bytes()
                    yosys_path.write_bytes(yosys_bytes)

                    # Update the image field because we used a temp file
                    annotation_data = json.loads(temp_annotation_path.read_text(encoding="utf-8"))
                    annotation_data["image"] = str(schematic_path.resolve())
                    annotation_path.write_text(json.dumps(annotation_data), encoding="utf-8")

                    overlay_bytes = temp_overlay_path.read_bytes()
                    overlay_path.write_bytes(overlay_bytes)

                    state["output_netlists"][idx] = str(netlist_path.resolve())
                    state["output_schematics"][idx] = str(schematic_path.resolve())
                    state["output_yosys"][idx] = str(yosys_path.resolve())
                    state["output_annotations"][idx] = str(annotation_path.resolve())
                    state["output_overlays"][idx] = str(overlay_path.resolve())

        if all(state["circuits_valid"]):
            message = HumanMessage(
                content=(
                    f"Running Circuit Validation...\nCircuit validation result for "
                    f"{state['gen_count_per_session']} netlist: {state['circuits_valid']}"
                )
            )
        else:
            final_msg = "Running Circuit Validation...\n"
            for idx, (if_valid, errors) in enumerate(zip(state["circuits_valid"], list_of_errors)):
                if if_valid:
                    final_msg += f"Circuit validation result for netlist {idx}: {if_valid}\n\n"
                    continue
                error_str = "\n".join(errors) if errors else "Unknown validation error."
                final_msg += (
                    f"Circuit validation result for netlist {idx}: {if_valid}\n"
                    f"Here are the errors for netlist {idx}:\n{error_str}\n\n"
                )
            message = HumanMessage(content=final_msg)

        logger.info(f"{type(message).__name__}:\n {message.content}\n")

        return {
            "messages": [message],
            "attempts": attempts,
            "timings": timings,
            "circuits_valid": state["circuits_valid"],
            "output_netlists": state["output_netlists"],
            "output_schematics": state["output_schematics"],
            "output_annotations": state["output_annotations"],
            "output_overlays": state["output_overlays"],
            "output_yosys": state["output_yosys"],
        }

    def route_for_regeneration(state: CircuitState) -> str:
        _stage_timings = stage_timings(state["timings"])
        stages = ", ".join(f"{stage} {spent:.2f}s" for stage, spent in _stage_timings.items())
        timing_summary = f"{_elapsed(state):.2f}s in total ({stages or 'no stage timed'})"

        if all(state["circuits_valid"]):
            stages = ", ".join(f"{stage} {spent:.2f}s" for stage, spent in _stage_timings.items())

            state["logger"].info(
                f"Session {state['rng_base_seed']} done after {state['attempts']} attempt(s): \n"
                f"{timing_summary}\n"
                f"On average, {sum(_stage_timings.values()) / state['gen_count_per_session']:.2f}s "
                f"per circuit\n"
            )
            return END

        if state["attempts"] >= state["max_attempts"]:
            # retry budget spent: the netlists still invalid stay failed
            state["logger"].warning(
                f"Giving up on session {state['rng_base_seed']} after {state['attempts']} attempt(s): \n"
                f"circuit validity {state['circuits_valid']}; {timing_summary}\n"
            )
            return END
        return "generate_circuit"

    # Build the graph
    builder = StateGraph(CircuitState)
    builder.add_node("initialize", initialize)
    builder.add_node("generate_circuit", generate_circuit)
    builder.add_node("validate_circuit", validate_circuit)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "generate_circuit")
    builder.add_edge("generate_circuit", "validate_circuit")
    builder.add_conditional_edges("validate_circuit", route_for_regeneration)

    graph = builder.compile()

    return graph


if __name__ == "__main__":
    # Example usage
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temp_dir:
        netlist_dir = Path(temp_dir) / "netlists"
        schematic_dir = Path(temp_dir) / "schematics"
        annotation_dir = Path(temp_dir) / "annotations"
        graph = netlist_gen_setup(netlist_dir, schematic_dir, annotation_dir, temperature=0.9)
        sys_prompt = Path(__file__).parent / "prompts/agent_system_prompt.md"
        system = SystemMessage(content=sys_prompt)
        graph.invoke(
            {
                "messages": [system],
                "circuit_valid": False,
                "rng_base_seed": str(uuid.uuid4()),
                "logger": get_logger(__name__),
                "worker_id": 0,
                "requirements": CircuitRequirements(),
            }
        )
        print("Graph setup complete.")
