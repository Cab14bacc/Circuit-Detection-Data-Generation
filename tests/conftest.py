"""Shared fixtures and helpers for the circuit_data_gen test suite.

Provides the repo root, the built skin, and small netlist fixtures used
across the pipeline tests.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DATASET_DIR = REPO_ROOT / "dataset"

# A minimal, fully-connected netlist: voltage source + divider into ground.
CONNECTED_NETLIST = """V1 N1 0 dc 5
R1 N1 N2 1k
R2 N2 0 1k
C1 N2 0 1u
"""

# Two loops sharing no net at all (different ground aliases would still merge,
# so the second loop uses floating nodes only).
ISOLATED_NETLIST = """V1 N1 0 dc 5
R1 N1 N2 1k
V2 M1 M2 dc 3
R2 M1 M2 2k
"""

# Two loops bridged through a wire (W N2 M2) — must count as connected.
WIRE_BRIDGED_NETLIST = """V1 N1 0 dc 5
R1 N1 N2 1k
W1 N2 M1
R2 M1 0 2k
"""

NETLIST_WITH_DESCENDERS = """V1 N1 0 dc 5
Ry N1 N2 1k
"""

NETLIST_MISSING_TAG = "V1 N1 0 dc 5\nR1 N1 N2 1k\n"  # no <netlist> tags

WELL_FORMED_TAGGED = "<netlist>\nV1 N1 0 dc 5\nR1 N1 N2 1k\n</netlist>"


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
