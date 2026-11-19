#!/usr/bin/env python3
"""Why a module variable counts as a record of some size (decompile.global_struct_hints).

usage: globalwhy.py EXE MAP 0xVA [0xVA ...]
For each address: the hint it fell in, what the module's variable table says, the record descriptors,
LSets and procedure parameters that named it in the raw lift.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile
from vb6dec import simplify, records
from vb6dec.ir import Call, Const, AddrOf, Var


def main(argv):
    exe, mp = argv[0], argv[1]
    want = [int(a, 0) for a in argv[2:]]
    m, bodies = decompile.first_pass_snapshot(exe, mp)
    sparams = decompile.struct_params(m, bodies)
    mods = m.module_records()
    hints = getattr(m, "global_struct_hint", {}) or {}
    reach = getattr(m, "global_struct_reach", ()) or ()

    def start(a):
        if isinstance(a, Const) and isinstance(a.value, int):
            return a.value & 0xffffffff
        if isinstance(a, AddrOf) and isinstance(a.e, Var) and a.e.space == "global" and isinstance(a.e.off, int):
            return a.e.off
        return None
    for g in want:
        print("0x%x" % g)
        for h, n in sorted(hints.items()):
            if h <= g < h + n:
                print("   in hint 0x%x size 0x%x%s" % (h, n, " (reach only)" if h in reach else ""))
        print("   module table:", mods.get(g))
        seen = set()
        for va, (lf, body) in bodies.items():
            if lf is None:
                continue
            for k, rva in getattr(lf, "rec_of", {}).items():
                if k == ("global", g):
                    rec = records.model_record(m, rva)
                    print("   rec_of in %x: descriptor %x size %s" % (va, rva, rec.size if rec else None))
            for _, ss, br in simplify.flatten(lf):
                for st in ss:
                    for e in getattr(st, "exprs", lambda: ())():
                        for x in e.walk():
                            if not isinstance(x, Call):
                                continue
                            if x.name == "LSet" and any(start(a) == g for a in x.args[:2]):
                                t = (va, "LSet", str(x.args[2]))
                            elif isinstance(x.raw, int):
                                hit = [i for i, a in enumerate(x.args) if start(a) == g]
                                if not hit:
                                    continue
                                t = (va, "call %x arg %d" % (x.raw, hit[0]), str(sparams.get((x.raw, hit[0]))))
                            else:
                                continue
                            if t not in seen:
                                seen.add(t)
                                print("   %x: %s: %s" % t)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
