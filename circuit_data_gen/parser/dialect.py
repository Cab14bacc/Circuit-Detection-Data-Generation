"""Netlist dialect selection and the constants shared by the parser modules.

The dialect ("lcapy" | "spice") is read once from CONVERT_CONFIG at import.
Helper modules (tokens, strict, grammar) read the dialect-dependent values as
``dialect.IS_SPICE`` / ``dialect.TO_SKIN_CONFIG`` at call time, so reloading
this module (as the test harness does to switch dialect) updates them too.
"""

from ..configs.config import get_config_value

NETLIST_FORMAT = str(get_config_value("convert", "netlist_format")).lower()
if NETLIST_FORMAT not in ("lcapy", "spice"):
    raise ValueError(f"CONVERT_CONFIG.NETLIST_FORMAT must be 'lcapy' or 'spice', got '{NETLIST_FORMAT}'")
IS_SPICE = NETLIST_FORMAT == "spice"
# SPICE only: tolerate models not declared in-file (external .lib models).
# When False, model-typed components must reference an in-file .model.
ALLOW_UNKNOWN_MODELS = bool(get_config_value("convert", "allow_unknown_models"))
# SPICE only: default for `strict` in _parse_netlist / to_yosys_json. Strict
# parsing rejects what ngspice would misread or refuse (see _strict_violations).
STRICT_PARSING = bool(get_config_value("convert", "strict_parsing"))
if IS_SPICE:
    TO_SKIN_CONFIG = get_config_value("convert", "convert_spice_config_path", "TO_SKIN_CONFIG")
    EXPRESSION_CONSTANTS = {
        c.lower() for c in get_config_value("convert", "convert_spice_config_path", "EXPRESSION_CONSTANTS")
    }
else:
    TO_SKIN_CONFIG = get_config_value("convert", "convert_lcapy_config_path", "TO_SKIN_CONFIG")
    EXPRESSION_CONSTANTS = set()

WIRE = "W"
SUBCKT_PREFIX = "X"
EXPLICIT_SPICE_GENERICS = [SUBCKT_PREFIX, "A", "U", "P", "N"]
# SPICE: coupled-inductor lines (K...) are dropped during preprocessing —
# their args are inductor references, not nodes, so there is nothing to draw.
COUPLED_INDUCTOR_PREFIXES = "Kk"
# lcapy uses "0"/"GND"; SPICE additionally allows lowercase "gnd"
GROUND_NAMES = {"0", "GND", "gnd"}
