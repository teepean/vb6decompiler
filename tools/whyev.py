#!/usr/bin/env python3
"""Which line of types.Types gives a variable its evidence for a type.

usage: whyev.py EXE MAP 0xPROC 0xGLOBAL TYPE      (MAP "" for none)
Runs the type collection of the procedure and prints every place that adds TYPE to what the
module variable at 0xGLOBAL is known to be, then all the evidence it ended with.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import decompile  # noqa: E402
from vb6dec.types import Types  # noqa: E402


def main():
    exe, map_, proc, addr, want = sys.argv[1:6]
    m, bodies = decompile.first_pass_snapshot(exe, map_ or None)
    key = ("global", int(addr, 0))

    class Watched(set):
        def add(self, x):
            if x == want:
                f = sys._getframe(1)
                if f.f_code.co_name == "update":
                    f = f.f_back
                print("adds", want, "at types.py line", f.f_back.f_lineno, f.f_back.f_code.co_name)
            set.add(self, x)

        def update(self, other):
            for x in other:
                self.add(x)

    class Evidence(dict):
        def setdefault(self, k, v=None):
            if k == key and k not in self:
                dict.__setitem__(self, k, Watched(v or ()))
            return dict.setdefault(self, k, v)

    class T(Types):
        def collect(self):
            self.ev = Evidence()
            Types.collect(self)

    lf, body = bodies[int(proc, 0)]
    print(T(lf, body, m).ev.get(key))


if __name__ == "__main__":
    main()
