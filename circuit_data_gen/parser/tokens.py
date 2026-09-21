"""Tokenizing: splitting netlist lines into Tokens and the lexical repairs
applied to SPICE argument lists before spec matching."""

import string

from . import dialect


class Token(str):
    """A netlist token that remembers where it came from.

    Compares and hashes exactly like the plain string (the token text with
    enclosing {}/quote delimiters removed), so parsing code can treat it as a
    str. `raw` is the token as written on the line and `span` its
    (start, end) offsets in that line, which lets strict parsing reject a
    braced node token ({n1}) and lets tools rewrite a token in place.
    """

    raw: str
    span: tuple[int, int]

    def __new__(cls, text: str, raw: str | None = None, span: tuple[int, int] = (0, 0)):
        token = super().__new__(cls, text)
        token.raw = raw if raw is not None else text
        token.span = span
        return token

    @property
    def braced(self) -> bool:
        """True when the whole token is wrapped in curly braces, e.g. {n1}."""
        return self.raw.startswith("{") and self.raw.endswith("}")


def _strip_hints(line: str) -> str | None:
    return line.split(";", 1)[0].split("#", 1)[0].strip()


def _get_tokens(line: str) -> list[Token]:
    """
    Split a line into tokens, separated by whitespaces, handling quoted strings and curly braces.
    Returns a list of Tokens: the token text without the {}/quote delimiters,
    carrying the token as written (`raw`) and its (start, end) span in `line`.
    """
    stack = []
    tokens = []
    current_token = ""
    start = None  # offset of the current token's first character (delimiters included)
    for idx, char in enumerate(line):
        if char in string.whitespace and not stack:
            if current_token:
                tokens.append(Token(current_token, line[start:idx], (start, idx)))
            current_token = ""
            start = None
        else:
            if start is None:
                start = idx
            if char == '"':
                if stack and stack[-1] == '"':
                    stack.pop()  # closing quote
                else:
                    stack.append('"')  # opening quote
                continue
            elif char == "'":
                if stack and stack[-1] == "'":
                    stack.pop()
                else:
                    stack.append("'")
                continue
            elif char == "{":
                stack.append("{")
                continue
            elif char == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
                continue
            elif char == "(" and dialect.IS_SPICE:
                # SPICE: function-call values (SINE(...), PULSE(...)) must stay
                # one token — the whole parenthesised group is a single value.
                stack.append("(")
            elif char == ")" and dialect.IS_SPICE and stack and stack[-1] == "(":
                stack.pop()

            current_token += char

    if current_token:
        tokens.append(Token(current_token, line[start:], (start, len(line))))
    return tokens


def _token_origin(token: str) -> dict:
    """Where a node token came from, stored on its connection: `braced` is
    True when it was written as {node}, `span` its (start, end) offsets in
    the (continuation-merged) element line. Tokens that were rebuilt during
    parsing carry no origin (braced False, span None)."""
    return {
        "braced": isinstance(token, Token) and token.braced,
        "span": token.span if isinstance(token, Token) else None,
    }


def _glue_paren_values(args: list[str]) -> list[str]:
    """LTspice allows a space between a function name and its argument list
    (PWL (0,0 10m,12)). _get_tokens can only paren-join without the space,
    so re-join bare-word + '(' pairs into one token here.

    Node names, model names and keywords never start with '(' and '=' may
    not be spaced, so a token starting with '(' always continues the previous
    bare word. The guard against gluing onto an already ')'-closed token
    keeps consecutive groups separate: `(0,0) (1m,5)` stays two tokens.
    """
    out: list[str] = []
    for tok in args:
        if out and tok.startswith("(") and not out[-1].endswith(")"):
            out[-1] += tok
        else:
            out.append(tok)
    return out


def _glue_comma_values(args: list[str]) -> list[str]:
    """SPICE writes multi-element values as comma lists that may span
    whitespace: IC=V1, I1, V2, I2. A comma is never a token boundary in this
    grammar (node names etc. never end with ','), so a token ending in ','
    continues the same value, as does a token starting with one."""
    out: list[str] = []
    for tok in args:
        if out and (out[-1].endswith(",") or tok.startswith(",")):
            out[-1] += tok
        else:
            out.append(tok)
    return out
