"""Print the designer text (Begin VB.Form ... End) decoded for each form of an EXE, or for one form.

usage: dumpfrm.py EXE [FORM]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model
from vb6dec.frm import decode_form
m = Model(sys.argv[1])
for o in m.prj.objects:
    if o.kind == "Form" and (len(sys.argv) < 3 or o.name == sys.argv[2]):
        txt, frx = decode_form(m, o)
        print(txt)
