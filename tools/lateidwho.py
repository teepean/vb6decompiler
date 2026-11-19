#!/usr/bin/env python3
"""Every late-bound call by DISPID whose member lateid_member cannot name: the
procedure, the DISPID, the receiver and what is known of its interface.

usage: lateidwho.py EXE
"""
import collections
import os
import sys

os.environ["VB6DEC_JOBS"] = "1"     # (what it watches must happen in this process)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec import calls            # noqa: E402
from vb6dec.model import Model      # noqa: E402
import decompile                    # noqa: E402


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    seen = collections.Counter()
    real = calls.lateid_member

    def spy(lf, ob, dispid):
        r = real(lf, ob, dispid)
        if r is None:
            iface = lf.iface_of(ob)
            seen[(lf.p.name, getattr(dispid, "value", dispid), type(ob).__name__, repr(ob)[:70],
                  getattr(iface, "name", None))] += 1
        return r
    calls.lateid_member = spy
    m = Model(argv[0], None)
    decompile.first_pass(m)
    for k, n in sorted(seen.items(), key=str):
        print(n, k)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
