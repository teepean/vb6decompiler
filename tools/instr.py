"""instr.py DIR: append Tr "<proc>" as first statement of each procedure (CRLF) + mTrace.bas.
Environment: VB6TRACE_ROTATE=N keeps only the last N to 2N lines, in trace0.txt and trace1.txt;
VB6TRACE_SKIP=REGEX leaves out the procedures it matches."""
import os, re, sys
d = sys.argv[1]
hdr = re.compile(r'^(Public |Private |Friend )?(Static )?(Sub|Function|Property Get|Property Let|Property Set) (\w+)')
for fn in os.listdir(d):
    if not fn.lower().endswith((".bas", ".cls", ".frm", ".ctl")): continue
    p = os.path.join(d, fn); lines = open(p, encoding="latin-1", newline="").read().split("\r\n")
    out, cont, pend = [], None, None
    for ln in lines:
        if pend and not ln.startswith(("Attribute ", "#")):
            out.append('    Tr "%s"' % pend); pend = None     # after Attribute / #If lines
        out.append(ln)
        m = hdr.match(ln)
        # (VB6TRACE_SKIP: a regular expression of procedures not traced, the often-called ones of a
        # long run)
        if m and " Declare " not in ln and not (
                os.environ.get("VB6TRACE_SKIP") and re.fullmatch(os.environ["VB6TRACE_SKIP"], m.group(4))):
            cont = "%s.%s" % (fn, m.group(4))
        if cont and not ln.endswith(" _"):
            pend, cont = cont, None
    open(p, "w", encoding="latin-1", newline="").write("\r\n".join(out))
ROTATE = os.environ.get("VB6TRACE_ROTATE")     # lines per file: only the last ones are kept, in
if ROTATE:                                      # trace0.txt / trace1.txt (a long run's tail)
    open(os.path.join(d, "mTrace.bas"), "w", newline="").write(
        'Attribute VB_Name = "mTrace"\r\nPublic trN As Long\r\nPublic trF As Integer\r\n'
        'Public Sub Tr(s As String)\r\n    Dim f As Integer\r\n    trN = trN + 1\r\n    f = FreeFile\r\n'
        '    If trN Mod %s = 1 Then\r\n        trF = 1 - trF\r\n'
        '        Open App.Path & "\\trace" & trF & ".txt" For Output As #f\r\n    Else\r\n'
        '        Open App.Path & "\\trace" & trF & ".txt" For Append As #f\r\n    End If\r\n'
        '    Print #f, s\r\n    Close #f\r\nEnd Sub\r\n' % ROTATE)
else:
    open(os.path.join(d, "mTrace.bas"), "w", newline="").write(
        'Attribute VB_Name = "mTrace"\r\nPublic Sub Tr(s As String)\r\n    Dim f As Integer\r\n    f = FreeFile\r\n'
        '    Open App.Path & "\\trace.txt" For Append As #f\r\n    Print #f, s\r\n    Close #f\r\nEnd Sub\r\n')
v = [x for x in os.listdir(d) if x.endswith(".vbp")][0]
t = open(os.path.join(d, v), encoding="latin-1", newline="").read()
if "mTrace" not in t:
    t = t.replace("Module=", "Module=mTrace; mTrace.bas\r\nModule=", 1)
open(os.path.join(d, v), "w", encoding="latin-1", newline="").write(t)
