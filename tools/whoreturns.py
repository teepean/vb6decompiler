#!/usr/bin/env python3
"""Who made a procedure a Function: every place in the first pass that sets proc_returns for it.

usage: whoreturns.py EXE MAP 0xVA
Prints the caller's address and statement where the value was seen as used, and whether the
procedure's own epilogue took that back afterwards (lift.no_result).
"""
import os
import sys

os.environ["VB6DEC_JOBS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile
from vb6dec.model import Model


def main(argv):
    exe, mp, want = argv[0], argv[1], int(argv[2], 0)

    class Watch(dict):
        def __setitem__(self, k, v):
            if k == want:
                f = sys._getframe(1)
                loc = f.f_locals
                print("set at decompile.py:%d, caller %s" % (f.f_lineno, hex(loc["va"]) if isinstance(loc.get("va"), int) else None))
                s = loc.get("s")
                if s is not None:
                    print("    statement:", str(s)[:200])
                body = loc.get("body")
                if body is not None and hasattr(s, "dst") and hasattr(s.dst, "key"):
                    t = str(s.dst)
                    for a, ss, br in body:
                        for o in ss:
                            if t in str(o) and o is not s:
                                print("    %x: %s" % (a, str(o)[:160]))
                        if br is not None and t in str(br.cond):
                            print("    %x: if %s" % (a, str(br.cond)[:160]))
                x = loc.get("callers")
                if x:
                    print("    callers returning its value:", [hex(c) for c in x])
            dict.__setitem__(self, k, v)
    m = decompile.load_model(exe, mp) if hasattr(decompile, "load_model") else Model(exe, mp)
    m.proc_returns = Watch(m.proc_returns)
    decompile.first_pass(m)
    print("in the end:", dict.get(m.proc_returns, want))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
