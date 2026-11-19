#!/usr/bin/env python3
"""How well owners.guess places module procedures: lift a program that has
a map, forget which module each module procedure is in, guess, and compare
with the map.

usage: ownercheck.py EXE MAP [-v]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main(argv):
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    import gc
    gc.disable()
    import decompile
    from vb6dec.model import Model
    from vb6dec import owners
    m = Model(argv[0], argv[1])
    bodies = decompile.first_pass(m)
    truth = {}
    for p in m.procs.values():
        t = getattr(p, "truth", None)
        if p.owner is None and t:
            truth[p.va] = t[0]
            p.truth = None
    got = owners.guess(m, bodies)
    right = sum(1 for va, mod in truth.items() if got.get(va) is not None and got[va].name == mod)
    print("%d module procedures, %d placed right (%.1f%%)" % (
        len(truth), right, 100.0 * right / max(len(truth), 1)))
    if verbose:
        for va in sorted(truth):
            g = got.get(va)
            if g is None or g.name != truth[va]:
                print("  %x  map %-16s guess %s" % (va, truth[va], g.name if g else None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
