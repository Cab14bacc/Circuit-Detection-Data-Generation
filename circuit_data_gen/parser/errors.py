"""Exceptions raised by the netlist parser."""


class NetlistError(Exception):
    """Raised when a netlist line is malformed or cannot be parsed into a component."""


class SpecError(Exception):
    """Raised when a skin spec in LCAPY_TO_SKIN is malformed (e.g. duplicate
    argument indices between arg_to_ports and args_to_values)."""
