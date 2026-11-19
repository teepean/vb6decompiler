#!/usr/bin/env python3
"""Why does a variable get a struct type?  Runs the first pass with
udt.group_structs instrumented and prints, each time it runs, the group the
variable lands in: its members, each member's field accesses, and the
ev.passed links that joined them.

usage: udtgroup.py EXE [MAP] VA:KEY [--nomap]
   VA   procedure VA (hex), or "g" for a module variable
   KEY  arg offset (hex, e.g. 4 = first parameter of a module procedure),
        local offset (-38), or a global VA for g
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def parse_node(s):
    va, key = s.split(":")
    if va == "g":
        return (None, ("global", int(key, 16)))
    off = int(key, 16) if not key.startswith("-") else -int(key[1:], 16)
    return (int(va, 16), ("arg" if off > 0 else "local", off))


def main(argv):
    import gc
    gc.disable()
    nomap = "--nomap" in argv
    argv = [a for a in argv if a != "--nomap"]
    exe, rest = argv[0], argv[1:]
    mp = rest[0] if len(rest) > 1 else None
    target = parse_node(rest[-1])
    if mp is None and not nomap:
        cand = os.path.splitext(exe)[0] + ".map"
        mp = cand if os.path.exists(cand) else None
    import decompile
    from vb6dec import udt
    from vb6dec.model import Model
    orig = udt.group_structs
    run = [0]

    def name(model, n):
        va, key = n
        p = model.procs.get(va) if va is not None else None
        return "%s:%s" % (p.name if p else ("%x" % va if va else "global"),
                          "%s %s" % (key[0], hex(key[1]) if isinstance(key[1], int) else key[1]))

    def wrapped(model, bodies):
        orig(model, bodies)
        run[0] += 1
        gid = model.synth_group.get(target)
        print("== group_structs run %d: %s -> %s" % (run[0], name(model, target),
                                                     gid and model.synth_names.get(gid)
                                                     if hasattr(model, "synth_names") else gid))
        if gid is None:
            return
        members = [x for x, g in model.synth_group.items() if g == gid]
        evs = {va: udt.collect(lf, body) for va, (lf, body) in bodies.items() if lf is not None}
        for x in sorted(members, key=str):
            accs = [sorted(ev.access.get(x[1], {}).items()) for va, ev in evs.items()
                    if (x[0] is None or va == x[0]) and x[1] in ev.access]
            print("  member %-40s acc %s" % (name(model, x), accs[:3]))
        mset = set(members)
        for va, ev in evs.items():
            for (callee, idx), keys in ev.passed.items():
                plist = model.plists.get(callee) or ()
                if idx >= len(plist):
                    continue
                ck = (callee, ("arg", plist[idx][0]))
                for k in keys:
                    c = (None, k) if k[0] == "global" else (va, k)
                    if c in mset or ck in mset:
                        print("  link %s -> %s" % (name(model, c), name(model, ck)))
    udt.group_structs = wrapped
    m = Model(exe, mp)
    decompile.first_pass(m)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
