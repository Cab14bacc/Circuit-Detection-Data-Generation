"""
The following is a list of all the supported prefixes and their arguments, each line defines a rule.
A full comprehensive list of all the meaning of the parameters is given right after this list.
Some general rules are: parameters that starts with 'N' are node names, those that start with 'V' are values,
and those that start with 'P' are pins, and most of others are keywords that are used to define the type of the component.
There are exceptions for example, "fo" is a value param, it refers to Sinewave frequency.
Optional arguments are in square brackets [key=value]. Here is the list to all the supported prefixes and their arguments:
================================================= start of list ================================================
format:ADCname Np Nm                                                    , component:ADC
format:AMname Np Nm                                                     , component:Ammeter
format:ANTname Np                                                       , component:Antenna
format:BATname Np Nm [Value=name]                                       , component:Battery
format:Cname Np Nm [Value=name] [IC]                                    , component:Capacitor
format:Dname Np Nm                                                      , component:Diode
format:DACname Np Nm                                                    , component:DAC
format:Dname Np Nm led                                                  , component:Light emitting diode
format:Dname Np Nm zener                                                , component:Zener diode
format:Dname Np Nm photo                                                , component:Photo diode
format:Dname Np Nm tunnel                                               , component:Tunnel diode
format:Dname Np Nm schottky                                             , component:Schottky diode
format:Ename Np Nm Ncp Ncm [Value=name] [Ac=0]                          , component:Voltage controlled voltage source
format:Ename Np Nm opamp Ncp Ncm [Ad=name] [Ac=0] [Ro=0]                , component:Opamp
format:Ename Np Nm fdopamp Ncp Ncm Nocm [Ad=name] [Ac=0]                , component:Fully differential opamp
format:Ename Np Nm inamp Ncp Ncm NRp NRm [Ad=name] [Ac=0] [Rf=Rf]       , component:Instrumentation opamp
format:Ename Np Nm amp Ncp Ncm [Ad=name] [Ac=0]                         , component:Amplifier
format:Fname Np Nm Vcontrol [Value=name]                                , component:Current controlled current source (note the control current is specified through a voltage source)
format:Gname Np Nm Ncp Ncm [Value=name]                                 , component:Voltage controlled current source
format:Hname Np Nm Vcontrol [Value=name]                                , component:Current controlled voltage source (note the control current is specified through a voltage source)
format:Iname Np Nm [Value=name]                                         , component:Current source
format:Iname Np Nm dc [Value=name]                                      , component:DC current source
format:Iname Np Nm ac [Value=name] [Phase] [Omega]                      , component:AC current source
format:Iname Np Nm sin Io Ia fo [td] [alpha] [Phase]                    , component:Sinusoidal current source
format:Jname Nd Ng Ns [Value=name]                                      , component:N channel JFET
format:Jname Nd Ng Ns njf [Value=name]                                  , component:N channel JFET
format:Jname Nd Ng Ns pjf [Value=name]                                  , component:P channel JFET
format:kname Np Nm [Value=name] [IC]                                    , component:Spring
format:Lname Np Nm [Value=name] [IC]                                    , component:Inductance
format:mname Np Nm [Value=name] [IC]                                    , component:Mass
format:Mname Nd Ng Ns [Value=name]                                      , component:N channel MOSFET
format:Mname Nd Ng Ns nmos [Value=name]                                 , component:N channel MOSFET
format:Mname Nd Ng Ns pmos [Value=name]                                 , component:P channel MOSFET
format:NRname Np Nm [Value=name]                                        , component:Noiseless resistor
format:Pname Np Np                                                      , component:Port
format:Qname Nc Nb Ne [Value=name]                                      , component:NPN transistor
format:Qname Nc Nb Ne npn [Value=name]                                  , component:NPN transistor
format:Qname Nc Nb Ne pnp [Value=name]                                  , component:PNP transistor
format:rname Np Nm [Value=name]                                         , component:Damper
format:Rname Np Nm [Value=name]                                         , component:Resistor
format:SWname Np Nm [Time=0]                                            , component:Switch normally open
format:SWname Np Nm nc [Time=0]                                         , component:Switch normally closed
format:SWname Np Nm no [Time=0]                                         , component:Switch normally open
format:SWname Np Nm push [Time=0]                                       , component:Pushbutton switch
format:SWname Nc Np Nm spdt [Time=0]                                    , component:SPDT switch
format:TFname Np Nm Ncp Ncm [Ns1=name] [Np1=1]                          , component:Ideal transformer (works to DC!)
format:TPname Np Nm Ncp Ncm                                             , component:Generic two-port
format:Vname Np Nm [Value=name]                                         , component:Voltage source
format:Vname Np Nm dc [Value=name]                                      , component:DC voltage source
format:Vname Np Nm ac [Value=name] [Phase] [Omega]                      , component:AC voltage source
format:Vname Np Nm sin Vo Va fo [td] [alpha] [Phase]                    , component:Sinusoidal voltage source
format:VMname Np Nm                                                     , component:Voltmeter
format:Wname Np Np                                                      , component:Wire
format:XTname Np Nm                                                     , component:Crystal
================================================ end of list ================================================

Here are all the keywords/arguments that are used in the above rules, and their meanings:
================================================= start of list ================================================
param:led       , type:keyword
param:zener     , type:keyword
param:photo     , type:keyword
param:tunnel    , type:keyword
param:schottky  , type:keyword
param:s         , type:keyword
param:ac        , type:keyword
param:core      , type:keyword
param:dc        , type:keyword
param:noise     , type:keyword
param:step      , type:keyword
param:sin       , type:keyword
param:njf       , type:keyword
param:pjf       , type:keyword
param:npn       , type:keyword
param:pnp       , type:keyword
param:nmos      , type:keyword
param:pmos      , type:keyword
param:no        , type:keyword
param:nc        , type:keyword
param:spdt      , type:keyword
param:tap       , type:keyword
param:tapcore   , type:keyword
param:opamp     , type:keyword
param:noisyopamp, type:keyword
param:inamp     , type:keyword
param:fdopamp   , type:keyword
param:amp       , type:keyword
param:core      , type:keyword
param:pp        , type:keyword
param:pm        , type:keyword
param:push      , type:keyword
param:and       , type:keyword
param:or        , type:keyword
param:nor       , type:keyword
param:scs       , type:keyword
param:scss      , type:keyword
param:sscss     , type:keyword
param:A         , type:keyword
param:B         , type:keyword
param:G         , type:keyword
param:H         , type:keyword
param:Y         , type:keyword
param:Z         , type:keyword
param:P         , type:pin       , meaning:Pin
param:Po        , type:pin       , meaning:Output pin
param:Nb        , type:node      , meaning:Base node
param:Nc        , type:node      , meaning:Collector node
param:Ncp       , type:node      , meaning:Positive control node
param:Ncm       , type:node      , meaning:Negative control node
param:Np1p      , type:node      , meaning:Positive node for primary winding 1
param:Np1m      , type:node      , meaning:Negative node for primary winding 1
param:Np2p      , type:node      , meaning:Positive node for primary winding 2
param:Np2m      , type:node      , meaning:Negative node for primary winding 2
param:Ns1p      , type:node      , meaning:Positive node for secondary winding 1
param:Ns1m      , type:node      , meaning:Negative node for secondary winding 1
param:Ns2p      , type:node      , meaning:Positive node for secondary winding 2
param:Ns2m      , type:node      , meaning:Negative node for secondary winding 2
param:Nocm      , type:node      , meaning:Output common-mode node
param:Nd        , type:node      , meaning:Drain node
param:Ne        , type:node      , meaning:Emitter node
param:Ng        , type:node      , meaning:Gate node
param:Nm        , type:node      , meaning:Negative node
param:No        , type:node      , meaning:Output node
param:Np        , type:node      , meaning:Positive node
param:NRp       , type:node      , meaning:Gain resistor positive node
param:NRm       , type:node      , meaning:Gain resistor negative node
param:Ns        , type:node      , meaning:Source node
param:Nt        , type:node      , meaning:Tap node
param:Np1       , type:value     , meaning:Number of turns on p1
param:Np2       , type:value     , meaning:Number of turns on p2
param:Ns1       , type:value     , meaning:Number of turns on s1
param:Ns2       , type:value     , meaning:Number of turns on s2
param:Phase     , type:value     , meaning:AC phase
param:Omega     , type:value     , meaning:AC angular frequency (rad/s)
param:Vo        , type:value     , meaning:DC voltage offset
param:Va        , type:value     , meaning:Sinewave voltage amplitude
param:Io        , type:value     , meaning:DC current offset
param:Ia        , type:value     , meaning:Sinewave current amplitude
param:fo        , type:value     , meaning:Sinewave frequency
param:td        , type:value     , meaning:Time delay
param:alpha     , type:value     , meaning:Damping factor
param:Time      , type:value     , meaning:Time
param:Value     , type:value     , meaning:Value
param:IC        , type:value     , meaning:Initial condition
param:NID       , type:value     , meaning:Noise identifier
param:Vcontrol  , type:name      , meaning:Control voltage name
param:Ac        , type:value     , meaning:Common-mode gain
param:Ad        , type:value     , meaning:Differential gain
param:Rf        , type:value     , meaning:Feedback resistance
param:Ro        , type:value     , meaning:Output resistance
param:Vn        , type:value     , meaning:Noise voltage
param:In        , type:value     , meaning:Noise current
param:A11       , type:value     , meaning:A11
param:A12       , type:value     , meaning:A12
param:A21       , type:value     , meaning:A21
param:A22       , type:value     , meaning:A22
param:B11       , type:value     , meaning:B11
param:B12       , type:value     , meaning:B12
param:B21       , type:value     , meaning:B21
param:B22       , type:value     , meaning:B22
param:G11       , type:value     , meaning:G11
param:G12       , type:value     , meaning:G12
param:G21       , type:value     , meaning:G21
param:G22       , type:value     , meaning:G22
param:H11       , type:value     , meaning:H11
param:H12       , type:value     , meaning:H12
param:H21       , type:value     , meaning:H21
param:H22       , type:value     , meaning:H22
param:Y11       , type:value     , meaning:Y11
param:Y12       , type:value     , meaning:Y12
param:Y21       , type:value     , meaning:Y21
param:Y22       , type:value     , meaning:Y22
param:Z11       , type:value     , meaning:Z11
param:Z12       , type:value     , meaning:Z12
param:Z21       , type:value     , meaning:Z21
param:Z22       , type:value     , meaning:Z22
param:V1        , type:value     , meaning:V1
param:I1        , type:value     , meaning:I1
param:V2        , type:value     , meaning:V2
param:I2        , type:value     , meaning:I2
================================================ end of list ================================================
"""  # noqa: E501

# Check out grammar.py in lcapy for a complete list of supported components in lcapy.
# Corresponds to skins from build_skin.py.
TO_SKIN_CONFIG = {
    # Resistor — Rname Np Nm [R]
    "R": [
        {
            # can be either, chosen randomly
            "skin_alias": ["r_h", "r_v"],
            # Maps non-kind args to port. The key is the index of the non-kind arg
            # (i.e kind keyword will be removed before using this index).
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {
                    "alias": "-",
                    # default False. When True, the port will be dropped,
                    # and the corresponding port_direction for this port can be omitted.
                    # This is used when the port is not in the skin we are using.
                    "drop": False,
                },
            },
            # Maps non kind args to value. The key is the index of the non-kind arg.
            "args_to_values": {
                2: {
                    # this is the name of the label in the skin, which will be used to display the value.
                    # if not specified, the value will not be displayed in the skin.
                    "skin_label": "value",
                    # value alias, this is used to match the value
                    # when the value arg is specified as a keyword arg
                    "alias": ["Value", "resistance", "r"],
                    # Default True. We assume every value arg is optional.
                    # When True, the value can be omitted, and either the default_value will be used.
                    # or if the default value is not specified, the label will be ommitted.
                    "is_optional": True,
                    # default value, used when is_optional is True and value is omitted.
                    # not used here as is_optional is False, but can be used in other cases.
                    "default_value": 1.0,
                }
            },
            # Maps port directions. The key is the port name.
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # VCVS / Opamp — Ename N+ N- [kind] Ninv Nnoninv [args]
    "E": [
        # VCVS (default, no kind) - Ename Np Nm Ncp Ncm [Value=name] [Ac=0]
        {
            # None for not implemented
            "skin_alias": ["vcvs_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                # Drop control ports as skin only has 2 ports
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {
                4: {
                    "skin_label": "value",
                    "alias": ["Value", "gain", "E"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["Ac"],
                },
            },
            # + is output as it is a voltage source. this should be the case for other sources.
            "port_directions": {"+": "output", "-": "input"},
        },
        # Opamp - Ename Np Nm opamp Ncp Ncm [Ad=name] [Ac=0] [Ro=0]
        {
            "skin_alias": "op",
            "arg_to_ports": {
                0: {"alias": "o+"},
                # opamp skin only has a single output port, so drop the other output port
                1: {"alias": "o-", "drop": True},
                2: {"alias": "i+"},
                3: {"alias": "i-"},
            },
            "args_to_values": {
                4: {
                    "skin_label": "value",
                    "alias": ["Ad", "differential_gain"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["Ac"],
                },
                6: {
                    "skin_label": None,
                    "alias": ["Ro"],
                },
            },
            "kind": ["opamp"],
            "port_directions": {"o+": "output", "i+": "input", "i-": "input"},
        },
        # Fully-differential opamp - Ename Np Nm fdopamp Ncp Ncm Nocm [Ad=name] [Ac=0]
        {
            "skin_alias": "op",
            "arg_to_ports": {
                0: {"alias": "o+"},
                1: {"alias": "o-", "drop": True},
                2: {"alias": "i+"},
                3: {"alias": "i-"},
                4: {"alias": "oc", "drop": True},
            },
            "args_to_values": {
                5: {
                    "skin_label": None,
                    "alias": ["Ad"],
                },
                6: {
                    "skin_label": None,
                    "alias": ["Ac"],
                },
            },
            "kind": ["fdopamp"],
            "port_directions": {"o+": "output", "i+": "input", "i-": "input"},
        },
        # Instrumentation amplifier - Ename Np Nm inamp Ncp Ncm NRp NRm [Ad=name] [Ac=0] [Rf=Rf]
        {
            "skin_alias": "op",
            "arg_to_ports": {
                0: {"alias": "o+"},
                1: {"alias": "o-", "drop": True},
                2: {"alias": "i+"},
                3: {"alias": "i-"},
                4: {"alias": "rp", "drop": True},
                5: {"alias": "rm", "drop": True},
            },
            "args_to_values": {
                6: {
                    "skin_label": None,
                    "alias": ["Ad"],
                },
                7: {
                    "skin_label": None,
                    "alias": ["Ac"],
                },
                8: {
                    "skin_label": None,
                    "alias": ["Rf"],
                },
            },
            "kind": ["inamp"],
            "port_directions": {"o+": "output", "i+": "input", "i-": "input"},
        },
        # Generic amplifier - Ename Np Nm amp Ncp Ncm [Ad=name] [Ac=0]
        {
            "skin_alias": "op",
            "arg_to_ports": {
                0: {"alias": "o+"},
                1: {"alias": "o-", "drop": True},
                2: {"alias": "i+"},
                3: {"alias": "i-"},
            },
            "args_to_values": {
                4: {
                    "skin_label": None,
                    "alias": ["Ad"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["Ac"],
                },
            },
            "kind": ["amp"],
            "port_directions": {"o+": "output", "i+": "input", "i-": "input"},
        },
    ],
    # Capacitor - Cname Np Nm [Value=name] [IC]
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
                    "alias": ["Value", "capacitance", "c"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["IC"],
                },
            },
            "port_directions": {"+": "input", "-": "output"},
        }
    ],
    # Inductor
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
                    "alias": ["Value", "inductance", "l"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["IC", "i0"],
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "G": [
        # voltage-controlled current source (VCCS) - Gname Np Nm Ncp Ncm [Value=name]
        {
            "skin_alias": ["vccs_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {
                4: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            # + is output as it is a current source. this should be the case for other sources.
            "port_directions": {"+": "output", "-": "input"},
        }
    ],
    # Noiseless resistor
    "NR": [
        {
            "skin_alias": ["r_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "resistance", "r"],
                }
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # Wire
    "W": [
        {
            "skin_alias": None,
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # Port
    "P": [
        {
            "skin_alias": "tp",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # Voltage source — Vname N+ N- [kind] [args]
    "V": [
        # DC voltage source (default, no kind)
        {
            "skin_alias": ["v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "v", "dc"],
                }
            },
            "port_directions": {"+": "output", "-": "input"},
        },
        {
            "skin_alias": ["v"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "v", "dc"],
                }
            },
            "kind": ["dc"],
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC voltage source
        {
            "skin_alias": ["sv"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "v", "ac"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["Phase"],
                },
                4: {
                    "skin_label": None,
                    "alias": ["Omega"],
                },
            },
            "kind": ["ac"],
            "port_directions": {"+": "output", "-": "input"},
        },
        # Sinusoidal voltage source - Vname Np Nm sin Vo Va fo [td] [alpha] [Phase]
        {
            "skin_alias": ["sv"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Vo"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["Va"],
                },
                4: {
                    "skin_label": None,
                    "alias": ["fo"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["td"],
                },
                6: {
                    "skin_label": None,
                    "alias": ["alpha"],
                },
                7: {
                    "skin_label": None,
                    "alias": ["Phase"],
                },
            },
            "kind": ["sin"],
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # Current source — Iname N+ N- [kind] [args]
    "I": [
        # DC current source (default, no kind)
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
                }
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
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "i", "dc"],
                }
            },
            "kind": ["dc"],
            "port_directions": {"+": "output", "-": "input"},
        },
        # AC current source - Iname Np Nm ac [Value=name] [Phase] [Omega]
        {
            "skin_alias": ["si"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "i", "ac"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["Phase"],
                },
                4: {
                    "skin_label": None,
                    "alias": ["Omega"],
                },
            },
            "kind": ["ac"],
            "port_directions": {"+": "output", "-": "input"},
        },
        # Sinusoidal current source - Iname Np Nm sin Io Ia fo [td] [alpha] [Phase]
        {
            "skin_alias": ["si"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Io"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["Ia"],
                },
                4: {
                    "skin_label": None,
                    "alias": ["fo"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["td"],
                },
                6: {
                    "skin_label": None,
                    "alias": ["alpha"],
                },
                7: {
                    "skin_label": None,
                    "alias": ["Phase"],
                },
            },
            "kind": ["sin"],
            "port_directions": {"+": "output", "-": "input"},
        },
    ],
    # Diode
    # Dname Np Nm
    "D": [
        # Default diode
        {
            "skin_alias": ["d_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "output"},
        },
        # LED - Dname Np Nm led
        {
            "skin_alias": ["d_led_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "kind": ["led"],
            "port_directions": {"+": "input", "-": "output"},
        },
        # Schottky - Dname Np Nm schottky
        {
            "skin_alias": ["d_sk_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "kind": ["schottky"],
            "port_directions": {"+": "input", "-": "output"},
        },
        # Zener - Dname Np Nm zener
        {
            "skin_alias": ["d_zener_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "kind": ["zener"],
            "port_directions": {"+": "input", "-": "output"},
        },
        # Photo - Dname Np Nm photo
        {
            "skin_alias": ["d_photo_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "kind": ["photo"],
            "port_directions": {"+": "input", "-": "output"},
        },
        # Tunnel - Dname Np Nm tunnel
        {
            "skin_alias": ["d_h"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "kind": ["tunnel"],
            "port_directions": {"+": "input", "-": "output"},
        },
    ],
    # BJT — Qname NC NB NE npn|pnp
    "Q": [
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_npn"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["npn"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
        {
            "skin_alias": ["q_pnp"],
            "arg_to_ports": {
                0: {"alias": "c"},
                1: {"alias": "b"},
                2: {"alias": "e"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["pnp"],
            "port_directions": {"b": "input", "c": "input", "e": "output"},
        },
    ],
    # JFET — Jname ND NG NS njf|pjf
    "J": [
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["njf"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["jfet_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["pjf"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
    ],
    # MOSFET — Mname ND NG NS nmos|pmos
    "M": [
        {
            "skin_alias": ["mos_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["mos_n"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["nmos"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
        {
            "skin_alias": ["mos_p"],
            "arg_to_ports": {
                0: {"alias": "d"},
                1: {"alias": "g"},
                2: {"alias": "s"},
            },
            "args_to_values": {
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "kind": ["pmos"],
            "port_directions": {"g": "input", "d": "output", "s": "output"},
        },
    ],
    # Switch — SWname N+ N- [kind]
    "SW": [
        # Default switch
        {
            "skin_alias": ["sw"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Time"],
                }
            },
            "port_directions": {"+": "input", "-": "input"},
        },
        # Normally-open
        {
            "skin_alias": ["sw_no"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Time"],
                }
            },
            "kind": ["no"],
            "port_directions": {"p": "input", "n": "input"},
        },
        # Normally-closed
        {
            "skin_alias": ["sw_nc"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Time"],
                }
            },
            "kind": ["nc"],
            "port_directions": {"p": "input", "n": "input"},
        },
        # Push
        {
            "skin_alias": ["sw_push"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
            },
            "args_to_values": {
                2: {
                    "skin_label": None,
                    "alias": ["Time"],
                }
            },
            "kind": ["push"],
            "port_directions": {"p": "input", "n": "input"},
        },
        # SPDT
        {
            "skin_alias": ["sw_spdt"],
            "arg_to_ports": {
                0: {"alias": "p"},
                1: {"alias": "n"},
                2: {"alias": "common"},
            },
            "args_to_values": {
                3: {
                    "skin_label": None,
                    "alias": ["Time"],
                }
            },
            "kind": ["spdt"],
            "port_directions": {"p": "input", "n": "input", "common": "input"},
        },
    ],
    # Misc
    "XT": [
        {
            "skin_alias": ["pz"],
            "arg_to_ports": {
                0: {"alias": "a"},
                1: {"alias": "b"},
            },
            "args_to_values": {},
            "port_directions": {"a": "input", "b": "input"},
        }
    ],
    "ANT": [
        {
            "skin_alias": ["antenna"],
            "arg_to_ports": {
                0: {"alias": "a"},
            },
            "args_to_values": {},
            "port_directions": {"a": "input"},
        }
    ],
    # BATname Np Nm [Value=name]
    "BAT": [
        {
            "skin_alias": ["battery"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value", "v"],
                }
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "AM": [
        {
            "skin_alias": ["ammeter"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "VM": [
        {
            "skin_alias": ["voltmeter"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "ADC": [
        {
            "skin_alias": ["adc"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "DAC": [
        {
            "skin_alias": ["dac"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # Mechanical
    "k": [
        {
            "skin_alias": "k_spring",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["IC"],
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "m": [
        {
            "skin_alias": "m_mass",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value"],
                },
                3: {
                    "skin_label": None,
                    "alias": ["IC"],
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    "r": [
        {
            "skin_alias": "r_damper",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "value",
                    "alias": ["Value"],
                }
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # current-controlled current source - Fname Np Nm Vcontrol [Value=name]
    "F": [
        {
            "skin_alias": ["cccs"],
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "vcontrol",
                    "alias": ["Vcontrol"],
                },
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                },
            },
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # current-controlled voltage source (CCVS) - Hname Np Nm Vcontrol [Value=name]
    "H": [
        {
            "skin_alias": "ccvs",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
            },
            "args_to_values": {
                2: {
                    "skin_label": "vcontrol",
                    "alias": ["Vcontrol"],
                },
                3: {
                    "skin_label": "value",
                    "alias": ["Value"],
                },
            },
            "port_directions": {"+": "output", "-": "input"},
        }
    ],
    # Transformer (2-winding) — TFname Np Nm Ncp Ncm [Ns1=name] [Np1=1]
    "TF": [
        {
            "skin_alias": "tf",
            "arg_to_ports": {
                0: {"alias": "sp"},
                1: {"alias": "sm"},
                2: {"alias": "pp"},
                3: {"alias": "pm"},
            },
            "args_to_values": {
                4: {
                    "skin_label": None,
                    "alias": ["Ns1", "secondary_turn"],
                },
                5: {
                    "skin_label": None,
                    "alias": ["Np1", "primary_turn"],
                    "default_value": 1.0,
                },
            },
            "port_directions": {"sp": "input", "sm": "input", "pp": "output", "pm": "output"},
        }
    ],
    "TP": [
        {
            "skin_alias": "tp",
            "arg_to_ports": {
                0: {"alias": "+"},
                1: {"alias": "-"},
                2: {"alias": "c+", "drop": True},
                3: {"alias": "c-", "drop": True},
            },
            "args_to_values": {},
            "port_directions": {"+": "input", "-": "input"},
        }
    ],
    # "GY": [
    #     {
    #         "skin_alias": None,
    #         "arg_to_ports": {
    #             0: {"alias": "+"},
    #             1: {"alias": "-"},
    #             2: {"alias": "c+", "drop": True},
    #             3: {"alias": "c-", "drop": True},
    #         },
    #         "args_to_values": {
    #             4: {
    #                 "skin_label": "value",
    #                 "alias": ["resistance", "R"],
    #             }
    #         },
    #         "port_directions": {"+": "input", "-": "input"}
    #     }
    # ],
}
