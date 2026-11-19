#!/usr/bin/env python3
"""Who gave a variable its record type?  Runs the first pass with every
Lifter's udt map replaced by one that records, for the watched keys, each
assignment: the procedure, the type, and the decompiler functions on the
stack at that moment (innermost last).

usage: udtwho.py EXE [MAP] KEY... [--depth N] [--map ATTR]
   KEY   g:GLOBALVA (a module variable), or VA:arg:OFF / VA:local:OFF
         (hex) / VA:reg:NAME, e.g. g:F04740 or 8B2B30:arg:8
   --map another Lifter dict keyed by variable: objtype (the object type
         a variable is declared with), cast_types, ...
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def parse(k):
    parts = k.split(":")
    if parts[0] == "g":
        return None, ("global", int(parts[1], 16))
    va, space, off = parts
    if space == "reg":
        return int(va, 16), (space, off)        # VA:reg:ebx
    off = int(off, 16) if not off.startswith("-") else -int(off[1:], 16)
    return int(va, 16), (space, off)


def main(argv):
    depth = 6
    attr = "udt"
    if "--map" in argv:
        i = argv.index("--map")
        attr = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if "--depth" in argv:
        i = argv.index("--depth")
        depth = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    exe = argv[0]
    rest = argv[1:]
    mp = rest[0] if rest and not (":" in rest[0]) else None
    keys = [parse(k) for k in rest if ":" in k]
    watched = {}
    for va, key in keys:
        watched.setdefault(key, set()).add(va)
    import gc
    gc.disable()                # (decompile.py does too)
    os.environ["VB6DEC_JOBS"] = "1"     # (what it watches must happen in this process: vb6dec/par.py)
    import decompile
    from vb6dec import lift
    from vb6dec.model import Model
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    seen = []

    class Tracking(dict):
        owner = None

        def __setitem__(self, k, v):
            if k in watched and (None in watched[k] or self.owner in watched[k]) and \
                    self.get(k) != v:
                st = [f for f in traceback.extract_stack()[:-1]
                      if os.path.abspath(f.filename).startswith(here)]
                where = " < ".join("%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name)
                                   for f in reversed(st[-depth:]))
                at = getattr(getattr(self, "lf", None), "cur_va", None)
                if isinstance(at, int):
                    where = "at %x: %s" % (at, where)   # the instruction being lifted
                seen.append((self.owner, k, self.get(k), v, where))
            dict.__setitem__(self, k, v)

        def setdefault(self, k, v=None):
            if k not in self:
                self[k] = v
            return dict.__getitem__(self, k)

        def update(self, *a, **kw):
            for k, v in dict(*a, **kw).items():
                self[k] = v

    orig_init = lift.Lifter.__init__

    def init(self, m, p, *a, **kw):
        orig_init(self, m, p, *a, **kw)
        t = Tracking(getattr(self, attr))
        t.owner, t.lf = p.va, self
        setattr(self, attr, t)
    lift.Lifter.__init__ = init
    m = Model(exe, mp)
    decompile.first_pass(m)
    first = {}
    for owner, k, old, new, where in seen:
        sig = (k, old, new, where)
        if sig in first:
            first[sig][1] += 1
            continue
        first[sig] = [owner, 1]
    for (k, old, new, where), (owner, n) in sorted(first.items(), key=lambda kv: -kv[1][1]):
        name = m.procs[owner].name if owner in m.procs else "%x" % owner
        print("%s %s: %s -> %s  (%d procedure(s), first %s)\n    %s" % (
            k[0], hex(k[1]) if isinstance(k[1], int) else k[1], getattr(old, "name", old),
            getattr(new, "name", new), n, name, where))
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    import decompile
    sys.exit(decompile.fast_exit(code))
