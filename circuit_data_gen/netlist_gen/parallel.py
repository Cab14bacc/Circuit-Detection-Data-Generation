import asyncio
import json
import random
import shutil
import uuid
import time

from tempfile import mkdtemp
from dataclasses import dataclass, field
from pathlib import Path
from .helper import generate_seed_prompt
from .sampling import CircuitRequirements, ComponentSample, build_pool, sample_requirements
from ..configs.config import get_logger, get_config_path_value

logger = get_logger(__name__)


@dataclass
class GenResult:
    """Result of a single netlist generation worker."""

    ok: bool = False
    worker_id: int = 0
    gen_idx: int = 0

    # stage -> time spent in seconds (the LLM stage is shared by all circuits of the session)
    timing: dict[str, float] = field(default_factory=dict)
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
    # what the netlist was asked to contain (for per-type pass rates)
    requirements: CircuitRequirements | None = None


async def run_pipeline_worker(
    graph,
    pool: list[ComponentSample],
    num_components_range: tuple[int, int] = (5, 25),
    base_seed: int | str | None = None,
    worker_id: int | None = None,
    gen_ids: list[int] = [0],
    log_dir: Path | None = None,
    max_attempts: int = 3,
) -> GenResult:
    """Run a single generation pipeline worker.

    Each netlist's requirements are drawn from `pool` (see
    sampling.sample_requirements).
    Returns a GenResult; any unexpected exception is captured. Netlists
    still invalid when the graph's retry budget (max_attempts, set by
    netlist_gen_setup) runs out come back not ok.
    """
    base_seed = str(base_seed) if base_seed is not None else f"{uuid.uuid4().hex[:5]}"
    worker_id = worker_id if worker_id is not None else 0
    gen_count_per_session = len(gen_ids)
    rng_base_seed = f"{base_seed}_{worker_id}"
    rng_seeds = [f"{rng_base_seed}_{gen_ids[idx]}" for idx in range(gen_count_per_session)]

    # draw each netlist's requirements from its own seeded rng (reproducible)
    constraints = [
        sample_requirements(random.Random(f"{rng_seeds[idx]}"), pool, num_components_range)
        for idx in range(gen_count_per_session)
    ]

    final_seed_prompt = generate_seed_prompt(gen_count_per_session, constraints)

    # Set up a per-worker logger
    log_path = (
        log_dir / f"debug_{rng_base_seed}.log"
        if log_dir is not None
        else Path(__file__).parent.parent / "debug" / "netlist_gen_logs" / f"debug_{rng_base_seed}.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    netlist_logger = get_logger(
        __name__ + f".run_{rng_base_seed}",
        extra_log_path=str(log_path),
        extra_log_formatter="formatter",
    )

    initial_state = {
        "messages": [final_seed_prompt],
        "circuits_valid": [False for _ in range(gen_count_per_session)],
        "rng_base_seed": rng_base_seed,
        "rng_seeds": rng_seeds,
        "worker_id": worker_id,
        "max_attempts": max_attempts,
        "gen_count_per_session": gen_count_per_session,
        "requirements": constraints,
        "logger": netlist_logger,
    }

    netlist_logger.info(
        (
            f"Worker {worker_id} starting.\n"
            f"rng_base_seed={rng_base_seed}\n"
            f"rng_seeds={rng_seeds}\n"
            f"max_attempts={max_attempts}\n"
            f"gen_count_per_session={gen_count_per_session}\n"
            f"Initial seed prompt: {final_seed_prompt}\n"
        )
    )

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
                gen_idx=gen_ids[idx],
                attempts=attempts,
                timing=final_state["timings"][idx],
                requirements=constraints[idx],
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
                gen_idx=gen_ids[idx],
                attempts=max_attempts,
                timing={},
            )
            for idx in range(gen_count_per_session)
        ]


def rename_outputs(gen_result, global_idx, netlist_dir, schematic_dir, annotation_dir):
    output_netlist_path = Path(gen_result.output_netlist)
    netlist_name = output_netlist_path.name.replace("netlist", f"netlist_{global_idx}")
    output_netlist_path = output_netlist_path.rename(netlist_dir / netlist_name)

    output_schematic_path = Path(gen_result.output_schematic)
    schematic_name = output_schematic_path.name.replace("schematic", f"schematic_{global_idx}")
    output_schematic_path = output_schematic_path.rename(schematic_dir / schematic_name)

    output_annotation_path = Path(gen_result.output_annotation)
    annotation_data = json.loads(output_annotation_path.read_text(encoding="utf-8"))
    annotation_data["image"] = str(output_schematic_path.resolve())
    output_annotation_path.write_text(json.dumps(annotation_data), encoding="utf-8")
    annotation_name = output_annotation_path.name.replace("annotation", f"annotation_{global_idx}")
    output_annotation_path = output_annotation_path.rename(annotation_dir / annotation_name)

    output_overlay_path = Path(gen_result.output_overlay)
    overlay_name = output_overlay_path.name.replace("overlay", f"overlay_{global_idx}")
    output_overlay_path = output_overlay_path.rename(annotation_dir / overlay_name)

    output_yosys_path = Path(gen_result.output_yosys)
    yosys_name = output_yosys_path.name.replace("yosys", f"yosys_{global_idx}")
    output_yosys_path = output_yosys_path.rename(schematic_dir / yosys_name)


async def scale_generation(
    output_dir: Path | str,
    num_netlists: int = 50,
    concurrency: int = 4,
    gen_count_per_session: int = 1,
    num_components_range: tuple[int, int] = (5, 25),
    max_attempts: int = 10,
    gen_seed: int | str | None = None,
    temperature: float = 0.9,
    project_name: str = "default_project",
    strict: bool | None = None,
    persevere: bool = False,
    rename: bool = True,
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
        SPICE strict parsing for validation, plus the ngspice smoke test
        when ngspice is found. None = config (convert.strict_parsing).
    """
    # prevent circular import
    from .setup import netlist_gen_setup, stage_timings  # noqa: PLC0415

    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

    output_dir = Path(output_dir).resolve()
    netlist_dir = output_dir / "netlists"
    schematic_dir = output_dir / "schematics"
    annotation_dir = output_dir / "annotations"

    # Create output directories
    netlist_dir.mkdir(parents=True, exist_ok=True)
    schematic_dir.mkdir(parents=True, exist_ok=True)
    annotation_dir.mkdir(parents=True, exist_ok=True)

    pool = build_pool()

    # Use a semaphore to limit concurrency
    semaphore = asyncio.Semaphore(concurrency)
    netlist_gen_debug_dir = get_config_path_value(
        "netlist_gen", "DEBUG_DIR", default="debug/netlist_gen_logs"
    )
    log_dir = netlist_gen_debug_dir / project_name
    log_dir.mkdir(parents=True, exist_ok=True)

    gen_seed = gen_seed if gen_seed is not None else f"{uuid.uuid4().hex[:5]}"
    total_start = time.perf_counter()
    try:
        if rename:
            temp_dir = Path(mkdtemp())
            work_netlist_dir = temp_dir / "netlists"
            work_schematic_dir = temp_dir / "schematics"
            work_annotation_dir = temp_dir / "annotations"
        else:
            work_netlist_dir = netlist_dir
            work_schematic_dir = schematic_dir
            work_annotation_dir = annotation_dir

        # Build the graph
        graph = netlist_gen_setup(
            work_netlist_dir,
            work_schematic_dir,
            annotation_dir=work_annotation_dir,
            temperature=temperature,
            strict=strict,
        )

        async def bounded_worker(
            worker_id: int, gen_ids: int | list, max_attempts: int = max_attempts
        ) -> GenResult:
            if isinstance(gen_ids, int):
                _gen_ids = list(range(gen_ids))
            else:
                _gen_ids = gen_ids

            async with semaphore:
                return await run_pipeline_worker(
                    num_components_range=num_components_range,
                    graph=graph,
                    worker_id=worker_id,
                    base_seed=str(gen_seed),
                    gen_ids=_gen_ids,
                    pool=pool,
                    log_dir=log_dir,
                    max_attempts=max_attempts,
                )

        batch_size = num_netlists // gen_count_per_session
        last_gen_count = gen_count_per_session
        if num_netlists % gen_count_per_session != 0:
            batch_size += 1  # Add an extra batch for the remainder
            last_gen_count = num_netlists % gen_count_per_session

        pending: set[asyncio.Task] = {
            asyncio.create_task(
                bounded_worker(i, gen_count_per_session if i < batch_size - 1 else last_gen_count)
            )
            for i in range(batch_size)
        }

        valid_samples = []
        invalid_results = {}
        global_idx = 0
        # stage -> seconds over all sessions (each session's LLM time once)
        global_stage_timings: dict[str, float] = {}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                results = task.result()
                # one task is one session: its circuits share the LLM time
                session_timings = stage_timings({idx: r.timing for idx, r in enumerate(results)})
                for stage, seconds in session_timings.items():
                    global_stage_timings[stage] = global_stage_timings.get(stage, 0.0) + seconds

                for gen_result in results:
                    if gen_result.ok:
                        valid_samples.append(gen_result)

                        if persevere:
                            worker_id = gen_result.worker_id
                            gen_idx = gen_result.gen_idx
                            global_idx = worker_id * gen_count_per_session + gen_idx

                        if rename:
                            # ===========================================================
                            # For more intuitive naming (i.e. sequential numbering),
                            # we rename and move the valid netlists and schematics to
                            # the final output directories
                            # ===========================================================
                            rename_outputs(
                                gen_result,
                                global_idx,
                                netlist_dir,
                                schematic_dir,
                                annotation_dir,
                            )

                        global_idx += 1
                    else:
                        invalid_results.setdefault(gen_result.worker_id, []).append(gen_result)

                if persevere and invalid_results:
                    for worker_id, gen_results in invalid_results.items():
                        pending.add(
                            asyncio.create_task(
                                bounded_worker(
                                    worker_id,
                                    [gen_result.gen_idx for gen_result in gen_results],
                                    max(gen_result.attempts for gen_result in gen_results) * 2,
                                )
                            )
                        )

                    invalid_results = {}

        results: list[list[GenResult]] = valid_samples + [
            gen_result for gen_results in invalid_results.values() for gen_result in gen_results
        ]

    except Exception as e:
        get_logger(__name__).error(f"Batch generation failed with exception: {e}")
        raise
    finally:
        if rename:
            shutil.rmtree(temp_dir)

    total_elapsed = time.perf_counter() - total_start
    total_time = sum(global_stage_timings.values())
    saved_time = total_time - total_elapsed
    stages = ", ".join(f"{stage} {seconds:.2f}s" for stage, seconds in global_stage_timings.items())
    per_schematic = f"{total_elapsed / len(valid_samples):.2f}s" if valid_samples else "n/a"

    logger.info(
        f"Batch generation complete: {len(valid_samples)}/{num_netlists} valid schematics generated "
        f"in {total_elapsed:.2f}s\n"
        f"Saved time (IO): {saved_time:.2f}s, stage timings: {stages or 'none'}\n"
        f"average time per schematic: {per_schematic}"
    )

    return results
