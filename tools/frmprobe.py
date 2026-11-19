#!/usr/bin/env python3
"""What VB6 writes into an EXE for a form: build a .frm under Wine and trace
its designer data record by record, property by property (opcode, bytes,
what the decoder made of it).  The oracle for unknown property opcodes.

usage: frmprobe.py FORM.frm [WORKDIR]      build FORM.frm alone and trace it
       frmprobe.py --exe EXE [FORM]        trace the forms of a built EXE

The .frm may use OCX controls: its `Object = "{GUID}#ver#0"; "X.OCX"` lines
go into the project.  Forms build under Wine since 2026-10-28 (the crash of
2026-10-24 is gone); fonts Wine lacks are stored under substitute names.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def build(frm_path, work):
    import vbmake
    text = open(frm_path, encoding="latin-1").read().replace("\r", "")
    name = re.search(r'Attribute VB_Name = "(\w+)"', text)
    name = name.group(1) if name else "Form1"
    objs = re.findall(r'^Object = ("\{[^"]+\}#[^"]+")\s*;\s*("[^"]+")', text, re.M)
    os.makedirs(work, exist_ok=True)
    if os.path.abspath(os.path.dirname(frm_path)) != os.path.abspath(work):
        shutil.copy(frm_path, os.path.join(work, os.path.basename(frm_path)))
        frx = os.path.splitext(frm_path)[0] + ".frx"
        if os.path.exists(frx):
            shutil.copy(frx, work)
    vbp = ["Type=Exe",
           "Reference=*\\G{00020430-0000-0000-C000-000000000046}#2.0#0#"
           "..\\..\\Windows\\SysWOW64\\stdole2.tlb#OLE Automation"]
    vbp += ["Object=%s; %s" % (g.strip('"'), f.strip('"')) for g, f in objs]
    vbp += ["Form=" + os.path.basename(frm_path), 'Startup="%s"' % name,
            'ExeName32="probe.exe"', 'Name="probe"', "CompilationType=0"]
    p = os.path.join(work, "probe.vbp")
    open(p, "w", newline="").write("\r\n".join(vbp) + "\r\n")
    ok, log = vbmake.make(p)
    if not ok:
        sys.exit("build failed:\n" + log)
    return os.path.join(work, "probe.exe")


def trace(exe, only=None):
    from vb6dec import model as M, frm
    from vb6dec.project import Project
    M.load_libs()

    class Lite:
        pass
    m = Lite()
    m.prj = Project(exe)
    m.im = m.prj.im
    M.Model._load_ocx_for_controls(m)
    orig_control, orig_prop = frm.FormDecoder.control, frm.FormDecoder.prop

    def control(self, is_form=False):
        d, p = self.r.d, self.r.p
        n = int.from_bytes(d[p:p + 4], "little")
        print("record @%04x len %d%s" % (p, n & 0x7FFFFFFF, " (array element)" if n >> 31 else ""))
        c = orig_control(self, is_form)
        print("  = %s %s (ctype %d)%s" % (c.vbclass, c.name, c.ctype,
                                         "" if c.index is None else " Index %d" % c.index))
        return c

    def prop(self, c, op, table, is_form, end):
        p, n = self.r.p, len(c.props)
        ok = orig_prop(self, c, op, table, is_form, end)
        raw = self.r.d[p:self.r.p] if ok else self.r.d[p:min(end, p + 24)]
        ent = table.get(op)
        last = c.props[-1] if ok and len(c.props) > n else None
        print("    op %3d (%02Xh) %-16s %-36s %s" % (
            op, op, ent[0] if ent else "?", raw.hex(" ")[:36],
            ("-> %s = %r" % (last.name, last.value)) if last is not None else
            ("" if ok else "NOT DECODED, rest skipped")))
        return ok
    frm.FormDecoder.control, frm.FormDecoder.prop = control, prop
    for o in m.prj.objects:
        if o.kind == "Form" and (only is None or o.name.lower() == only.lower()):
            print("== form", o.name)
            frm.decode_form(m, o)


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    if argv[0] == "--exe":
        trace(argv[1], argv[2] if len(argv) > 2 else None)
        return 0
    work = argv[1] if len(argv) > 1 else tempfile.mkdtemp(prefix="frmprobe")
    trace(build(argv[0], work))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
