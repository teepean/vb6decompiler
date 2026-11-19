#!/usr/bin/env python3
"""Compile a VB6 project with the real VB6 compiler, under Wine.

usage: vbmake.py PROJECT.vbp [...]

Prints VB6's build log; the exit status is 0 when every build succeeded.

The Wine prefix (VB6WINE, default re/wine-vb6 next to this repository) holds
VB98 from the VM, DAO350.DLL and the HKCR\\Licenses keys VB6 Setup wrote in the
VM (without them VB6 behaves as the Working Model Edition: "No make
available"), plus the VBA / VB runtime / VB6.OLB type library registrations.

Forms build too (2026-10-28: all the VM-built form tests do), and so do
ActiveX controls once VB6.EXE imports tools/vb6fix/vb6fix.dll, which works
around five bugs in Wine's type library and picture code (tools/patchvb6.py).  The
crash of 2026-10-24 (a NULL interface released at VB6.EXE+0x581F1, before
LINK) shows as "VB6 wrote no log".  VB6DIR=VB98SP6 builds with Service
Pack 6.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = os.environ.get("VB6WINE", os.path.normpath(os.path.join(HERE, "..", "..", "wine-vb6")))
# VB98: VB6 as first released (6.0.81.76, C2 12.0.8041), which built every
# cached original; VB98SP6: Service Pack 6 (6.0.97.82, C2 12.0.8783), copied
# from the VM on 2026-10-28.  Both VB6.EXEs import tools/vb6fix (patchvb6.py).
VB6 = r"C:\Program Files\Microsoft Visual Studio\%s\VB6.EXE" % os.environ.get("VB6DIR", "VB98")


def has_forms(vbp):
    text = open(vbp, encoding="latin-1").read().replace("\r", "")
    return any(ln.startswith(("Form=", "UserControl=", "UserDocument=", "PropertyPage="))
               for ln in text.split("\n"))


LAST_BOX = []          # text of the message box the last run() stopped at, if any


def run(exe, timeout=60, args=()):
    """Run a built program under Wine (cwd = its folder); True when it ended.
    A message box (a VB runtime error, or the program's own) ends the run at
    once: nothing would close it.  Its text is left in LAST_BOX."""
    import re
    import threading
    env = dict(os.environ, WINEPREFIX=PREFIX, WINEDEBUG="-all,trace+msgbox")
    d, name = os.path.split(os.path.abspath(exe))
    del LAST_BOX[:]
    proc = subprocess.Popen(["wine", name] + list(args), cwd=d, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    box = threading.Event()

    def watch():
        for ln in proc.stderr:
            m = re.search(rb'MSGBOX_OnInit L"(.*)"', ln)
            if m:
                LAST_BOX.append(m.group(1).decode("latin-1", "replace"))
                box.set()
                return
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    import time
    t0 = time.time()
    while proc.poll() is None and not box.is_set() and time.time() - t0 < timeout:
        time.sleep(0.1)
    if proc.poll() is None:
        kill_windows_process(os.path.join(d, name))
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return False
    return True


def kill_windows_process(path):
    """End the Windows process running path (not only the wine launcher), and
    nothing else: the same program may run from another folder at the same
    time (rtsuite runs a test's map and no-map builds side by side)."""
    import signal
    want = ("Z:" + os.path.abspath(path).replace("/", "\\")).lower()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0")[0]
        except OSError:
            continue
        if cmd.decode("latin-1").lower() == want:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass


def vb6_pids(d):
    """The VB6 building in folder d: others may run side by side (rtsuite, errcensus)."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if open("/proc/%s/comm" % pid).read().strip().upper() == "VB6.EXE" and \
                    os.readlink("/proc/%s/cwd" % pid) == d:
                out.append(pid)
        except OSError:
            pass
    return out


def end_vb6(d):
    import signal
    for pid in vb6_pids(d):
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def vb6_dialog(d):
    """(display, window) of a visible window called just "Microsoft Visual Basic": the box of a
    /make that will not go on by itself."""
    for pid in vb6_pids(d):
        try:
            items = open("/proc/%s/environ" % pid, "rb").read().split(b"\0")
            display = next((i[8:].decode() for i in items if i.startswith(b"DISPLAY=")), None)
            if not display:
                continue
            env = dict(os.environ, DISPLAY=display)
            wins = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", pid], env=env,
                                  capture_output=True, text=True, timeout=20).stdout.split()
            for w in wins:
                t = subprocess.run(["xdotool", "getwindowname", w], env=env, capture_output=True,
                                   text=True, timeout=20).stdout.strip()
                g = subprocess.run(["xdotool", "getwindowgeometry", "--shell", w], env=env,
                                   capture_output=True, text=True, timeout=20).stdout
                m = re.search(r"WIDTH=(\d+)", g)
                # (the main window has the same title when it opens, and is big)
                if t == "Microsoft Visual Basic" and m and int(m.group(1)) < 800:
                    return display, w
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def make(vbp, timeout=300):
    """Build one project; returns (succeeded, log text)."""
    vbp = os.path.abspath(vbp)
    d, name = os.path.split(vbp)
    log = os.path.join(d, "make.log")
    if os.path.exists(log):
        os.remove(log)
    env = dict(os.environ, WINEPREFIX=PREFIX, WINEDEBUG="-all")
    import time
    proc = subprocess.Popen(["wine", VB6, "/make", name, "/out", "make.log"], cwd=d, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = last = time.time()
    up = 0
    while proc.poll() is None:
        time.sleep(0.25)
        now = time.time()
        if now - t0 > timeout:
            end_vb6(d)
            proc.kill()
            return False, "vbmake: VB6 did not finish in %d s\n" % timeout
        if now - last >= 10:
            last = now
            box = vb6_dialog(d)
            up = up + 1 if box else 0
            if up >= 2:
                # nothing would close it: a run-time error in a UserControl's code, which VB6 runs
                # when it loads a form, sits on the user's desktop for the whole timeout
                png = os.path.join(d, "make_box.png")
                try:
                    subprocess.run(["import", "-window", box[1], png], env=dict(env, DISPLAY=box[0]),
                                   timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    png = "no picture"
                end_vb6(d)
                proc.kill()
                return False, "vbmake: VB6 stopped on a dialog (%s)\n" % png
    text = open(log, encoding="latin-1").read().replace("\r", "") if os.path.exists(log) \
        else "vbmake: VB6 wrote no log (it crashed%s)\n" % (
            "; forms with controls crash VB6 under Wine" if has_forms(vbp) else "")
    return "succeeded" in text, text


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    ok = True
    for vbp in argv:
        good, text = make(vbp)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        ok = ok and good
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
