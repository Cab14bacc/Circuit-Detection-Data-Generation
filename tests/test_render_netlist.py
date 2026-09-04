"""Integration tests for render_netlist — the convert -> yosys -> netlistsvg path.

These exercise the real renderer (node + the built netlistsvg), so they
double as checks that `npx tsc` output is in sync with the pipeline.
Skipped automatically when the built skin/classes are missing.
"""

import json

import pytest

from circuit_data_gen.render_netlist import render_netlist

from tests.conftest import CONNECTED_NETLIST


@pytest.fixture
def render_out(tmp_path):
    """Factory returning (out_file, annotation_path) in a temp dir."""

    def _make(stem: str = "render"):
        out = tmp_path / f"{stem}.png"
        ann = tmp_path / "annotations" / f"{stem}.json"
        return out, ann

    return _make


class TestRenderPng:
    def test_render_produces_png_and_annotation(self, tmp_netlist, skin_path, classes_path, render_out):
        netlist_path = tmp_netlist(CONNECTED_NETLIST)
        out, ann = render_out()
        json_path, parsed = render_netlist(
            netlist_path, skin_path, out, annotation_path=ann, format="png", scale=2.0
        )
        assert out.exists() and out.stat().st_size > 0
        assert ann.exists()
        data = json.loads(ann.read_text())
        assert {"image", "canvas", "classes", "components"} <= set(data.keys())
        assert {"width", "height"} <= set(data["canvas"].keys())
        # PNG magic
        assert out.read_bytes()[:4] == b"\x89PNG"

    def test_annotation_structure(self, tmp_netlist, skin_path, classes_path, render_out):
        netlist_path = tmp_netlist(CONNECTED_NETLIST)
        out, ann = render_out()
        render_netlist(netlist_path, skin_path, out, annotation_path=ann, format="png", scale=2.0)
        data = json.loads(ann.read_text())
        assert {"canvas", "components", "classes"} <= set(data.keys())
        keys = {c["key"] for c in data["components"]}
        assert {"V1", "R1", "R2", "C1"} <= keys
        for comp in data["components"]:
            assert {"x", "y", "w", "h"} <= set(comp["bbox"].keys())
            assert {"x", "y", "w", "h"} <= set(comp["bboxNorm"].keys())
            for lab in comp["labels"]:
                assert lab["attr"] in ("ref", "value")
                assert {"bbox", "bboxNorm"} <= set(lab.keys())
            for pin in comp["pins"]:
                assert {"x", "y", "xNorm", "yNorm"} <= set(pin.keys())

    def test_annotations_in_output_pixels(self, tmp_netlist, skin_path, classes_path, render_out):
        """Annotations are in OUTPUT pixels: bbox == bboxNorm * canvas.

        ELK's layout is not deterministic across node processes (same netlist
        yields different routings), so we can't compare two renders; instead
        we verify the coordinate contract inside a single annotation file.
        """
        netlist_path = tmp_netlist(CONNECTED_NETLIST)
        out, ann = render_out()
        render_netlist(netlist_path, skin_path, out, annotation_path=ann, format="png", scale=2.0)
        d = json.loads(ann.read_text())
        cw, ch = d["canvas"]["width"], d["canvas"]["height"]
        # the annotation canvas IS the output pixel canvas (scale already
        # applied): PNG dims must match it 1:1 (rounded)
        from struct import unpack

        png_w, png_h = unpack(">II", out.read_bytes()[16:24])
        assert png_w == pytest.approx(cw, abs=1.5)
        assert png_h == pytest.approx(ch, abs=1)
        for comp in d["components"]:
            assert comp["bbox"]["x"] == pytest.approx(comp["bboxNorm"]["x"] * cw, rel=1e-6)
            assert comp["bbox"]["y"] == pytest.approx(comp["bboxNorm"]["y"] * ch, rel=1e-6)
            assert comp["bbox"]["w"] == pytest.approx(comp["bboxNorm"]["w"] * cw, rel=1e-6)
            for lab in comp["labels"]:
                assert lab["bbox"]["x"] == pytest.approx(lab["bboxNorm"]["x"] * cw, rel=1e-6)
                assert lab["bbox"]["y"] == pytest.approx(lab["bboxNorm"]["y"] * ch, rel=1e-6)


class TestRenderSvg:
    def test_svg_output(self, tmp_netlist, skin_path, render_out):
        netlist_path = tmp_netlist(CONNECTED_NETLIST)
        out, _ = render_out()  # named render.png but format=svg re-extends it
        json_path, parsed = render_netlist(netlist_path, skin_path, out, format="svg")
        svg_path = out.with_suffix(".svg")
        assert svg_path.exists()
        text = svg_path.read_text()
        assert text.lstrip().startswith("<svg")
        assert "stroke-width: 0.8" in text  # wire stroke from the skin
        # yosys json is written next to the output
        assert json_path.exists()


class TestRenderFailure:
    def test_invalid_netlist_raises(self, tmp_netlist, skin_path, render_out):
        netlist_path = tmp_netlist("R1 N1 N2 1k\nGARBAGE LINE ###\n")
        out, ann = render_out()
        with pytest.raises(Exception):
            render_netlist(netlist_path, skin_path, out, annotation_path=ann, format="png")

    def test_unknown_format_raises(self, tmp_netlist, skin_path, render_out):
        netlist_path = tmp_netlist(CONNECTED_NETLIST)
        out, _ = render_out()
        with pytest.raises(ValueError, match="Unsupported format"):
            render_netlist(netlist_path, skin_path, out, format="gif")
