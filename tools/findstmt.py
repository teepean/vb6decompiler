#!/usr/bin/env python3
"""Statements of the first pass that contain a text, with their procedure and address.

usage: findstmt.py EXE TEXT [--map MAP] [--proc 0xVA]
Without --proc it looks through the lifted bodies of the whole program; with it, through that
procedure's flattened statements.  The text is matched against the IR's own form, for instance
'Var(global,0x531054)' or 'Proc_4C05D0('.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import decompile  # noqa: E402
from vb6dec import simplify  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("text")
    ap.add_argument("--map")
    ap.add_argument("--proc", type=lambda x: int(x, 0))
    a = ap.parse_args()
    _, bodies = decompile.first_pass_snapshot(a.exe, a.map)
    if a.proc is not None:
        lf, _ = bodies[a.proc]
        items = [(a.proc, lf, simplify.flatten(lf))]
    else:
        items = [(va, lf, body) for va, (lf, body) in bodies.items() if lf is not None]
    for va, lf, body in items:
        for block, stmts, _ in body:
            for s in stmts:
                if a.text in str(s):
                    print(hex(va), lf.p.name, hex(block), str(s)[:230],
                          "va", hex(getattr(s, "va", 0) or 0))


if __name__ == "__main__":
    main()
