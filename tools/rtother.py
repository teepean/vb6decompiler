#!/usr/bin/env python3
"""Round-trip test projects through our decompiler and another one, and tabulate each step for both.

usage: rtother.py [-j N] [--tests DIR] --other CMD WORKDIR [NAME...]
       rtother.py --one VBP WORK LABEL CMD          (one round trip, printed as a JSON line)

CMD is the other decompiler's command line with {exe} and {out} in it, for example 'sandbox.sh {exe} {out}'
(run it through a sandbox if it is someone else's code).  Needs roundtrip.py and vbmake.py beside it, and VB6
under Wine (VB6WINE).  Ours runs as `decompile.py --nomap`: the other has
no map either.  Tests come from tests/ unless --tests names another folder; NAMEs pick some of them.

For each test and decompiler, in WORKDIR/NAME/ours and WORKDIR/NAME/other: the original built with VB6 under
Wine (cached in VB6RTCACHE as roundtrip.py does), decompiled, built again, then compared:
  decompile   ok, or what went wrong; seconds; procedures given up on (ours: "decompile:" lines on stderr;
              the other: "failed to decompile" in its output)
  raw         runtime calls left untranslated in the output (__vba*, rtc*, ordinals): VB6 stops at the first
              compile error, so this says more about how much is left than the build line does
  build       ok, or VB6's first compile error
  project     same, or which properties differ (roundtrip.same_project: version, title, icon...)
  behaviour   same, or how it differs (roundtrip.same_behaviour or same_files, as the test's rt.txt says)
An EXE built under another name than the original's is taken all the same.  WORKDIR/results.json keeps it all.
"""
import concurrent.futures
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import roundtrip        # noqa: E402
import vbmake           # noqa: E402

SOURCES = (".bas", ".cls", ".frm", ".ctl")


def quiet(f, *a, **kw):
    """f's result and what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = f(*a, **kw)
    return r, buf.getvalue()


def one(vbp, work, label, cmd):
    res = {"test": os.path.basename(os.path.dirname(vbp)), "label": label}
    orig, dec = os.path.join(work, "orig"), os.path.join(work, "dec")
    for d in (orig, dec):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.dirname(vbp), orig, ignore=shutil.ignore_patterns("*.exe", "*.map", "*.log", "*.OBJ"))
    cdir = os.path.join(roundtrip.CACHE, roundtrip.tree_key(orig))
    os.makedirs(cdir, exist_ok=True)
    (ok, log, _), _ = quiet(roundtrip.make_orig, os.path.join(orig, os.path.basename(vbp)), cdir)
    if not ok:
        res["orig"] = "the original did not build"
        return res
    exe_name = roundtrip.exe_name(vbp)
    exe = os.path.join(orig, exe_name)
    if label == "ours":
        argv = [sys.executable, os.path.join(ROOT, "decompile.py"), "--nomap", exe, "-o", dec]
    else:
        argv = [a.replace("{exe}", exe).replace("{out}", dec) for a in shlex.split(cmd)]
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        res["decompile"] = "timeout"
        return res
    res["seconds"] = round(time.time() - t0, 1)
    vbps = [f for f in os.listdir(dec) if f.lower().endswith(".vbp")] if os.path.isdir(dec) else []
    if r.returncode or not vbps:
        last = [ln for ln in (r.stderr + r.stdout).split("\n") if ln.strip()]
        res["decompile"] = "failed: " + (last[-1][:160] if last else "exit %d" % r.returncode)
        return res
    res["decompile"] = "ok"
    srcs = [os.path.join(dec, f) for f in os.listdir(dec) if f.lower().endswith(SOURCES)]
    text = "".join(open(p, encoding="latin-1").read() for p in srcs)
    res["failed_procs"] = sum(ln.startswith("decompile: ") for ln in r.stderr.split("\n")) + \
        text.count("failed to decompile")
    res["lines"] = text.count("\n")
    # runtime calls left untranslated: __vba* / rtc* names, or an ordinal import (ord517)
    res["raw"] = len(re.findall(r"\b(?:__vba\w+|rtc[A-Z]\w*|ord\d+)\b", text))
    ok2, log2 = vbmake.make(os.path.join(dec, vbps[0]))
    want = os.path.join(dec, exe_name)
    if ok2 and not os.path.exists(want):
        exes = [f for f in os.listdir(dec) if f.lower().endswith(".exe")]
        if len(exes) == 1:
            shutil.copy2(os.path.join(dec, exes[0]), want)
            res["exe_name"] = exes[0]
    if not ok2 or not os.path.exists(want):
        errs = roundtrip.compile_errors(log2)
        if errs:
            f, n, msg = errs[0]
            src = os.path.join(dec, f)
            line = roundtrip.source_line(src, n)[1].strip() if os.path.exists(src) else ""
            res["build"] = "%s: %s | %s" % (f, msg, line[:100])
        else:
            res["build"] = (log2.strip().split("\n") or ["no log"])[-1][:160] or "no EXE"
        return res
    res["build"] = "ok"
    same, out = quiet(roundtrip.same_project, exe, want)
    res["project"] = "same" if same else ", ".join(
        ln.split(":", 1)[1].split(" ")[1] for ln in out.split("\n") if ln.startswith("== project:"))
    spec = roundtrip.run_spec(vbp)
    if spec:
        same, out = quiet(roundtrip.same_files, orig, dec, exe_name, spec, cdir)
    else:
        same, out = quiet(roundtrip.same_behaviour, orig, dec, exe_name, cdir)
    said = [ln[len("== behaviour: "):] for ln in out.split("\n") if ln.startswith("== behaviour: ")]
    res["behaviour"] = "same" if same and said else ("not compared" if same else (said[0][:160] if said else "?"))
    return res


def main(argv):
    if argv[:1] == ["--one"]:
        vbp, work, label, cmd = argv[1:5]
        print(json.dumps(one(vbp, work, label, cmd)))
        return 0
    tests = os.path.join(ROOT, "tests")
    jobs, cmd = 4, None
    while argv and argv[0].startswith("-"):
        k = argv.pop(0)
        if k == "-j":
            jobs = int(argv.pop(0))
        elif k == "--tests":
            tests = os.path.abspath(argv.pop(0))
        elif k == "--other":
            cmd = argv.pop(0)
        else:
            print(__doc__)
            return 2
    if not cmd or not argv:
        print(__doc__)
        return 2
    work = os.path.abspath(argv[0])
    names = argv[1:] or sorted(os.listdir(tests))
    runs = []
    for n in names:
        d = os.path.join(tests, n)
        vbps = [f for f in os.listdir(d) if f.lower().endswith(".vbp")] if os.path.isdir(d) else []
        if vbps:
            for label in ("ours", "other"):
                runs.append([os.path.join(d, vbps[0]), os.path.join(work, n, label), label, cmd])

    def go(args):
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--one"] + args,
                           capture_output=True, text=True, errors="replace")
        try:
            return json.loads(r.stdout.strip().split("\n")[-1])
        except (ValueError, IndexError):
            return {"test": os.path.basename(os.path.dirname(args[0])), "label": args[2],
                    "decompile": "driver failed: " + (r.stderr.strip().split("\n") or [""])[-1][:160]}
    os.makedirs(work, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(go, runs))
    json.dump(results, open(os.path.join(work, "results.json"), "w"), indent=1)
    by = {}
    for r in results:
        by.setdefault(r["test"], {})[r["label"]] = r

    def cell(r):
        if not r:
            return "-"
        for k in ("orig", "decompile", "build", "behaviour"):
            if k in r and r[k] not in ("ok", "same"):
                return "%s: %s" % (k, r[k])
        return "same" + ("" if r.get("project") == "same" else " (project: %s)" % r.get("project"))
    print("%-14s | %-60s | %s" % ("test", "ours", "other"))
    for t in sorted(by):
        print("%-14s | %-60s | %s" % (t, cell(by[t].get("ours"))[:60], cell(by[t].get("other"))[:110]))
    for label in ("ours", "other"):
        rs = [r for r in results if r["label"] == label and "orig" not in r]
        print("%-5s: %d tests, decompiled %d, built %d, same behaviour %d, same project %d, %.0f s decompiling, "
              "%d untranslated runtime calls in %d tests" % (
            label, len(rs), sum(r.get("decompile") == "ok" for r in rs), sum(r.get("build") == "ok" for r in rs),
            sum(r.get("behaviour") == "same" for r in rs), sum(r.get("project") == "same" for r in rs),
            sum(r.get("seconds", 0) for r in rs), sum(r.get("raw", 0) for r in rs), sum(r.get("raw", 0) > 0 for r in rs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
