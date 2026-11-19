#!/usr/bin/env python3
"""Get and Put of a field with the wrong width: the compiled __vbaGet3/__vbaPut3 takes the variable's
byte count, and the file stays in step only if the decompiled Type agrees.

usage: iowidth.py EXE [MAP]

Lists each Get / Put whose target's declared width in the recovered Types differs from the count."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile                    # noqa: E402
from vb6dec import apidb, ir, udt   # noqa: E402

WIDTH = {"Byte": 1, "Boolean": 2, "Integer": 2, "Long": 4, "Single": 4, "String": 4, "Object": 4,
         "Double": 8, "Currency": 8, "Date": 8, "Variant": 16}


def walk_field(t, off):
    """(name, type) of what is at off in Type t, going into nested Types and array fields."""
    lay = apidb.layout(t)
    if not lay:
        return None
    best = None
    for o, n, ft in lay[0]:
        if o <= off:
            best = (o, n, ft)
    if best is None:
        return None
    o, n, ft = best
    if off > o and apidb.layout(ft):
        return walk_field(ft, off - o)
    return n, ft, off - o


def main(argv):
    exe = argv[0]
    mp = argv[1] if len(argv) > 1 else None
    m, out = decompile.first_pass_snapshot(exe, mp)
    bad = 0
    seen = set()
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        n = getattr(x, "put_size", None) if isinstance(x, ir.Call) else None
                        if not n or not x.args:
                            continue
                        y = x.args[-1].e if isinstance(x.args[-1], ir.AddrOf) else x.args[-1]
                        bo = udt.base_off(y) if isinstance(y, ir.Deref) else None
                        if bo is None or not isinstance(bo[0], ir.Var):
                            continue
                        t = lf.udt.get(bo[0].key())
                        f = walk_field(t, bo[1]) if t else None
                        if f is None:
                            continue
                        w = WIDTH.get(f[1].split("(")[0].strip())
                        if w is None and apidb.layout(f[1]):
                            w = apidb.layout(f[1])[1]
                        if f[2] or w == n or (t, bo[1], n) in seen:
                            continue
                        seen.add((t, bo[1], n))
                        bad += 1
                        print("%s %s+%X %s As %s: count %d, width %s" % (
                            lf.p.name, t, bo[1], f[0], f[1], n, w))
    print("iowidth: %d mismatch(es)" % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
