"""SPICE → Yosys JSON converter for netlistsvg's lcapy skin.

Handles Lcapy SPICE netlists with or without layout hints. Maps component
prefixes to skin cells, merges nodes via W wires, emits a gnd cell for
ground-touching nets, and recognises Vcc/Vee rails.

Uses the custom lcapy.svg skin (co-located with this file) which mirrors
Lcapy's Circuitikz visual vocabulary.
"""
from __future__ import annotations

import json
import re
import os
import subprocess
from pathlib import Path


# Component prefix → (skin alias, {lcapy-node-position: skin-pin-id}, port_direction_map)
# The port_direction_map maps skin pin id → "input" | "output" | "input"
LCAPY_TO_SKINSVG = {
    # 2-terminal passives (symmetric, inout)
    "R":         ("r_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Rvariable": ("r_var_h",   {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "REL":       ("r_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "NR":        ("r_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Y":         ("r_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Z":         ("r_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "C":         ("c_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Cpolar":    ("c_polar_h", {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Celectrolytic": ("c_polar_h", {0: "+",  1: "-"}, {"+": "input", "-": "input"}),
    "Cvariable": ("c_var_h",   {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "L":         ("l_h",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Lchoke":    ("l_choke_h", {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "Lvariable": ("l_var_h",   {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    # Sources: + output, - input
    "V":         ("v",         {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "sV":        ("sv",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Vdc":       ("v",         {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Vac":       ("sv",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Vsin":      ("sv",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "I":         ("i",         {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "sI":        ("si",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Idc":       ("i",         {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Iac":       ("si",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    "Isin":      ("si",        {0: "+",  1: "-"},   {"+": "output", "-": "input"}),
    # Diodes: anode input, cathode output
    "D":         ("d_h",       {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    "Dled":      ("d_led_h",   {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    "Dschottky": ("d_sk_h",    {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    "Dzener":    ("d_zener_h", {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    "Dphoto":    ("d_photo_h", {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    "Dtunnel":   ("d_h",       {0: "+",  1: "-"},   {"+": "input", "-": "output"}),
    # Transistors: base/gate input, collector/drain + emitter/source outputs
    "Q":         ("q_npn",     {0: "b",  1: "c",  2: "e"},
                                  {"b": "input", "c": "input", "e": "output"}),
    "Qnpn":      ("q_npn",     {0: "b",  1: "c",  2: "e"},
                                  {"b": "input", "c": "input", "e": "output"}),
    "Qpnp":      ("q_pnp",     {0: "b",  1: "c",  2: "e"},
                                  {"b": "input", "c": "input", "e": "output"}),
    "J":         ("jfet_n",    {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    "Jnjf":      ("jfet_n",    {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    "Jpjf":      ("jfet_p",    {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    "M":         ("mos_n",     {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    "Mnmos":     ("mos_n",     {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    "Mpmos":     ("mos_p",     {0: "g",  1: "d",  2: "s"},
                                  {"g": "input", "d": "output", "s": "output"}),
    # Opamp (E1 N+ N- opamp Ninv Nnoninv — keyword detection below)
    "Eopamp":    ("op",        {0: "+",  1: "-",  2: "out"},
                                  {"+": "input", "-": "input", "out": "output"}),
    "Efdopamp":  ("op",        {0: "+",  1: "-",  2: "out"},
                                  {"+": "input", "-": "input", "out": "output"}),
    "Einamp":    ("op",        {0: "+",  1: "-",  2: "out"},
                                  {"+": "input", "-": "input", "out": "output"}),
    "Eamp":      ("op",        {0: "+",  1: "-",  2: "out"},
                                  {"+": "input", "-": "input", "out": "output"}),
    # Switches
    "SW":        ("sw",        {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "SWno":      ("sw_no",     {0: "p",  1: "n"},   {"p": "input", "n": "input"}),
    "SWnc":      ("sw_nc",     {0: "p",  1: "n"},   {"p": "input", "n": "input"}),
    "SWpush":    ("sw_push",   {0: "p",  1: "n"},   {"p": "input", "n": "input"}),
    "SWspdt":    ("sw_spdt",   {0: "p",  1: "n", 2: "common"},
                                  {"p": "input", "n": "input", "common": "input"}),
    # Misc
    "XT":        ("xtal",      {0: "a",  1: "b"},   {"a": "input", "b": "input"}),
    "ANT":       ("antenna",   {0: "a"},            {"a": "input"}),
    "BAT":       ("battery",   {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "AM":        ("ammeter",   {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "VM":        ("voltmeter", {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "ADC":       ("ammeter",   {0: "+"},            {"+": "input"}),   # use ammeter shape
    "DAC":       ("ammeter",   {0: "+"},            {"+": "output"}),
    "FB":        ("fb",        {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "CPE":       ("cpe",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "RV":        ("pot",       {0: "p",  1: "n", 2: "wiper"},
                                  {"p": "input", "n": "input", "wiper": "output"}),
    "FS":        ("sw_nc",     {0: "p",  1: "n"},   {"p": "input", "n": "input"}),
    # Mechanical
    "k":         ("cpe",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "m":         ("cpe",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
    "r":         ("cpe",       {0: "+",  1: "-"},   {"+": "input", "-": "input"}),
}

GROUND_NAMES = {"0", "GND"}

# Kind keywords that appear inline in E-family opamp lines and must not be
# treated as node names (E1 Nout Nref opamp Ninv Nnoninv).
OPAMP_KEYWORDS = {"opamp", "fdopamp", "inamp", "amp"}
RAIL_TAGS = {"vcc": "vcc", "vee": "vee"}


def strip_hints(line: str) -> str:
    return line.split(";", 1)[0].split("#", 1)[0].strip()


def lcapy_prefix(name: str) -> str:
    # Mechanical analogues are case-sensitive single-letter (k, m, r)
    if len(name) >= 2 and name[0] in ("k", "m", "r") and (name[1].isdigit() or name[1] == "_"):
        return name[0]
    for p in ("Dled", "Dschottky", "Dzener", "Dphoto", "Dtunnel",
              "CPE",  # constant phase element (3-letter prefix)
              "Cpolar", "Celectrolytic", "Cvariable",
              "Lchoke", "Lvariable",
              "RV", "Rvariable",
              "Qnpn", "Qpnp", "Eopamp", "Efdopamp", "Einamp", "Eamp",
              "SWspdt", "SWpush", "SWno", "SWnc", "SW", "Jnjf", "Jpjf",
              "Mnmos", "Mpmos",
              "VM", "AM", "ADC", "DAC", "REL", "NR",
              "XT", "ANT", "BAT", "FB",
              "sV", "sI", "Vdc", "Vac", "Vsin", "Idc", "Iac", "Isin",
              "Y", "Z"):
        if name.startswith(p):
            return p
    return name[0].upper() if name else ""


def parse_netlist(text: str):
    for raw in text.splitlines():
        cleaned = strip_hints(raw)
        if not cleaned or cleaned.startswith("."):
            continue
        tokens = cleaned.split()
        yield tokens[0], tokens[1:]


def split_nodes_value(tokens):
    nodes, value_parts, seen_value = [], [], False
    for t in tokens:
        if seen_value:
            value_parts.append(t)
        elif t.startswith("{"):
            value_parts.append(t); seen_value = True
        elif re.match(r"^-?\d", t):
            value_parts.append(t); seen_value = True
        else:
            nodes.append(t)
    return nodes, (" ".join(value_parts) if value_parts else None)


def assign_net_ids(parsed):
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
            nodes, _ = split_nodes_value(tokens)
            for n in nodes[1:]:
                union(nodes[0], n)
    canonical = sorted({find(n) for _n, tokens in parsed
                        for n in split_nodes_value(tokens)[0]
                        if n not in GROUND_NAMES})
    id_map = {node: i + 2 for i, node in enumerate(canonical)}
    for g in GROUND_NAMES:
        id_map[g] = 0
    return id_map, find


def is_ground_node(n: str) -> bool:
    return n in GROUND_NAMES or (len(n) >= 2 and n[0] == "G" and n[1:].isdigit())


def rail_kind_for(comp_name: str) -> str | None:
    lc = comp_name.lower()
    for tag, skin_name in RAIL_TAGS.items():
        if tag in lc:
            return skin_name
    return None


def to_yosys_json(netlist_text: str, module_name: str = "circuit") -> dict:
    parsed = list(parse_netlist(netlist_text))
    id_map, find = assign_net_ids(parsed)

    ground_net_id = None
    for _n, tokens in parsed:
        for n in split_nodes_value(tokens)[0]:
            if is_ground_node(n):
                ground_net_id = id_map.get(find(n), 0)
                break
        if ground_net_id is not None:
            break

    cells = {}

    if ground_net_id is not None:
        cells["gnd"] = {
            "type": "gnd",
            "port_directions": {"A": "input"},
            "connections": {"A": [ground_net_id]},
            "attributes": {"name": "GND"},
        }

    for name, tokens in parsed:
        if name.upper() == "W":
            continue
        prefix = lcapy_prefix(name)
        nodes, value = split_nodes_value(tokens)
        if prefix.startswith("E"):
            nodes = [n for n in nodes if n.lower() not in OPAMP_KEYWORDS]
        if not nodes:
            continue

        # Rail source (Vcc/Vee)
        rail = rail_kind_for(name) if prefix == "V" else None
        if rail is not None:
            cells[name] = {
                "type": rail,
                "port_directions": {"A": "output"},
                "connections": {"A": [id_map.get(find(n), 0) for n in nodes
                                      if not is_ground_node(n)]},
                "attributes": {"name": name, "value": value or ""},
            }
            continue

        # Op-amp keyword detection (E1 N+ N- opamp Ninv Nnoninv)
        if prefix == "E" and any(t.lower() in OPAMP_KEYWORDS for t in tokens):
            skin_type, pin_map = "op", {0: "out", 2: "-", 3: "+"}
            direction_map = {"+": "input", "-": "input", "out": "output"}
        else:
            entry = LCAPY_TO_SKINSVG.get(prefix)
            if entry is None:
                skin_type, pin_map = "generic", {i: chr(ord("A") + i) for i in range(len(nodes))}
                direction_map = {pid: "input" for pid in pin_map.values()}
            else:
                skin_type, pin_map, direction_map = entry

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


