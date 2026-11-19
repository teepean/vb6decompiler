"""An ActiveX control's .frm properties from the state its EXE keeps.

The EXE has the bytes of the control's IPersistStreamInit::Save; a .frm has
the property bag text VB writes through IPersistPropertyBag::Save.  Going
from one to the other takes the control itself, so tools/ocxbag/ocxbag.exe
loads each control and saves it into a bag that writes .frm lines: on Windows
as it is, elsewhere under Wine (the prefix tools/vbmake.py builds in, where
the OCXs are registered).  A control that isn't registered, or no ocxbag.exe,
and nothing comes back: frm.py falls back to the extent and version
(stream_props).
"""
import os
import re
import struct
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.normpath(os.path.join(HERE, "..", "tools", "ocxbag", "ocxbag.exe"))
PREFIX = os.environ.get("VB6WINE", os.path.normpath(os.path.join(HERE, "..", "..", "wine-vb6")))
FRX = "@frx@"           # the .frx name in ocxbag's output, until rebase() puts the real one


NATIVE = sys.platform == "win32"


def available():
    return os.path.exists(EXE) and (NATIVE or os.path.isdir(PREFIX) and shutil.which("wine") is not None)


def winpath(path):
    return os.path.abspath(path) if NATIVE else "Z:" + os.path.abspath(path).replace("/", "\\")


def properties(controls, timeout=600):
    """controls: [(clsid, stream bytes)].  Returns {index: (lines, frx bytes)}
    for the controls ocxbag could read: .frm lines (three spaces a nesting
    level, none for the control's own) whose .frx references count from 0."""
    if not controls or not available():
        return {}
    out = {}
    with tempfile.TemporaryDirectory(prefix="ocxbag", ignore_cleanup_errors=True) as d:
        jobs = []
        for i, (clsid, stream) in enumerate(controls):
            base = os.path.join(d, "c%d" % i)
            with open(base + ".stream", "wb") as f:
                f.write(stream)
            jobs.append("\t".join([clsid, winpath(base + ".stream"), winpath(base + ".txt"),
                                   winpath(base + ".frx"), "0", FRX]))
        with open(os.path.join(d, "jobs.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(jobs) + "\n")
        jobfile = winpath(os.path.join(d, "jobs.txt"))
        if NATIVE:
            cmd, kw = [EXE, jobfile], {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            cmd, kw = ["wine", EXE, jobfile], {"env": dict(os.environ, WINEPREFIX=PREFIX, WINEDEBUG="-all")}
        try:
            subprocess.run(cmd, cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, **kw)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        for i in range(len(controls)):
            base = os.path.join(d, "c%d" % i)
            try:
                text = open(base + ".txt", encoding="cp1252", errors="replace", newline="").read()
                frx = open(base + ".frx", "rb").read()
            except OSError:
                continue
            lines, frx = spill_strings(text, frx)
            lines = [ln for ln in lines if ln]
            if lines and not any("could not be read" in ln for ln in lines):
                out[i] = (lines, frx)
    return out


def spill_strings(text, frx):
    """.frm lines from ocxbag's text, the Strings a line can't hold in the .frx as VB does:
    $"x.frx":offset, the bytes with a dword length.  (A RichTextBox's TextRTF has line breaks:
    "datirob.frm could not be loaded", no compile error at all; DarwinBots.)"""
    def closed(s):
        return '"' in s.replace('""', "")
    frx = bytes(frx)
    out, lines, i = [], text.split("\r\n"), 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r'^(\s*\w+\s*=\s*)"(.*)$', ln, re.S)
        if m:
            body, j = m.group(2), i
            while not closed(body) and j + 1 < len(lines):
                j += 1
                body += "\r\n" + lines[j]
            if j > i or len(body) > 900 or any(ord(c) < 32 for c in body):
                raw = body[:body.rstrip().rfind('"')].replace('""', '"').encode("cp1252", "replace")
                out.append('%s$"%s":%04X' % (m.group(1), FRX, len(frx)))
                frx += struct.pack("<I", len(raw)) + raw
                i = j + 1
                continue
        out.append(ln)
        i += 1
    return out, frx


def rebase(lines, base, frx_name):
    """ocxbag's .frx references, moved to where its bytes go in the form's
    .frx and named after it."""
    def fix(m):
        return '"%s":%04X' % (frx_name, int(m.group(1), 16) + base)
    return [re.sub(r'"%s":([0-9A-Fa-f]+)' % re.escape(FRX), fix, ln) for ln in lines]
