"""
Default skin configuration for circuit components.

pin_anchors:
    defaults to west/east, need to specify if not bipole.
pin_ids:
    defaults to ["+", "-"], list of pin ids used in yosys json
pin_sides:
    defaults to lateral (i.e. left/right). If not lateral, it overrides the port_direction in yosys json.
    this pin sides is used in netlistsvg. It is bad naming as it has nothing to do with the direction/orientation of the pin.
    - "top" → rider (signal flows INTO the cell)
    - "bottom" → driver (signal flows OUT of the cell)
    - "left" or `"right" → lateral (no default direction)
vertical:
    defaults to False. If True, the component is drawn vertically (e.g. from (0, 0) to (0, -3))
    instead of horizontally (e.g. from (0, 0) to (3, 0)). This affects the default pin anchors and label anchors.
colors:
    default colors is assigned in order of MARKER_RGB. First assigned to extra_anchors, then pins, eventually to labels.
    Specify if want to override.
extra_anchors:
    defaults to None. If specified, it is a list of (anchor, pid, side) for each extra anchor of the component.
if_node:
    defaults to True. If False, the component is treated as a bipole (2-pin) instead of a node (multi-pin).
label_anchors:
    (label_subnode, anchor, text_anchor, label_name) for each label of the component.
    For horizontal bipole components, defaults to [("label", "south", "above", "ref"), ("annotation", "north", "below", "value")].
    For vertical bipole components, defaults to [("label", "west", "right", "ref"), ("annotation", "east", "left", "value")].
    For node components, defaults to [("", "north", "above", "ref")].
    - label_subnode:
        the subnode reference of the component.
        If component in circuitikz is named T, then T<label_subnode> is the subnode accessed,
        and is used as the target we anchor to.
        If label_subnode is empty, T is used as the target we anchor to.
    - anchor:
        the anchor point of the label, accessed via T<label_subnode>.<anchor> in circuitikz
    - text_anchor:
        the direction which the label text expands. Naming follows circuitikz convention.
        e.g above, below, left, right. Normally, when anchor is north, text_anchor is above.
        When anchor is east, text_anchor is right, so on and so forth.
    - label_name:
        the name of the label, matched with label in yosys json.

    We currently don't do special anchor handlings when vertical is true, be aware when drawing bipoles vertically.
    When vertical is true, we draw bipole components from (0, 0) to (0, -3), therefore north becomes right, west is top.
"""  # noqa: E501

COMPONENTS = {
    # ------------ Generic ------------
    "generic": {
        "skin_type": "generic",
        "annotation_class": "generic",
        "is_generic": True,  # generic is hand-crafted below
    },
    # ------------ Resistors ------------
    "r_h": {
        "cpt": "R",
        "preamble_extra": "\\ctikzset{american}\n",
        "skin_type": "r_h",
        "annotation_class": "resistor",
        "if_node": False,
    },
    "r_v": {
        "cpt": "R",
        "vertical": True,
        "skin_type": "r_v",
        "annotation_class": "resistor",
        "if_node": False,
    },
    "r_var_h": {
        "cpt": "vR",
        "skin_type": "r_var_h",
        "annotation_class": "resistor_variable",
        "if_node": False,
    },
    # ----------- Capacitors ------------
    "c_h": {
        "cpt": "C",
        "skin_type": "c_h",
        "annotation_class": "capacitor",
        "if_node": False,
    },
    "c_polar_h": {
        "cpt": "cC",
        "skin_type": "c_polar_h",
        "annotation_class": "capacitor_polar",
        "if_node": False,
    },
    "c_var_h": {
        "cpt": "vC",
        "skin_type": "c_var_h",
        "annotation_class": "capacitor_variable",
        "if_node": False,
    },
    "c_v": {
        "cpt": "C",
        "vertical": True,
        "skin_type": "c_v",
        "annotation_class": "capacitor",
        "if_node": False,
    },
    "pz": {
        "cpt": "piezoelectric",
        "annotation_class": "piezoelectric",
        "skin_type": "pz",
        "if_node": False,
        "pin_ids": ["a", "b"],
    },
    # ----------- Switches ------------
    "switch": {
        "cpt": "switch",
        "annotation_class": "switch",
        "skin_type": "sw",
        "if_node": False,
    },
    "switch_no": {
        "cpt": "nos",
        "annotation_class": "switch_no",
        "skin_type": "sw_no",
        "pin_ids": ["p", "n"],
        "if_node": False,
    },
    "switch_nc": {
        "cpt": "ncs",
        "annotation_class": "switch_nc",
        "skin_type": "sw_nc",
        "pin_ids": ["p", "n"],
        "if_node": False,
    },
    "switch_push": {
        "cpt": "push button",
        "annotation_class": "switch_push",
        "skin_type": "sw_push",
        "pin_ids": ["p", "n"],
        "if_node": False,
    },
    "spdt": {
        "cpt": "spdt",
        "annotation_class": "switch_spdt",
        "skin_type": "sw_spdt",
        "pin_anchors": ["in", "out 1", "out 2"],
        "pin_ids": ["p", "n", "common"],
        "pin_sides": ["left", "right", "right"],
    },
    # ----------- Diodes ------------
    "diode": {
        "cpt": "D",
        "annotation_class": "diode",
        "skin_type": "d_h",
        "if_node": False,
    },
    "diode_led": {
        "cpt": "leD",
        "annotation_class": "diode_led",
        "skin_type": "d_led_h",
        "if_node": False,
    },
    "diode_schottky": {
        "cpt": "sD",
        "annotation_class": "diode_schottky",
        "skin_type": "d_sk_h",
        "if_node": False,
    },
    "diode_zener": {
        "cpt": "zD",
        "annotation_class": "diode_zener",
        "skin_type": "d_zener_h",
        "if_node": False,
    },
    "diode_photo": {
        "cpt": "pD",
        "annotation_class": "diode_photo",
        "skin_type": "d_photo_h",
        "if_node": False,
    },
    # ----------- Inductors ------------
    "inductor": {
        "cpt": "L",
        "annotation_class": "inductor",
        "skin_type": "l_h",
        "if_node": False,
    },
    "inductor_choke": {
        "cpt": "cute choke",
        "annotation_class": "inductor_choke",
        "skin_type": "l_choke_h",
        "if_node": False,
    },
    "inductor_var": {
        "cpt": "vL",
        "annotation_class": "inductor_var",
        "skin_type": "l_var_h",
        "if_node": False,
    },
    "l_v": {
        "cpt": "L",
        "annotation_class": "inductor",
        "vertical": True,
        "skin_type": "l_v",
        "if_node": False,
    },
    # ----------- V-sources ------------
    "vsource": {
        "cpt": "V",
        "annotation_class": "vsource",
        "vertical": True,
        "preamble_extra": "\\ctikzset{american voltages, american currents}\n",
        "skin_type": "v",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "svsource": {
        "cpt": "sV",
        "vertical": True,
        "annotation_class": "svsource",
        "preamble_extra": "\\ctikzset{american voltages, american currents}\n",
        "skin_type": "sv",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "vcvs_h": {
        "annotation_class": "controlled_vsource",
        "cpt": "controlled voltage source",
        "skin_type": "vcvs_h",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "ccvs": {
        "annotation_class": "controlled_vsource",
        "cpt": "controlled voltage source",
        "skin_type": "ccvs",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    # ----------- I-sources ------------
    "isource": {
        "cpt": "I",
        "annotation_class": "isource",
        "vertical": True,
        "preamble_extra": "\\ctikzset{american currents}\n",
        "skin_type": "i",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "sisource": {
        "cpt": "sI",
        "annotation_class": "sisource",
        "vertical": True,
        "preamble_extra": "\\ctikzset{american currents}\n",
        "skin_type": "si",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "vccs_h": {
        "cpt": "controlled current source",
        "annotation_class": "controlled_isource",
        "skin_type": "vccs_h",
        "pin_sides": ("bottom", "top"),  # + drives
        "if_node": False,
    },
    "cccs": {
        "annotation_class": "controlled_isource",
        "cpt": "controlled current source",
        "skin_type": "cccs",
        "if_node": False,
    },
    # ----------- Mechanical ------------
    "k": {
        "cpt": "spring",
        "annotation_class": "spring",
        "skin_type": "k_spring",
        "if_node": False,
    },
    "m": {
        "cpt": "mass",
        "annotation_class": "mass",
        "skin_type": "m_mass",
        "if_node": False,
    },
    "r": {
        "cpt": "damper",
        "annotation_class": "damper",
        "skin_type": "r_damper",
        "if_node": False,
    },
    # ----------- two-port -----------
    "tp": {
        "annotation_class": "twoport",
        "cpt": "twoport, D",
        "skin_type": "tp",
        "if_node": False,
    },
    # ----------- meters -----------
    "ammeter": {
        "annotation_class": "ammeter",
        "cpt": "ammeter",
        "skin_type": "ammeter",
        "if_node": False,
    },
    "voltmeter": {
        "annotation_class": "voltmeter",
        "cpt": "voltmeter",
        "skin_type": "voltmeter",
        "if_node": False,
    },
    # ---------- battery -----------
    "battery": {
        "cpt": "battery",
        "annotation_class": "battery",
        "skin_type": "battery",
        "if_node": False,
    },
    # ------------ node components ------------
    "ground": {
        "cpt": "ground",
        "annotation_class": "ground",
        "skin_type": "gnd",
        "pin_anchors": ["north"],
        "pin_ids": ["A"],
        "pin_sides": ["left"],
        "label_anchors": [("", "south", "below", "ref")],
    },
    "q_npn": {
        "cpt": "npn",
        "annotation_class": "npn",
        "skin_type": "q_npn",
        "pin_anchors": ["B", "C", "E"],
        "pin_ids": ["b", "c", "e"],
        "pin_sides": ["left", "right", "right"],
    },
    "q_pnp": {
        "cpt": "pnp",
        "annotation_class": "pnp",
        "skin_type": "q_pnp",
        "pin_anchors": ["B", "C", "E"],
        "pin_ids": ["b", "c", "e"],
        "pin_sides": ["left", "right", "right"],
    },
    "jfet_n": {
        "cpt": "njfet",
        "annotation_class": "njfet",
        "skin_type": "jfet_n",
        "pin_anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "pin_sides": ["left", "right", "right"],
    },
    "jfet_p": {
        "cpt": "pjfet",
        "annotation_class": "pjfet",
        "skin_type": "jfet_p",
        "pin_anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "pin_sides": ["left", "right", "right"],
    },
    "mos_n": {
        "cpt": "nmos",
        "annotation_class": "nmos",
        "skin_type": "mos_n",
        "pin_anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "pin_sides": ["left", "right", "right"],
    },
    "mos_p": {
        "cpt": "pmos",
        "annotation_class": "pmos",
        "skin_type": "mos_p",
        "pin_anchors": ["G", "D", "S"],
        "pin_ids": ["g", "d", "s"],
        "pin_sides": ["left", "right", "right"],
    },
    "op": {
        "cpt": "op amp",
        "annotation_class": "opamp",
        "skin_type": "op",
        "pin_anchors": ["+", "-", "out"],
        "pin_ids": ["i+", "i-", "o+"],
        "pin_sides": ["top", "top", "bottom"],
    },
    "pot": {
        "cpt": "pR",
        "annotation_class": "potentiometer",
        "if_node": False,
        "skin_type": "pot",
        "pin_ids": ["p", "n", "wiper"],
        "pin_anchors": ["west", "east", "wiper"],
        "pin_sides": ["left", "right", "right"],
    },
    "tf": {
        "cpt": "transformer",
        "annotation_class": "transformer",
        "skin_type": "tf",
        "pin_ids": ["sp", "sm", "pp", "pm"],
        "pin_anchors": ["B1", "B2", "A1", "A2"],
        "pin_sides": ["bottom", "bottom", "top", "top"],
    },
    # --- ADC / DAC ---
    "adc": {
        "cpt": "adc",
        "annotation_class": "adc",
        "skin_type": "adc",
        "if_node": False,
    },
    "dac": {
        "cpt": "dac",
        "annotation_class": "dac",
        "skin_type": "dac",
        "if_node": False,
    },
    # --- 1-pin monopoles ---
    "antenna": {
        "cpt": "antenna",
        "skin_type": "antenna",
        "annotation_class": "antenna",
        "pin_ids": ["a"],
        "pin_anchors": ["south"],
        "pin_sides": ["left"],
    },
    # "vcc": {
    #     "cpt": "vcc",
    #     "skin_type": "vcc",
    #     "pin_ids": ["A"],
    #     "pin_anchors": ["south"],
    #     "pin_sides": ["left"],
    # },
    # "vee": {
    #     "cpt": "vee",
    #     "skin_type": "vee",
    #     "pin_ids": ["A"],
    #     "pin_anchors": ["north"],
    #     "pin_sides": ["left"],
    # },
}
