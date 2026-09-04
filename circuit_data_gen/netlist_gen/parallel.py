import asyncio
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..convert import WIRE
from ..configs.config import get_logger, get_config_value


@dataclass
class GenResult:
    """Result of a single netlist generation worker."""

    ok: bool = False
    worker_id: int = 0
    exception: str | None = None
    render_error: str | None = None
    sanity_errors: list[str] = field(default_factory=list)


@dataclass
class CircuitRequirements:
    """Result of a single netlist generation worker."""

    min_components: int = 0
    required_components: list[tuple[str, str]] = field(default_factory=list)


async def run_pipeline_worker(
    graph,
    all_components: list[tuple[str, str]],
    num_components_range: tuple[int, int] = (5, 25),
    worker_id: int | None = None,
    gen_seed: int | str | None = None,
    log_dir: Path | None = None,
) -> GenResult:
    """Run a single generation pipeline worker.

    Returns a GenResult; any unexpected exception is captured.
    """
    gen_seed = str(gen_seed) if gen_seed is not None else f"{uuid.uuid4().hex[:5]}"
    worker_id = worker_id if worker_id is not None else 0
    rng_seed = f"{gen_seed}_{worker_id}"

    # Generate unique seed constraints
    rng = random.Random(rng_seed)
    min_components = rng.randint(num_components_range[0], num_components_range[1])

    # Randomly select a subset of component types the LLM must use
    subset_min = min_components // 2
    subset_max = min(len(all_components), min_components)
    num_required = rng.randint(subset_min, subset_max)
    required_components = rng.sample(all_components, num_required)

    component_list = ", ".join(
        f"`{prefix}{' with kind keyword: ' + kind if kind else ' without kind keyword'}`"
        for prefix, kind in required_components
    )
    prompt_seed = (
        f"# Special Instructions For This Task Instance:\n"
        f"This is the minimum number of components for this instance: "
        f"{min_components}\n"
        f"You must include at least one of each of these component types (excluding wires): "
        f"{component_list}\n"
    )

    # Set up a per-worker logger
    log_path = (
        log_dir / f"debug_{gen_seed}_{worker_id}.log"
        if log_dir is not None
        else Path(__file__).parent.parent / "debug" / "netlist_gen_logs" / f"debug_{gen_seed}_{worker_id}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    netlist_logger = get_logger(
        __name__ + f".run_{gen_seed}_{worker_id}",
        extra_log_path=str(log_path),
    )

    initial_state = {
        "messages": [prompt_seed],
        "circuit_valid": False,
        "rng_seed": rng_seed,
        "index": worker_id,
        "logger": netlist_logger,
        "requirements": CircuitRequirements(
            min_components=min_components, required_components=required_components
        ),
    }

    netlist_logger.info(f"Worker {worker_id} starting (gen_seed={gen_seed})")
    netlist_logger.info(f"Initial prompt seed: {prompt_seed}\n")

    try:
        final_state = await graph.ainvoke(initial_state)
        ok = final_state.get("circuit_valid", False)
        result = GenResult(ok=ok, worker_id=worker_id)
        if not ok:
            # Extract last error message if available
            msgs = final_state.get("messages", [])
            if msgs:
                last_msg = msgs[-1]
                content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
                if "errors" in content.lower():
                    result.sanity_errors = [content]
        return result

    except Exception as e:
        netlist_logger.error(f"Worker {worker_id} failed with exception: {e}")
        return GenResult(
            ok=False,
            worker_id=worker_id,
            exception=str(e),
        )


async def scale_generation(
    output_dir: Path | str,
    batch_size: int = 50,
    concurrency: int = 4,
    num_components_range: tuple[int, int] = (5, 25),
    max_attempts: int = 3,
    gen_seed: int | str | None = None,
    temperature: float = 0.9,
    project_name: str = "default_project",
) -> list[GenResult]:
    """Generate a batch of netlists in parallel.

    Parameters
    ----------
    output_dir : Path | str
        Root output directory. ``netlists/`` and ``schematics/`` subdirs
        are created inside.
    batch_size : int
        Number of netlists to generate.
    concurrency : int
        Maximum number of simultaneous LLM calls.
    max_attempts : int
        Maximum number of regeneration attempts per sample (unused for now)
    gen_seed : int | None
        Seed for the seed-prompt generator (reproducible batches).
    temperature : float
        LLM sampling temperature.
    """
    # prevent circular import
    from .setup import netlist_gen_setup  # noqa: PLC0415

    output_dir = Path(output_dir).resolve()
    netlist_dir = output_dir / "netlists"
    schematic_dir = output_dir / "schematics"
    annotation_dir = output_dir / "annotations"

    # Create output directories
    netlist_dir.mkdir(parents=True, exist_ok=True)
    schematic_dir.mkdir(parents=True, exist_ok=True)
    annotation_dir.mkdir(parents=True, exist_ok=True)

    # Build the graph
    graph = netlist_gen_setup(
        netlist_dir,
        schematic_dir,
        temperature=temperature,
        annotation_dir=annotation_dir,
    )

    to_skin_config = get_config_value("convert", "convert_config_path", "TO_SKIN_CONFIG")
    all_components = []
    for prefix, component_specs in to_skin_config.items():
        if prefix == WIRE:
            continue  # Skip wire, as it's not a component to be required
        for spec in component_specs:
            kinds = spec.get("kind", [])
            all_components.append((prefix, " ".join(list(map(str.lower, kinds)))))

    # Use a semaphore to limit concurrency
    semaphore = asyncio.Semaphore(concurrency)
    log_dir = Path(__file__).parent.parent / "debug" / "netlist_gen_logs" / project_name
    log_dir.mkdir(parents=True, exist_ok=True)

    gen_seed = gen_seed if gen_seed is not None else f"{uuid.uuid4().hex[:5]}"

    async def bounded_worker(worker_id: int) -> GenResult:
        async with semaphore:
            return await run_pipeline_worker(
                num_components_range=num_components_range,
                worker_id=worker_id,
                graph=graph,
                gen_seed=str(gen_seed),
                all_components=all_components,
                log_dir=log_dir,
            )

    tasks = [bounded_worker(i) for i in range(batch_size)]
    results: list[GenResult] = await asyncio.gather(*tasks)

    valid_samples = [r for r in results if r.ok]
    print(f"Batch complete: {len(valid_samples)}/{batch_size} valid schematics generated.")

    return results
