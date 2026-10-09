"""The convert config as the parser uses it, and the constants shared by
the parser modules.

The tables of the SPICE convert config (TO_SKIN_CONFIG, MODEL_CONFIG,
EXPRESSION_CONSTANTS) are loaded once at import and normalized to lower
case, since SPICE ignores letter case: element letters, slot aliases,
specifier keys, kind keywords, model types (in specs and as MODEL_CONFIG
keys), references and the expression constants. Slot keys keep their
spelling (`"AC"`, `"phase"`). The
tables are changed in place, so modules that read them through `macro`
see the same normalized objects.
"""

from ..configs.config import get_config_value
from functools import cache

# SPICE: default for `strict` in _parse_netlist / to_yosys_json. Strict
# parsing rejects what ngspice would misread or refuse (see _strict_violations).
STRICT_PARSING = bool(get_config_value("convert", "strict_parsing"))
temp_skin_config = get_config_value("convert", "convert_spice_config_path", "TO_SKIN_CONFIG")
TO_SKIN_CONFIG = {}
for skin_key, skin_specs in temp_skin_config.items():
    TO_SKIN_CONFIG[skin_key.lower()] = skin_specs
    for skin_spec in skin_specs:
        if "args_to_values" in skin_spec:
            for arg_key, arg_spec in skin_spec["args_to_values"].items():
                if "alias" in arg_spec:
                    arg_spec["alias"] = [a.lower() for a in arg_spec["alias"]]

                if "is_reference" in arg_spec:
                    arg_spec["is_reference"] = arg_spec["is_reference"].lower()

            if "model_type" in skin_spec:
                skin_spec["model_type"] = (
                    [t.lower() for t in skin_spec["model_type"]]
                    if isinstance(skin_spec["model_type"], list)
                    else {k.lower(): v for k, v in skin_spec["model_type"].items()}
                )

            if "kind" in skin_spec:
                skin_spec["kind"] = [k.lower() for k in skin_spec["kind"]]

            if "specifiers" in skin_spec:
                skin_spec["specifiers"] = {k.lower(): v for k, v in skin_spec["specifiers"].items()}


EXPRESSION_CONSTANTS = {
    c.lower() for c in get_config_value("convert", "convert_spice_config_path", "EXPRESSION_CONSTANTS")
}
# .model TYPE (lowered) -> [{"args_to_values": ...}, ...]
# d1 | d2, is merge where d2 takes precedence.
# lowers model type, and model args alias
temp_model_config = get_config_value("convert", "convert_spice_config_path", "MODEL_CONFIG")
MODEL_CONFIG = {}
for model_type, model_specs in temp_model_config.items():
    MODEL_CONFIG[model_type.lower()] = model_specs
    for model_spec in model_specs:
        if "args_to_values" in model_spec:
            for arg_key, arg_spec in model_spec["args_to_values"].items():
                if "alias" in arg_spec:
                    arg_spec["alias"] = [a.lower() for a in arg_spec["alias"]]


SUBCKT_PREFIX = "X".lower()
EXPLICIT_SPICE_GENERICS = {p.lower() for p in [SUBCKT_PREFIX, "A", "U", "P", "N"]}
# SPICE: coupled-inductor lines (K...) are dropped during preprocessing —
# their args are inductor references, not nodes, so there is nothing to draw.
COUPLED_INDUCTOR_PREFIXES = "K".lower()
GROUND_NAMES = {"0", "gnd"}


@cache
def _get_all_kind_keywords():
    """Return a set of all kind keywords across all component specs in TO_SKIN_CONFIG."""
    all_kinds = set()
    for prefix, component_specs in TO_SKIN_CONFIG.items():
        for spec in component_specs:
            kinds = [k.lower() for k in spec.get("kind", [])]
            all_kinds.update(kinds)
    return all_kinds


@cache
def _get_spice_directive_prefixes():
    """SPICE dot-directive prefixes that are filtered before parsing.
    Element lines never start with '.'."""
    return {
        ".model",
        ".lib",
        ".include",
        ".inc",
        ".tran",
        ".ac",
        ".dc",
        ".op",
        ".four",
        ".fourier",
        ".param",
        ".params",
        ".step",
        ".temp",
        ".ic",
        ".nodeset",
        ".measure",
        ".meas",
        ".noise",
        ".save",
        ".probe",
        ".options",
        ".option",
        ".plot",
        ".print",
        ".pz",
        ".sens",
        ".tf",
        ".disto",
        ".global",
        ".func",
        ".if",
        ".elseif",
        ".else",
        ".endif",
        ".endc",
        ".backanno",
        ".end",
        ".wave",
        ".net",
        ".title",
        ".width",
    }
