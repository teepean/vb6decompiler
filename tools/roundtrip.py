#!/usr/bin/env python3
"""Source -> EXE -> decompiled source -> EXE, without leaving Linux.

usage: roundtrip.py [--nomap] [--nocache] PROJECT.vbp WORKDIR

  WORKDIR/orig  the project, built with VB6 under Wine (tools/vbmake.py)
  WORKDIR/dec   decompile.py's output for that EXE, built again

--nomap decompiles without the linker map (no true procedure names), the way
a program from elsewhere would be.  Projects with forms that have controls
do not build under Wine; see vbmake.py.

By default both builds run without arguments and must write the same
out.txt.  An rt.txt next to the .vbp says otherwise, one "key: value" per line:
  args: -timedemo demo1 -hidden      command line for both runs
  compare: sync.txt shot.bmp         files that must be byte-identical
  data: freedoom1.wad default.cfg    copied next to the decompiled EXE
  timeout: 600                       seconds per run

The original's build and its run outputs are kept in VB6RTCACHE (default
/tmp/vb6dec-rtcache) under a hash of the project's files and vbmake.py, and
reused while those are unchanged (--nocache builds and runs it again).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake           # noqa: E402

CACHE = os.environ.get("VB6RTCACHE") or "/tmp/vb6dec-rtcache"


def exe_name(vbp):
    for ln in open(vbp, encoding="latin-1").read().replace("\r", "").split("\n"):
        if ln.startswith("ExeName32="):
            return ln.split("=", 1)[1].strip('"')
    return os.path.splitext(os.path.basename(vbp))[0] + ".exe"


def files_of(d):
    """{path relative to d: mtime} of every file under d."""
    out = {}
    for root, dirs, fs in os.walk(d):
        for f in fs:
            p = os.path.join(root, f)
            out[os.path.relpath(p, d)] = os.stat(p).st_mtime_ns
    return out


def tree_key(d):
    """Hash of the files under d (names and contents) and of vbmake.py."""
    h = hashlib.sha256(open(os.path.join(HERE, "vbmake.py"), "rb").read())
    for rel in sorted(files_of(d)):
        h.update(rel.encode("utf-8", "surrogateescape") + b"\0")
        h.update(hashlib.sha256(open(os.path.join(d, rel), "rb").read()).digest())
    return h.hexdigest()[:24]


def store(cdir, part, files):
    """Save {relative path: bytes or None} as cdir/part (absent files listed
    in part.json); written to a temporary name first, so that a concurrent
    round trip of the same project sees all of it or nothing."""
    tmp = os.path.join(cdir, "%s.tmp%d" % (part, os.getpid()))
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for rel, data in files.items():
        if data is not None:
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            open(os.path.join(tmp, rel), "wb").write(data)
    json.dump(sorted(files), open(os.path.join(tmp, part + ".json"), "w"))
    try:
        os.rename(tmp, os.path.join(cdir, part))
    except OSError:                     # another round trip stored it first
        shutil.rmtree(tmp, ignore_errors=True)


def load(cdir, part, into):
    """What store() saved, copied into the folder `into`: {relative path:
    bytes or None}, or None when nothing is stored."""
    d = os.path.join(cdir, part) if cdir else None
    if not d or not os.path.isdir(d):
        return None
    out = {}
    for rel in json.load(open(os.path.join(d, part + ".json"))):
        p = os.path.join(d, rel)
        out[rel] = open(p, "rb").read() if os.path.exists(p) else None
        if out[rel] is not None:
            os.makedirs(os.path.dirname(os.path.join(into, rel)), exist_ok=True)
            shutil.copy2(p, os.path.join(into, rel))
    return out


def make_orig(vbp, cdir):
    """vbmake.make, or what an earlier build of the same files wrote:
    (succeeded, log text, whether it came from the cache)."""
    d = os.path.dirname(vbp)
    got = load(cdir, "build", d)
    if got is not None:
        return True, got["make.log"].decode("latin-1").replace("\r", ""), True
    before = files_of(d)
    ok, log = vbmake.make(vbp)
    if ok and cdir:
        new = [rel for rel, t in files_of(d).items() if before.get(rel) != t]
        store(cdir, "build", {rel: open(os.path.join(d, rel), "rb").read() for rel in new})
    return ok, log, False


def run_build(d, exe, names, cdir, **kw):
    """Run the build in d (vbmake.run arguments in kw) and read the files it
    writes: ({name: bytes or None}, whether it ended).  With cdir (the
    original's), an earlier run's files are taken instead when there are any."""
    got = load(cdir, "run", d)
    if got is not None and sorted(got) == sorted(names):
        return got, True
    for attempt in (1, 2, 3):
        for f in names:
            if os.path.exists(os.path.join(d, f)):
                os.remove(os.path.join(d, f))
        done = vbmake.run(os.path.join(d, exe), **kw)
        got = {f: open(os.path.join(d, f), "rb").read()
               if os.path.exists(os.path.join(d, f)) else None for f in names}
        if not done or any(v is not None for v in got.values()) or attempt == 3 or \
                vbmake.LAST_BOX:
            break
        # nothing written at all: once in a while Wine fails to start a
        # process (rtsuite -j 8, more often since the suite has VBDoom: t05m's
        # decompiled build); a real runtime error writes nothing every time
        print("== note: %s wrote nothing; running it once more" % os.path.basename(d))
        import time
        time.sleep(1)
    if done and cdir:
        store(cdir, "run", got)
    return got, done


def main(argv):
    nomap = "--nomap" in argv
    nocache = "--nocache" in argv
    argv = [a for a in argv if a not in ("--nomap", "--nocache")]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    vbp, work = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    orig, dec = os.path.join(work, "orig"), os.path.join(work, "dec")
    for d in (orig, dec):
        if os.path.isdir(d):
            shutil.rmtree(d)
    shutil.copytree(os.path.dirname(vbp), orig,
                    ignore=shutil.ignore_patterns("*.exe", "*.map", "*.log", "*.OBJ"))
    cdir = None if nocache else os.path.join(CACHE, tree_key(orig))
    if cdir:
        os.makedirs(cdir, exist_ok=True)
    ok, log, cached = make_orig(os.path.join(orig, os.path.basename(vbp)), cdir)
    print("== original%s:" % (" (cached)" if cached else ""), log.strip())
    if not ok:
        return 1
    exe = os.path.join(orig, exe_name(vbp))
    cmd = [sys.executable, os.path.join(HERE, "..", "decompile.py"), exe]
    if nomap:
        cmd.append("--nomap")
    r = subprocess.run(cmd + ["-o", dec], capture_output=True, text=True)
    if r.returncode:
        print("== decompile failed:\n" + r.stdout + r.stderr)
        return 1
    srcs = [os.path.join(dec, f) for f in sorted(os.listdir(dec))
            if f.lower().endswith((".bas", ".cls", ".frm", ".ctl"))]
    for tool in ("lint.py", "vbcheck.py"):
        if not os.path.exists(os.path.join(HERE, tool)):     # (lint.py is not published)
            continue
        args = srcs if tool == "lint.py" else [dec]
        out = subprocess.run([sys.executable, os.path.join(HERE, tool)] + args,
                             capture_output=True, text=True).stdout
        print("== %s: %s" % (tool, out.strip().split("\n")[-1]))
        for ln in out.strip().split("\n")[:-1][:20]:
            print("   " + ln)
    vbps = [f for f in os.listdir(dec) if f.lower().endswith(".vbp")]
    ok2, log2 = vbmake.make(os.path.join(dec, vbps[0]))
    print("== decompiled:", log2.strip())
    for f, n, msg in compile_errors(log2):
        src = os.path.join(dec, f)
        if os.path.exists(src):
            i, ln = source_line(src, n)
            print("   %s:%d: %s\n   | %s" % (f, i, msg, ln.strip()))
    if ok2 and not os.path.exists(os.path.join(dec, exe_name(vbp))):
        print("== the decompiled project built no %s" % exe_name(vbp))
        ok2 = False
    if ok2:
        ok2 = same_project(os.path.join(orig, exe_name(vbp)), os.path.join(dec, exe_name(vbp)))
        spec = run_spec(vbp)
        if spec:
            ok2 = same_files(orig, dec, exe_name(vbp), spec, cdir) and ok2
        else:
            ok2 = same_behaviour(orig, dec, exe_name(vbp), cdir) and ok2
    return 0 if ok2 else 1


def same_project(a, b):
    """What the project's properties leave in the EXE, which App gives back and Explorer shows, must come back."""
    sys.path.insert(0, os.path.dirname(HERE))
    from vb6dec.project import Project
    from vb6dec import detect, resources

    def facts(path):
        p = Project(path)
        v = resources.version_info(p.im) or {}
        f = {"title": p.title, "EXE name": p.exe_base, "help file": p.help_file, "thread flags": p.thread_flags,
             "version": (v.get("major"), v.get("minor"), v.get("revision")), "icon": resources.icon_images(p.im)}
        f.update(v.get("strings", {}))
        return f, detect.options(p.im)
    (x, xo), (y, yo) = facts(a), facts(b)
    bad = [k for k in sorted(set(x) | set(y)) if x.get(k) != y.get(k)]
    for k in bad:
        print("== project: %s %r, rebuilt %r" % (k, "(differs)" if k == "icon" else x.get(k),
                                                 "(differs)" if k == "icon" else y.get(k)))
    # (read from the code, and a check nothing needed leaves no mark: not a failure)
    for k in ("optimisation", "BoundsCheck", "OverflowCheck", "FlPointCheck", "FDIVCheck"):
        if xo.get(k) != yo.get(k):
            print("== note: %s %r, rebuilt %r" % (k, xo.get(k), yo.get(k)))
    return not bad


def run_spec(vbp):
    """The test's rt.txt as a dict (see the module docstring), or None."""
    p = os.path.join(os.path.dirname(vbp), "rt.txt")
    if not os.path.exists(p):
        return None
    spec = {"args": [], "compare": [], "data": [], "timeout": ["120"]}
    for ln in open(p, encoding="latin-1").read().replace("\r", "").split("\n"):
        if ":" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split(":", 1)
            spec[k.strip()] = v.split()
    return spec


def same_files(orig, dec, exe, spec, cdir=None):
    """Run both builds with the test's arguments; the listed output files must
    be identical."""
    for f in spec["data"]:
        if not os.path.exists(os.path.join(dec, f)):
            shutil.copy(os.path.join(orig, f), os.path.join(dec, f))
    got = []
    for d in (orig, dec):
        kw = dict(timeout=int(spec["timeout"][0]), args=spec["args"])
        out, done = run_build(d, exe, spec["compare"], cdir if d == orig else None, **kw)
        got.append(out)
        if not done and vbmake.LAST_BOX:
            print("== behaviour: the %s build stopped at a message box: %s" % (
                "original" if d == orig else "decompiled", vbmake.LAST_BOX[0][:200]))
        elif not done:
            print("== behaviour: the %s build did not end in %s s" % (
                "original" if d == orig else "decompiled", spec["timeout"][0]))
    ok = True
    for f in spec["compare"]:
        a, b = got[0][f], got[1][f]
        if a is None:
            print("== behaviour: the ORIGINAL wrote no %s" % f)
            ok = False
        elif b is None:
            print("== behaviour: the decompiled build wrote no %s (a runtime error?)" % f)
            ok = False
        elif a != b:
            i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            ln = a[:i].count(b"\n") + 1
            print("== behaviour: %s DIFFERS from byte %d (line %d)" % (f, i, ln))
            if f.endswith((".txt", ".log")):
                la = a.split(b"\n")[ln - 1:ln + 1]
                lb = b.split(b"\n")[ln - 1:ln + 1]
                print("   original:   %r\n   decompiled: %r" % (la, lb))
            ok = False
    if ok:
        print("== behaviour: same output (%s)" % " ".join(spec["compare"]))
    return ok


def same_behaviour(orig, dec, exe, cdir=None):
    """Tests whose Sub Main writes out.txt next to the EXE: run both builds,
    the decompiled one must write the same text."""
    outs = []
    for d in (orig, dec):
        out = run_build(d, exe, ["out.txt"], cdir if d == orig else None)[0]["out.txt"]
        # (as a text-mode read gave it: any line end is "\n")
        outs.append(out.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
                    if out is not None else None)
    if outs[0] is None:
        src = "".join(open(os.path.join(orig, f), encoding="latin-1").read()
                      for f in os.listdir(orig) if f.lower().endswith((".bas", ".cls", ".frm", ".ctl")))
        if "out.txt" in src:
            print("== behaviour: the ORIGINAL wrote no out.txt (a runtime error?)")
            return False
        return True                     # the test does not write one
    if outs[1] is None:
        print("== behaviour: the decompiled build wrote no out.txt (a runtime error?)")
        return False
    if not outs[0]:
        # (Print # keeps its text until Close: a runtime error leaves an empty file, both ways)
        print("== behaviour: the ORIGINAL's out.txt is empty: nothing compared (a runtime error?)")
        return False
    same = outs[0] == outs[1]
    print("== behaviour: %s" % ("same output" if same else "DIFFERENT output"))
    if not same:
        print("   original:   %r\n   decompiled: %r" % (outs[0][:300], (outs[1] or "")[:300]))
    return same


def compile_errors(log):
    """(file name, VB line, message) for each 'Compile Error' in a build log."""
    import re
    out = []
    for m in re.finditer(r"Compile Error in File '([^']*)', Line (\d+) : (.*)", log):
        out.append((m.group(1).replace("\\", "/").split("/")[-1], int(m.group(2)), m.group(3)))
    return out


def source_line(path, n):
    """VB counts lines from 0, after the Attribute lines: (file line, text).
    An empty event procedure (header, End Sub, the blank line after) is not
    counted: VB drops it."""
    import re
    lines = open(path, encoding="latin-1").read().replace("\r", "").split("\n")
    last = max((i for i, ln in enumerate(lines) if ln.startswith("Attribute ")), default=-1)
    owners = {"form", "mdiform", "class", "usercontrol"} | {
        m.group(1).lower() for m in (re.match(r"^\s*Begin \S+ (\w+)", ln) for ln in lines[:last])
        if m}
    i, left = last + 1, n
    while i < len(lines):
        m = re.match(r"^(?:Private |Public )?Sub (\w+)_[A-Za-z]+\(", lines[i])     # Command1_Click
        if m and m.group(1).lower() in owners and i + 1 < len(lines) and \
                lines[i + 1].startswith("End Sub"):
            i += 2 + (i + 2 < len(lines) and not lines[i + 2].strip())
            continue
        if left == 0:
            break
        left -= 1
        i += 1
    return i + 1, lines[i] if i < len(lines) else ""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
