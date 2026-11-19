#!/usr/bin/env python3
"""What render sees for calls to one API: its raw name and the form of its arguments.

usage: dbgapicall.py EXE API_NAME OUTDIR"""
import os
import runpy
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from vb6dec import render, ir       # noqa: E402
exe, name, out = sys.argv[1:4]
orig = render.expr
seen = set()


def spy(e, nm, prec=0):
    if isinstance(e, ir.Call) and e.name == name:
        t = (repr(e.raw), tuple(type(a).__name__ + ":" + str(getattr(a, "space", getattr(getattr(a, "e", None), "space", ""))) for a in e.args))
        if t not in seen:
            seen.add(t)
            print("CALL", t, file=sys.stderr)
    return orig(e, nm, prec)


render.expr = spy
sys.argv = [os.path.join(HERE, "..", "decompile.py"), exe, "-o", out]
runpy.run_path(sys.argv[0], run_name="__main__")
