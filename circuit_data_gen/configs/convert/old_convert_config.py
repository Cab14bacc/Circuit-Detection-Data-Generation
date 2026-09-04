# NETLIST_TO_SKIN = {
#     # 2-terminal passives (symmetric, inout)
#     "R": ("r_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Rvariable": ("r_var_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "REL": ("r_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "NR": ("r_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Y": ("r_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Z": ("r_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "C": ("c_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Cpolar": ("c_polar_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Celectrolytic": ("c_polar_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Cvariable": ("c_var_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "L": ("l_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Lchoke": ("l_choke_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "Lvariable": ("l_var_h", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     # Sources: + output, - input
#     "V": ("v", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "sV": ("sv", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Vdc": ("v", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Vac": ("sv", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Vsin": ("sv", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "I": ("i", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "sI": ("si", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Idc": ("i", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Iac": ("si", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     "Isin": ("si", {0: "+", 1: "-"}, {"+": "output", "-": "input"}),
#     # Diodes: anode input, cathode output
#     "D": ("d_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     "Dled": ("d_led_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     "Dschottky": ("d_sk_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     "Dzener": ("d_zener_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     "Dphoto": ("d_photo_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     "Dtunnel": ("d_h", {0: "+", 1: "-"}, {"+": "input", "-": "output"}),
#     # Transistors: base/gate input, collector/drain + emitter/source outputs
#     "Q": (
#         "q_npn",
#         {0: "b", 1: "c", 2: "e"},
#         {"b": "input", "c": "input", "e": "output"},
#     ),
#     "Qnpn": (
#         "q_npn",
#         {0: "b", 1: "c", 2: "e"},
#         {"b": "input", "c": "input", "e": "output"},
#     ),
#     "Qpnp": (
#         "q_pnp",
#         {0: "b", 1: "c", 2: "e"},
#         {"b": "input", "c": "input", "e": "output"},
#     ),
#     "J": (
#         "jfet_n",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     "Jnjf": (
#         "jfet_n",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     "Jpjf": (
#         "jfet_p",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     "M": (
#         "mos_n",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     "Mnmos": (
#         "mos_n",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     "Mpmos": (
#         "mos_p",
#         {0: "g", 1: "d", 2: "s"},
#         {"g": "input", "d": "output", "s": "output"},
#     ),
#     # Opamp (E1 N+ N- opamp Ninv Nnoninv — keyword detection below)
#     "Eopamp": (
#         "op",
#         {0: "+", 1: "-", 2: "out"},
#         {"+": "input", "-": "input", "out": "output"},
#     ),
#     "Efdopamp": (
#         "op",
#         {0: "+", 1: "-", 2: "out"},
#         {"+": "input", "-": "input", "out": "output"},
#     ),
#     "Einamp": (
#         "op",
#         {0: "+", 1: "-", 2: "out"},
#         {"+": "input", "-": "input", "out": "output"},
#     ),
#     "Eamp": (
#         "op",
#         {0: "+", 1: "-", 2: "out"},
#         {"+": "input", "-": "input", "out": "output"},
#     ),
#     # Switches
#     "SW": ("sw", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "SWno": ("sw_no", {0: "p", 1: "n"}, {"p": "input", "n": "input"}),
#     "SWnc": ("sw_nc", {0: "p", 1: "n"}, {"p": "input", "n": "input"}),
#     "SWpush": ("sw_push", {0: "p", 1: "n"}, {"p": "input", "n": "input"}),
#     "SWspdt": (
#         "sw_spdt",
#         {0: "p", 1: "n", 2: "common"},
#         {"p": "input", "n": "input", "common": "input"},
#     ),
#     # Misc
#     "XT": ("xtal", {0: "a", 1: "b"}, {"a": "input", "b": "input"}),
#     "ANT": ("antenna", {0: "a"}, {"a": "input"}),
#     "BAT": ("battery", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "AM": ("ammeter", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "VM": ("voltmeter", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "ADC": ("ammeter", {0: "+"}, {"+": "input"}),  # use ammeter shape
#     "DAC": ("ammeter", {0: "+"}, {"+": "output"}),
#     "FB": ("fb", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "CPE": ("cpe", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "RV": (
#         "pot",
#         {0: "p", 1: "n", 2: "wiper"},
#         {"p": "input", "n": "input", "wiper": "output"},
#     ),
#     "FS": ("sw_nc", {0: "p", 1: "n"}, {"p": "input", "n": "input"}),
#     # Mechanical
#     "k": ("cpe", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "m": ("cpe", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "r": ("cpe", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     # For LTspice
#     # LTspice: Wxxx = current-controlled switch (W1 n+ n- vnam model),
#     #          Sxxx = voltage-controlled switch (S1 n+ n- nc+ nc- model).
#     # Both drawn as a 2-pin switch; control refs land in the value label.
#     "W": ("sw", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     "S": ("sw", {0: "+", 1: "-"}, {"+": "input", "-": "input"}),
#     # END For LTspice
# }
