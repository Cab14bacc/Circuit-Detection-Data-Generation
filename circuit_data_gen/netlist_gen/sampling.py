"""Component sampling for netlist generation.

The pool of component types a netlist may be asked to use is built from
TO_SKIN_CONFIG: one ComponentSample per distinct
(prefix, model_type, kind, specifiers, skin). Specs that agree on all five
fields draw the same symbol and are merged into one sample (Q with 3 or 4
nodes, E linear or vol=); specs that differ in any of them stay apart (NPN
vs PNP, B V= vs B I=).

Each netlist gets a CircuitRequirements: a weighted draw from the pool, of
which REQUIRED_FRACTION must appear and the rest may, plus (when
simulating) anchor samples that make a valid DC operating point reachable,
and the V sources that F/H/W reference. Nothing outside the allowed set may
appear. The seed prompt and the validation describe samples with the same
ComponentSample.describe(), so both use one wording.
"""

import math
import random
from dataclasses import dataclass, field, replace

from ..configs.config import get_config_value
from ..parser import dialect
from ..parser.grammar import _render_spec_grammar
from ..parser.strict import _spec_simulatable

# how a prefix's weight (always 1) is split among its samples
WEIGHTINGS = ("by_skin", "uniform")


def _skin_key(skin_alias) -> tuple[str, ...]:
    """A spec's or element's skin_alias as a hashable, order-free tuple."""
    if skin_alias is None:
        return ()
    if isinstance(skin_alias, str):
        return (skin_alias,)
    return tuple(sorted(skin_alias))


def _is_sampleable(spec_or_element: dict) -> bool:
    """Only drawn components are sampled: not the lcapy wire (no skin), not
    parse-only specs."""
    return spec_or_element.get("skin_alias") is not None and not spec_or_element.get("parse_only", False)


@dataclass(frozen=True)
class ComponentSample:
    """One component type a netlist can be asked to use. `weight` is the
    sampling weight and `simulatable_only` restricts specs()/forms() to the
    specs ngspice can simulate (a sample drawn for simulation, e.g. C by
    value but not C by charge); neither is part of the identity (see key())."""

    prefix: str
    model_type: tuple[str, ...] = ()
    kind: tuple[str, ...] = ()
    specifiers: tuple[str, ...] = ()
    skin: tuple[str, ...] = ()
    weight: float = field(default=1.0, compare=False)
    simulatable_only: bool = field(default=False, compare=False)

    def key(self) -> tuple:
        return (self.prefix, self.model_type, self.kind, self.specifiers, self.skin)

    @classmethod
    def from_spec(cls, prefix: str, spec: dict, simulatable_only: bool = False) -> "ComponentSample":
        return cls(
            prefix=prefix,
            model_type=tuple(m.upper() for m in spec.get("model_type", [])),
            kind=tuple(spec.get("kind", [])),
            specifiers=tuple(spec.get("specifiers", {})),
            skin=_skin_key(spec.get("skin_alias")),
            simulatable_only=simulatable_only,
        )

    @classmethod
    def from_element(cls, element: dict) -> "ComponentSample":
        """The sample of a parsed element. Elements record their matched
        spec's model_type, kind, specifiers and skin_alias, so this is the
        key of the spec that parsed the line."""
        return cls(
            prefix=element["prefix"],
            model_type=tuple(m.upper() for m in element.get("model_type", [])),
            kind=tuple(element.get("kind", [])),
            specifiers=tuple(element.get("specifiers", [])),
            skin=_skin_key(element.get("skin_alias")),
        )

    def specs(self) -> list[dict]:
        """The TO_SKIN_CONFIG specs this sample merges (only the simulatable
        ones when `simulatable_only`)."""
        return [
            spec
            for spec in dialect.TO_SKIN_CONFIG.get(self.prefix, [])
            if ComponentSample.from_spec(self.prefix, spec).key() == self.key()
            and (not self.simulatable_only or _spec_simulatable(spec))
        ]

    def forms(self) -> list[str]:
        """The grammar line of every merged spec (duplicates dropped)."""
        if dialect.IS_SPICE and self.prefix in dialect.EXPLICIT_SPICE_GENERICS:
            # generic specs declare no ports: the pin count comes from a definition
            if self.prefix == dialect.SUBCKT_PREFIX:
                return [
                    f"{self.prefix}name N1 ... Nn SUBNAM (with a .subckt SUBNAM N1 ... Nn ... .ends block)"
                ]
            return [f"{self.prefix}name N1 ... Nn <definition name>"]
        forms: list[str] = []
        for spec in self.specs():
            form = _render_spec_grammar(self.prefix, spec)
            if form not in forms:
                forms.append(form)
        return forms

    def describe(self) -> str:
        """One line naming this component type, shared by the seed prompt and
        the validation messages, e.g.
        `Q` with a `.model` card of TYPE PNP, with kind keyword `off`; written
        as `Qname NC NB NE mname off` or `Qname NC NB NE NS mname off`
        """
        specs = dialect.TO_SKIN_CONFIG.get(self.prefix, [])
        parts: list[str] = []
        if self.model_type:
            parts.append("with a `.model` card of TYPE " + " or ".join(self.model_type))
        elif any(spec.get("model_type") for spec in specs):
            parts.append("without a model name")
        if self.kind:
            parts.append("with kind keyword " + ", ".join(f"`{k}`" for k in self.kind))
        elif any(spec.get("kind") for spec in specs):
            parts.append("without a kind keyword")
        if self.specifiers:
            parts.append("with specifier(s) " + ", ".join(f"`{s}`" for s in self.specifiers))
        text = f"`{self.prefix}`" + (" " + ", ".join(parts) if parts else "")
        forms = self.forms()
        if forms:
            text += "; written as " + " or ".join(f"`{form}`" for form in forms)
        return text


@dataclass
class CircuitRequirements:
    """What one netlist must contain: exactly `num_components` components,
    each type in `required` at least once, and no type outside `allowed`
    (which includes `required`)."""

    num_components: int = 0
    required: list[ComponentSample] = field(default_factory=list)
    allowed: list[ComponentSample] = field(default_factory=list)


def samples_in(parsed_netlist: dict) -> list[ComponentSample]:
    """The distinct samples of a parsed netlist's drawn components, in
    netlist order. Undefined generics (prefix None) are skipped; they are
    reported by validate.undefined_components."""
    samples: dict[tuple, ComponentSample] = {}
    for element in parsed_netlist.values():
        if element.get("prefix") is None or not _is_sampleable(element):
            continue
        sample = ComponentSample.from_element(element)
        samples.setdefault(sample.key(), sample)
    return list(samples.values())


def _weigh(samples: list[ComponentSample], weighting: str) -> list[ComponentSample]:
    """Split a prefix's weight of 1 among its samples: `uniform` gives each
    an equal share, `by_skin` gives each distinct symbol an equal share and
    splits it among that symbol's samples."""
    if weighting == "uniform":
        return [replace(s, weight=1 / len(samples)) for s in samples]
    # count how many samples uses each skin
    per_skin: dict[tuple, int] = {}
    for s in samples:
        per_skin[s.skin] = per_skin.get(s.skin, 0) + 1
    return [replace(s, weight=1 / len(per_skin) / per_skin[s.skin]) for s in samples]


def build_pool(simulate: bool = False, weighting: str | None = None) -> list[ComponentSample]:
    """Every sample TO_SKIN_CONFIG offers, weighted so that each prefix sums
    to 1. With `simulate`, specs ngspice cannot simulate are left out
    (before weighting). `weighting` defaults to sampling.weighting."""
    weighting = weighting or str(get_config_value("sampling", "weighting"))
    if weighting not in WEIGHTINGS:
        raise ValueError(f"sampling weighting must be one of {WEIGHTINGS}, got '{weighting}'")

    by_prefix: dict[str, dict[tuple, ComponentSample]] = {}
    for prefix, specs in dialect.TO_SKIN_CONFIG.items():
        for spec in specs:
            if not _is_sampleable(spec) or (simulate and not _spec_simulatable(spec)):
                continue
            sample = ComponentSample.from_spec(prefix, spec, simulatable_only=simulate)
            by_prefix.setdefault(prefix, {}).setdefault(sample.key(), sample)
    return [s for samples in by_prefix.values() for s in _weigh(list(samples.values()), weighting)]


def _weighted_order(rng: random.Random, samples: list[ComponentSample]) -> list[ComponentSample]:
    """The samples in a random order biased by weight (Efraimidis-Spirakis:
    sort by u^(1/w)); a prefix of it is a weighted draw without replacement."""
    return sorted(samples, key=lambda s: rng.random() ** (1.0 / s.weight), reverse=True)


def _weighted_choice(rng: random.Random, samples: list[ComponentSample]) -> ComponentSample:
    return rng.choices(samples, weights=[s.weight for s in samples])[0]


def _matches(sample: ComponentSample, matcher: dict) -> bool:
    """Whether a sample fits an anchor matcher: every field the matcher
    names (prefix, model_type, kind, specifiers) must equal the sample's,
    case-insensitively; an empty list means "none"."""
    if "prefix" in matcher and matcher["prefix"] != sample.prefix:
        return False
    for name in ("model_type", "kind", "specifiers"):
        if name in matcher:
            expected = tuple(x.upper() for x in matcher[name])
            if expected != tuple(x.upper() for x in getattr(sample, name)):
                return False
    return True


def _fill_anchors(
    rng: random.Random, pool: list[ComponentSample], allowed: list[ComponentSample], anchors: list[dict]
) -> list[ComponentSample]:
    """For every anchor group ({"any_of": [matcher, ...]}) that no allowed
    sample fits yet, draw one fitting pool sample by weight."""
    added: list[ComponentSample] = []
    for group in anchors:
        matchers = group["any_of"]
        if any(_matches(s, m) for s in allowed + added for m in matchers):
            continue
        candidates = [s for s in pool if any(_matches(s, m) for m in matchers)]
        if candidates:
            added.append(_weighted_choice(rng, candidates))
    return added


def _fill_references(
    rng: random.Random, pool: list[ComponentSample], allowed: list[ComponentSample]
) -> list[ComponentSample]:
    """Elements whose spec references another element (F/H/W name a V
    source through an "is_reference" slot) cannot be written without one:
    for each referenced prefix no allowed sample has, draw one by weight."""
    added: list[ComponentSample] = []
    for sample in allowed:
        for spec in sample.specs():
            for entry in spec.get("args_to_values", {}).values():
                reference = entry.get("is_reference")
                if reference is None or any(s.prefix == reference for s in allowed + added):
                    continue
                candidates = [s for s in pool if s.prefix == reference]
                if candidates:
                    added.append(_weighted_choice(rng, candidates))
    return added


def sample_requirements(
    rng: random.Random,
    pool: list[ComponentSample],
    num_components_range: tuple[int, int],
    simulate: bool = False,
    required_fraction: float | None = None,
) -> CircuitRequirements:
    """Draw the requirements of one netlist.

    The component count is drawn from `num_components_range`, then between
    half of it and all of it (capped by the pool) distinct samples by weight.
    The first ceil(required_fraction * k) are required, the rest allowed.
    Then, only allowed: with `simulate`, one sample per unmet
    SIMULATION_ANCHORS group, and the elements the drawn ones reference.
    `required_fraction` defaults to sampling.required_fraction.
    """
    if required_fraction is None:
        required_fraction = float(get_config_value("sampling", "required_fraction"))
    if not 0.0 <= required_fraction <= 1.0:
        raise ValueError(f"required_fraction must be within [0, 1], got {required_fraction}")

    num_components = rng.randint(num_components_range[0], num_components_range[1])
    subset_max = min(len(pool), num_components)
    num_drawn = rng.randint(min(num_components // 2, subset_max), subset_max)
    drawn = _weighted_order(rng, pool)[:num_drawn]
    rng.shuffle(drawn)
    required = drawn[: math.ceil(required_fraction * num_drawn)]

    allowed = list(drawn)
    if simulate:
        allowed += _fill_anchors(rng, pool, allowed, dialect.SIMULATION_ANCHORS)
    allowed += _fill_references(rng, pool, allowed)
    return CircuitRequirements(num_components=num_components, required=required, allowed=allowed)
