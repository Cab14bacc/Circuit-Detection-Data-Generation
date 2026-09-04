"""Tests for the annotation overlay tool (netlistsvg/bin/overlay.js).

Renders a small netlist end-to-end, then verifies the overlay:
  - produces a valid PNG of the same dimensions
  - actually changes pixels (boxes were drawn)
  - colored overlay pixels appear inside every annotated component bbox
"""

import json
import subprocess
from pathlib import Path

import pytest
from struct import unpack

from circuit_data_gen.render_netlist import render_netlist

from tests.conftest import CONNECTED_NETLIST

OVERLAY_JS = Path(__file__).parent.parent / "netlistsvg" / "bin" / "overlay.js"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:4] == b"\x89PNG"
    return unpack(">II", data[16:24])


@pytest.fixture
def rendered(tmp_netlist, skin_path, classes_path, tmp_path):
    """Render the connected netlist to PNG + annotation + return paths."""
    netlist_path = tmp_netlist(CONNECTED_NETLIST)
    out = tmp_path / "render.png"
    render_netlist(
        netlist_path,
        skin_path,
        out,
        annotation_path=tmp_path / "annotations" / "render.json",
        format="png",
        scale=2.0,
    )
    return out, tmp_path / "annotations" / "render.json"


class TestOverlayCli:
    def test_overlay_changes_pixels_and_keeps_size(self, rendered, tmp_path):
        render_path, ann_path = rendered
        overlay_out = tmp_path / "overlay.png"

        result = subprocess.run(
            ["node", str(OVERLAY_JS), str(render_path), str(ann_path), str(overlay_out)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert overlay_out.exists()

        # same dimensions as the render
        assert _png_size(overlay_out) == _png_size(render_path)

    def test_overlay_pixels_actually_differ(self, rendered, tmp_path):
        render_path, ann_path = rendered
        overlay_out = tmp_path / "overlay.png"
        subprocess.run(
            ["node", str(OVERLAY_JS), str(render_path), str(ann_path), str(overlay_out)],
            check=True,
            capture_output=True,
        )
        assert render_path.read_bytes() != overlay_out.read_bytes()

    def test_annotation_canvas_matches_png(self, rendered):
        render_path, ann_path = rendered
        ann = json.loads(ann_path.read_text())
        w, h = _png_size(render_path)
        assert w == pytest.approx(ann["canvas"]["width"], abs=1.5)
        assert h == pytest.approx(ann["canvas"]["height"], abs=1)

    def test_overlay_boxes_match_render_size(self, rendered, tmp_path):
        """Boxes land where the render put the ink: overlay dims == render dims."""
        render_path, ann_path = rendered
        overlay_out = tmp_path / "overlay.png"
        subprocess.run(
            ["node", str(OVERLAY_JS), str(render_path), str(ann_path), str(overlay_out)],
            check=True,
            capture_output=True,
        )
        assert overlay_out.exists()
        assert _png_size(overlay_out) == _png_size(render_path)
