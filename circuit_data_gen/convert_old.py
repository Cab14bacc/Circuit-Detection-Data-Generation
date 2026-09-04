"""SPICE → Yosys JSON converter for netlistsvg's lcapy skin.

Handles Lcapy SPICE netlists with or without layout hints. Maps component
prefixes to skin cells, merges nodes via W wires, emits a gnd cell for
ground-touching nets, and recognises Vcc/Vee rails.

Uses the custom lcapy.svg skin (co-located with this file) which mirrors
Lcapy's Circuitikz visual vocabulary.
"""

from __future__ import annotations
from .configs.config import get_config_value, get_logger

logger = get_logger(__name__)

TO_SKIN_CONFIG = get_config_value("convert", "convert_config_path", "LCAPY_TO_SKIN")

GROUND_NAMES = {"0", "GND"}
# Kind keywords that appear inline in E-family opamp lines and must not be
# treated as node names (E1 Nout Nref opamp Ninv Nnoninv).
OPAMP_KEYWORDS = {"opamp", "fdopamp", "inamp", "amp"}
RAIL_TAGS = {"vcc": "vcc", "vee": "vee"}

# .model type keyword -> lcapy prefix used for skin selection.
# LTspice Q/J/M lines carry a model name, not the device type
# (e.g. "Q1 1 2 3 2N3904" + ".model 2N3904 NPN(...)").
MODEL_TYPES = {
    "Q": {"NPN": "Qnpn", "PNP": "Qpnp"},
    "J": {"NJF": "Jnjf", "PJF": "Jpjf"},
    "M": {"NMOS": "Mnmos", "PMOS": "Mpmos", "VDMOS": "Mnmos"},
}


def _strip_hints(line: str) -> str:
    return line.split(";", 1)[0].split("#", 1)[0].strip()


def _lcapy_prefix(name: str) -> str:
    # Mechanical analogues are case-sensitive single-letter (k, m, r)
    if len(name) >= 2 and name[0] in ("k", "m", "r") and (name[1].isdigit() or name[1] == "_"):
        return name[0]

    for p in (
        "Dled",
        "Dschottky",
        "Dzener",
        "Dphoto",
        "Dtunnel",
        "CPE",  # constant phase element (3-letter prefix)
        "Cpolar",
        "Celectrolytic",
        "Cvariable",
        "Lchoke",
        "Lvariable",
        "RV",
        "Rvariable",
        "Qnpn",
        "Qpnp",
        "Eopamp",
        "Efdopamp",
        "Einamp",
        "Eamp",
        "SWspdt",
        "SWpush",
        "SWno",
        "SWnc",
        "SW",
        "Jnjf",
        "Jpjf",
        "Mnmos",
        "Mpmos",
        "VM",
        "AM",
        "ADC",
        "DAC",
        "REL",
        "NR",
        "XT",
        "ANT",
        "BAT",
        "FB",
        "sV",
        "sI",
        "Vdc",
        "Vac",
        "Vsin",
        "Idc",
        "Iac",
        "Isin",
        "Y",
        "Z",
    ):
        if name.startswith(p):
            return p
    return name[0].upper() if name else ""


def _parse_netlist(text: str):
    # deal with continuation lines
    text_lines = text.splitlines()
    processed_text_lines = []

    # append continuation lines to the previous line
    for line in text_lines:
        cleaned_line = _strip_hints(line)
        if not cleaned_line:
            continue

        if cleaned_line.startswith("+") and len(processed_text_lines) > 0:
            cont_line = cleaned_line[1:].strip()
            if cont_line:
                processed_text_lines[-1] += " " + cleaned_line[1:].strip()
            continue

        processed_text_lines.append(cleaned_line)

    models, processed_text_lines = _parse_models(processed_text_lines)
    subckt_pins, processed_text_lines = _parse_subckt_pins(processed_text_lines)

    parsed_netlist = []
    for cleaned in processed_text_lines:
        # Skip blanks, '.' directives (guard), SPICE '*' comment lines
        if cleaned[0] in "*.":
            continue

        # K1 L1 L2 1 -- coupling between inductor *names*, not net
        # connectivity; P<digits>/O<digits> are lcapy port / open-circuit
        # markers: drawn as a pair of open terminal rings, they carry no
        # connectivity, so they are dropped like probes.
        tokens = cleaned.split()
        first = tokens[0]
        rest = tokens[1:]
        if first.startswith("K"):
            continue
        if first[0].upper() in "OP":
            continue

        parsed_netlist.append((first, rest))

    return parsed_netlist, models, subckt_pins


def _parse_models(cleaned_text_lines: list[str]):
    """Collect '.model NAME TYPE ...' declarations -> {NAME: TYPE}.

    Only the first line of each .model is needed
    """
    models = {}
    processed_text_lines = []
    for cleaned in cleaned_text_lines:
        if cleaned.lower().startswith(".model"):
            parts = cleaned.replace("(", " ").replace(")", " ").split()
            if len(parts) >= 3:
                models[parts[1]] = parts[2].upper()
        processed_text_lines.append(cleaned)

    return models, processed_text_lines


def _parse_subckt_pins(cleaned_text_lines: list[str]):
    """Collect '.subckt NAME pin1 pin2 ...' header lines -> {NAME: [pins]}.

    Bodies are skipped by parse_netlist; only the pin names are needed so
    X-instantiations of locally defined subcircuits can label the generic
    cell with real pin names instead of letters. Trailing params
    ('params:' keyword or 'key=value' tokens) are not pins.
    """
    pins = {}
    in_subckt = False
    processed_text_lines = []
    for cleaned in cleaned_text_lines:
        if cleaned.lower().startswith(".subckt"):
            in_subckt = True
            tokens = cleaned.split()
            if len(tokens) < 2:
                continue
            names = []
            for tok in tokens[2:]:
                if "=" in tok or tok.lower() == "params:":
                    break
                names.append(tok)
            pins[tokens[1].upper()] = names

        if not in_subckt:
            processed_text_lines.append(cleaned)

        if in_subckt and cleaned.lower().startswith(".ends"):
            in_subckt = False

    return pins, processed_text_lines


def _node_arity(name: str, tokens) -> int | None:
    """Pin count for known component types, else None (unknown arity).

    For E-prefix lines carrying an inline opamp keyword
    (E1 Nout Nref opamp Ninv Nnoninv), the keyword is excluded.
    """
    if name.upper() == "W":
        return len(tokens)
    prefix = _lcapy_prefix(name)
    if prefix == "E" and any(t.lower() in OPAMP_KEYWORDS for t in tokens):
        return 4
    entry = TO_SKIN_CONFIG.get(prefix)
    if entry is None:
        return None
    return max(entry[1]) + 1


def _strip_opamp_keywords(name, tokens):
    """Strip inline opamp keywords that must not be treated as nodes."""
    if _lcapy_prefix(name).startswith("E"):
        return [t for t in tokens if t.lower() not in OPAMP_KEYWORDS]
    return tokens


def _split_nodes_value(name, tokens):
    arity = _node_arity(name, tokens)
    elem_tokens = _strip_opamp_keywords(name, tokens)
    if arity is None:
        # Unknown arity: SPICE convention is nodes first, value/model last.
        eq = next((i for i, t in enumerate(elem_tokens) if "=" in t), None)
        if _lcapy_prefix(name) == "X":
            # X ... subcktname [params]: trailing key=value tokens are
            # instance params, never nodes.
            if eq is not None:
                elem_tokens = elem_tokens[:eq]
        elif eq is not None:
            # e.g. B1 n+ n- V=... : the key=value tail IS the value.
            return elem_tokens[:eq], " ".join(elem_tokens[eq:])
        if len(elem_tokens) <= 1:
            return elem_tokens, None
        return elem_tokens[:-1], elem_tokens[-1]

    return elem_tokens[:arity], (" ".join(elem_tokens[arity:]) if len(elem_tokens) > arity else None)


def _assign_net_ids(parsed):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for name, tokens in parsed:
        if name.upper() == "W":
            nodes, _ = _split_nodes_value(name, tokens)
            for n in nodes[1:]:
                union(nodes[0], n)

    canonical = set()
    ground_root_node = None
    for _n, tokens in parsed:
        nodes, _ = _split_nodes_value(_n, tokens)
        for n in nodes:
            if n not in GROUND_NAMES:
                canonical.add(find(n))
            elif ground_root_node is None:
                ground_root_node = find(n)

    canonical = sorted(canonical)
    id_map = {node: i + 2 for i, node in enumerate(canonical)}

    for g in GROUND_NAMES:
        id_map[g] = 0

    ground_net_id = None
    if ground_root_node is not None:
        ground_net_id = id_map.get(ground_root_node, 0)

    return id_map, find, ground_net_id


def _is_ground_node(n: str) -> bool:
    return n in GROUND_NAMES or (len(n) >= 2 and n[0] == "G" and n[1:].isdigit())


def _get_rail_kind(comp_name: str) -> str | None:
    lc = comp_name.lower()
    for tag, skin_name in RAIL_TAGS.items():
        if tag in lc:
            return skin_name
    return None


def to_yosys_json(netlist_text: str, module_name: str = "circuit") -> dict:
    parsed_netlist, models, subckt_pins = _parse_netlist(netlist_text)
    id_map, find, ground_net_id = _assign_net_ids(parsed_netlist)

    cells = {}

    if ground_net_id is not None:
        cells["gnd"] = {
            "type": "gnd",
            "port_directions": {"A": "input"},
            "connections": {"A": [ground_net_id]},
            "attributes": {"name": "GND"},
        }

    for name, tokens in parsed_netlist:
        # if wire then ignore, already handled by union-find above. Wires are not components/cells.
        if name.upper() == "W":
            continue

        prefix = _lcapy_prefix(name)
        nodes, value = _split_nodes_value(name, tokens)

        if not nodes:
            continue

        if name in cells:
            raise ValueError(f"Duplicate component name: {name}")

        # Rail source (Vcc/Vee)
        rail = _get_rail_kind(name) if prefix == "V" else None
        if rail is not None:
            cells[name] = {
                "type": rail,
                "port_directions": {"A": "output"},
                "connections": {
                    "A": [id_map.get(find(node), 0) for node in nodes if not _is_ground_node(node)]
                },
                "attributes": {"ref": name, "value": value or ""},
            }
            continue

        entry = TO_SKIN_CONFIG.get(prefix)
        if entry is not None:
            skin_type, pin_map, direction_map = entry
            # Refine LTspice transistors via their .model declaration
            model_name = value.split()[0] if value else None
            refined = MODEL_TYPES.get(prefix, {}).get(models.get(model_name))
            if refined:
                skin_type, pin_map, direction_map = TO_SKIN_CONFIG[refined]
        elif prefix == "E" and any(t.lower() in OPAMP_KEYWORDS for t in tokens):
            cur_opamp_keywords = [t.lower() for t in tokens if t.lower() in OPAMP_KEYWORDS]

            if len(cur_opamp_keywords) > 1:
                logger.warning(
                    f"Multiple opamp keywords in {name}: {cur_opamp_keywords}, taking the first one."
                )

            opamp_keyword = cur_opamp_keywords[0]
            entry = TO_SKIN_CONFIG.get(f"E{opamp_keyword}")

            if entry is not None:
                skin_type, pin_map, direction_map = entry
            else:
                skin_type, pin_map = "op", {0: "out", 2: "-", 3: "+"}
                direction_map = {"+": "input", "-": "input", "out": "output"}
        else:
            skin_type = "generic"
            # Label pins with the subcircuit's declared pin names when
            # the .subckt header is known, else fall back to letters.
            subckt = value.split()[0].upper() if value else None
            pin_names = subckt_pins.get(subckt) if subckt else None
            pin_map = {
                i: (
                    pin_names[i]
                    if pin_names and i < len(pin_names)
                    else (chr(ord("A") + i) if i < 26 else f"P{i + 1}")
                )
                for i in range(len(nodes))
            }
            # SPICE pins are directionless; place the first half on the
            # left edge and the rest on the right, mimicking an IC.
            half = (len(nodes) + 1) // 2
            direction_map = {pin_map[i]: "input" if i < half else "output" for i in pin_map}

        connections = {}
        for pos, pid in pin_map.items():
            if pos < len(nodes):
                n = nodes[pos]
                connections[pid] = [id_map.get(find(n), 0)]

        if not connections:
            continue

        cells[name] = {
            "type": skin_type,
            "port_directions": {pid: direction_map.get(pid, "input") for pid in connections},
            "connections": connections,
            "attributes": {"ref": name, "value": value or ""},
        }

    return {"modules": {module_name: {"cells": cells}}}
