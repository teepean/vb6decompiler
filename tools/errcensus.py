#!/usr/bin/env python3
"""Every procedure of a decompiled project that VB6 will not compile, not
just the first: build a copy, note the first compile error, empty the body of
the procedure it is in, build again (VB stops at the first error).

usage: errcensus.py DECOMPILED_DIR WORKDIR [--max N] [--static] [-j N]

Prints one line per failing procedure (file, procedure, message, the line)
and the counts by message.  The copy in WORKDIR ends up with the failing
bodies emptied; DECOMPILED_DIR is not changed.  An error outside any
procedure (a declaration) ends the census.

--static first empties every procedure tools/lint.py and tools/vbcheck.py
flag (listed as "static: ..."): VB reports one error a build, and a big
program (4224 vbcheck problems in one, a minute a build) would take
days otherwise.

-j N builds N groups of files side by side (parallel_census).

The event procedures of UserControls are renamed in the copy: VB6 runs them when it loads a form
holding one, compiling each on demand, and to report an error there it opens its main window, which
crashes under Wine (no MSO97RT.DLL), or stops on a run-time error box.  Renamed they still compile.
"""
import collections
import os
import re
import shutil
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake           # noqa: E402
from roundtrip import compile_errors, source_line   # noqa: E402

HEAD = re.compile(r"^(Public |Private |Friend )?(Static )?(Sub|Function|Property (Get|Let|Set)) (\w+)")


def a0_back(lines, i):
    """The last statement line of the procedure before line i (0-based), or None."""
    j = i - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j >= 0 and lines[j].startswith(("End Sub", "End Function", "End Property")):
        j -= 1
        return j if j >= 0 and not HEAD.match(lines[j]) else None
    return None


def procedure_at(lines, i):
    """(first line, last line, name) of the procedure holding line i (0-based)."""
    start = None
    for j in range(i, -1, -1):
        m = HEAD.match(lines[j])
        if m:
            start, name = j, m.group(5)
            break
        if j < i and lines[j].startswith(("End Sub", "End Function", "End Property")):
            return None                 # (the error on an End line is that procedure's)
    if start is None:
        return None
    for j in range(i, len(lines)):
        if lines[j].startswith(("End Sub", "End Function", "End Property")):
            return start, j, name
    return None


def quiet_usercontrols(work):
    """Rename the UserControls' event procedures, so that loading the forms runs none of them."""
    for f in os.listdir(work):
        if f.lower().endswith(".ctl"):
            path = os.path.join(work, f)
            s = open(path, encoding="latin-1", newline="").read()
            s = re.sub(r"\bUserControl_(\w+)", r"CensusUC_\1", s)
            open(path, "w", encoding="latin-1", newline="").write(s)


def empty_flagged(work):
    """Empty the procedures lint.py and vbcheck.py flag; returns the census
    lines for them."""
    import subprocess
    py = sys.executable
    files = [os.path.join(work, f) for f in sorted(os.listdir(work))
             if f.lower().endswith((".bas", ".cls", ".frm", ".ctl"))]
    out = subprocess.run([py, os.path.join(HERE, "lint.py")] + files,
                         capture_output=True, text=True, errors="replace").stdout
    out += subprocess.run([py, os.path.join(HERE, "vbcheck.py"), work],
                          capture_output=True, text=True, errors="replace").stdout
    flagged = {}                        # path -> {line: message}
    for ln in out.split("\n"):
        m = re.match(r"^(.*?):(\d+): (.*)$", ln)
        if m and os.path.exists(m.group(1)):
            flagged.setdefault(m.group(1), {}).setdefault(int(m.group(2)), m.group(3))
    found = []
    for path, at in sorted(flagged.items()):
        text = open(path, encoding="latin-1", newline="").read()
        lines = text.split("\r\n")
        cuts = {}
        for n, msg in sorted(at.items()):
            where = procedure_at(lines, n - 1)
            if where is not None and where[0] not in cuts:
                cuts[where[0]] = (where, msg, lines[n - 1].strip())
        for a, ((_, b, name), msg, line) in sorted(cuts.items(), reverse=True):
            lines[a + 1:b] = ["    ' (errcensus: emptied)"]
            found.append((os.path.basename(path), name, "static: " + msg.split(":")[0], line))
        open(path, "w", encoding="latin-1", newline="").write("\r\n".join(lines))
    for f, name, msg, line in sorted(found):
        print("%-14s %-28s %-40s | %s" % (f, name, msg[:40], line[:70]), flush=True)
    return found


def empty_all(path):
    """Empty every procedure of a source file: what the other files of a build
    see of it (declarations, signatures) stays."""
    lines = open(path, encoding="latin-1", newline="").read().split("\r\n")
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        if HEAD.match(lines[i]):
            j = i + 1
            while j < len(lines) and not lines[j].startswith(("End Sub", "End Function",
                                                              "End Property")):
                j += 1
            out.append("    ' (errcensus: other group)")
            i = j
            continue
        i += 1
    open(path, "w", encoding="latin-1", newline="").write("\r\n".join(out))


def census_group(work, group, limit):
    """census of the files in group, in a copy of work with every other file's
    procedures emptied.  Returns (found, stop message or None)."""
    vbp = os.path.join(work, next(f for f in os.listdir(work) if f.lower().endswith(".vbp")))
    found = []
    for f in os.listdir(work):
        if f.lower().endswith((".bas", ".cls", ".frm", ".ctl")) and f not in group:
            empty_all(os.path.join(work, f))
    for _ in range(limit):
        ok, log = vbmake.make(vbp)
        if ok:
            return found, None
        errs = compile_errors(log)
        if not errs:
            return found, "no compile error to act on: " + log.strip().split("\n")[-1]
        f, n, msg = errs[0]
        path = os.path.join(work, f)
        lines = open(path, encoding="latin-1", newline="").read().split("\r\n")
        k, line = source_line(path, n)
        where = procedure_at(lines, k - 1)
        if where is not None and k - 1 == where[0] and lines[where[0]].rstrip().endswith("()") and a0_back(lines, where[0]):
            # VB's count drifts by an empty event procedure (roundtrip.source_line): a bare header
            # can't be wrong, it's the last line before it (DarwinBots Form1 Qadd)
            k = a0_back(lines, where[0]) + 1
            line = lines[k - 1]
            where = procedure_at(lines, k - 1)
        if where is None:
            return found, "%s:%d: %s (outside a procedure)" % (f, k, msg)
        a, b, name = where
        if found.count((f, name, msg, line.strip())) >= 2:
            # emptying it doesn't help: the error isn't where the line number says
            return found, "%s:%d: %s (stays after emptying %s)" % (f, k, msg, name)
        found.append((f, name, msg, line.strip()))
        print("%-14s %-28s %-40s | %s" % (f, name, msg[:40], line.strip()[:70]), flush=True)
        if k - 1 == a:
            m = re.match(r"^(.*?\w+)\((.*)\)(.*)$", lines[a])
            if m is None or not m.group(2):
                return found, "%s: the signature cannot be cut back" % name
            lines[a] = m.group(1) + "()" + m.group(3)
        lines[a + 1:b] = ["    ' (errcensus: emptied)"]
        open(path, "w", encoding="latin-1", newline="").write("\r\n".join(lines))
    return found, "--max reached"


def parallel_census(work, vbp, jobs, limit):
    """-j N: the files in N groups, each with its own copy of the project in
    which the other groups' procedures are empty (a build there is seconds, and
    VB reaches this group's first error at once), until no group has an error.
    Rounds repeat what a signature cut back in one group breaks in another."""
    import concurrent.futures
    found = []
    for rnd in range(limit):
        files = [f for f in sorted(os.listdir(work))
                 if f.lower().endswith((".bas", ".cls", ".frm", ".ctl"))]
        files.sort(key=lambda f: -os.path.getsize(os.path.join(work, f)))
        groups = [[0, []] for _ in range(jobs)]
        for f in files:
            g = min(groups, key=lambda x: x[0])
            g[0] += os.path.getsize(os.path.join(work, f))
            g[1].append(f)

        def one(i):
            # (a directory this run has just made: nothing already there is replaced,
            # and only these are removed below)
            d = tempfile.mkdtemp(prefix="%s.g%d." % (os.path.basename(work.rstrip("/")), i),
                                 dir=os.path.dirname(os.path.abspath(work)))
            shutil.copytree(work, d, ignore=shutil.ignore_patterns("*.exe", "*.log", "*.OBJ"),
                            dirs_exist_ok=True)
            got, stop = census_group(d, set(groups[i][1]), limit)
            return d, groups[i][1], got, stop
        with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
            res = list(pool.map(one, range(jobs)))
        n = 0
        for d, group, got, stop in res:
            for f in group:
                shutil.copy(os.path.join(d, f), os.path.join(work, f))
            found += got
            n += len(got)
            if stop:
                print("== group stopped: %s" % stop, flush=True)
            shutil.rmtree(d)
        print("== round %d: %d procedure(s)" % (rnd + 1, n), flush=True)
        if n == 0:
            break
    return found


def main(argv):
    limit = 500
    if "--max" in argv:
        i = argv.index("--max")
        limit = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    jobs = 1
    if "-j" in argv:
        i = argv.index("-j")
        jobs = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    static = "--static" in argv
    argv = [a for a in argv if a != "--static"]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    src, work = argv
    if os.path.lexists(work):
        # WORKDIR is created here, never cleared: a mistyped path must not lose a directory
        print("errcensus: %s already exists; give a new directory" % work, file=sys.stderr)
        return 2
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("*.exe", "*.log", "*.OBJ"))
    vbp = os.path.join(work, next(f for f in os.listdir(work) if f.lower().endswith(".vbp")))
    quiet_usercontrols(work)
    found = []
    if static:
        found += empty_flagged(work)
    if jobs > 1:
        found += parallel_census(work, vbp, jobs, limit)
        limit = 1               # (the build that must pass now, or one more error)
    for _ in range(limit):
        ok, log = vbmake.make(vbp)
        if ok:
            print("== builds once the procedures above are emptied")
            break
        errs = compile_errors(log)
        if not errs:
            print("== no compile error to act on:", log.strip().split("\n")[-1])
            break
        f, n, msg = errs[0]
        path = os.path.join(work, f)
        text = open(path, encoding="latin-1", newline="").read()
        lines = text.split("\r\n")
        k, line = source_line(path, n)
        where = procedure_at(lines, k - 1)
        if where is None:
            print("%s:%d: %s (outside a procedure; the census stops)\n    | %s" % (
                f, k, msg, line.strip()))
            break
        a, b, name = where
        if found.count((f, name, msg, line.strip())) >= 2:
            # emptying it doesn't help: the error isn't where the line number says
            return found, "%s:%d: %s (stays after emptying %s)" % (f, k, msg, name)
        found.append((f, name, msg, line.strip()))
        print("%-14s %-28s %-40s | %s" % (f, name, msg[:40], line.strip()[:70]), flush=True)
        if k - 1 == a:
            # in the signature itself: emptying the body cannot help, so the
            # parameter list goes (callers then fail in their turn)
            m = re.match(r"^(.*?\w+)\((.*)\)(.*)$", lines[a])
            if m is None or not m.group(2):
                print("== the signature cannot be cut back; the census stops")
                break
            lines[a] = m.group(1) + "()" + m.group(3)
        # the body goes; the signature stays, with a comment: VB leaves an
        # empty event procedure out of its line count (3 lines each), which
        # shifted every later error (12 lines after four empty handlers)
        lines[a + 1:b] = ["    ' (errcensus: emptied)"]
        open(path, "w", encoding="latin-1", newline="").write("\r\n".join(lines))
    counts = collections.Counter(m for _, _, m, _ in found)
    print("\n%d procedure(s) emptied" % len(found))
    for m, c in counts.most_common():
        print("%5d  %s" % (c, m))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
