#!/usr/bin/env python3
"""How much of a decompiled VB project is still machine level, by procedure: for comparing decompilers' output.

usage: decstats.py DIR [DIR...]

For each folder of .frm/.bas/.cls/.ctl: files, lines, procedures, and how many procedures hold each kind of
leftover (one procedure can count in several):
  runtime   runtime helpers left as calls: __vba*, rtc*, or an import by ordinal (ord517)
  memory    raw memory: Mem32(...), VarPtr(stack_..), [arg@..], [edx], [esp-76], ?asm
  vtable    members or classes known only by slot or GUID: vtbl_..., Unknown_Method.., Form1_Method2FC, Dispid<..>,
            <DataPtr>, '<.., {GUID}
  register  CPU registers or flags left as names: edx, reg_eax, st0, cond_ne
  goto      GoTo
  failed    the decompiler gave the procedure up
  any machine level   runtime, memory, vtable or register
and how many lines hold text outside ASCII, with the files' encoding as read (VB6 reads them as ANSI).
"""
import os
import re
import sys

KINDS = {
    "runtime": re.compile(r"\b(?:__vba\w+|rtc[A-Z]\w*|ord\d+)\b"),
    "memory": re.compile(r"\bMem(?:8|16|32)\(|VarPtr\(stack_|\[arg@|\[e(?:[abcd]x|[sd]i|[bs]p)(?:[+-]\w+)?\]|\?asm"),
    "vtable": re.compile(r"vtbl_[0-9A-F]+|Unknown_Method|_Method[0-9A-F]{3}\b|Dispid<|<DataPtr>|'<|\{[0-9A-F]{8}-[0-9A-F]{4}-"),
    "register": re.compile(r"\b(?:reg_)?(?:e[abcd]x|e[sd]i|e[bs]p)\b|\bst[0-7]\b|\bcond_[a-z]+\b"),
    "goto": re.compile(r"\bGoTo\b"),
    "failed": re.compile(r"failed to decompile|could not be decompiled|vb6dec: .* could not"),
}
HEAD = re.compile(r"^\s*(?:(?:Public|Private|Friend)\s+)?(?:Static\s+)?(?:Sub|Function|Property\s+(?:Get|Let|Set))\s+\w",
                  re.I)
END = re.compile(r"^\s*End\s+(?:Sub|Function|Property)\b", re.I)


def stats(top):
    out = {"files": 0, "lines": 0, "procedures": 0, "non-ASCII lines": 0, "utf-8 files": 0}
    out.update({k: 0 for k in KINDS})
    out["any machine level"] = 0
    for root, _, files in os.walk(top):
        for f in sorted(files):
            if not f.lower().endswith((".frm", ".bas", ".cls", ".ctl")):
                continue
            raw = open(os.path.join(root, f), "rb").read()
            out["files"] += 1
            try:
                raw.decode("ascii")
            except UnicodeDecodeError:
                try:
                    raw.decode("utf-8")
                    out["utf-8 files"] += 1
                except UnicodeDecodeError:
                    pass
            lines = raw.decode("latin-1").replace("\r", "").split("\n")
            out["lines"] += len(lines)
            out["non-ASCII lines"] += sum(any(ord(c) > 127 for c in ln) for ln in lines)
            body = None
            for ln in lines:
                if body is None and HEAD.match(ln) and "Declare " not in ln:
                    body = []
                    out["procedures"] += 1
                elif body is not None and END.match(ln):
                    text = "\n".join(body)
                    for k, rx in KINDS.items():
                        out[k] += bool(rx.search(text))
                    out["any machine level"] += any(KINDS[k].search(text) for k in ("runtime", "memory", "vtable", "register"))
                    body = None
                elif body is not None:
                    body.append(ln)
    return out


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    rows = [(d, stats(d)) for d in argv]
    keys = list(rows[0][1])
    print("%-28s" % "" + "".join("%14s" % os.path.basename(os.path.normpath(d))[:13] for d, _ in rows))
    for k in keys:
        print("%-28s" % k + "".join("%14d" % s[k] for _, s in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
