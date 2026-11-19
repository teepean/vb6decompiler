#!/usr/bin/env python3
"""Do the .frm properties recovered for an EXE's ActiveX controls
(vb6dec/ocxprops.py, tools/ocxbag) give the controls back?  Each control
goes alone on a form with those properties; VB6 builds it under Wine; the
state VB stores for it in the new EXE is compared with the original's.

usage: ocxcheck.py EXE [WORKDIR] [--max N]

Prints, per distinct control state: same (bytes), same properties (the
bytes differ only where the control writes pointers or stray words: the
state read back gives the same .frm text), differs (first differing byte),
or the build error.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def states(exe):
    """[(form, control, class, state)] of an EXE's ActiveX controls."""
    from vb6dec import model as M, frm
    from vb6dec.project import Project

    class Lite:
        pass
    m = Lite()
    m.prj = Project(exe)
    m.im = m.prj.im
    M.Model._load_ocx_for_controls(m)
    out = []
    orig = frm.FormDecoder.ocx
    for o in m.prj.objects:
        if o.kind != "Form":
            continue
        found = []

        def ocx(self, c, end, found=found):
            orig(self, c, end)
            found.append(c)
        frm.FormDecoder.ocx = ocx
        try:
            frm.decode_form(m, o)
        finally:
            frm.FormDecoder.ocx = orig
        out += [(o.name, c.name, c.progid, c.stream) for c in found]
    return m, out


def main(argv):
    limit = 1000
    if "--max" in argv:
        i = argv.index("--max")
        limit = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__.strip())
        return 2
    import vbmake
    from vb6dec import frm, ocxprops
    work = argv[1] if len(argv) > 1 else tempfile.mkdtemp(prefix="ocxcheck")
    m, found = states(argv[0])
    seen, todo = set(), []
    for form, name, progid, state in found:
        if (progid, state) not in seen:
            seen.add((progid, state))
            todo.append((form, name, progid, state))
    todo = todo[:limit]
    clsids = [frm.coclass_guid(p) for _, _, p, _ in todo]
    got = ocxprops.properties([(c, s) for c, (_, _, _, s) in zip(clsids, todo) if c])
    k = 0
    for i, (form, name, progid, state) in enumerate(todo):
        if not clsids[i]:
            print("%-12s %-14s %-26s no CLSID" % (form, name, progid))
            continue
        res = got.get(k)
        k += 1
        if res is None:
            print("%-12s %-14s %-26s ocxbag could not read it" % (form, name, progid))
            continue
        lines, frx = res
        d = os.path.join(work, "c%d" % i)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
        lib = progid.split(".")[0]
        body = ["VERSION 5.00"]
        from vb6dec.typelib import Registry
        for lb in Registry.libs:
            if getattr(lb, "lib_name", None) == lib:
                body.append('Object = "%s#%d.%d#0"; "%s"' % (lb.libid, lb.lib_major, lb.lib_minor,
                                                            os.path.basename(lb.name)))
        body += ["Begin VB.Form Form1", '   Caption = "F"', "   ClientHeight = 3000",
                 "   ClientWidth = 4000", "   ScaleHeight = 3000", "   ScaleWidth = 4000",
                 "   Begin %s ctl" % progid]
        body += ["      " + ln for ln in ocxprops.rebase(lines, 0, "Form1.frx")]
        body += ["   End", "End", 'Attribute VB_Name = "Form1"',
                 "Attribute VB_GlobalNameSpace = False", "Attribute VB_Creatable = False",
                 "Attribute VB_PredeclaredId = True", "Attribute VB_Exposed = False"]
        with open(os.path.join(d, "Form1.frm"), "w", encoding="cp1252", newline="") as f:
            f.write("\r\n".join(body) + "\r\n")
        with open(os.path.join(d, "Form1.frx"), "wb") as f:
            f.write(frx)
        vbp = ["Type=Exe", "Reference=*\\G{00020430-0000-0000-C000-000000000046}#2.0#0#"
               "..\\..\\Windows\\SysWOW64\\stdole2.tlb#OLE Automation"]
        vbp += [l.replace("Object = ", "Object=").replace('"', "") for l in body[1:2]
                if l.startswith("Object")]
        vbp += ["Form=Form1.frm", 'Startup="Form1"', 'ExeName32="c.exe"', 'Name="c"']
        with open(os.path.join(d, "c.vbp"), "w", newline="") as f:
            f.write("\r\n".join(vbp) + "\r\n")
        ok, log = vbmake.make(os.path.join(d, "c.vbp"))
        if not ok:
            err = open(os.path.join(d, "Form1.log"), encoding="latin-1").read().strip() \
                if os.path.exists(os.path.join(d, "Form1.log")) else log.strip().split("\n")[-1]
            print("%-12s %-14s %-26s build failed: %s" % (form, name, progid, err[:90]))
            continue
        _, new = states(os.path.join(d, "c.exe"))
        if not new:
            print("%-12s %-14s %-26s no control in the new EXE" % (form, name, progid))
            continue
        s2 = new[0][3]
        if s2 == state:
            print("%-12s %-14s %-26s same (%d bytes)" % (form, name, progid, len(state)))
            continue
        # the controls write pointers and stray words into their state: what
        # matters is that the properties read back the same
        again = ocxprops.properties([(clsids[i], s2)]).get(0)
        if again is not None and again[0] == lines and again[1] == frx:
            print("%-12s %-14s %-26s same properties (%d bytes, %d differ)" % (
                form, name, progid, len(state),
                sum(1 for a, b in zip(state, s2) if a != b) + abs(len(state) - len(s2))))
        else:
            at = next((j for j in range(min(len(s2), len(state))) if s2[j] != state[j]),
                      min(len(s2), len(state)))
            print("%-12s %-14s %-26s differs at %d (%d / %d bytes)" % (
                form, name, progid, at, len(state), len(s2)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
