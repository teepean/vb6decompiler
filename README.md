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

`tools/vbcheck.py PROJECT_DIR` finds some things VB6 would reject in the output. run_vb6dec.py runs it.
`tools/ocxbag/build.sh` builds `ocxbag.exe` with MinGW.

## Testing

The test projects are not in this repository. Each one is a small VB6 project written to exercise one
construct. It is compiled with VB6, decompiled, compiled again, and both builds must give the same output.
Over 120 such projects pass, with and without a map file. Between them they cover every runtime function a
program commonly calls, plus Implements, WithEvents, GoSub, AddressOf, For Each, Currency, Decimal, line
numbers and Erl.

Larger open-source programs, decompiled and rebuilt:

- DarwinBots 2.48.32, a 1.7 MB simulator: the rebuilt program writes the original's save files byte for byte
  over 750 000 cycles, apart from the stored run time.
- ChessBrainVB, a 15.7k-line chess engine: the same output at search depth 13.
- VBDoom, a 29k-line Doom port compiled without optimisation: the same per-tic state and the same
  screenshots over a demo.
- VbQRCodegen, ZipArchive, VBA-JSON and VBA-Dictionary: the same program output.
