#!/usr/bin/env python3
"""Find the statement of a built source that raises an uncaught run time error: number the lines of
one procedure, give it a handler that writes Err and Erl to a file, build and run it.

usage: errline.py SRC.vbp FILE.bas PROC WORKDIR [OUTFILE_HANDLE]

Copies the project to the new WORKDIR, edits the copy, runs it under Wine (the runs may show windows)
and prints the numbered line the error came from."""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    vbp, fname, proc, work = argv[:4]
    src = os.path.dirname(os.path.abspath(vbp))
    if os.path.exists(work):
        print("errline: %s exists; give a new directory" % work)
        return 2
    shutil.copytree(src, work)
    p = os.path.join(work, fname)
    lines = open(p, encoding="cp1252", newline="").read().split("\r\n")
    out, inside, n, table = [], False, 0, {}
    log = os.path.join(work, "errline.txt").replace("/", "\\")
    for ln in lines:
        if re.match(r"(?:Public |Private |Friend )?(?:Sub|Function) %s\(" % re.escape(proc), ln):
            out += [ln, "    On Error GoTo LERR99"]
            inside = True
            continue
        if inside and re.match(r"End (Sub|Function)", ln):
            kind = ln.split()[1]
            out += ["    Exit %s" % kind, "LERR99:", '    Dim h99 As Integer: h99 = FreeFile',
                    '    Open "Z:%s" For Output As #h99' % log,
                    "    Print #h99, Err.Number, Err.Description, Erl", "    Close #h99",
                    "    End", ln]
            inside = False
            continue
        if inside and ln.startswith("    ") and not re.match(
                r"\s*(Dim|End|Else|ElseIf|Next|Loop|Case|Select|For|If|Do|With|Wend|While|L[0-9A-F]+:)\b", ln):
            n += 1
            table[n] = ln.strip()
            ln = "%d %s" % (n, ln.strip())
        out.append(ln)
    open(p, "w", encoding="cp1252", newline="").write("\r\n".join(out))
    r = subprocess.run([sys.executable, os.path.join(HERE, "vbmake.py"), os.path.join(work, os.path.basename(vbp))],
                       capture_output=True, text=True)
    if "succeeded" not in r.stdout:
        print(r.stdout[-400:])
        return 1
    exe = [f for f in os.listdir(work) if f.lower().endswith(".exe")]
    env = dict(os.environ, WINEPREFIX=os.environ.get("VB6WINE") or os.path.join(HERE, "..", "..", "wine-vb6"))
    try:
        subprocess.run(["wine", exe[0]], cwd=work, env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        print("errline: timed out")
    f = os.path.join(work, "errline.txt")
    if not os.path.exists(f):
        print("errline: no error reached the handler")
        return 0
    t = open(f, encoding="cp1252").read().strip()
    print(t)
    m = re.search(r"(\d+)\s*$", t)
    if m:
        print(table.get(int(m.group(1)), "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
