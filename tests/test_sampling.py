"""Tests for component sampling (netlist_gen/sampling.py): the pool of
component samples, their weights, the per-netlist draw with its required
and allowed samples, the simulation anchors and references, and the seed
prompt built from them. No LLM involved.

Tests request ``convert`` (both dialects) or ``convert_spice`` so the
dialect is loaded before sampling reads it.
"""

import math
import random
from collections import Counter

import pytest

from circuit_data_gen.netlist_gen import sampling
from circuit_data_gen.netlist_gen.helper import generate_seed_prompt
from circuit_data_gen.netlist_gen.sampling import (
    ComponentSample,
    build_pool,
    sample_requirements,
    samples_in,
)


def _pool_by_prefix(pool):
    by_prefix = {}
    for sample in pool:
        by_prefix.setdefault(sample.prefix, []).append(sample)
    return by_prefix


def _sample(pool, prefix, **fields):
    """The one pool sample of `prefix` whose given fields match."""
    matches = [
        s
        for s in pool
        if s.prefix == prefix and all(getattr(s, name) == value for name, value in fields.items())
    ]
    assert len(matches) == 1, (prefix, fields, matches)
    return matches[0]


class TestPool:
    @pytest.mark.parametrize("simulate", [False, True])
    def test_no_duplicate_keys(self, convert, simulate):
        keys = [s.key() for s in build_pool(simulate=simulate)]
        assert len(keys) == len(set(keys))

    def test_spice_current_switch_is_sampled(self, convert_spice):
        # W is the lcapy wire, but a switch in SPICE: it must be in the pool
        assert any(s.prefix == "W" for s in build_pool())

    def test_lcapy_wire_is_not_sampled(self, convert):
        if convert.IS_SPICE:
            pytest.skip("lcapy-only behavior")
        assert not any(s.prefix == "W" for s in build_pool())

    def test_simulation_drops_unsimulatable(self, convert_spice):
        full = {s.key() for s in build_pool()}
        sim = build_pool(simulate=True)
        assert {s.key() for s in sim} < full
        assert not any("LPNP" in s.model_type for s in sim)
        assert not {s.prefix for s in sim} & {"N", "P", "A", "U"}
        assert any(s.prefix == "X" for s in sim)
        # C by charge cannot be simulated (ngspice 32), C by value can
        c_forms = {form for s in sim if s.prefix == "C" for form in s.forms()}
        assert not any("Q=" in form for form in c_forms)
        assert any(form.startswith("Cname N+ N- <C>") for form in c_forms)

    def test_symbols_split_samples(self, convert_spice):
        pool = build_pool()
        # B V= and B I= share every field but the symbol
        assert {s.skin for s in pool if s.prefix == "B"} == {("v",), ("i",)}
        # NPN and PNP differ in model type and symbol
        assert {s.model_type for s in pool if s.prefix == "Q" and not s.kind} >= {("NPN",), ("PNP",)}

    def test_specs_with_same_fields_merge(self, convert_spice):
        # the 3- and 4-node NPN specs draw the same symbol: one sample, two forms
        npn = _sample(build_pool(), "Q", model_type=("NPN",), kind=())
        assert len(npn.specs()) == 2
        assert npn.forms() == ["Qname NC NB NE mname", "Qname NC NB NE NS mname"]


class TestWeights:
    @pytest.mark.parametrize("weighting", ["by_skin", "uniform"])
    @pytest.mark.parametrize("simulate", [False, True])
    def test_each_prefix_sums_to_one(self, convert, weighting, simulate):
        for prefix, samples in _pool_by_prefix(build_pool(simulate, weighting)).items():
            assert math.isclose(sum(s.weight for s in samples), 1.0), prefix

    def test_by_skin_splits_per_symbol(self, convert_spice):
        # S: sw_no is drawn by SW and SW OFF, sw_nc by SW ON alone
        pool = build_pool(weighting="by_skin")
        assert _sample(pool, "S", kind=("ON",)).weight == pytest.approx(0.5)
        assert _sample(pool, "S", kind=("OFF",)).weight == pytest.approx(0.25)
        assert _sample(pool, "S", kind=()).weight == pytest.approx(0.25)

    def test_uniform_splits_per_sample(self, convert_spice):
        pool = build_pool(weighting="uniform")
        assert all(s.weight == pytest.approx(1 / 3) for s in pool if s.prefix == "S")

    def test_unknown_weighting_rejected(self, convert):
        with pytest.raises(ValueError, match="weighting"):
            build_pool(weighting="by_prefix")

    def test_weighted_order_follows_weights(self, convert):
        heavy = ComponentSample("R", weight=0.75)
        light = ComponentSample("C", weight=0.25)
        rng = random.Random(0)
        firsts = Counter(sampling._weighted_order(rng, [heavy, light])[0].prefix for _ in range(20000))
        assert firsts["R"] / 20000 == pytest.approx(0.75, abs=0.02)


class TestDraw:
    def test_reproducible(self, convert):
        pool = build_pool()

        def draw():
            reqs = sample_requirements(random.Random("seed"), pool, (5, 20))
            return reqs.num_components, [s.key() for s in reqs.required], [s.key() for s in reqs.allowed]

        assert draw() == draw()

    @pytest.mark.parametrize("seed", range(20))
    def test_sizes_and_uniqueness(self, convert, seed):
        reqs = sample_requirements(random.Random(seed), build_pool(), (5, 20), required_fraction=1.0)
        assert 5 <= reqs.num_components <= 20
        keys = [s.key() for s in reqs.allowed]
        assert len(keys) == len(set(keys))
        # without simulation nothing is added: all drawn, all required
        assert keys == [s.key() for s in reqs.required] or any(
            s.prefix in ("F", "H", "W") for s in reqs.allowed
        )
        assert reqs.num_components // 2 <= len(reqs.required) <= reqs.num_components

    @pytest.mark.parametrize("fraction", [0.0, 0.5, 1.0])
    def test_required_fraction(self, convert, fraction):
        pool = build_pool()
        reqs = sample_requirements(random.Random(3), pool, (10, 10), required_fraction=fraction)
        drawn = sample_requirements(random.Random(3), pool, (10, 10), required_fraction=1.0).required
        assert [s.key() for s in reqs.required] == [
            s.key() for s in drawn[: math.ceil(fraction * len(drawn))]
        ]
        assert {s.key() for s in reqs.required} <= {s.key() for s in reqs.allowed}

    def test_required_fraction_out_of_range(self, convert):
        with pytest.raises(ValueError, match="required_fraction"):
            sample_requirements(random.Random(0), build_pool(), (5, 5), required_fraction=1.5)


class TestAnchors:
    def test_unmet_groups_are_filled(self, convert_spice):
        pool = build_pool(simulate=True)
        capacitor = _sample(pool, "C", model_type=())
        added = sampling._fill_anchors(
            random.Random(0), pool, [capacitor], sampling.dialect.SIMULATION_ANCHORS
        )
        assert len(added) == 2
        assert added[0].prefix in ("V", "I")
        assert added[1].prefix == "R" and added[1].model_type == ()

    def test_met_groups_add_nothing(self, convert_spice):
        pool = build_pool(simulate=True)
        current = _sample(pool, "I", specifiers=("DC",))
        resistor = _sample(pool, "R", model_type=())
        added = sampling._fill_anchors(
            random.Random(0), pool, [current, resistor], sampling.dialect.SIMULATION_ANCHORS
        )
        assert added == []

    def test_model_resistor_does_not_meet_the_resistor_group(self, convert_spice):
        pool = build_pool(simulate=True)
        model_r = _sample(pool, "R", model_type=("R", "RES"))
        added = sampling._fill_anchors(random.Random(0), pool, [model_r], sampling.dialect.SIMULATION_ANCHORS)
        assert [s.prefix for s in added if s.prefix == "R"] == ["R"]

    @pytest.mark.parametrize("seed", range(30))
    def test_simulated_draw_is_always_anchored_and_anchors_are_optional(self, convert_spice, seed):
        reqs = sample_requirements(random.Random(seed), build_pool(simulate=True), (5, 20), simulate=True)
        assert any(s.prefix in ("V", "I") for s in reqs.allowed)
        assert any(s.prefix == "R" and s.model_type == () for s in reqs.allowed)
        # everything beyond the (fully required) draw is an anchor or a reference
        required = {s.key() for s in reqs.required}
        extras = [s for s in reqs.allowed if s.key() not in required]
        assert all(s.prefix in ("V", "I", "R") for s in extras), extras

    def test_no_anchors_without_simulation(self, convert_spice):
        pool = build_pool()
        for seed in range(30):
            reqs = sample_requirements(
                random.Random(seed), pool, (5, 20), simulate=False, required_fraction=1.0
            )
            required = {s.key() for s in reqs.required}
            # only references may be added
            assert all(s.prefix == "V" for s in reqs.allowed if s.key() not in required)


class TestReferences:
    @pytest.mark.parametrize("prefix", ["F", "H", "W"])
    def test_controlled_elements_bring_a_voltage_source(self, convert_spice, prefix):
        pool = build_pool()
        element = next(s for s in pool if s.prefix == prefix)
        added = sampling._fill_references(random.Random(0), pool, [element])
        assert [s.prefix for s in added] == ["V"]

    def test_existing_voltage_source_is_enough(self, convert_spice):
        pool = build_pool()
        element = next(s for s in pool if s.prefix == "F")
        source = _sample(pool, "V", specifiers=("DC",))
        assert sampling._fill_references(random.Random(0), pool, [element, source]) == []


class TestFromElement:
    @pytest.mark.parametrize(
        "netlist, prefix, fields",
        [
            ("R1 a 0 1k\n", "R", {"model_type": ()}),
            ("R1 a 0 Rm\n.model Rm R\n", "R", {"model_type": ("R", "RES")}),
            ("V1 a 0 5\n", "V", {"specifiers": ("DC",)}),
            ("V1 a 0 5 AC 1\n", "V", {"specifiers": ("DC", "AC")}),
            ("Q1 c b e 0 Qp off\n.model Qp PNP\n", "Q", {"model_type": ("PNP",), "kind": ("off",)}),
            ("S1 a b c d Sm ON\n.model Sm SW\n", "S", {"kind": ("ON",)}),
            ("W1 a b V1 Wm OFF\n.model Wm CSW\n", "W", {"kind": ("OFF",)}),
            ("B1 a 0 V=1\n", "B", {"skin": ("v",)}),
            ("B1 a 0 I=1m\n", "B", {"skin": ("i",)}),
            ("E1 a 0 vol='2*v(b)'\n", "E", {}),
            ("C1 a 0 Q=2n\n", "C", {"model_type": ()}),
            ("X1 a b amp\n.subckt amp p n\n.ends\n", "X", {}),
        ],
    )
    def test_parsed_element_maps_to_its_pool_sample(self, convert_spice, netlist, prefix, fields):
        parsed = convert_spice._parse_netlist(netlist, strict=False, simulate=False)
        (sample,) = [s for s in samples_in(parsed) if s.prefix == prefix]
        assert sample.key() == _sample(build_pool(), prefix, **fields).key()

    def test_samples_in_deduplicates(self, convert):
        parsed = convert._parse_netlist("V1 a 0 5\nR1 a b 1k\nR2 b 0 1k\n")
        assert [s.prefix for s in samples_in(parsed)] == ["V", "R"]


class TestDescribeAndSeedPrompt:
    def test_describe_names_type_kind_and_forms(self, convert_spice):
        sample = _sample(build_pool(), "Q", model_type=("PNP",), kind=("off",))
        assert sample.describe() == (
            "`Q` with a `.model` card of TYPE PNP, with kind keyword `off`; written as "
            "`Qname NC NB NE mname off` or `Qname NC NB NE NS mname off`"
        )

    def test_describe_marks_modelless_forms(self, convert_spice):
        plain = _sample(build_pool(), "R", model_type=())
        assert plain.describe().startswith("`R` without a model name; written as `Rname N+ N- <r>`")

    def test_seed_prompt_lists_required_and_optional(self, convert):
        pool = build_pool()
        reqs = sample_requirements(random.Random(1), pool, (10, 10), required_fraction=0.5)
        prompt = generate_seed_prompt(1, [reqs])
        assert "{gen_count}" not in prompt and "Contraints" not in prompt
        assert "You have to generate 1 netlist(s) in total." in prompt
        assert "exact number of components for this instance: 10" in prompt
        assert "You MUST use each of the following components at least once:" in prompt
        assert "You MAY also use the following components:" in prompt
        for sample in reqs.allowed:
            assert f"- {sample.describe()}" in prompt
        assert "No other components are allowed." in prompt

    def test_seed_prompt_without_optional_section(self, convert):
        reqs = sample_requirements(random.Random(1), build_pool(), (10, 10), required_fraction=1.0)
        prompt = generate_seed_prompt(1, [reqs])
        if len(reqs.allowed) == len(reqs.required):
            assert "You MAY also use" not in prompt
