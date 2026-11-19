#!/usr/bin/env python3
"""Where does VB6 keep a UserControl's design-time properties?  Build the same small project with one
property different each time, and list the bytes that differ from the plain build.

usage: ucprops.py WORK [NAME=VALUE ...]
WORK must be a new folder.  Each NAME=VALUE goes in the .ctl's header in a build of its own; without any,
the usual ones.  Prints each difference as file offset, plain byte -> new byte, with the bytes around.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vbmake  # noqa: E402

VBP = """Type=Exe
Reference=*\\G{00020430-0000-0000-C000-000000000046}#2.0#0#..\\..\\WINDOWS\\SYSTEM\\STDOLE2.TLB#OLE Automation
UserControl=UC1.ctl
Form=Form1.frm
Startup="Form1"
Command32=""
Name="ucp"
ExeName32="ucp.exe"
CompilationType=0
OptimizationType=0
"""
FRM = """VERSION 5.00
Begin VB.Form Form1
   Caption         =   "Form1"
   ClientHeight    =   1000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   2000
   Begin ucp.UC1 UC11
      Height          =   500
      Left            =   100
      TabIndex        =   0
      Top             =   100
      Width           =   1000
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
"""
CTL = """VERSION 5.00
Begin VB.UserControl UC1
   ClientHeight    =   600
   ClientLeft      =   0
   ClientTop       =   0
   ClientWidth     =   1500
%s   ScaleHeight     =   600
   ScaleWidth      =   1500
End
Attribute VB_Name = "UC1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Public Sub Touch()
End Sub
"""
USUAL = ["DefaultCancel=-1", "ForwardFocus=-1", "CanGetFocus=0", "ControlContainer=-1", "Alignable=-1",
         "InvisibleAtRuntime=-1", "BackStyle=0", "ClipBehavior=0", "HitBehavior=0", "Windowless=-1",
         "EditAtDesignTime=-1", "AccessKeys=\"a\"", "AutoRedraw=-1", "MaskColor=255", "ToolboxBitmap=0"]


def build(d, prop):
    os.makedirs(d)
    line = "   %-15s =   %s\n" % tuple(prop.split("=", 1)) if prop else ""
    for name, text in (("ucp.vbp", VBP), ("Form1.frm", FRM), ("UC1.ctl", CTL % line)):
        open(os.path.join(d, name), "w", newline="").write(text.replace("\n", "\r\n"))
    ok, log = vbmake.make(os.path.join(d, "ucp.vbp"), timeout=120)
    if not ok:
        return None, log.strip().split("\n")[-1]
    return open(os.path.join(d, "ucp.exe"), "rb").read(), None


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    work = argv[0]
    os.makedirs(work)
    props = argv[1:] or USUAL
    plain, err = build(os.path.join(work, "plain"), "")
    if plain is None:
        print("plain:", err)
        return 1
    # (each build has new GUIDs and a new time: what two plain builds differ in says nothing)
    noise = set()
    for k in range(3):
        again, err = build(os.path.join(work, "plain%d" % k), "")
        noise |= {i for i in range(min(len(again), len(plain))) if again[i] != plain[i]}
    for n, prop in enumerate(props):
        new, err = build(os.path.join(work, "p%02d" % n), prop)
        if new is None:
            print("%-26s no build: %s" % (prop, err[:90]))
            continue
        same = len(new) == len(plain)
        diff = [i for i in range(min(len(new), len(plain))) if new[i] != plain[i] and i not in noise]
        print("%-26s size %d -> %d, %d bytes differ" % (prop, len(plain), len(new), len(diff)), flush=True)
        if same:
            for i in diff[:14]:
                print("    %06x: %02x -> %02x   %s" % (i, plain[i], new[i], new[max(0, i - 8):i + 9].hex()))
        else:
            print("    first at %06x" % (diff[0] if diff else -1))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
