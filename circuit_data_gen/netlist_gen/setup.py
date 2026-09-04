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

from ..configs.config import get_logger, get_config_path_value
from ..validate import connected_component_groups, hanging_nodes
from ..render_netlist import render_netlist
from .parallel import CircuitRequirements


SKIN_PATH = get_config_path_value("netlistsvg", "skin_path").resolve()


class CircuitState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    circuit_valid: bool
    rng_seed: str
    logger: logging.Logger
    index: int
    requirements: CircuitRequirements


def smoke_test_render(
    state: CircuitState,
    temp_netlist_path: Path,
    temp_schematic_path: Path,
    temp_annotation_path: Path,
    temp_overlay_path: Path,
) -> tuple[bool, list[str]]:
    """Attempt to render the netlist and return (valid, errors)."""
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
        )
        # check if the rendered schematic file exists and is not empty
        if not (temp_schematic_path.exists() and temp_schematic_path.stat().st_size > 0):
            errors.append("Rendering produced an empty or missing SVG.")

        # check if the circuit contains at least the minimum number of components
        if not len(parsed_netlist) >= state["requirements"].min_components:
            errors.append(
                f"Netlist has {len(parsed_netlist)} components, "
                f"which is less than the required minimum of "
                f"{state['requirements'].min_components}."
            )

        types_in_netlist = {(data["prefix"], " ".join(data["kind"])) for name, data in parsed_netlist.items()}
        # validate the netlist for required components
        comps_in_netlist = {
            (name, data["prefix"], " ".join(data["kind"])) for name, data in parsed_netlist.items()
        }
        unused_required_components = [
            req for req in state["requirements"].required_components if req not in types_in_netlist
        ]
        logger.info(f"types_in_netlist: {types_in_netlist}")
        logger.info(f"components_in_netlist: {comps_in_netlist}")
        # check for required components that are missing from the netlist
        if len(unused_required_components) > 0:
            unused_required_components_str = ", ".join(
                f"`{prefix}{' with kind keyword: ' + kind if kind else ''}`"
                for prefix, kind in unused_required_components
            )
            errors.append(f"Netlist is missing required components: {unused_required_components_str}.")

        (hanging_nodes_before_drop, hanging_nodes_after_drop, hanging_nodes_from_dropping) = hanging_nodes(
            parsed_netlist
        )

        logger.info(f"hanging_nodes_before_drop: {hanging_nodes_before_drop}")
        logger.info(f"hanging_nodes_after_drop: {hanging_nodes_after_drop}")
        # check for hanging nodes after dropping unsupported connections due to skin limits
        # realize dropping connections can also make a originally hanging node to become non-hanging
        # by removing all connections to that node.
        if len(hanging_nodes_after_drop) > 0:
            msg = (
                f"Netlist has {len(hanging_nodes_after_drop)}"
                f"hanging nodes: {', '.join(hanging_nodes_after_drop)}.\n"
            )
            hanging_nodes_from_dropping = hanging_nodes_after_drop - hanging_nodes_before_drop
            if len(hanging_nodes_from_dropping) > 0:
                msg += "Nodes that became hanging due to dropping (skin doesn't support this port): "
                msg += f"{', '.join(hanging_nodes_from_dropping)}."
            errors.append(msg)

        # check if circuit is a connected graph (i.e., no isolated subgraphs).
        is_connected, comp_groups = connected_component_groups(parsed_netlist)
        if not is_connected:
            subgraphs_str = "; ".join(", ".join(sorted(group)) for group in comp_groups)
            errors.append(
                f"Netlist is not a connected graph: it has {len(comp_groups)} "
                f"isolated subgraphs: [{subgraphs_str}]. "
                "Connect all components electrically (share nets, e.g. via ground)."
            )

        if_valid = True if len(errors) == 0 else False
    except Exception as e:
        errors.append(str(e))

    return if_valid, errors


def netlist_gen_setup(
    netlist_dir: str | Path,
    schematic_dir: str | Path,
    annotation_dir: str | Path,
    temperature: float = 0.9,
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
    """
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

    sys_prompt_path = os.path.join(os.path.dirname(__file__), "prompts/agent_system_prompt.md")

    with open(sys_prompt_path, "r") as f:
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
            init_state["requirements"] = CircuitRequirements()

        return init_state

    async def generate_circuit(state: CircuitState) -> dict:
        logger = state["logger"]
        logger.info("Generating circuit...")

        system = SystemMessage(content=sys_prompt)
        response = await llm.ainvoke([system] + state["messages"])

        logger.info(f"{type(response).__name__}:\n {response.content or response.tool_calls}\n")
        return {"messages": [response]}

    # validate_circuit  (smoke test: attempt to render the netlist)
    def validate_circuit(state: CircuitState) -> dict:
        logger = state["logger"]
        logger.info("Validating circuit (smoke test)...")

        llm_msg = state["messages"][-1].content

        match = re.search(r"<netlist>(.*?)</netlist>", llm_msg, re.DOTALL)
        if not match:
            message = HumanMessage(
                content=(
                    "No valid netlist found. Please ensure the netlist is "
                    "enclosed within <netlist> and </netlist> tags."
                )
            )
            logger.info(f"{type(message).__name__}:\n {message.content}\n")
            return {"circuit_valid": False, "messages": [message]}

        netlist = match.group(1).strip()

        # ---- smoke test: write temp netlist and try to render it ---- #
        if_valid = False
        errors: list[str] = []

        with TemporaryDirectory() as temp_dir:
            temp_netlist_path = Path(temp_dir) / "temp.net"
            temp_schematic_path = Path(temp_dir) / "temp_schematic.png"
            temp_yosys_json_path = temp_schematic_path.with_suffix(".json")
            temp_annotation_path = Path(temp_dir) / "temp_annotation.png"
            temp_overlay_path = Path(temp_dir) / "temp_overlay.png"
            temp_netlist_path.write_text(netlist)

            try:
                if_valid, errors = smoke_test_render(
                    state,
                    temp_netlist_path,
                    temp_schematic_path,
                    temp_annotation_path,
                    temp_overlay_path,
                )
            except Exception as e:
                errors.append(str(e))

            #  persist valid netlists and schematics
            if if_valid:
                netlist_path = Path(netlist_dir) / f"netlist_{state['index']}.net"
                schematic_path = Path(schematic_dir) / f"schematic_{state['index']}.png"
                yosys_path = Path(schematic_dir) / f"yosys_{state['index']}.json"
                overlay_path = Path(annotation_dir) / f"overlay_{state['index']}.png"
                annotation_path = Path(annotation_dir) / f"annotation_{state['index']}.json"

                Path(netlist_dir).mkdir(parents=True, exist_ok=True)
                Path(schematic_dir).mkdir(parents=True, exist_ok=True)
                Path(annotation_dir).mkdir(parents=True, exist_ok=True)

                netlist_path.write_text(netlist)

                schematic_bytes = temp_schematic_path.read_bytes()
                schematic_path.write_bytes(schematic_bytes)

                yosys_bytes = temp_yosys_json_path.read_bytes()
                yosys_path.write_bytes(yosys_bytes)

                # Update the image field because we used a temp file
                annotation_data = json.loads(temp_annotation_path.read_text())
                annotation_data["image"] = str(schematic_path.resolve())
                annotation_path.write_text(json.dumps(annotation_data))

                overlay_bytes = temp_overlay_path.read_bytes()
                overlay_path.write_bytes(overlay_bytes)

        if if_valid:
            message = HumanMessage(
                content=(f"Running Circuit Validation...\nCircuit validation result: {if_valid}")
            )
        else:
            error_str = "\n".join(errors) if errors else "Unknown validation error."
            message = HumanMessage(
                content=(
                    f"Running Circuit Validation...\n"
                    f"Circuit validation result: {if_valid}\n"
                    f"Here are the errors:\n{error_str}"
                )
            )

        logger.info(f"{type(message).__name__}:\n {message.content}\n")
        return {"circuit_valid": if_valid, "messages": [message]}

    def route_for_regeneration(state: CircuitState) -> str:
        if state["circuit_valid"]:
            return END
        else:
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
