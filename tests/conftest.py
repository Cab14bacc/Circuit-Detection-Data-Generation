"""Shared fixtures and helpers for the circuit_data_gen test suite.

Provides the repo root, the built skin, and small netlist fixtures used
across the pipeline tests.

Netlist dialect selection
-------------------------
`convert.py` locks its dialect at import time (``IS_SPICE``, ``TO_SKIN_CONFIG``
are module-level constants read once from ``CONVERT_CONFIG["NETLIST_FORMAT"]``).
Tests that care which grammar is active request the ``convert`` fixture, which
swaps the config and reloads the module so the suite exercises BOTH dialects
(lcapy and spice) in a single pytest run. Set ``CIRD_NETLIST_FORMAT=lcapy|spice``
to restrict the run to one dialect.
"""

import importlib
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DATASET_DIR = REPO_ROOT / "dataset"

# --- netlist dialect plumbing -----------------------------------------------
NETLIST_FORMATS = ("lcapy", "spice")
_FORMAT_ENV = "CIRD_NETLIST_FORMAT"


def _selected_formats() -> tuple[str, ...]:
    """Dialects to parametrize over: both by default, or the env override."""
    requested = os.environ.get(_FORMAT_ENV, "").strip().lower()
    if not requested:
        return NETLIST_FORMATS
    if requested not in NETLIST_FORMATS:
        raise ValueError(
            f"{_FORMAT_ENV} must be one of {NETLIST_FORMATS}, got '{requested}'"
        )
    return (requested,)


def _reload_convert(netlist_format: str):
    """Reload ``circuit_data_gen.convert`` with ``netlist_format`` active.

    ``importlib.reload`` re-executes the module body into the SAME module
    ``__dict__`` object, so already-imported function objects (whose
    ``__globals__`` is that dict) transparently see the reloaded
    ``IS_SPICE`` / ``TO_SKIN_CONFIG``. Only names bound via
    ``from convert import X`` go stale — so tests must read dialect state off
    the returned module object rather than a module-level import.
    """
    import circuit_data_gen.configs.config as config
    import circuit_data_gen.convert as convert

    config.CONVERT_CONFIG["NETLIST_FORMAT"] = netlist_format
    return importlib.reload(convert)


@pytest.fixture(params=_selected_formats())
def convert(request):
    """The converter module, parametrized over the supported netlist dialects.

    Each test requesting this fixture runs once per dialect. Read dialect state
    as ``convert.IS_SPICE`` inside the test body (not at module import).
    """
    return _reload_convert(request.param)


@pytest.fixture
def convert_spice(convert):
    """Like ``convert``, but skips the lcapy dialect (SPICE-only behavior).

    Depending on ``convert`` guarantees the module was reloaded for the
    current dialect before the test body runs.
    """
    if not convert.IS_SPICE:
        pytest.skip("SPICE-only behavior")
    return convert


@pytest.fixture
def netlist_format():
    """The dialect currently loaded into ``convert`` (the configured default)."""
    from circuit_data_gen.convert import NETLIST_FORMAT

    return NETLIST_FORMAT

# A minimal, fully-connected netlist: voltage source + divider into ground.
# Value written without the 'dc' specifier so the fixture parses in BOTH
# dialects (SPICE 'dc 5' interleaved form is not yet supported).
CONNECTED_NETLIST = """V1 N1 0 5
R1 N1 N2 1k
R2 N2 0 1k
C1 N2 0 1u
"""

# Two loops sharing no net at all (different ground aliases would still merge,
# so the second loop uses floating nodes only).
ISOLATED_NETLIST = """V1 N1 0 5
R1 N1 N2 1k
V2 M1 M2 3
R2 M1 M2 2k
"""

# Two loops bridged through a wire (W N2 M2) — must count as connected.
WIRE_BRIDGED_NETLIST = """V1 N1 0 5
R1 N1 N2 1k
W1 N2 M1
R2 M1 0 2k
"""

NETLIST_WITH_DESCENDERS = """V1 N1 0 5
Ry N1 N2 1k
"""

NETLIST_MISSING_TAG = "V1 N1 0 5\nR1 N1 N2 1k\n"  # no <netlist> tags

WELL_FORMED_TAGGED = "<netlist>\nV1 N1 0 5\nR1 N1 N2 1k\n</netlist>"


@pytest.fixture(scope="session")
def skin_path() -> Path:
    """The dataset skin, resolved from the centralized config exactly like
    production code does (render_netlist/cli). Skips when the skin hasn't
    been built yet."""
    from circuit_data_gen.configs.config import get_config_path_value

    p = get_config_path_value("netlistsvg", "skin_path")
    if not p.exists():
        pytest.skip(
            "dataset skin not built; run `cirdg build_skin --all` first "
            "or change the config to point to a valid skin"
        )
    return p


@pytest.fixture(scope="session")
def classes_path() -> Path:
    """The centralized class registry, resolved from config. Skips when
    `cirdg build_skin --write-classes` hasn't been run yet."""
    from circuit_data_gen.configs.config import get_config_path_value

    p = get_config_path_value("netlistsvg", "annotation.classes_path")
    if not p.exists():
        pytest.skip(
            "classes.txt not generated; run `cirdg build_skin --all --write-classes`"
            "or change the config to point to a valid classes.txt"
        )
    return p


@pytest.fixture
def tmp_netlist(tmp_path: Path):
    """Factory: write netlist text to a temp file and return its path."""

    def _write(text: str, name: str = "test.net") -> Path:
        p = tmp_path / name
        p.write_text(text)
        return p

    return _write


@pytest.fixture(scope="session", autouse=True)
def _restore_default_netlist_format():
    """Leave the process in the configured default dialect after the run.

    The ``convert`` fixture swaps CONVERT_CONFIG["NETLIST_FORMAT"] per test;
    session-scoped fixtures (skin_path/classes_path) and any later code should
    see the repository default, not whichever dialect ran last. The configured
    default is captured BEFORE the first test mutates it.
    """
    import circuit_data_gen.configs.config as config

    original = config.CONVERT_CONFIG["NETLIST_FORMAT"]
    yield
    if config.CONVERT_CONFIG["NETLIST_FORMAT"] != original:
        _reload_convert(original)
