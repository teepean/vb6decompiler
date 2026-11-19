# vb6dec - a decompiler for natively compiled Visual Basic 6 programs

vb6dec reads a VB6 EXE, DLL or OCX that was compiled to native code and writes back a VB6 project:
`.vbp`, `.frm` (designer and code), `.frx`, `.bas` and `.cls`. You can open the project in VB6, read it,
change it and build it again.

This is an early alpha. Only use it on programs you are allowed to decompile.

## What you need

- Python 3 (tested with 3.14) with `capstone` and `pefile`: `pip install -r requirements.txt`.
- Some of Visual Basic 6's own files. vb6dec reads them but does not include them: `VB6.OLB` and
  `MSVBVM60.DLL` give the names of the runtime's and the controls' members, `STDOLE2.TLB` and the
  program's OCX files the rest, and `WIN32API.TXT` the `Declare` statements. On Windows with VB6 installed,
  they are found through the registry. Otherwise, copy them to a folder and pass `--vb6-dir FOLDER`, or set
  `VB6DIR`. `vb6dec/msfiles.py` describes the search order.
- VB6 itself, to build the result.

Windows and Linux both work. Most of the testing so far has been under Wine on Linux.

## How to use it

    python run_vb6dec.py PROGRAM.exe                  # decompile it
    python run_vb6dec.py PROGRAM.exe --detect         # only say what it is
    python run_vb6dec.py PROGRAM.exe -o NEW_FOLDER    # write the project to a new folder
    python run_vb6dec.py PROGRAM.exe --vb6-dir DIR    # VB6's files are copied to DIR
    python run_vb6dec.py PROGRAM.exe --ocx-dir DIR    # the program's OCX files are in DIR

First it prints what it detected: VB6 or something else, native code or p-code, and the flags it could read
from the program. Then it lists which of VB6's files it found. It writes nothing over existing files, and
leaves a `vb6dec-report.txt` in the output folder. That report lists procedures it could not decompile,
controls whose OCX it did not find, and lines VB6 will probably reject.

`decompile.py` is the lower-level entry point:

    python decompile.py PROGRAM.exe -o OUTDIR       # write a project
    python decompile.py PROGRAM.exe [PROGRAM.map]   # annotated listing
    VB6DEC_JOBS=1 ...                               # no worker processes
    VB6DEC_DEBUG=1 ...                              # tracebacks

A `.map` file (VB6 writes one with `LinkSwitches=/MAP` in the `.vbp`) is used only to check the recovered
procedure names while testing. The decompiler does not need it.

A small program takes seconds. A very large one can take ten minutes or more and several GB of memory.

### The Windows package

Releases carry a zip that needs nothing but Windows and VB6: an embedded Python, the decompiler and a
`vb6dec.bat` to drop an EXE on (`alpha/README.txt` is its README). `tools/mkalpha.py` builds it:

    python tools/mkalpha.py NEW_FOLDER --embed python-3.14.x-embed-amd64.zip \
        --wheel capstone-...-win_amd64.whl --wheel pefile-...-py3-none-any.whl

The package is named after `VERSION` in `run_vb6dec.py`.

## What to expect

- A compiled program has no comments or local variable names, so you get names like `v24`, `a1` and
  `Proc_401A30`. Names of forms, controls and public members do come back.
- The code is meant to do the same as the original, not to look like what its author wrote.
- Forms are most complete when the program's OCX files are available. Their properties come from the
  controls themselves: `tools/ocxbag/ocxbag.exe` loads each control from the state the EXE keeps, on Windows
  or under Wine (`VB6WINE` names the Wine prefix). Without it, the output keeps only each control's size and
  version.
- Only native code from VB6 is supported. Programs made with VB5, compiled to p-code, or packed are detected
  and refused rather than decompiled wrongly.
- ActiveX DLLs and OCXs have been tried much less than EXE programs.

## How it works

| stage | file | what it does |
| --- | --- | --- |
| detection | `detect.py`, `pe.py` | VB6 or not, native or p-code, compiler flags |
| VB structures | `project.py` | VB header, project info, objects, controls, event tables, `Declare` table |
| type libraries | `typelib.py`, `msfiles.py` | VB6.OLB, MSVBVM60, STDOLE2 and the OCXs: control classes, events, property and method names |
| designer data | `frm.py`, `ocxprops.py`, `resources.py` | the form's property stream back into `Begin VB.Form ... End` (and `.frx`) |
| procedures | `disasm.py` | procedure discovery (events, thunks, call graph) and control-flow graphs |
| cleanup | `cfg.py` | removes the compiler's HRESULT, overflow, bounds and FDIV checks |
| lifting | `lift.py`, `insns.py`, `calls.py`, `machine.py` | symbolic execution into expressions and statements |
| runtime | `runtime.py` | what each `__vba*` / `rtc*` helper means in VB |
| Win32 | `apidb.py`, `apiarity.py` | `Declare` statements and `Type`s from WIN32API.TXT; argument counts from the DLLs |
| simplification | `simplify.py`, `udt.py`, `types.py`, `records.py` | temporaries, merged calls, record and variable types |
| structure | `structure.py` | If/ElseIf, Do/Loop, For/Next, Select Case, error handlers |
| output | `render.py`, `writer.py` | VB source text and the project files |

`docs/vb6-findings.md` holds the notes behind each stage: how VB6 lays out a native EXE, what its compiler and
runtime do, and the decompiler bugs each finding fixed.

`tools/vbcheck.py PROJECT_DIR` finds some things VB6 would reject in the output. run_vb6dec.py runs it.
`tools/README.md` lists every tool in `tools/` with a line on what it does.
`tools/ocxbag/build.sh` builds `ocxbag.exe` with MinGW.

Two tools compare decompilers. `tools/decstats.py DIR...` counts, per procedure, the machine-level code left in
decompiled output: runtime helpers left as calls, raw memory, members known only by vtable slot, CPU registers.
`tools/rtother.py --other 'CMD {exe} {out}' WORKDIR` runs test projects through vb6dec and another decompiler,
then rebuilds each output with VB6 under Wine and compares its behaviour with the original's.

The round-trip harness that rtother.py uses can also be run directly:

    python tools/vbmake.py PROJECT.vbp              # build with VB6 under Wine, print VB6's log
    python tools/roundtrip.py [--nomap] PROJECT.vbp WORKDIR   # build, decompile, rebuild, compare

`roundtrip.py` builds the project with VB6, decompiles the EXE, builds the output again, and checks that both
EXEs carry the same project properties and that both builds write the same `out.txt`. An `rt.txt` beside the
`.vbp` can name other files to compare, arguments and a timeout (see the script). The original's build and runs
are cached in `VB6RTCACHE` (default `/tmp/vb6dec-rtcache`).

They need Wine, `xdotool`, and ImageMagick's `import` command (for screenshots of message boxes). They also
need a Wine prefix with VB6 installed, set by `VB6WINE`. VB6 and its licence keys have to come from your own
installation. `VB6DIR=VB98SP6` builds with Service Pack 6 if the prefix has it.

Projects with ActiveX controls need VB6.EXE patched to load `tools/vb6fix/vb6fix.dll`, which works around five
bugs in Wine's type library and picture code (described in `vb6fix.c`):

    sh tools/vb6fix/build.sh                       # optional: rebuild vb6fix.dll and regtlb.exe with MinGW
    python tools/patchvb6.py "$VB6WINE/drive_c/Program Files/Microsoft Visual Studio/VB98/VB6.EXE"

`patchvb6.py` keeps the original as `VB6.EXE.orig`, adds an import of vb6fix.dll in a new section without
changing any VB6 code, and copies the DLL next to VB6.EXE. `--check` only reports whether a VB6.EXE is patched.
`regtlb.exe FILE...` registers type libraries under Wine, which has no tool for that. MSCOMCTL.OCX, for
example, needs MSDATSRC.TLB registered.

## Testing

`tests/` (68 projects) and `tests_wip/coverage/` (52) are small VB6 projects, each written to exercise one
construct. Each is compiled with VB6, decompiled, compiled again, and both builds must give the same output
(`out.txt`, or the files the test's `rt.txt` names). Between them they cover every runtime function a program
commonly calls, plus Implements, WithEvents, GoSub, AddressOf, For Each, Currency, Decimal, line numbers and
Erl. All of them pass with and without a map file, except `t36lset` in coverage, an open bug.

    python tools/rtsuite.py -j 4 WORKDIR                               # tests/, each with and without the map
    python tools/rtsuite.py -j 4 --tests tests_wip/coverage WORKDIR    # the coverage tests
    python tools/rtsuite.py WORKDIR t01m t03c                          # some of them

This needs VB6 under Wine (see the round-trip harness above). The source files are ANSI with CRLF line ends,
and `.gitattributes` keeps Git from converting them.

Larger open-source programs, decompiled and rebuilt:

- DarwinBots 2.48.32, a 1.7 MB simulator: the rebuilt program writes the original's save files byte for byte
  over 750 000 cycles, apart from the stored run time.
- ChessBrainVB, a 15.7k-line chess engine: the same output at search depth 13.
- VBDoom, a 29k-line Doom port compiled without optimisation: the same per-tic state and the same
  screenshots over a demo.
- VbQRCodegen, ZipArchive, VBA-JSON and VBA-Dictionary: the same program output.

These programs belong to their authors and are not in this repository. Get them from their own repositories to
test with them.

## License

vb6dec is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License version 3, as published by the Free Software Foundation. It comes with no warranty. See
[LICENSE](LICENSE) for the full text.
