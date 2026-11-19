#!/usr/bin/env python3
"""Which procedures give a variable a type, and by which rule?  Runs the
first pass and the parameter lists as decompile.py does, then builds every
procedure's Types (what writer.global_types reads) with Types.add watched.

usage: typewho.py EXE [MAP] KEY TYPE [--depth N] [--fresh]
   --fresh runs the first pass again instead of using its snapshot (vb6dec/snapshot.py)
   KEY   g:GLOBALVA, or VA:arg:OFF / VA:local:OFF (hex), as in udtwho.py
   TYPE  the type name to watch for (Variant, Long, TRec11...)
   --relift lift the procedure again as the writer does (the first pass's body can differ)
   --order  for a procedure's KEY: every variable in that procedure as it first
            gets TYPE, in order: the first ones are where a type spreads from
"""
import os
import sys
import traceback
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main(argv):
    depth = 4
    if "--depth" in argv:
        i = argv.index("--depth")
        depth = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    fresh = [a for a in argv if a == "--fresh"]
    order = "--order" in argv
    relift = "--relift" in argv
    argv = [a for a in argv if a not in ("--fresh", "--order", "--relift")]
    exe, rest = argv[0], argv[1:]
    mp = rest[0] if len(rest) > 2 else None
    key_s, want = rest[-2], rest[-1]
    from udtwho import parse
    owner, key = parse(key_s)
    import gc
    gc.disable()
    import decompile
    from vb6dec import render, types
    from vb6dec.ir import Var, Deref
    from vb6dec.model import Model
    if fresh:
        m = Model(exe, mp)
        bodies = decompile.first_pass(m)
    else:
        m, bodies = decompile.first_pass_snapshot(exe, mp)  # (vb6dec/snapshot.py)
    for va, (lf, body) in bodies.items():
        if lf is not None:
            try:
                render.proc_source(m, lf, body)
            except Exception:
                pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seen = Counter()
    first = {}
    firsts = {}
    orig_add = types.Types.add

    def add(self, e, t):
        x = e
        if isinstance(x, Deref) and isinstance(x.e, Var) and x.e.space == "arg" and x.off == 0:
            x = x.e
        if order and t == want and isinstance(x, Var) and self.lf.p.va == owner and \
                x.key() not in firsts:
            caller = sys._getframe(1)
            firsts[x.key()] = "%d %s" % (caller.f_lineno, str(caller.f_locals.get("s", caller.f_locals.get("x", "")))[:110])
        if t == want and isinstance(x, Var) and x.key() == key and \
                (owner is None or self.lf.p.va == owner):
            st = [f for f in traceback.extract_stack()[:-1]
                  if os.path.abspath(f.filename).startswith(here)]
            where = " < ".join("%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name)
                               for f in reversed(st[-depth:]))
            seen[(self.lf.p.name, where)] += 1
            first.setdefault(where, self.lf.p.name)
        return orig_add(self, e, t)
    types.Types.add = add
    ev_hits = []
    for va, (lf, body) in bodies.items():
        if lf is None or owner is not None and va != owner:
            continue
        if relift:              # (the lift the writer makes, not the first pass's)
            from vb6dec.lift import Lifter
            from vb6dec import simplify
            lf = Lifter(m, m.procs[va]).run()
            body = simplify.run(lf)
        ty = types.Types(lf, body, m)
        if want in ty.ev.get(key, ()):
            ev_hits.append(lf.p.name)
    print("%d procedure(s) have %s evidence for %s" % (len(ev_hits), want, key_s))
    for (name, where), n in seen.most_common(40):
        print("  %-36s x%-3d %s" % (name, n, where))
    for k, line in firsts.items():
        print("  first %-24s types.py:%s" % (k, line))
    print("in ev but not through Types.add:", sorted(set(ev_hits) - {n for n, _ in seen})[:40])
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    import decompile
    sys.exit(decompile.fast_exit(code))
