"""Simulation step: run a generated netlist through ngspice (via PySpice) and
decide whether it is electrically valid, i.e. ngspice finds its DC
operating point directly (convergence aids off, see parser/export.py).

Each simulation runs in a fresh process: after some errors the ngspice
shared library cannot recover ("awaits to be detached") and the process
hangs, so the child is always terminated once its result has been read,
and killed when it does not answer within the timeout.

A run passes when ngspice raised nothing, produced a non-empty operating
point, reported no singular matrix, printed no warning outside
SIMULATION_CONFIG["IGNORED_WARNINGS"] (unknown warnings reject, so the list
is only ever extended deliberately), and not every node voltage is 0 V (an
undriven circuit is valid but useless). Failures are translated into
LLM-facing instructions (translate_errors), with ngspice's lower-cased names
mapped back to the netlist's spelling.

PySpice is an optional dependency (`pip install .[sim]`) and is only
imported in the child process.
"""

import multiprocessing
import os
import queue
import re
import time
from dataclasses import dataclass, field

from .configs.config import get_config_value
from .parser.export import to_ngspice_netlist

# The ngspice version simulations must use. The error translations and pass
# rules were checked against it: newer ngspice (37+) adds a transient-op
# convergence fallback and words its messages differently.
NGSPICE_VERSION = 32

# a node voltage below this magnitude counts as 0 V for the "driven" check
ZERO_VOLTAGE = 1e-9
# seconds the child gets to exit on its own after reporting, before it is killed
_EXIT_GRACE_S = 1.0
# ngspice's convergence aids announcing themselves in its output: gmin and
# source stepping (ngspice 34+, "Starting ... gmin stepping") and the
# transient operating point fallback (ngspice 37+, "Transient op started")
_CONVERGENCE_AID_RE = re.compile(r"gmin stepping|source stepping|transient op started", re.IGNORECASE)


@dataclass
class SimResult:
    """The outcome of one simulation. `errors` are LLM-facing instructions;
    `warnings` are the harmless ngspice warnings that were ignored."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    node_voltages: dict[str, float] = field(default_factory=dict)
    branch_currents: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    timed_out: bool = False
    raw_log: str = ""
    ngspice_version: int | None = None

    def to_json(self) -> dict:
        """The operating point as persisted next to the dataset sample."""
        return {
            "analysis": "op",
            "ngspice_version": self.ngspice_version,
            "node_voltages": self.node_voltages,
            "branch_currents": self.branch_currents,
            "warnings": self.warnings,
        }


def ngspice_available() -> bool:
    """Whether PySpice can be imported (the ngspice library itself is only
    located when a simulation starts)."""
    try:
        import PySpice.Spice.NgSpice.Shared  # noqa: F401, PLC0415
    except Exception:
        return False
    return True


def check_ngspice() -> None:
    """Fail fast unless ngspice loads and is NGSPICE_VERSION. Without this, a
    missing library would only show up as an error on every netlist (burning
    the LLM's attempts), and another version would be judged by rules written
    for this one. Loads the library in this process; PySpice reads its
    version from ngspice's `version` command, no simulation needed."""
    ngspice_install_hint = (
        f"on Windows run `pyspice-post-installation --ngspice-version {NGSPICE_VERSION} "
        "--install-ngspice-dll`; "
        f"on Linux/macOS run `conda install -c conda-forge ngspice-lib={NGSPICE_VERSION}` in this environment"
    )
    try:
        from PySpice.Spice.NgSpice.Shared import NgSpiceShared  # noqa: PLC0415

        version = NgSpiceShared.new_instance().ngspice_version
    except Exception as e:
        raise RuntimeError(
            f"ngspice {NGSPICE_VERSION} could not be loaded ({type(e).__name__}: {e}): "
            f"{ngspice_install_hint}."
        ) from e
    if version != NGSPICE_VERSION:
        raise RuntimeError(
            f"Simulation needs ngspice {NGSPICE_VERSION}, but ngspice {version} was loaded: "
            f"{ngspice_install_hint}."
        )


def _ngspice_child(deck: str, out: "multiprocessing.Queue") -> None:
    """Child process body: load and run `deck`, report the op vectors and
    everything ngspice printed. Must stay a module-level function (spawn)."""
    # ngspice also prints to the process's own stdout/stderr;
    # Point the file descriptors (stdout/stderr) to devnull
    # PySpice still keeps its copy (ng.stdout / ng.stderr)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    result: dict = {"exception": None, "plot": None, "vectors": {}, "log": "", "version": None}
    try:
        from PySpice.Spice.NgSpice.Shared import NgSpiceShared  # noqa: PLC0415
    except Exception as e:
        result["exception"] = f"PySpice is not available: {e}"
        out.put(result)
        return
    ng = None
    try:
        ng = NgSpiceShared.new_instance()
        result["version"] = ng.ngspice_version
        ng.load_circuit(deck)
        try:
            ng.run()
        except Exception as e:
            # PySpice 1.5 raises on every stderr line not starting with
            # "Warning:", and newer ngspice (41) prints harmless "Note:" lines
            # there. So a raise here does not mean the run failed: keep going,
            # and let translate_errors judge the operating point and the log.
            result["run_error"] = f"{type(e).__name__}: {e}"
        # A plot is one set of results. Each analysis ngspice runs creates a plot,
        # named after the analysis plus a counter (e.g. "op1", "tran2").
        # The last plot is the one from the last analysis. (we expect "op1")
        result["plot"] = ng.last_plot
        if str(ng.last_plot).lower().startswith("op"):
            # ng.plot returns a dict-like object from vector name to vector
            plot = ng.plot(None, ng.last_plot)

            # one vector per node, holding its voltage (e.g. in -> [5.0], out -> [3.3]);
            # one vector per current ngspice solves for, named <element>#branch, such as v1#branch
            # (the current through voltage source V1);
            # some elements add internal vectors: t1#int1, t1#i1 for a transmission
            # line, o1#i1 for the LTRA model.

            # an operating point is real; take the real part if numpy hands back complex
            result["vectors"] = {
                name: [float(getattr(x, "real", x)) for x in vector._data] for name, vector in plot.items()
            }
    except Exception as e:
        result["exception"] = f"{type(e).__name__}: {e}"
    if ng is not None:
        result["log"] = "\n".join(part for part in (ng.stdout, ng.stderr) if part)
    out.put(result)
    # flush the report now: after some errors the ngspice library wedges the
    # process, which would stop the queue's feeder thread from sending it
    out.close()
    out.join_thread()


def run_ngspice(deck: str, timeout: float) -> dict | None:
    """Run an ngspice deck in a fresh process. Returns the child's report
    ({"exception", "plot", "vectors", "log"}), a report with "crashed" set
    when the child died without reporting, or None when it timed out. The
    child is always terminated afterwards."""
    context = multiprocessing.get_context("spawn")
    out = context.Queue()
    child = context.Process(target=_ngspice_child, args=(deck, out), daemon=True)
    child.start()
    report = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                report = out.get(timeout=0.1)
                break
            except queue.Empty:
                if not child.is_alive():
                    # died without reporting; one last look in case the
                    # report and the exit raced
                    try:
                        report = out.get(timeout=0.1)
                    except queue.Empty:
                        report = {"crashed": child.exitcode, "exception": None, "vectors": {}, "log": ""}
                    break
    finally:
        child.join(_EXIT_GRACE_S)
        if child.is_alive():
            child.terminate()
            child.join()
        out.close()
    return report


def _spellings(netlist_text: str) -> dict[str, str]:
    """ngspice lower-cases every name; map them back to the netlist's
    spelling (first occurrence wins)."""
    spellings: dict[str, str] = {}
    for line in netlist_text.splitlines():
        # names are word characters (subcircuit paths add "." and branches "#")
        # split on non-words, and the literals '.' and '#', to get the tokens
        for token in re.split(r"[^\w.#]+", line):
            if token:
                spellings.setdefault(token.lower(), token)
    return spellings


def _describe_node(name: str, spellings: dict[str, str]) -> str:
    """A node as the netlist writes it: `mid`, or `mid inside X1` for a
    subcircuit-internal node (ngspice writes x1.mid)."""
    if "." in name:
        instance, _, node = name.rpartition(".")
        return f"{spellings.get(node, node)} inside subcircuit instance {spellings.get(instance, instance)}"
    return spellings.get(name, name)


def translate_errors(
    report: dict | None, netlist_text: str, ignored_warnings: list[str], timeout: float
) -> tuple[list[str], list[str]]:
    """Turn a child report into (LLM-facing errors, ignored warnings)."""
    if report is None:
        return (
            [
                f"The simulation did not finish within {timeout:g} s. Simplify the circuit and use "
                f"realistic component values."
            ],
            [],
        )
    if report.get("crashed") is not None:
        # not the netlist's fault as far as we can tell: report it plainly
        return [f"The simulator process exited unexpectedly (exit code {report['crashed']})."], []
    log = report.get("log", "")
    spellings = _spellings(netlist_text)
    errors: list[str] = []

    # lines ngspice rejected while loading ("Error on line N :", the line, the reason)
    for line, reason in re.findall(r"Error on line \d+ :\s*\n(.+)\n(.+)", log):
        original = next(
            (ln.strip() for ln in netlist_text.splitlines() if ln.strip().lower() == line.strip().lower()),
            line.strip(),
        )
        errors.append(f"ngspice rejected the line `{original}`: {reason.strip()}.")
    # a device ngspice refuses to set up (LTRA with G != 0, TXL without length, ...)
    for message in re.findall(r"Fatal error:\s*([^\n]+)", log):
        errors.append(f"ngspice rejected the circuit: {message.strip()}.")
    for symbol in re.findall(r"Undefined number \[(\w+)\]", log):
        errors.append(
            f"ngspice: '{spellings.get(symbol, symbol)}' is undefined; "
            f"define it with .param or write a number."
        )

    # singular matrix: a node without a DC path, or a branch in a V/L loop.
    # ngspice 34 writes "check nodes a and b", ngspice 41 "check node a".
    singular = re.findall(r"singular matrix:\s+check nodes? (\S+)(?: and (\S+))?", log)
    for name1, name2 in dict.fromkeys(singular):
        names = (name1, name2) if name2 and name1 != name2 else (name1,)
        for name in names:
            if "#" in name:
                element = spellings.get(name.split("#")[0], name.split("#")[0])
                errors.append(
                    f"{element} is part of a loop made only of voltage sources and/or inductors "
                    f"(an inductor is a short circuit at DC), so the loop current is undefined; "
                    f"put a resistor in the loop or remove one of its elements."
                )
            else:
                errors.append(
                    f"Node {_describe_node(name, spellings)} has no DC path to ground: at DC "
                    f"capacitors are open and current sources only set a current, so its voltage "
                    f"is undefined. Connect it to ground or to another node through a resistor "
                    f"(or another element that conducts at DC)."
                )

    # every other warning: ignored when listed, an error otherwise (fail closed)
    ignored: list[str] = []
    for warning in re.findall(r"(?i)warning:\s*([^\n]+)", log):
        warning = warning.strip()
        if "singular matrix" in warning.lower():
            continue
        if any(re.search(pattern, warning, re.IGNORECASE) for pattern in ignored_warnings):
            ignored.append(warning)
        else:
            errors.append(f"ngspice warning: {warning}")

    # a convergence aid was used: the circuit did not solve directly. The deck
    # turns off gmin and source stepping, but ngspice 37+ also falls back to a
    # "transient op", which no option reliably turns off, so any sign of one
    # rejects the run (usually alongside a singular-matrix message above).
    if _CONVERGENCE_AID_RE.search(log) and not errors:
        errors.append(
            "ngspice could only find the DC operating point with a convergence aid, which is not "
            "accepted: the circuit must solve directly. Give every node a DC path to ground, avoid "
            "loops made only of voltage sources and inductors, and use realistic values."
        )

    # no operating point: nothing loaded, no op plot, or a plot with empty vectors
    no_operating_point = not any(report.get("vectors", {}).values())
    if no_operating_point and not errors:
        if "PySpice is not available" in str(report.get("exception")):
            errors.append(f"Simulation unavailable: {report['exception']}")
        else:
            errors.append(
                "ngspice could not find the DC operating point: Newton iteration did not converge "
                "(convergence aids are off). Use realistic values (supply voltages of a few volts, "
                "resistances between ohms and megaohms), bias every transistor and diode through resistors, "
                "and avoid feedback loops with very high gain."
            )
    return list(dict.fromkeys(errors)), ignored


def simulation_validation(
    netlist_text: str, timeout: float | None = None, ignored_warnings: list[str] | None = None
) -> SimResult:
    """Simulate a (strictly parsed) netlist's DC operating point and judge
    it. `timeout` and `ignored_warnings` default to SIMULATION_CONFIG."""
    if timeout is None:
        timeout = float(get_config_value("simulation", "timeout_s"))
    if ignored_warnings is None:
        ignored_warnings = list(get_config_value("simulation", "ignored_warnings"))

    report = run_ngspice(to_ngspice_netlist(netlist_text), timeout)
    errors, ignored = translate_errors(report, netlist_text, ignored_warnings, timeout)
    result = SimResult(
        ok=False,
        errors=errors,
        warnings=ignored,
        timed_out=report is None,
        raw_log=(report or {}).get("log", ""),
        ngspice_version=(report or {}).get("version"),
    )
    if errors:
        return result

    spellings = _spellings(netlist_text)
    for name, values in report["vectors"].items():
        if not values:
            continue
        if "#" in name:
            element, _, branch = name.partition("#")
            result.branch_currents[f"{spellings.get(element, element)}#{branch}"] = values[0]
        else:
            result.node_voltages[_describe_node(name, spellings)] = values[0]

    if all(abs(v) < ZERO_VOLTAGE for v in result.node_voltages.values()):
        result.errors.append(
            "Every node voltage is 0 V at the DC operating point: nothing drives the circuit. Give at "
            "least one independent V or I source a non-zero DC value (a source with only an AC value, or "
            "SIN(...) with a zero offset, is 0 at DC)."
        )
        return result
    result.ok = True
    return result
