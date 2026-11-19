"""Check decompiled VB source for things VB6 would reject."""
import re
import sys

KEYWORDS = {"abs", "and", "as", "boolean", "byref", "byte", "byval", "call", "case", "circle",
            "close", "const", "currency", "date", "declare", "dim", "do", "double", "each",
            "else", "elseif", "end", "enum", "eqv", "erase", "error", "event", "exit", "false",
            "for", "friend", "function", "get", "global", "gosub", "goto", "if", "imp",
            "implements", "in", "input", "integer", "is", "let", "like", "line", "load", "lock",
            "long", "loop", "lset", "me", "mod", "new", "next", "not", "nothing", "null", "on",
            "open", "option", "optional", "or", "preserve", "print", "private", "property",
            "pset", "public", "put", "raiseevent", "redim", "rem", "resume", "return", "scale",
            "seek", "select", "set", "single", "spc", "static", "step", "stop", "string", "sub",
            "tab", "then", "to", "true", "type", "unload", "until", "variant", "wend", "while",
            "with", "withevents", "write", "xor"}
PROC = re.compile(r"^\s*(?:Public |Private |Friend )?(?:Static )?(Sub|Function|Property \w+)\s+(\w+)")
DECL = re.compile(r"^\s*(?:Dim|Static|Public|Private)\s+(?!Declare|Type|Const|Enum|Sub|"
                  r"Function|Property|WithEvents|Event)(\w+)")
LABEL = re.compile(r"^(\w+):\s*$")
GOTO = re.compile(r"\bGo(?:To|Sub)\s+(\w+)|\bResume\s+(\w+)")


def check(path):
    text = open(path, encoding="latin-1").read().replace("\r", "")
    lines = text.split("\n")
    out = []
    scope, seen, labels, gotos = None, set(), set(), []
    depth = 0
    opened = []
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        m = PROC.match(ln)
        if m:
            scope, seen = m.group(2), set()
            if scope.lower() in KEYWORDS:
                out.append((i, "procedure name is a keyword: %s" % scope))
            continue
        if re.match(r"^\s*End (Sub|Function|Property)", ln):
            if depth:
                out.append((i, "block not closed in %s: opened at %s" %
                            (scope, ", ".join(str(x) for x in opened[-depth:]))))
            depth, opened = 0, []
            scope = None
            continue
        m = DECL.match(ln)
        if m:
            name = m.group(1)
            if name.lower() in KEYWORDS:
                out.append((i, "variable name is a keyword: %s" % name))
            if name in seen:
                out.append((i, "declared twice in %s: %s" % (scope, name)))
            seen.add(name)
        m = LABEL.match(ln)
        if m:
            labels.add(m.group(1))
        for g in GOTO.finditer(ln):
            t = g.group(1) or g.group(2)
            if t and t not in ("0", "Next"):
                gotos.append((i, t))
        if re.match(r"^\s*(If .*Then\s*$|For\b|Do\b|While\b|Select Case\b|With\b)", ln):
            depth += 1
            opened.append(i)
        elif re.match(r"^\s*(End If\b|Next\b|Loop\b|Wend\b|End Select\b|End With\b)", ln):
            depth -= 1
            if opened:
                opened.pop()
    for i, t in gotos:
        if t not in labels:
            out.append((i, "jump to a label that is not defined: %s" % t))
    if depth:
        out.append((0, "unbalanced block structure (%+d)" % depth))
    return out


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        for i, msg in check(p):
            print("%s:%d: %s" % (p, i, msg))
            bad += 1
    print("lint: %d problem(s)" % bad)
