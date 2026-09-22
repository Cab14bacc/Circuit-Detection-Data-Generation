import asyncio
import random
import shutil
import uuid
from tempfile import mkdtemp
from dataclasses import dataclass, field
from pathlib import Path

from .helper import generate_seed_prompt
from ..parser.convert import WIRE
from ..configs.config import get_logger, get_config_value


@dataclass
class GenResult:
    """Result of a single netlist generation worker."""

    ok: bool = False
    worker_id: int = 0
    gen_idx_in_session: int = 0
    # LLM generations the session used (shared by the netlists of a session)
    attempts: int = 0
    exception: str | None = None
    render_error: str | None = None
    sanity_errors: list[str] = field(default_factory=list)
    output_netlist: Path = field(default_factory=Path)
    output_schematic: Path = field(default_factory=Path)
    output_annotation: Path = field(default_factory=Path)
    output_overlay: Path = field(default_factory=Path)
    output_yosys: Path = field(default_factory=Path)


@dataclass
class CircuitRequirements:
    """Result of a single netlist generation worker."""

    num_components: int = 0
    component_subset: list[tuple[str, str]] = field(default_factory=list)


async def run_pipeline_worker(
    graph,
    all_components: list[tuple[str, str]],
    num_components_range: tuple[int, int] = (5, 25),
    gen_count_per_session: int = 1,
    worker_id: int | None = None,
    gen_seed: int | str | None = None,
    log_dir: Path | None = None,
) -> GenResult:
    """Run a single generation pipeline worker.

    Returns a GenResult; any unexpected exception is captured. Netlists
    still invalid when the graph's retry budget (max_attempts, set by
    netlist_gen_setup) runs out come back not ok.
    """
    gen_seed = str(gen_seed) if gen_seed is not None else f"{uuid.uuid4().hex[:5]}"
    worker_id = worker_id if worker_id is not None else 0
    rng_base_seed = f"{gen_seed}_{worker_id}"

    # Generate unique seed constraints

    constraints = []
    for idx in range(gen_count_per_session):
        rng_seed = f"{rng_base_seed}_{idx}"
        rng = random.Random(rng_seed)
        num_components = rng.randint(num_components_range[0], num_components_range[1])

        # Randomly select a subset of component types the LLM must use
        subset_min = num_components // 2
        subset_max = min(len(all_components), num_components)
        num_required = rng.randint(subset_min, subset_max)
        component_subset = rng.sample(all_components, num_required)

        constraints.append(
            CircuitRequirements(num_components=num_components, component_subset=component_subset)
        )

    final_seed_prompt = generate_seed_prompt(gen_count_per_session, constraints)

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
        "messages": [final_seed_prompt],
        "circuits_valid": [False for _ in range(gen_count_per_session)],
        "rng_seed": rng_base_seed,
        "index": worker_id,
        "logger": netlist_logger,
        "gen_count_per_session": gen_count_per_session,
        "requirements": constraints,
    }

    netlist_logger.info(f"Worker {worker_id} starting (gen_seed={gen_seed})")
    netlist_logger.info(f"Initial prompt seed: {final_seed_prompt}\n")

    try:
        final_state = await graph.ainvoke(initial_state)
        attempts = final_state.get("attempts", 0)
        ok = final_state.get("circuits_valid", [False for _ in range(gen_count_per_session)])
        output_netlists = final_state.get("output_netlists", ["" for _ in range(gen_count_per_session)])
        output_schematics = final_state.get("output_schematics", ["" for _ in range(gen_count_per_session)])
        output_annotations = final_state.get("output_annotations", ["" for _ in range(gen_count_per_session)])
        output_overlays = final_state.get("output_overlays", ["" for _ in range(gen_count_per_session)])
        output_yosyss = final_state.get("output_yosys", ["" for _ in range(gen_count_per_session)])

        result = [
            GenResult(
                ok=k,
                output_netlist=output_netlists[idx],
                output_schematic=output_schematics[idx],
                output_annotation=output_annotations[idx],
                output_overlay=output_overlays[idx],
                output_yosys=output_yosyss[idx],
                worker_id=worker_id,
                gen_idx_in_session=idx,
                attempts=attempts,
            )
            for idx, k in enumerate(ok)
        ]
        if not all(ok):
            # Extract last error message if available
            msgs = final_state.get("messages", [])
            if msgs:
                last_msg = msgs[-1]
                content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
                if "errors" in content.lower():
                    for r in result:
                        if not r.ok:
                            r.sanity_errors = [content]
        return result

    except Exception as e:
        netlist_logger.error(f"Worker {worker_id} failed with exception: {e}")
        return [
            GenResult(
                ok=False,
                worker_id=worker_id,
                exception=str(e),
            )
        ]


async def scale_generation(
    output_dir: Path | str,
    num_netlists: int = 50,
    concurrency: int = 4,
    gen_count_per_session: int = 1,
    num_components_range: tuple[int, int] = (5, 25),
    max_attempts: int = 3,
    gen_seed: int | str | None = None,
    temperature: float = 0.9,
    project_name: str = "default_project",
    strict: bool | None = None,
) -> list[GenResult]:
    """Generate a batch of netlists in parallel.

    Parameters
    ----------
    output_dir : Path | str
        Root output directory. ``netlists/`` and ``schematics/`` subdirs
        are created inside.
    num_netlists : int
        Number of netlists to generate.
    concurrency : int
        Maximum number of simultaneous LLM calls.
    max_attempts : int
        Maximum number of LLM generations per session, the first one
        included (>= 1). Netlists still invalid after the last one fail.
    gen_seed : int | None
        Seed for the seed-prompt generator (reproducible batches).
    temperature : float
        LLM sampling temperature.
    strict : bool | None
        SPICE strict parsing (and the strict system prompt) for validation.
        None = config (convert.strict_parsing or simulation.enabled).
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

    is_spice = get_config_value("convert", "netlist_format").lower() == "spice"

    if is_spice:
        to_skin_config = get_config_value("convert", "convert_spice_config_path", "TO_SKIN_CONFIG")
    else:
        to_skin_config = get_config_value("convert", "convert_lcapy_config_path", "TO_SKIN_CONFIG")

    all_components = []
    for prefix, component_specs in to_skin_config.items():
        if prefix == WIRE:
            continue  # Skip wire, as it's not a component to be required
        for spec in component_specs:
            kinds = spec.get("kind", [])
            specifiers = spec.get("specifiers", [])

            all_components.append((prefix, tuple(map(str.lower, kinds)), tuple(map(str.lower, specifiers))))

    # Use a semaphore to limit concurrency
    semaphore = asyncio.Semaphore(concurrency)
    log_dir = Path(__file__).parent.parent / "debug" / "netlist_gen_logs" / project_name
    log_dir.mkdir(parents=True, exist_ok=True)

    gen_seed = gen_seed if gen_seed is not None else f"{uuid.uuid4().hex[:5]}"

    try:
        temp_dir = Path(mkdtemp())
        tmp_netlist_dir = temp_dir / "netlists"
        tmp_schematic_dir = temp_dir / "schematics"
        tmp_annotation_dir = temp_dir / "annotations"

        # Build the graph
        graph = netlist_gen_setup(
            tmp_netlist_dir,
            tmp_schematic_dir,
            annotation_dir=tmp_annotation_dir,
            temperature=temperature,
            strict=strict,
            max_attempts=max_attempts,
        )

        async def bounded_worker(worker_id: int, gen_count: int) -> GenResult:
            async with semaphore:
                return await run_pipeline_worker(
                    num_components_range=num_components_range,
                    worker_id=worker_id,
                    graph=graph,
                    gen_seed=str(gen_seed),
                    gen_count_per_session=gen_count,
                    all_components=all_components,
                    log_dir=log_dir,
                )

        batch_size = num_netlists // gen_count_per_session
        last_gen_count = gen_count_per_session
        if num_netlists % gen_count_per_session != 0:
            batch_size += 1  # Add an extra batch for the remainder
            last_gen_count = num_netlists % gen_count_per_session

        tasks = [
            bounded_worker(i, gen_count_per_session if i < batch_size - 1 else last_gen_count)
            for i in range(batch_size)
        ]
        results: list[list[GenResult]] = await asyncio.gather(*tasks)
        valid_samples = [_r for r in results for _r in r if _r.ok]

        global_idx = 0
        for valid_sample in valid_samples:
            output_netlist_path = Path(valid_sample.output_netlist)
            netlist_name = output_netlist_path.name.replace("netlist", f"netlist_{global_idx}")
            output_netlist_path.rename(netlist_dir / netlist_name)
            output_schematic_path = Path(valid_sample.output_schematic)
            schematic_name = output_schematic_path.name.replace("schematic", f"schematic_{global_idx}")
            output_schematic_path.rename(schematic_dir / schematic_name)

            output_annotation_path = Path(valid_sample.output_annotation)
            annotation_name = output_annotation_path.name.replace("annotation", f"annotation_{global_idx}")
            output_annotation_path.rename(annotation_dir / annotation_name)

            output_overlay_path = Path(valid_sample.output_overlay)
            overlay_name = output_overlay_path.name.replace("overlay", f"overlay_{global_idx}")
            output_overlay_path.rename(annotation_dir / overlay_name)

            output_yosys_path = Path(valid_sample.output_yosys)
            yosys_name = output_yosys_path.name.replace("yosys", f"yosys_{global_idx}")
            output_yosys_path.rename(schematic_dir / yosys_name)
            global_idx += 1
    except Exception as e:
        get_logger(__name__).error(f"Batch generation failed with exception: {e}")
        raise
    finally:
        shutil.rmtree(temp_dir)

    print(f"Batch complete: {len(valid_samples)}/{num_netlists} valid schematics generated.")

    return results
