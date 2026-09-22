import copy
import json
import os
import uuid
import re
import logging

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
from .parallel import CircuitRequirements


SKIN_PATH = get_config_path_value("netlistsvg", "skin_path").resolve()
# generation parses strictly when asked to, and always when the generated
# netlists will be simulated (ngspice needs the strict guarantees)
GEN_STRICT = bool(get_config_value("convert", "strict_parsing")) or bool(
    get_config_value("simulation", "enabled")
)
# LLM generations per session (the first one included) before giving up
DEFAULT_MAX_ATTEMPTS = 3


class CircuitState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    rng_seed: str
    logger: logging.Logger
    index: int
    attempts: int
    gen_count_per_session: int
    circuits_valid: list[bool]
    requirements: list[CircuitRequirements]
    output_netlists: list[Path]
    output_schematics: list[Path]
    output_annotations: list[Path]
    output_overlays: list[Path]
    output_yosys: list[Path]


def _resolve_strict(strict: bool | None) -> bool:
    """Generation strictness: the explicit value (e.g. `cirdg gen_data
    --strict/--no-strict`), else the config-derived GEN_STRICT."""
    return GEN_STRICT if strict is None else strict


def system_prompt_key(strict: bool | None = None) -> str:
    """Config key (under netlist_gen) of the LLM system prompt: the strict
    SPICE prompt when generation parses strictly, else the default one.
    Strict parsing is SPICE only, so the lcapy dialect always uses the default."""
    is_spice = str(get_config_value("convert", "netlist_format")).lower() == "spice"
    if _resolve_strict(strict) and is_spice:
        return "LLM_STRICT_SYSTEM_PROMPT_PATH"
    return "LLM_SYSTEM_PROMPT_PATH"


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

    `strict` selects strict SPICE parsing (None = config-derived GEN_STRICT).
    `elk_seed` fixes the ELK randomization seed for the render; None keeps
    the skin's seed. Callers pass the worker's rng_seed so the smoke-test
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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
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
        SPICE strict parsing for the smoke test, and the matching (strict)
        system prompt. None = config-derived GEN_STRICT
        (convert.strict_parsing or simulation.enabled).
    max_attempts : int
        LLM generations per session, the first one included (>= 1). Each
        call regenerates every netlist of the session that is still
        invalid; those still invalid after the last call fail.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    strict = _resolve_strict(strict)
    load_dotenv()
    api_key = os.getenv("CIRDG_API_KEY")
    model_name = os.getenv("CIRDG_MODEL_NAME")
    base_url = os.getenv("CIRDG_BASE_URL")

    llm = ChatOpenAI(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        temperature=temperature,
    )

    sys_prompt_path = get_config_path_value("netlist_gen", system_prompt_key(strict)).resolve()

    with open(sys_prompt_path, "r", encoding="utf-8") as f:
        sys_prompt = f.read()

    def initialize(state: CircuitState) -> CircuitState:
        """Ensure logger and rng_seed exist in state."""
        init_state: CircuitState = copy.copy(state)

        if not state.get("logger"):
            logger = get_logger(__name__)
            init_state["logger"] = logger

        if not state.get("rng_seed"):
            init_state["rng_seed"] = f"{uuid.uuid4().hex[:8]}"
            llm.bind(seed=init_state["rng_seed"])
        else:
            llm.bind(seed=state["rng_seed"])

        if not state.get("requirements"):
            init_state["requirements"] = [CircuitRequirements()]

        init_state["attempts"] = 0

        if not state.get("gen_count_per_session"):
            init_state["gen_count_per_session"] = 1 or len(state.get("requirements", ["dummy"]))

        if not state.get("circuits_valid"):
            count = 1 if state.get("gen_count_per_session") is None else init_state["gen_count_per_session"]
            init_state["circuits_valid"] = [False] * count

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
        response = await llm.ainvoke([system] + state["messages"])

        return {"messages": [response]}

    # validate_circuit  (smoke test: attempt to render the netlist)
    def validate_circuit(state: CircuitState) -> dict:
        logger = state["logger"]
        llm_response = state["messages"][-1]
        llm_msg = llm_response.content
        # each validated LLM response is one attempt of the session's budget
        attempts = state.get("attempts", 0) + 1

        logger.info(f"{type(llm_response).__name__}:\n {llm_response.content or llm_response.tool_calls}\n")
        logger.info(f"Validating circuit (smoke test, attempt {attempts}/{max_attempts})...")

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

                logger.info(f"smoke test for netlist {idx}:\n")
                rng_seed = state.get("rng_seed")
                try:
                    if_valid, errors = smoke_test_render(
                        state,
                        idx,
                        temp_netlist_path,
                        temp_schematic_path,
                        temp_annotation_path,
                        temp_overlay_path,
                        strict=strict,
                        # the circuits's rng_seed drives the ELK layout seed
                        elk_seed=rng_seed + f"_{idx}" if rng_seed is not None else None,
                    )
                except Exception as e:
                    errors.append(str(e))

                list_of_errors[idx] = errors
                state["circuits_valid"][idx] = if_valid

                #  persist valid netlists and schematics
                if if_valid:
                    netlist_path = Path(netlist_dir) / f"netlist_{state['index']}_{idx}.net"
                    schematic_path = Path(schematic_dir) / f"schematic_{state['index']}_{idx}.png"
                    yosys_path = Path(schematic_dir) / f"yosys_{state['index']}_{idx}.json"
                    overlay_path = Path(annotation_dir) / f"overlay_{state['index']}_{idx}.png"
                    annotation_path = Path(annotation_dir) / f"annotation_{state['index']}_{idx}.json"

                    Path(netlist_dir).mkdir(parents=True, exist_ok=True)
                    Path(schematic_dir).mkdir(parents=True, exist_ok=True)
                    Path(annotation_dir).mkdir(parents=True, exist_ok=True)

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
                    f"Here are the errors for netlist {idx}:\n{error_str}'\n\n"
                )
            message = HumanMessage(content=final_msg)

        logger.info(f"{type(message).__name__}:\n {message.content}\n")

        return {
            "messages": [message],
            "attempts": attempts,
            "circuits_valid": state["circuits_valid"],
            "output_netlists": state["output_netlists"],
            "output_schematics": state["output_schematics"],
            "output_annotations": state["output_annotations"],
            "output_overlays": state["output_overlays"],
            "output_yosys": state["output_yosys"],
        }

    def route_for_regeneration(state: CircuitState) -> str:
        if all(state["circuits_valid"]):
            return END
        if state["attempts"] >= max_attempts:
            # retry budget spent: the netlists still invalid stay failed
            state["logger"].warning(
                f"Giving up after {state['attempts']} attempt(s): circuit validity {state['circuits_valid']}"
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
                "rng_seed": str(uuid.uuid4()),
                "logger": get_logger(__name__),
                "index": 0,
                "requirements": CircuitRequirements(),
            }
        )
        print("Graph setup complete.")
