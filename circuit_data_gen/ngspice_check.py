"""ngspice smoke test: check that ngspice accepts a generated netlist.

Runs after the render smoke test during strict generation, when an ngspice
executable is available (find_ngspice). ngspice loads the netlist and sets
the circuit up, and the netlist is rejected when ngspice reports a load or
setup error (an unknown parameter, a malformed line, a missing model, an
undefined .param, a reference to an element that does not exist). Setup
errors such as a K line naming a missing inductor only show once an
analysis starts, so the deck runs an `op` in a .control block; its result
is not checked, and convergence messages are ignored.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .configs.config import get_config_value, get_logger
from .parser.export import to_ngspice_netlist

logger = get_logger(__name__)

SMOKE_TITLE = "* ngspice smoke test"
# load and setup errors: the netlist is not proper ngspice input
_ERROR_RE = re.compile(
    r"error on line|error in netlist line|unknown parameter|mal formed|can't find model"
    r"|unable to find definition|undefined parameter|fatal error|not enough parameters"
    r"|unknown subckt|mismatch: .* params|could not find include file|unknown controlling source",
    re.IGNORECASE,
)
# ngspice output that is not about the netlist: notes, the analysis and its
# convergence messages, and the generic follow-ups of an error
_NOISE_RE = re.compile(
    r"^(note:|circuit:|using sparse|doing analysis|no\. of data rows|simulation interrupted"
    r"|error: incomplete or empty netlist|or no \"\.plot\"|doanalyses:|\S+ simulation\(s\) aborted"
    r"|error: fatal error in ngspice|warning: singular matrix|warning: .*stepping|dc solution failed"
    r"|last node voltages|error: the operating point|error: transient op|any of the following steps"
    r"|no simulations run|in line no\. \d+ of file)",
    re.IGNORECASE,
)


def find_ngspice() -> str | None:
    """The ngspice executable for the smoke test: netlist_gen.ngspice_path
    when set, else the first of `ngspice_con` / `ngspice` on PATH (on
    Windows `ngspice.exe` is the GUI build, which prints nothing, so the
    console build `ngspice_con.exe` comes first). None when there is none."""
    configured = str(get_config_value("netlist_gen", "ngspice_path", default="")).strip()
    if configured:
        return configured if Path(configured).is_file() else shutil.which(configured)
    for name in ("ngspice_con", "ngspice"):
        path = shutil.which(name)
        if path:
            return path
    return None


# the ngspice version the grammar and the strict parser were checked against
TESTED_NGSPICE_VERSION = 47


def ngspice_version(executable: str) -> int | None:
    """The major version `executable --version` reports (`** ngspice-47 :
    ...`), None when it cannot be read."""
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"ngspice-(\d+)", result.stdout + result.stderr)
    return int(match.group(1)) if match else None


def warn_if_untested_version(executable: str) -> None:
    """Warn when `executable` is not ngspice TESTED_NGSPICE_VERSION: other
    versions may accept or reject lines differently (ngspice 32 rejects
    `Q={...}` on C, for one), so the smoke test may disagree with the grammar."""
    version = ngspice_version(executable)
    if version == TESTED_NGSPICE_VERSION:
        return
    found = f"ngspice {version}" if version is not None else "an ngspice of unknown version"
    logger.warning(
        f"The ngspice smoke test uses {found} ({executable}); the grammar is checked against "
        f"ngspice {TESTED_NGSPICE_VERSION}, so the smoke test may reject netlists the grammar allows."
    )


def ngspice_smoke_test(
    netlist_text: str, executable: str, timeout: float | None = None, logger=logger
) -> list[str]:
    """Load `netlist_text` into ngspice (batch mode) and return the problems
    ngspice reports, as LLM-facing messages; empty when it accepts the
    netlist. A run that exceeds `timeout` seconds (netlist_gen.ngspice_timeout_s
    when None) is not counted against the netlist: the timeout comes from the
    analysis, not from loading."""
    if timeout is None:
        timeout = float(get_config_value("netlist_gen", "ngspice_timeout_s"))
    deck = to_ngspice_netlist(netlist_text, title=SMOKE_TITLE, analysis=".control\nop\n.endc")
    with tempfile.TemporaryDirectory() as tmp:
        deck_path = Path(tmp) / "smoke.cir"
        deck_path.write_text(deck, encoding="utf-8")
        try:
            # cwd: ngspice may write files (spice.log, ...) next to itself
            result = subprocess.run(
                [executable, "-b", str(deck_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=tmp,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return []
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not any(_ERROR_RE.search(line) for line in lines):
        return []
    reported: list[str] = []
    for line in lines:
        if _NOISE_RE.search(line) or line in reported:
            continue
        reported.append(line)
    return ["ngspice rejected the netlist:\n" + "\n".join(reported)]
