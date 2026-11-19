#!/usr/bin/env python3
"""The address of a procedure by the name the decompiler gives it.

usage: procva.py EXE NAME...   (any part of the name, any case)"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model

m = Model(sys.argv[1], None)
for va, p in sorted(m.procs.items()):
    if any(n.lower() in p.name.lower() for n in sys.argv[2:]):
        print("%X %s" % (va, p.name))
