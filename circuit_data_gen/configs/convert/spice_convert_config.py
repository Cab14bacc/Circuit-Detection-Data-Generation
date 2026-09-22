"""
SPICE (ngspice/LTspice) netlist conversion configuration.

This module defines TO_SKIN_CONFIG (keyed by element letter, following the
ngspice element-letter convention from the ngspice manual Table 2.2). It is
loaded when NETLIST_FORMAT == "spice" in config.py; the lcapy grammar lives
in default_convert_config.py (TO_SKIN_CONFIG) and the two are never mixed at
parse time — convert.py branches on the format for its logic.

SPICE syntax differences from the lcapy dialect handled here:
  - Values may be function calls: SINE(0 1m 100), PULSE(0 5 100u 10n 10n 500u 1)
    — the tokenizer joins balanced parentheses into a single token (a space
    before the paren, PWL (0,0 ...), is glued by _glue_paren_values).
  - Elements reference MODEL names (D/Q/J/M/Z/R/C/L): the model name token is
    positional and consumed (skin_label None), NOT a node; its .model TYPE
    selects the spec (kind matching via the model table).
  - Independent sources (V/I) use mode SPECIFIERS — bare keywords with fixed
    operand arity (DC VALUE, AC [ACMAG [ACPHASE]]) that are declaratively
    expanded into key=value tokens before matching; the DC vs AC specifier
    split doubles as DC-source vs AC-source skin discrimination.
  - Ground conventions: node "0", plus "gnd"/"GND" (auto-merged).
  - Directives (.model .lib .tran .backanno .end ...) are filtered before parsing.
  - Subcircuits (.subckt / X expansion) are NOT supported yet: X lines fall
    back to the generic skin.

===========================================================================
GRAMMAR — feed this section to an LLM to generate netlists in this dialect.

Each netlist line defines exactly ONE element:
    <Name> <args...>
Name = element letter + instance id (R1, Vsrc, Mload); the element letter
selects the rule below. Node "0" (also GND / gnd) is ground — all ground
nodes are merged into one. Lines are case-insensitive.

TOKEN NOTATION — what each kind of token in the rules below means:
  Ncode      NODE placeholder: write the net's real name (in, out, vdd, 0).
             N+ is the main positive terminal and N- the negative one;
             element-specific codes are named per rule below (NC collector,
             NB base, NE emitter, ND drain, NG gate, NS source, NP/N switch
             terminals). Real net names are any word.
             Some nodes are HIDDEN (named in the rule's comment): they are
             STILL REQUIRED in the line and electrically connected, but the
             symbol has no pin for them, so they are NOT drawn (the spec
             marks them "drop"). Used for: Q substrate NS, M bulk NB,
             E/G/S control pairs NC+ NC-, and the far-end pair NP2+ NP2-
             of transmission lines. Write 0 or a real net, as a plain name:
             strict parsing rejects braced node tokens ({n1}).
  <value>    REQUIRED value token: write the number/expression itself
             (1k, 5, 2m, SIN(0 1m 100)), never the placeholder word.
             Expressions go in curly braces ({2*Rbase}); every name in them
             must be a .param, a built-in constant (EXPRESSION_CONSTANTS),
             a function, v(node) or i(Vname).
  [ ... ]    OPTIONAL: the whole bracketed group may be omitted.
  Key=val    KEYWORD argument, written exactly in that form (Q=2n, Z0=50,
             TD=10n, cur='...', V='...'). Unbracketed Key=val = required,
             bracketed [Key=val] = optional.
  bare kw    LITERAL keyword written verbatim: off (device starts off),
             ON / OFF (switch state), DC / AC (source mode specifiers).
  mname      MODEL name: written after all nodes and values of a
             model-typed element (only off / ON / OFF may follow it),
             and a .model card declaring it is REQUIRED in the same
             netlist. The card's TYPE selects the symbol; the COMPLETE
             set of supported TYPEs is: D (D), NPN / PNP / LPNP (Q),
             NJF / PJF (J), NMOS / PMOS (M), NMF / PMF (Z), SW (S),
             CSW (W), R / RES (R), C (C), L (L), LTRA (O), TXL (Y) —
             element letter in parentheses. A model TYPE outside this set
             (URC, LTspice's VDMOS / CAP / IND, ...) has NO spec, and a
             model name without a .model card cannot be resolved: both
             make the line fail. Only use the listed TYPEs.
  Vcontrol   Controlling-source REFERENCE: the NAME of a V element defined
             elsewhere in the netlist (e.g. V1) — not a node, not drawn.

KIND KEYWORDS vs SPECIFIERS — two classes of bare literal words, not
interchangeable:
  kind keyword  ONE bare word, NO operands, written right after mname;
                selects which VARIANT of the element the line is
                (symbol or state):
                off (device starts off, same symbol), ON / OFF (switch
                state: ON closed, OFF open, omitted = default open).
                Omitting it selects the default variant. For model-typed
                elements the variant comes from the .model TYPE instead
                (NPN vs PNP, NMOS vs PMOS, ...) — the model type acts as
                an implicit kind keyword.
  specifier     V/I SOURCES ONLY: a bare keyword that OPENS a mode
                segment with a fixed operand arity, expanded into
                keyword slots before matching:
                  DC <value>           transparent — binds like a bare value
                  AC <mag> [<phase>]   makes the source an AC source
                                       (different symbol); bare AC == AC 1
                The AC specifier doubles as DC-vs-AC source
                discrimination: the DC-source rule treats a bare AC
                token as foreign and rejects it, the AC-source rule
                requires it. DC and AC segments may coexist on one line
                (V3 a b 5 AC 1m = DC 5 with AC magnitude 1m).
Reserved spellings — never use as net names: DC, AC, off, ON, OFF.
===========================================================================

Passives (plain value forms; semiconductor forms additionally end in mname):
    Rname N+ N- <r>                       resistance; e.g. R1 in out 1k
    Cname N+ N- <C>                       capacitance; e.g. C1 in out 1u
    Lname N+ N- <L>                       inductance; e.g. L1 in out 10u
    (semiconductor forms — referenced via a .model card, mname LAST):
    Rname N+ N- [<r>] mname               model R/RES; r defaults 1e-3
    Cname N+ N- [<CAP>] mname             model C; CAP defaults 0
    Lname N+ N- [<L>] mname               model L; L defaults 0
    (capacitor specified by charge instead of capacitance):
    Cname N+ N- Q=<charge>                e.g. C1 in out Q=2n

Independent sources (the AC specifier decides DC vs AC source):
    Vname N+ N- [<value>|DC <value>]      DC source; e.g. V1 in 0 5 / V2 in 0 DC 5
    Vname N+ N- [<value>|DC <value>] AC <mag> [<phase>]
                                          AC source;
                                          e.g. V3 in 0 AC 1  /  V4 a b 5 AC 1m
    Iname N+ N- [<value>|DC <value>]      DC source; e.g. I1 vdd 0 2m
    Iname N+ N- [<value>|DC <value>] AC <mag> [<phase>]
                                          AC source
    - `[<value>|DC <value>]` = pick AT MOST ONE: a bare value, or the DC
      specifier with its value — they bind the SAME slot, never write both
      (Vname N+ N- 5 DC 7 is invalid). The whole group may be omitted:
      `Vname N+ N-` and a trailing bare `DC` are valid and mean no value.
    - a bare AC segment (AC, then magnitude, then optional phase) makes the
      element an AC source; a line without AC is a DC source. Bare AC with
      no operands means magnitude 1. The value/DC group must come BEFORE
      the AC segment; extra Key=val parameters may follow it.
    - DC is transparent: `DC 5` binds exactly like a bare `5`.
    - function values are single tokens: SIN(0 1m 1k) PULSE(0 5 ...)
      PWL(0,0 1m,5)

Controlled sources (control terminals are written even though not drawn):
    Ename N+ N- NC+ NC- <gain>            VCVS (voltage_gain); NC+ NC- hidden
    Ename N+ N- vol='<expr>'              non-linear V source (2 nodes only)
    Gname N+ N- NC+ NC- <gm>              VCCS (transconductance); NC+ NC- hidden
    Gname N+ N- cur='<expr>'              non-linear I source (2 nodes only)
    Fname N+ N- Vcontrol <gain>           CCCS (current_gain)
    Hname N+ N- Vcontrol <resistance>     CCVS (transresistance)

Behavioral sources (exactly one of V= / I=; the keyword selects the symbol):
    Bname N+ N- V=<expr>                  draws as a voltage source
    Bname N+ N- I=<expr>                  draws as a current source

Model-typed elements (mname follows the nodes and matches a .model card;
optional `off` after mname starts the device off; the card's TYPE
picks the symbol variant — accepted TYPEs listed per rule):
    Dname N+ N- mname [off]               TYPE: D
    Qname NC NB NE [NS] mname [off]       NS = substrate node, hidden;
                                          TYPE: NPN | PNP | LPNP
    Jname ND NG NS mname [off]            TYPE: NJF | PJF
    Mname ND NG NS NB mname [off]         NB = bulk node, hidden;
                                          TYPE: NMOS | PMOS
    Zname ND NG NS mname [off]            MESFET; TYPE: NMF | PMF

Switches (model TYPE SW for S, CSW for W; ON or OFF selects the state,
omitted = default open switch):
    Sname NP N NC+ NC- mname [ON|OFF]     voltage-controlled; NP/N = switch
                                          terminals, NC+ NC- = control
                                          pair, hidden
    Wname N+ N- Vcontrol mname [ON|OFF]   current-controlled

Transmission lines (the far-end pair NP2+ NP2- is hidden: still written,
not drawn — the bipole symbol has one pin pair):
    Tname N+ N- NP2+ NP2- Z0=<val> [TD=<val>] [F=<freq> [NL=<len>]]
                                          [IC=<v1,i1,v2,i2>]
    Oname N+ N- NP2+ NP2- mname           lossy line (LTRA model)
    Yname N+ N- NP2+ NP2- mname [LEN=<len>]   KSPICE TXL
    Their .model cards REQUIRE the line parameters (per-unit-length R, L,
    G, C and the line length); ngspice has no defaults, write them all:
    .model mname LTRA R=<value> L=<value> C=<value> LEN=<value> G=0
    .model mname TXL R=<value> L=<value> G=<value> C=<value> LENGTH=<value>
    LTRA: G MUST be 0 (only RLC / RC / LC lines are implemented; write
    L=0 for an RC line, R=0 for an LC line). TXL: the length belongs in
    the CARD, LEN= on the Y line only overrides it.

Generic elements (explicit generics: pin count depends on a definition,
drawn as a generic box; the last non-keyword token is the definition name):
    Xname N1 ... Nn SUBNAM [param=value ...]   subcircuit instance; strict
                                          parsing requires a .subckt SUBNAM
    Aname ...                             XSPICE code model
    Nname ...                             OSDI (Verilog-A) device
    Pname ...                             coupled multiconductor line (CPL)
    Uname ...                             uniform RC line / digital device

Directives (not drawn). Strict parsing accepts ONLY:
    .model mname TYPE(param=val ...)      REQUIRED for every model-typed
                                          element reference
    .param name=value ...                 names used in {expressions}
    .subckt SUBNAM N1 ... Nn / .ends      subcircuit definition (body ignored)
    .end
Lenient parsing ignores every other directive (.tran .ac .dc .op .lib
.include .backanno ...) and skips .control ... .endc blocks; strict parsing
rejects them (see STRICT_ALLOWED_DIRECTIVES in convert.py).
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
                2: {"skin_label": "value", "alias": ["r", "resistance"], "is_optional": False},
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
                },
            },
            "model_type": ["R", "RES"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Capacitor — Cname N+ N- <value> [mname] <ic=v>  (ngspice §3.3.6)
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
                    "alias": ["C"],
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
                    "alias": ["CAP", "C"],
                    "default_value": "0",
                },
            },
            "model_type": ["C"],
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
                "Q": {"skin_label": "value", "alias": ["Q"], "is_positional": False, "is_optional": False},
                # 3rd arg may be a model name (mname) — consumed, not shown
            },
            "port_directions": {"+": "input", "-": "output"},
        },
    ],
    # Inductor — Lname N+ N- <value> [mname]  (ngspice §3.3.10)
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
                    "alias": ["L", "IND"],
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
                2: {"skin_label": "value", "alias": ["L", "IND"], "is_optional": True, "default_value": "0"},
            },
            "model_type": ["L"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Voltage source — Vname N+ N- <value|function|specifier stream>  (ngspice §4.1)
    # SPICE value forms are single tokens once parens are joined:
    #   V1 a b 5            plain DC
    #   V3 IN 0 SINE(0 1m 100)
    #   V4 a b PULSE(0 5 100u 10n 10n 500u 1)
    #   V1 a b PWL(0,0 1m 5)
    # Mode specifiers (DC/AC) are declaratively expanded before matching, and
    # ALSO split the source into two specs (mirroring the lcapy dialect's
    # dc/ac kind split):
    #   DC source (skin v):  value and/or DC specifier; AC is FOREIGN here —
    #                        any AC on the line rejects this candidate.
    #   AC source (skin sv): AC specifier REQUIRED; value-only lines reject.
    #   V1 a b DC 5 AC 1m    -> AC source: value=5, ac=1m
    #   V1 a b AC 1 90       -> AC source: ac=1, phase=90 (bare AC -> ac=1)
    "V": [
        # DC voltage source (skin v): plain value and/or the DC specifier.
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
                    "alias": ["Value", "v", "dc", "DC"],
                },
            },
            "specifiers": {
                # DC VALUE  -> transparent: operand lands in the value slot
                "DC": {"refs": [2], "is_optional": True},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC voltage source: AC specifier is required, so value-only lines
        # reject this spec and match the DC spec instead.
        {
            "skin_alias": ["sv"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "v", "dc", "DC"],
                },
                "AC": {
                    "skin_label": None,
                    "alias": ["AC", "ac", "mag"],
                    "is_positional": False,
                },
                "phase": {
                    "skin_label": None,
                    "alias": ["phase", "Phase"],
                    "is_positional": False,
                },
            },
            "specifiers": {
                # DC VALUE  -> transparent: operand lands in the value slot
                "DC": {"refs": [2], "is_optional": True},
                # AC [ACMAG [ACPHASE]] — bare AC == AC 1 (default injected at
                # expansion time when the specifier opens with no operands)
                "AC": {"refs": ["AC", "phase"], "is_optional": False, "default": "1"},
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
                    "alias": ["Value", "i", "dc"],
                },
            },
            "specifiers": {
                "DC": {"refs": [2], "is_optional": True},
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC current source (skin si): AC specifier required.
        {
            "skin_alias": ["si"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "i", "dc"],
                },
                "AC": {
                    "skin_label": None,
                    "alias": ["AC", "ac", "mag"],
                    "is_positional": False,
                },
                "phase": {
                    "skin_label": None,
                    "alias": ["phase", "Phase"],
                    "is_positional": False,
                },
            },
            "specifiers": {
                "DC": {"refs": [2], "is_optional": True},
                "AC": {"refs": ["AC", "phase"], "is_optional": False, "default": "1"},
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
    # token: its .model TYPE (NPN/PNP/LPNP) selects the spec — kind matching
    # via the model table, exactly like a lcapy kind keyword.
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
            "skin_alias": ["q_npn"],  # LPNP (lateral PNP) draws NPN-style here
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {},
            "model_type": ["LPNP"],
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
            "model_type": ["LPNP"],
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
            "model_type": ["LPNP"],
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
            "model_type": ["LPNP"],
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
    # skin is 2-pin: control nodes are DROPPED (same as lcapy dialect E spec).
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
            "args_to_values": {4: {"skin_label": "value", "alias": ["voltage_gain"], "is_optional": False}},
            "port_directions": {"+": "output", "-": "input"},
        },
        # Non-linear (2-node): the expression keyword is the only argument.
        # It MUST be a keyword (cur=) — the key identifies the
        # expression form, so is_positional is False.
        {
            "skin_alias": ["cvs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["vol"],
                    "is_positional": False,
                    "is_optional": False,
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
                    "alias": ["transconductance"],
                    "is_optional": False,
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        # Non-linear (2-node): the expression keyword is the only argument.
        # It MUST be a keyword (cur=/value=/table=) — is_positional False.
        {
            "skin_alias": ["ccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                "cur": {
                    "skin_label": "value",
                    "alias": ["cur"],
                    "is_positional": False,
                    "is_optional": False,
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # CCCS — Fname N+ N- Vname value  (ngspice §4.2.3)
    # Vname is a controlling VOLTAGE SOURCE reference (not a node); the lcapy
    # dialect shows it via the 'vcontrol' skin label.
    "F": [
        {
            "skin_alias": ["ccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Vcontrol", "VNAM"],
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                },
                3: {
                    "skin_label": "value",
                    "alias": ["current_gain"],
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
                    "alias": ["Vcontrol", "VNAM"],
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                },
                3: {
                    "skin_label": "value",
                    "alias": ["transresistance"],
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
                    "alias": ["Z0"],
                    "is_positional": False,
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
                    "alias": ["Vcontrol", "VNAM"],
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                },
            },
            "model_type": ["CSW"],
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
                    "alias": ["Vcontrol", "VNAM"],
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                },
            },
            "model_type": ["CSW"],
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
                    "alias": ["Vcontrol", "VNAM"],
                    # strict parsing: must name a V element of the netlist
                    "is_reference": "V",
                    "is_optional": False,
                },
            },
            "model_type": ["CSW"],
            "kind": ["OFF"],
            "port_directions": {"+": "input", "-": "input"},
        },
    ],
    # Behavioral source — Bname N+ N- V=expr | I=expr  (ngspice §5.1.1)
    # The expression is a single token (no spaces in the corpus). Two specs
    # split on the V=/I= alias: a voltage expression draws as a voltage
    # source, a current expression as a current source. The V=/I= token is a
    # KEYWORD arg (is_positional False) — its key is what discriminates the
    # source type, so it can only be provided in keyword form.
    "B": [
        {
            "skin_alias": ["v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                "V": {
                    "skin_label": "value",
                    "alias": ["V"],
                    "is_positional": False,
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
                "I": {
                    "skin_label": "value",
                    "alias": ["I"],
                    "is_positional": False,
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
                4: {"skin_label": None, "alias": ["LEN", "len"]},
            },
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
    # NOT SUPPORTED yet (needs .subckt expansion / model-name table).
    # Placeholder: parse into the generic skin (numeric pin slots + the
    # subckt name as value), matching the lcapy generic fallback behavior.
    "X": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
        }
    ],
    # XSPICE code model: needs a code-model .model card, which the grammar
    # does not teach, so it is not offered for simulation.
    "A": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
            "simulatable": False,
        }
    ],
    # URC line / digital device: needs a .model ... URC card, which is not
    # in MODEL_CONFIG, so it is not offered for simulation.
    "U": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
            "simulatable": False,
        }
    ],
    # Coupled multiconductor line (CPL): its model takes R/L/G/C matrices,
    # so it is not offered for simulation.
    "P": [
        {
            "skin_alias": "generic",
            "arg_to_ports": {},
            "args_to_values": {},
            "port_directions": {},
            "simulatable": False,
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
            "simulatable": False,
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
#   simulatable    : False when the bundled ngspice (34) cannot simulate the
#                    configuration at all; defaults to True.
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
    # ngspice 34 rejects it: "model type mismatch", with or without substrate node
    "LPNP": [{"args_to_values": {}, "simulatable": False}],
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
# unknown function is reported by ngspice itself at simulation time.
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
