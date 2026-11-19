#!/usr/bin/env python3
"""vb6dec: turn a program compiled to native code by Visual Basic 6 back into a VB6 project.

usage: vb6dec PROGRAM.exe [--detect] [-o NEW_FOLDER] [--vb6-dir FOLDER] [--ocx-dir FOLDER]

  --detect          only say what the program is: VB6 or something else, native or p-code, its flags
  -o NEW_FOLDER     where to write the project; default output\\PROGRAM beside vb6dec.bat
  --vb6-dir FOLDER  a folder with copies of VB6.OLB and the like, if VB6 is not installed here
  --ocx-dir FOLDER  a folder with the OCX files the program uses, if they are not registered here
  --version         which version this is
"""
import os
import platform
import sys
import time
import traceback

VERSION = "alpha 2"
HERE = os.path.dirname(os.path.abspath(__file__))
for d in (HERE, os.path.join(HERE, "lib")):
    if d not in sys.path:
        sys.path.insert(0, d)
REPORT = "vb6dec-report.txt"


class Tee:
    """Keeps what goes through, for the report."""

    def __init__(self, out):
        self.out, self.lines = out, []

    def write(self, s):
        self.lines.append(s)
        return self.out.write(s)

    def flush(self):
        self.out.flush()


def parse(argv):
    a = {"exe": None, "out": None, "vb6": None, "ocx": None, "detect": False}
    i = 0
    while i < len(argv):
        x = argv[i]
        if x in ("-h", "--help", "/?"):
            print(__doc__.strip())
            sys.exit(0)
        if x == "--version":
            print("vb6dec " + VERSION)
            sys.exit(0)
        if x == "--detect":
            a["detect"] = True
            i += 1
            continue
        if x in ("-o", "--vb6-dir", "--ocx-dir"):
            if i + 1 == len(argv):
                sys.exit("%s needs a folder after it" % x)
            a[{"-o": "out", "--vb6-dir": "vb6", "--ocx-dir": "ocx"}[x]] = argv[i + 1]
            i += 2
            continue
        if x.startswith("-") or a["exe"]:
            sys.exit("I don't understand: %s\n\n%s" % (x, __doc__.strip()))
        a["exe"] = x
        i += 1
    return a


def new_folder(exe, given):
    """A folder nothing is in, so nothing is overwritten."""
    if given:
        if os.path.isdir(given) and os.listdir(given):
            sys.exit("%s already has files in it.  Please name a new folder: nothing here gets overwritten." % given)
        return given
    name = os.path.splitext(os.path.basename(exe))[0]
    for top in (os.path.join(os.path.dirname(HERE), "output"), os.path.join(os.path.expanduser("~"), "vb6dec-output")):
        try:
            os.makedirs(top, exist_ok=True)
            probe = os.path.join(top, ".vb6dec-write-test")
            open(probe, "w").close()
            os.remove(probe)
        except OSError:
            continue
        for n in range(1, 1000):
            p = os.path.join(top, name if n == 1 else "%s_%d" % (name, n))
            if not os.path.exists(p):
                return p
    sys.exit("I found no folder to write to.  Please name one with -o.")


def check_files():
    """What the decompiler reads from VB6 and Windows.  False if it can't go on."""
    from vb6dec import msfiles
    ok = True
    for name, path, without in msfiles.report():
        if path and os.path.exists(path):
            print("  %-13s %s" % (name, path))
        elif without is None:
            print("  %-13s NOT FOUND" % name)
            ok = False
        else:
            print("  %-13s not found: %s" % (name, without))
    if not ok:
        print("\nThese come with Visual Basic 6.  Install it here, or copy its files to a folder and say\n"
              "where: vb6dec PROGRAM.exe --vb6-dir FOLDER\n"
              "Without them forms come out empty and the code wrong, so I stop here.")
    return ok


def unknown_controls(m):
    """Controls whose OCX was not found: (form, control)."""
    out = []
    for o in m.prj.objects:
        for c in o.controls:
            if not c.guid:
                continue
            try:
                if m.control_class(c) == (None, None, None):
                    out.append((o.name, c.name))
            except Exception:
                pass
    return out


def project_file(out):
    for fn in sorted(os.listdir(out)):
        if fn.lower().endswith(".vbp"):
            return os.path.join(out, fn)
    return out


def write_report(where, exe, seconds, text):
    path = os.path.join(where, REPORT)
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write("vb6dec %s\nPython %s, %s\nProgram: %s\nSeconds: %d\n\n%s\n" % (
                VERSION, platform.python_version(), platform.platform(), os.path.basename(exe), seconds, text))
        return path
    except OSError:
        return None


def main(argv):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(errors="replace")
    a = parse(argv)
    print("vb6dec %s: please see README.txt for what it can do so far.\n" % VERSION)
    if not a["exe"]:
        print(__doc__.strip() + "\n")
        try:
            a["exe"] = input("Program to decompile (or drop it on vb6dec.bat): ").strip().strip('"')
        except EOFError:
            return 2
        if not a["exe"]:
            return 2
    exe = a["exe"]
    if not os.path.isfile(exe):
        print("There is no file %s" % exe)
        return 2
    from vb6dec import detect
    info = detect.detect(exe)
    print("Detected:")
    print("\n".join(detect.lines(info)))
    print()
    if a["detect"]:
        return 0 if info.get("supported") else 3
    if not info.get("supported") and not os.environ.get("VB6DEC_TRY"):
        return 3
    if a["vb6"]:
        os.environ["VB6DIR"] = a["vb6"]
    if a["ocx"]:
        os.environ["VBOCXDIR"] = a["ocx"]
    print("Reading from VB6 and Windows:")
    if not check_files():
        return 3
    out = new_folder(exe, a["out"])
    print("\nProgram: %s\nOutput:  %s\n\nThis takes from seconds to many minutes, depending on the size of the program.\n"
          % (exe, out))
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    err = Tee(sys.stderr)
    sys.stderr = err
    from vb6dec.model import Unsupported
    import decompile

    def phase(name):
        print("  %5d s  %s" % (time.time() - t0, name), flush=True)
    decompile.phase = phase
    try:
        code = decompile.main([exe, "-o", out])
    except Unsupported as e:
        sys.stderr = err.out
        print("\n%s" % e)
        return 3
    except Exception as e:
        sys.stderr = err.out
        # (pefile's and the VB header's own words for a file that is no VB6 program)
        if type(e).__name__ == "PEFormatError" or str(e).startswith("not a VB5/VB6"):
            print("\nThis does not look like a Visual Basic 6 program.")
            return 3
        report = write_report(out, exe, time.time() - t0, traceback.format_exc())
        print("\nSomething went wrong inside the decompiler (%s: %s)." % (type(e).__name__, e))
        print("Please send this file to whoever gave you vb6dec: %s" % report)
        return 4
    sys.stderr = err.out
    failed = [ln.strip() for ln in "".join(err.lines).split("\n") if ln.startswith("decompile: ")]
    missing = unknown_controls(decompile.LAST_MODEL) if decompile.LAST_MODEL else []
    problems = []
    try:
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import vbcheck
        problems = list(vbcheck.Project(out).check())
    except Exception as e:
        problems = [("", 0, "vbcheck itself failed: %s" % e)]
    lines = []
    if failed:
        lines.append("%d procedure(s) could not be decompiled and are missing:" % len(failed))
        lines += ["  " + x for x in failed[:40]]
    if missing:
        lines.append("%d control(s) have no OCX found, so their forms are incomplete (use --ocx-dir):" % len(missing))
        lines += ["  %s.%s" % x for x in missing[:40]]
    if problems:
        lines.append("%d thing(s) VB6 will probably reject:" % len(problems))
        lines += ["  %s:%d: %s" % p for p in problems[:40]]
    report = write_report(out, exe, time.time() - t0, "\n".join(lines) or "Nothing to report.")
    print("\nDone in %d seconds." % (time.time() - t0))
    for ln in lines[:12]:
        print(ln)
    print("\nOpen this in Visual Basic 6, then File > Make:\n  %s" % project_file(out))
    if report:
        print("The report on this run is in %s" % report)
    return code


def started():
    """main, whatever becomes of it, and the window kept open if Explorer opened it for this: a double click or a
    drop on vb6dec.bat runs cmd /c, and the window would close before anyone could read it."""
    try:
        code = main(sys.argv[1:])
    except SystemExit as e:
        code = e.code
        if isinstance(code, str):
            print(code)
            code = 2
    except Exception:
        traceback.print_exc(file=sys.stdout)
        print("\nSomething went wrong (vb6dec %s).  Please send this text to whoever gave you vb6dec." % VERSION)
        code = 4
    if " /c " in os.environ.get("VB6DEC_STARTED", "").lower() and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(started())
