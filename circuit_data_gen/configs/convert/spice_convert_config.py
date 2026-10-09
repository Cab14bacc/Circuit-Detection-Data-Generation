"""
SPICE (ngspice) netlist conversion configuration.

TO_SKIN_CONFIG maps each element letter (ngspice manual, Table 2.2) to its
specs: the variants of the element and the skin cell each one draws.
MODEL_CONFIG lists the .model TYPEs and the card parameters they need, and
EXPRESSION_CONSTANTS the names allowed in {expressions} without a .param.
The field reference is docs/CONVERT_CONFIG.md. parser/macro.py loads these
tables once and lower-cases prefixes, keys and model types, so this file may
use either case.

How a line is read (parser/convert.py):
  - The model name (a token naming a .model card of the netlist) is removed
    from the line; its card's TYPE must be in the spec's `model_type`.
  - Kind keywords (off, ON, OFF) are removed; the spec's `kind` must match.
  - V/I specifiers (DC <value>, AC [<mag> [<phase>]]) are removed with their
    operands, which fill value slots directly (parser/helper.py,
    _read_specifiers). Whether AC is present picks the DC or AC source spec.
  - Transient functions (SIN(...), PULSE(...), ...) are single tokens: the
    tokenizer joins balanced parentheses, and a space before the paren
    (PWL (0,0 ...)) is glued.
  - What remains is matched by index: nodes (`arg_to_ports`), then
    positional values; key=value arguments fill keyword slots.
  - Every spec of the letter is tried; the one with the fewest errors, then
    the fewest warnings, then the earliest one wins.
Ground is node 0, also GND / gnd. X lines are subcircuit instances drawn as
generic boxes, with pins named after the .subckt ports.

===========================================================================
GRAMMAR — the forms config_to_grammar() renders from this file (checked
against ngspice 47). The LLM system prompt keeps a hand-written copy.

Notation:
  N...       node: write the net's name (in, out, vdd, 0). Nodes listed as
             "hidden" are written and connected but not drawn (the spec
             marks them "drop"): Q substrate NS, M bulk NB, the E/G/S
             control pair NC+ NC-, the T/O/Y far end NP2+ NP2-.
  <value>    a value: a number with an optional suffix (1k, 2.2u, 1Meg), or
             an expression in braces ({2*Rbase}) whose names are .params,
             EXPRESSION_CONSTANTS, functions, v(node) or i(Vname).
  [ ... ]    optional group; a|b = one of the alternatives.
  key=<val>  keyword argument, written in that form; key={<val>} must keep
             its braces (or quotes) even for a plain number.
  mname      model name: after all nodes, before any key=value; for C, L
             and Y before the value, for W after <Vcontrol>. A .model card
             declaring it is required in the same netlist.
  off on     kind keywords, right after mname.
  dc ac      V/I specifiers, in any order.
  <Vcontrol> the NAME of a V element of the netlist (not a node).
SPICE ignores letter case; DC, AC, off, ON, OFF are reserved (no node names).

Passives:
    Rname N+ N- <r>
    Rname N+ N- [<r>] mname                    TYPE R | RES
    Cname N+ N- <c>
    Cname N+ N- mname [<c>]                    TYPE C
    Cname N+ N- q={<charge>}
    Lname N+ N- <l>
    Lname N+ N- inductance=<value>
    Lname N+ N- mname [<inductance>]           TYPE L

Independent sources (an ac segment makes it an AC source, drawn differently):
    Vname N+ N- [<value>|dc <value>] [SIN(...)|PULSE(...)|PWL(...)|...]
    Vname N+ N- [<value>|dc <value>] [SIN(...)|...] ac [<mag>] [<phase>]
    Iname ... (same two forms)
  - <value> and dc <value> fill the same slot: write at most one.
  - At most one function; a bare DC value comes before it (or dc anywhere).
  - A bare ac means ac 1.

Controlled and behavioral sources:
    Ename N+ N- NC+ NC- <gain>                 NC+ NC- hidden
    Ename N+ N- vol={<expr>}
    Gname N+ N- NC+ NC- <gain>                 NC+ NC- hidden
    Gname N+ N- cur={<expr>}
    Fname N+ N- <Vcontrol> <gain>
    Hname N+ N- <Vcontrol> <gain>
    Bname N+ N- v=<expr>
    Bname N+ N- i=<expr>
  vol=, cur=, v=, i= are the first argument after the nodes.

Semiconductors (the card's TYPE picks the symbol):
    Dname N+ N- mname [off]                    TYPE D
    Qname NC NB NE [NS] mname [off]            TYPE NPN | PNP; NS hidden
    Jname ND NG NS mname [off]                 TYPE NJF | PJF
    Mname ND NG NS NB mname [off]              TYPE NMOS | PMOS; NB hidden
    Zname ND NG NS mname [off]                 TYPE NMF | PMF (MESFET)

Switches (on = closed, off or nothing = open):
    Sname NP N NC+ NC- mname [on|off]          TYPE SW; NC+ NC- hidden
    Wname N+ N- <Vcontrol> mname [on|off]      TYPE CSW

Transmission lines (NP2+ NP2- hidden):
    Tname N+ N- NP2+ NP2- z0=<value>           (td=, f=, nl=, ic= are passed through)
    Oname N+ N- NP2+ NP2- mname                TYPE LTRA
    Yname N+ N- NP2+ NP2- mname [len=<value>]  TYPE TXL
  Their cards need the line parameters (no ngspice defaults):
    .model mname ltra r=<value> l=<value> c=<value> len=<value> g=0
    .model mname txl r=<value> l=<value> g=<value> c=<value> length=<value>
  LTRA's g must be 0; TXL's length belongs in the card (len= overrides it).

Subcircuits:
    Xname N1 ... Nn SUBNAM [param=<value> ...]
  with a .subckt SUBNAM N1 ... Nn [params: ...] ... .ends block in the
  netlist. The instance counts as one component; its body is not drawn.
  A, N, P, U are parsed as generic boxes but are not part of the grammar.

Directives: strict parsing accepts only .model, .param, .subckt / .ends and
.end (STRICT_ALLOWED_DIRECTIVES in parser/strict.py). Lenient parsing ignores
the others and skips .control ... .endc blocks.
Continuation lines start with '+'; comments start with '*' or ';'.
===========================================================================
"""  # noqa: E501

TO_SKIN_CONFIG = {
    # Resistor — Rname N+ N- <value|r=expr>  (ngspice §3.3.1)
    "R": [
        {
            "skin_alias": ["r_h", "r_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["r", "resistance"],
                    "is_optional": False,
                    "is_positional": "positional_or_keyword",
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        },
        # Semiconductor Resistor
        {
            "skin_alias": ["r_h", "r_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["r", "resistance"],
                    "is_optional": True,
                    "default_value": "1e-3",
                    "is_positional": "positional_or_keyword",
                },
            },
            "model_type": ["R", "RES"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Capacitor — Cname N+ N- <value> <ic=v>, or Cname N+ N- mname [<value>]  (ngspice §3.3.6)
    "C": [
        {
            "skin_alias": ["c_h", "c_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["c", "cap", "capacitance"],
                    "is_optional": False,
                    "is_positional": "positional_or_keyword",
                },
            },
            "port_directions": {"+": "input", "-": "output"},
        },
        # Semiconductor Capacitor
        {
            "skin_alias": ["c_h", "c_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    # CAP is model capacitance
                    "alias": ["c", "cap", "capacitance"],
                    "default_value": "0",
                    "is_positional": "positional_or_keyword",
                },
            },
            # ngspice (32 to 47) only reads a model name written BEFORE the
            # value: `C1 a b CM 1u`; `C1 a b 1u CM` fails with "unknown
            # parameter (cm)", although the manual's general form shows the
            # value first
            "model_type": {"C": {"ref_arg": 2, "before_arg": True}},
            "port_directions": {"+": "input", "-": "output"},
        },
        # Specify by charge
        {
            "skin_alias": ["c_h", "c_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                "Q": {
                    "skin_label": "value",
                    "alias": ["Q"],
                    "display_alias": "charge",
                    "is_positional": "keyword",
                    "is_optional": False,
                    "is_expression": True,
                },
                # 3rd arg may be a model name (mname) — consumed, not shown
            },
            # ngspice 32 rejects a constant charge (`Q=2n`, `Q={2n}`, `Q='2n'`:
            # "unknown parameter (q)"); 41+ needs it in {} or ''
            "port_directions": {"+": "input", "-": "output"},
        },
    ],
    # Inductor — Lname N+ N- <value>, or Lname N+ N- mname [<value>]  (ngspice §3.3.10)
    "L": [
        {
            "skin_alias": ["l_h", "l_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["L"],
                    # required, as in ngspice (and as for C)
                    "is_optional": False,
                    "is_positional": "positional",
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        },
        {
            "skin_alias": ["l_h", "l_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["inductance"],
                    # required, as in ngspice (and as for C)
                    "is_optional": False,
                    # the inductance= spelling, valid anywhere among the keywords
                    # (`L1 a b m=1 inductance=1m`); a bare value is the spec above's
                    "is_positional": "keyword",
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        },
        {
            "skin_alias": ["l_h", "l_v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    # doesn't accept "L" with a model
                    "alias": ["inductance"],
                    "is_optional": True,
                    "default_value": "0",
                    "is_positional": "positional_or_keyword",
                },
            },
            # as for C: `L1 a b LM 1m`, never `L1 a b 1m LM`
            "model_type": {"L": {"ref_arg": 2, "before_arg": True}},
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Voltage source — Vname N+ N- <value|function|specifier stream>  (ngspice §4.1)
    # SPICE value forms are single tokens once parens are joined:
    #   V1 a b 5            plain DC
    #   V3 IN 0 SINE(0 1m 100)
    #   V4 a b PULSE(0 5 100u 10n 10n 500u 1)
    #   V1 a b PWL(0,0 1m 5)
    # Mode specifiers (DC/AC) are read out of the line before matching
    # (_read_specifiers), and ALSO split the source into two specs:
    #   DC source (skin v):  value and/or DC specifier; AC is FOREIGN here —
    #                        any AC on the line rejects this candidate.
    #   AC source (skin sv): AC specifier REQUIRED; value-only lines reject.
    #   V1 a b DC 5 AC 1m    -> AC source: value=5, ac=1m
    #   V1 a b AC 1 90       -> AC source: ac=1, phase=90 (bare AC -> ac=1)
    "V": [
        # DC voltage source (skin v): plain value0
        # . and/or the DC specifier.
        # The AC specifier is NOT declared here, so an AC token is foreign
        # to this spec — foreign detection routes the line to the AC spec.
        {
            "skin_alias": ["v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["dc"],
                    "display_alias": "value",
                    "is_positional": "positional_or_keyword",
                },
                # transient function (SIN(...), PULSE(...), ...): a value of
                # its own next to the DC value (V1 a 0 DC 2 SIN(1 0.5 1k)),
                # read by _read_specifiers; written alone it is the value above
                "tran": {
                    "skin_label": None,
                    "display_alias": "tran",
                    "alias": [],
                    "is_positional": "keyword",
                    "functions": ["sin", "sine", "pulse", "pwl", "exp", "sffm", "am"],
                },
            },
            "specifiers": {
                # DC VALUE  -> transparent: operand lands in the value slot
                "DC": {"refs": [2], "is_optional": True},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC voltage source: chosen by an AC magnitude, written as the AC
        # specifier (AC 1) or as ac= / acmag=. The specifier is optional so
        # that `V1 a 0 ac=1` can match; a value-only line matches both specs
        # and the DC spec wins the tie (it comes first), while an ac= keyword
        # is undeclared on the DC spec, which costs it a warning.
        {
            "skin_alias": ["sv"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["dc"],
                    "display_alias": "value",
                    "is_positional": "positional_or_keyword",
                },
                # transient function (SIN(...), PULSE(...), ...): a value of
                # its own next to the DC value (V1 a 0 DC 2 SIN(1 0.5 1k)),
                # read by _read_specifiers; written alone it is the value above
                "tran": {
                    "skin_label": None,
                    "display_alias": "tran",
                    "alias": [],
                    "is_positional": "keyword",
                    "functions": ["sin", "sine", "pulse", "pwl", "exp", "sffm", "am"],
                },
                "AC": {
                    "skin_label": None,
                    "alias": ["ac", "acmag"],
                    "display_alias": "mag",
                    "is_positional": "keyword",
                },
                "phase": {
                    "skin_label": None,
                    "alias": ["acphase"],
                    "display_alias": "phase",
                    "is_positional": "keyword",
                },
            },
            "specifiers": {
                # DC VALUE  -> transparent: operand lands in the value slot
                "DC": {"refs": [2], "is_optional": True},
                # AC [ACMAG [ACPHASE]] — bare AC == AC 1 (default injected at
                # expansion time when the specifier opens with no operands)
                "AC": {"refs": ["AC", "phase"], "is_optional": True, "default": "1"},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # Current source — same DC/AC specifier split as V  (ngspice §4.1)
    "I": [
        # DC current source (skin i): plain value and/or DC specifier.
        {
            "skin_alias": ["i"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["c", "dc"],
                    "display_alias": "value",
                    "is_positional": "positional_or_keyword",
                },
                # transient function (SIN(...), PULSE(...), ...): a value of
                # its own next to the DC value (V1 a 0 DC 2 SIN(1 0.5 1k)),
                # read by _read_specifiers; written alone it is the value above
                "tran": {
                    "skin_label": None,
                    "display_alias": "tran",
                    "alias": [],
                    "is_positional": "keyword",
                    "functions": ["sin", "sine", "pulse", "pwl", "exp", "sffm", "am"],
                },
            },
            "specifiers": {
                "DC": {"refs": [2], "is_optional": True},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC current source (skin si): chosen like the AC voltage source.
        {
            "skin_alias": ["si"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["c", "dc"],
                    "display_alias": "value",
                    "is_positional": "positional_or_keyword",
                },
                # transient function (SIN(...), PULSE(...), ...): a value of
                # its own next to the DC value (V1 a 0 DC 2 SIN(1 0.5 1k)),
                # read by _read_specifiers; written alone it is the value above
                "tran": {
                    "skin_label": None,
                    "display_alias": "tran",
                    "alias": [],
                    "is_positional": "keyword",
                    "functions": ["sin", "sine", "pulse", "pwl", "exp", "sffm", "am"],
                },
                "AC": {
                    "skin_label": None,
                    "alias": ["AC", "acmag"],
                    "display_alias": "mag",
                    "is_positional": "keyword",
                },
                "phase": {
                    "skin_label": None,
                    "alias": ["acphase"],
                    "display_alias": "phase",
                    "is_positional": "keyword",
                },
            },
            "specifiers": {
                "DC": {"refs": [2], "is_optional": True},
                "AC": {"refs": ["AC", "phase"], "is_optional": True, "default": "1"},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # Diode — Dname N+ N- mname [area]  (ngspice §7.2)
    # model name is the LAST token; its .model TYPE (in-file table) must be
    # 'd' for this spec to match. Nothing displayed.
    "D": [
        {
            "skin_alias": ["d_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "model_type": ["D"],
            "port_directions": {"+": "input", "-": "output"},
        },
        {
            "skin_alias": ["d_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "model_type": ["D"],
            "kind": ["off"],
            "port_directions": {"+": "input", "-": "output"},
        },
    ],
    # BJT — Qname C B E [substrate] mname  (ngspice §7.3.1)
    # the skin has 3 pins (c/b/e); the optional substrate node (4th token when
    # present) has no skin pin and is DROPPED. The model name is the LAST
    # token: its .model TYPE (NPN/PNP) selects the spec through the model
    # table.
    "Q": [
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {},
            "model_type": ["NPN"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
                3: {"alias": "ns", "drop": True},  # substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["NPN"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {},
            "model_type": ["NPN"],
            "kind": ["off"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
                3: {"alias": "ns", "drop": True},  # substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["NPN"],
            "kind": ["off"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_pnp"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {},
            "model_type": ["PNP"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_pnp"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
                3: {"alias": "ns", "drop": True},  # substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["PNP"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_pnp"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {},
            "model_type": ["PNP"],
            "kind": ["off"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_pnp"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
                3: {"alias": "ns", "drop": True},  # substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["PNP"],
            "kind": ["off"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
    ],
    # JFET — Jname D G S mname  (ngspice §7.4.1). Model type NJF/PJF selects.
    "J": [
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["NJF"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["NJF"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["PJF"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["PJF"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
    ],
    # MOSFET — Mname D G S B mname  (ngspice §7.6.1; LTspice VDMOS: 3 nodes)
    # skin has 3 pins (d/g/s); the bulk node (4th token in the 5-token form)
    # is DROPPED as the skin has no bulk pin. Model name is the LAST token:
    # its .model TYPE (NMOS/PMOS) selects the spec via the model table.
    "M": [
        {
            "skin_alias": ["mos_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
                3: {"alias": "b", "drop": True},  # bulk/substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["NMOS"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["mos_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
                3: {"alias": "b", "drop": True},  # bulk/substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["PMOS"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["mos_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
                3: {"alias": "b", "drop": True},  # bulk/substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["NMOS"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["mos_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
                3: {"alias": "b", "drop": True},  # bulk/substrate: no skin pin
            },
            "args_to_values": {},
            "model_type": ["PMOS"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
    ],
    # VCVS — Ename N+ N- NC+ NC- value  (ngspice §4.2.2 linear; §5.2 non-linear)
    # skin is 2-pin: control nodes are DROPPED (hidden).
    # Linear form puts the gain at index 4. Non-linear forms (ngspice §5.2)
    # have NO control nodes — the gain is a keyword arg, so a 2nd spec
    # consumes it at index 2.
    "E": [
        {
            "skin_alias": ["cvs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {4: {"skin_label": "value", "alias": ["gain"], "is_optional": False}},
            "port_directions": {"+": "output", "-": "input"},
        },
        # Non-linear (2-node): the expression keyword is the only argument.
        # It MUST be a keyword (cur=) — the key identifies the
        # expression form, so is_positional is "keyword".
        {
            "skin_alias": ["cvs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                # first argument after the nodes, always written vol=...:
                # ngspice rejects `E1 a b m=1 vol={...}` and a bare `{...}`
                2: {
                    "skin_label": "value",
                    "alias": ["vol"],
                    "display_alias": "expr",
                    "is_positional": "positional_with_key",
                    "is_optional": False,
                    "is_expression": True,
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # VCCS — Gname N+ N- NC+ NC- value  (ngspice §4.2.1 linear; §5.3 non-linear)
    # Linear gain at index 4; non-linear 2-node keyword forms (cur=/value=)
    # get their own spec, same split as E.
    "G": [
        {
            "skin_alias": ["ccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {
                4: {
                    "skin_label": "value",
                    "alias": ["gain"],
                    "is_optional": False,
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # Non-linear (2-node): the expression keyword is the only argument.
        # It MUST be a keyword (cur=/value=/table=) — is_positional "keyword".
        {
            "skin_alias": ["ccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                # first argument after the nodes, always written cur=...
                # (as E's vol=)
                2: {
                    "skin_label": "value",
                    "alias": ["cur"],
                    "display_alias": "expr",
                    "is_positional": "positional_with_key",
                    "is_optional": False,
                    "is_expression": True,
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # CCCS — Fname N+ N- Vname value  (ngspice §4.2.3)
    # Vname is a controlling VOLTAGE SOURCE reference (not a node, not drawn).
    "F": [
        {
            "skin_alias": ["ccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    # no alias
                    "skin_label": None,
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                    "is_positional": "positional_only",
                    "display_alias": "Vcontrol",
                },
                3: {
                    "skin_label": "value",
                    "alias": ["gain"],
                    "is_optional": False,
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # CCVS — Hname N+ N- Vname value  (ngspice §4.2.4)
    "H": [
        {
            "skin_alias": ["cvs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "vcontrol",
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                    "is_positional": "positional_only",
                    "display_alias": "Vcontrol",
                },
                3: {
                    "skin_label": "value",
                    "alias": ["gain"],
                    "is_optional": False,
                },
            },
            "port_directions": {"+": "output", "-": "input"},
        }
    ],
    # Voltage-controlled switch — Sname N+ N- NC+ NC- mname [ON|OFF]
    # (ngspice §3.3.15). 4 nodes: the second pair controls the switch; the
    # skin only has 2 pins so the control pair is DROPPED. The model name
    # binds to a .model ... SW card (kind = SW).
    "S": [
        {
            "skin_alias": ["sw_no"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {},
            "model_type": ["SW"],
            "port_directions": {"p": "input", "n": "input"},
        },
        {
            "skin_alias": ["sw_nc"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {},
            "model_type": ["SW"],
            "kind": ["ON"],
            "port_directions": {"p": "input", "n": "input"},
        },
        {
            "skin_alias": ["sw_no"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {},
            "model_type": ["SW"],
            "kind": ["OFF"],
            "port_directions": {"p": "input", "n": "input"},
        },
    ],
    # Lossless transmission line — Tname N1 N2 N3 N4 Z0=val TD=val  (ngspice §6.1)
    # skin is the dedicated 2-pin tline symbol (circuitikz to[tline]); the
    # 2nd port pair (N3/N4) has no pin on the bipole skin and is DROPPED.
    # Keyword args (Z0=) are consumed via the '=' check.
    "T": [
        {
            "skin_alias": ["tline"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "p2+", "drop": True},
                3: {"alias": "p2-", "drop": True},
            },
            "args_to_values": {
                "Z0": {
                    "skin_label": None,
                    "alias": ["Z0", "zo"],
                    "is_positional": "keyword",
                    "is_optional": False,
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # Current-controlled switch — Wname N+ N- Vname mname <ON|OFF>
    # (ngspice §3.3.15; model type CSW). The 3rd arg VNAM is a controlling
    # VOLTAGE SOURCE reference (not a node) — shown via the 'vcontrol' skin
    # label, same convention as the F/H controlled sources. The model name
    # binds to a .model ... CSW card (kind = CSW).
    "W": [
        {
            "skin_alias": ["csw"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                # controlling voltage anme
                2: {
                    "skin_label": None,
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                    "display_alias": "Vcontrol",
                    "is_positional": "positional_only",
                },
            },
            # the model name comes AFTER the controlling V source; ngspice
            # reads `W1 a b WM V1` as a model named V1
            "model_type": {"CSW": {"ref_arg": 2, "before_arg": False}},
            "port_directions": {"+": "input", "-": "input"},
        },
        {
            "skin_alias": ["csw"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                    "display_alias": "Vcontrol",
                    "is_positional": "positional_only",
                },
            },
            # the model name comes AFTER the controlling V source; ngspice
            # reads `W1 a b WM V1` as a model named V1
            "model_type": {"CSW": {"ref_arg": 2, "before_arg": False}},
            "kind": ["ON"],
            "port_directions": {"+": "input", "-": "input"},
        },
        {
            "skin_alias": ["csw"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                    "display_alias": "Vcontrol",
                    "is_positional": "positional_only",
                },
            },
            # the model name comes AFTER the controlling V source; ngspice
            # reads `W1 a b WM V1` as a model named V1
            "model_type": {"CSW": {"ref_arg": 2, "before_arg": False}},
            "kind": ["OFF"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Behavioral source — Bname N+ N- V=expr | I=expr  (ngspice §5.1.1)
    # The expression is a single token (no spaces in the corpus). Two specs
    # split on the V=/I= alias: a voltage expression draws as a voltage
    # source, a current expression as a current source. The V=/I= token is a
    # KEYWORD arg (is_positional "keyword") — its key is what discriminates the
    # source type, so it can only be provided in keyword form.
    "B": [
        {
            "skin_alias": ["v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                # first argument after the nodes, always written V=...:
                # ngspice rejects `B1 a b tc1=0 V=2` and a bare `2`
                2: {
                    "skin_label": "value",
                    "alias": ["V"],
                    "is_positional": "positional_with_key",
                    "display_alias": "expr",
                    "is_optional": False,  # V= is the spec discriminator
                },
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        {
            "skin_alias": ["i"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                # first argument after the nodes, always written I=... (as V=)
                2: {
                    "skin_label": "value",
                    "alias": ["I"],
                    "is_positional": "positional_with_key",
                    "display_alias": "expr",
                    "is_optional": False,  # I= is the spec discriminator
                },
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # Lossy transmission line — Oname N1 N2 N3 N4 mname  (ngspice §6.2, LTRA)
    # Same shape as T: the dedicated tline bipole symbol is used and the 2nd
    # port pair (N3/N4) is DROPPED. mname must name an LTRA .model card,
    # which holds the line parameters (MODEL_CONFIG); it does not change
    # the symbol.
    "O": [
        {
            "skin_alias": ["tline"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "p2+", "drop": True},
                3: {"alias": "p2-", "drop": True},
            },
            "args_to_values": {},
            "model_type": ["LTRA"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # KSPICE single lossy transmission line (TXL) — Yname N1 0 N2 0 mname
    # <LEN=len>  (ngspice §6.4.1). Same bipole tline symbol; the second node
    # pair (N1b=0, N2b=0) is DROPPED. LEN= keyword consumed via '=' check.
    "Y": [
        {
            "skin_alias": ["tline"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "p2+", "drop": True},
                3: {"alias": "p2-", "drop": True},
            },
            "args_to_values": {
                "len": {
                    "skin_label": None,
                    "alias": ["len"],
                    "is_positional": "keyword",
                },
            },
            # the model name comes BEFORE the length (`Y1 a 0 b 0 YM LEN=2`);
            # written after it, ngspice (32, 47) reads the
            # length as the model name ("Unable to find definition of model")
            "model_type": ["txl"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # MESFET — Zname D G S mname  (ngspice NMF/PMF). The model TYPE (NMF =
    # n-channel, PMF = p-channel) selects the JFET symbol via the model
    # table, exactly like the J prefix.
    "Z": [
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["NMF"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["NMF"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["PMF"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {},
            "model_type": ["PMF"],
            "kind": ["off"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
    ],
    # Subcircuit invocation — Xname N1... SUBNAM [param=value]  (ngspice §2.6.3)
    # An explicit generic: parsed without this spec into the generic skin,
    # with pins named after the .subckt ports (numbered when the definition
    # is missing) and the subckt name as value. Strict parsing checks the
    # instance against its .subckt (port count, declared params).
    "X": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
        }
    ],
    # XSPICE code model: needs a code-model .model card, which the grammar
    # does not teach.
    "A": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
        }
    ],
    # URC line / digital device: needs a .model ... URC card, which is not
    # in MODEL_CONFIG.
    "U": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
        }
    ],
    # Coupled multiconductor line (CPL): its model takes R/L/G/C matrices.
    "P": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
        }
    ],
    # OSDI (Verilog-A compiled) device — Nname N1 ... mname [param=value]
    # (ngspice §2.1 table 2.2). Pin count depends on the compiled model, so it
    # is an explicit generic like X/A/U/P.
    "N": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
            # ngspice 34 has no OSDI support: "unknown device type"
        }
    ],
}


def _required(*names: str) -> dict:
    """args_to_values entries for .model params that must be PRESENT on the
    card (0 is a fine value), keyed by the canonical name (the spelling
    ngspice accepts; matched case-insensitively)."""
    return {name: {"alias": [name], "is_optional": False} for name in names}


# .model cards, keyed by model TYPE (upper case). The specs in TO_SKIN_CONFIG
# refer to these through their "model_type"; every model_type used there must
# have an entry here. The value is a LIST of configurations, like a prefix's
# spec list in TO_SKIN_CONFIG: a card is valid when it matches ONE of them.
#
#   args_to_values : the configuration's parameters, declared like a spec's
#                    args_to_values. .model params are always key=value, so
#                    entries are keyed by the canonical param name and take
#                    "alias", "is_optional", "default_value" (and optionally
#                    "skin_label"). A required param must be PRESENT on the
#                    card; 0 is a valid value. An entry with "is_zero" must
#                    additionally BE 0 (or, when optional, absent). Only
#                    params worth declaring are listed: ngspice defaults
#                    most of them, so most types declare none; undeclared
#                    params are allowed (ngspice reports bad ones).
#   variant        : label for error messages / the grammar listing when a
#                    type has several configurations (the netlist never
#                    writes it, unlike an element kind keyword).

MODEL_CONFIG = {
    # resistors / capacitors / inductors (semiconductor forms)
    "R": [{"args_to_values": {}}],
    "RES": [{"args_to_values": {}}],
    "C": [{"args_to_values": {}}],
    "L": [{"args_to_values": {}}],
    "D": [{"args_to_values": {}}],
    # BJT
    "NPN": [{"args_to_values": {}}],
    "PNP": [{"args_to_values": {}}],
    # JFET / MOSFET / MESFET
    "NJF": [{"args_to_values": {}}],
    "PJF": [{"args_to_values": {}}],
    "NMOS": [{"args_to_values": {}}],
    "PMOS": [{"args_to_values": {}}],
    "NMF": [{"args_to_values": {}}],
    "PMF": [{"args_to_values": {}}],
    # switches
    "SW": [{"args_to_values": {}}],
    "CSW": [{"args_to_values": {}}],
    # Lossy line (ngspice §6.2.1). LEN has no default and is required.
    # Omitted R/L/C only warn ("assumed zero"), but are required here so a
    # card always states the line it means (any mix is accepted: RLC, RC
    # with L=0, LC with R=0). G must be 0: ngspice rejects a non-zero one
    # with "Nonzero G (except RG) line not supported yet". The RG line
    # (non-zero R and G, zero L and C) does run, but ngspice prints a
    # "Fatal error" line about the missing capacitance while doing so, so
    # it is not offered here.
    "LTRA": [
        {
            "args_to_values": {
                **_required("R", "L"),
                **_required("C", "LEN"),
                "G": {"alias": ["G"], "is_optional": False, "is_zero": True},
            },
        },
    ],
    # KSPICE lossy line (ngspice §6.4.1). Unlike LTRA, every one of R/L/G/C
    # must be PRESENT (ngspice: "lossy line ... not given" is fatal here even
    # though the manual lists 0.0 defaults), and the length belongs in the
    # CARD: LEN= on the Y line only OVERRIDES it, so a card without "length"
    # fails with "lossy line length must be given". Non-zero G is fine.
    "TXL": [{"args_to_values": _required("R", "L", "G", "C", "LENGTH")}],
}


# Bare identifiers that ngspice resolves inside expressions without a .param
# definition (strict parsing only). Matched case-insensitively. Function names
# (sin, exp, PULSE, ...) are NOT listed: the parser does not check them, an
# unknown function is reported by ngspice itself (the ngspice smoke test).
EXPRESSION_CONSTANTS = {
    "pi",
    "e",
    "time",
    "temper",
    "hertz",
    "boltz",
    "planck",
    "echarge",
    "kelvin",
}
