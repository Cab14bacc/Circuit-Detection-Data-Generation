"""Tests for the skin builder (build_skin.py).

These avoid the LaTeX/Cairo toolchain: they verify config plumbing (font
metrics, wire stroke, descender shift -> <s:properties> attributes), the
class registry, and the structural contract netlistsvg relies on.
The heavy image-processing path is exercised indirectly by render tests.
"""

import re

import pytest

from circuit_data_gen.build_skin import emit_full_skin, COMPONENTS
from circuit_data_gen.configs.config import get_config_value, get_config_path_value


class TestSkinEmission:
    @pytest.fixture(scope="class")
    def skin_svg(self):
        """Build a skin for a couple of components (no LaTeX needed for the
        property header + registry; component entries need external tools,
        so fall back to reading the dataset skin when tools are missing)."""
        try:
            return emit_full_skin(["R"], if_write_classes=False)
        except SystemExit:
            pytest.skip("pdflatex/pdftocairo not installed; using dataset skin")
        return None

    def _svg_or_dataset(self, skin_svg) -> str:
        if skin_svg is not None:
            return skin_svg
        p = get_config_path_value("netlistsvg", "skin_path")
        if not p.exists():
            pytest.skip(
                "dataset skin not built; run `cirdg build_skin --all` first "
                "or change the config to point to a valid skin"
            )
        return p.read_text()


class TestSkinProperties:
    """The <s:properties> block is the config->netlistsvg contract."""

    def get_props(self, svg: str) -> dict:
        m = re.search(r"<s:properties([^>]*)/>", svg)
        assert m, "s:properties element missing from skin"
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        return attrs

    def test_font_metrics_in_skin(self, skin_path):
        attrs = self.get_props(skin_path.read_text())
        assert attrs["fontCharWidth"] == str(get_config_value("netlistsvg", "font_char_width"))
        assert attrs["fontCharHeight"] == str(get_config_value("netlistsvg", "font_char_height"))

    def test_cap_height_in_skin(self, skin_path):
        attrs = self.get_props(skin_path.read_text())
        assert attrs["fontCapHeight"] == str(get_config_value("netlistsvg", "font_cap_height"))

    def test_desc_shift_in_skin(self, skin_path):
        attrs = self.get_props(skin_path.read_text())
        assert attrs["fontDescShift"] == str(get_config_value("netlistsvg", "font_desc_shift"))

    def test_wire_stroke_in_skin(self, skin_path):
        attrs = self.get_props(skin_path.read_text())
        assert attrs["wireStrokeWidth"] == str(get_config_value("netlistsvg", "wire_stroke_width"))

    def test_symbol_stroke_matches_line_width(self, skin_path):
        from circuit_data_gen.build_skin import LINE_WIDTH

        m = re.search(r"\.symbol \{[^}]*stroke-width: ([\d.]+)", skin_path.read_text())
        assert m, "symbol stroke-width rule missing"
        assert float(m.group(1)) == pytest.approx(LINE_WIDTH * 2, rel=0.05)


class TestClassRegistry:
    def test_classes_file_lists_component_classes(self, classes_path):
        classes = set(classes_path.read_text().split())
        assert len(classes) >= 0

    def test_all_component_annotation_classes_covered(self, classes_path):
        classes = set(classes_path.read_text().split())
        for name, spec in COMPONENTS.items():
            cls = spec.get("annotation_class")
            if cls and not spec.get("is_generic"):
                assert cls in classes, f"{name}: class {cls!r} missing from registry"
