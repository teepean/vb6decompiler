# Tools

Helpers for developing vb6dec. Each script's docstring (or header comment) has its full usage; this is the
one-line index. Most debugging tools take an EXE and a procedure address and run part of the pipeline.
Tools that build or run programs need VB6 under Wine: see the main README.

## Running and checking the decompiler

| tool | what it does |
| --- | --- |
| `vbcheck.py` | Project-level checks for compile errors VB6 would report in decompiled source: ByRef argument type mismatches, undeclared constants in Type bounds, assignments to whole array fields. |
| `lint.py` | Check decompiled VB source for things VB6 would reject. |
| `bld.sh` | decompile EXE into a new DIR, vbcheck, build, show the first errors. usage: bld.sh EXE DIR |
| `markers.sh` | count the constructs a decompiled project still has unresolved |
| `decstats.py` | How much of a decompiled VB project is still machine level, by procedure: for comparing decompilers' output. |
| `needfiles.py` | What each of Microsoft's files is worth to the output: decompile a program with each taken away in turn, then with all of them away but those given, and count the lines of output that change. |
| `vbprobe.py` | Is an EXE a VB6 native-code program worth decompiling? Reads the file, runs nothing. |
| `optprobe.py` | What a VB6 EXE's code says about the compile options it was made with. |
| `regstats.py` | How a VB6 EXE's code uses esi, edi and ebx, and which runtime functions it calls, to tell its optimisation. |
| `rtcmissing.py` | MSVBVM60's exports that the decompiler has no entry for, and whether a compiled program can use them: names only VBA's p-code engine calls (Ex*, Cdbl..., numbers) can't be told from here, so the… |
| `derive_sigs.py` | Derive calling-convention facts for every MSVBVM60 export by looking at its code: stack bytes popped by 'ret N', registers read before written, FPU result. Output: data/runtime_auto.json |
| `m60sym.py` | COFF symbols of msvbvm60.dbg mapped through OMAP_FROM_SRC to the final DLL; writes rva name(undecorated) |
| `checkdecode.py` | disasm.decode (plain Insn objects, encodings in a trie) against capstone, for every instruction Model(EXE) decodes: all that callers can read of it. |
| `targetrun.py` | Decompile and build a list of programs one by one: what fails, and how much of it (for programs with no source, whose output stays private: the results only improve the decompiler). |

## Building and round-tripping with VB6 under Wine

| tool | what it does |
| --- | --- |
| `vbmake.py` | Compile a VB6 project with the real VB6 compiler, under Wine. |
| `roundtrip.py` | Source -> EXE -> decompiled source -> EXE, without leaving Linux. |
| `rtsuite.py` | Round-trip every test project in tests/ (source -> EXE -> decompiled source -> EXE, with VB6 under Wine), with and without the linker map. |
| `rt.sh` | round trip one test (build, decompile, rebuild, compare out.txt) |
| `rtother.py` | Round-trip test projects through our decompiler and another one, and tabulate each step for both. |
| `patchvb6.py` | Let VB6.EXE build projects with OCX controls under Wine: give it an import of vb6fix.dll (tools/vb6fix/), which corrects some of Wine's type library and picture functions in memory when it loads.… |
| `builderr.py` | Build a project once and print its first compile error with the source line it means. |
| `buildrun.py` | Build a VB6 project folder in place under Wine and run the program: for diagnostic copies edited by hand (an extra Print # in both the original and the decompiled source, to find where two runs… |
| `testbuild.sh` | The build half of guitest.sh: decompile, census and build a logging test copy, in a new directory WORK/N, without starting either program. usage: testbuild.sh EXE DATA_DIR WORK N HANDLER_SUB… |
| `errcensus.py` | Every procedure of a decompiled project that VB6 will not compile, not just the first: build a copy, note the first compile error, empty the body of the procedure it is in, build again (VB stops… |
| `errlines.py` | every compile error of a decompiled project, not just the first. Works on a copy in the new directory WORK: comments out the offending line and rebuilds, up to NN times. A line that opens or… |
| `errline.py` | Find the statement of a built source that raises an uncaught run time error: number the lines of one procedure, give it a handler that writes Err and Erl to a file, build and run it. |
| `errlog.py` | A test copy of a decompiled project that logs its run-time errors. |
| `winetestfix.py` | A test build of a census copy with Wine's false errors worked round. |
| `firsterr.py` | Where a decompiled program fails at run time: copy its folder, trace every procedure entry (instr.py), build, run with the given arguments, and print the message box the run stopped at and the… |
| `instr.py` | append Tr "<proc>" as first statement of each procedure (CRLF) + mTrace.bas. Environment: VB6TRACE_ROTATE=N keeps only the last N to 2N lines, in trace0.txt and trace1.txt; VB6TRACE_SKIP=REGEX… |
| `tracebuild.py` | A traced build of a decompiled program, for running by hand or through runshot.py: every procedure entered goes to trace.txt beside the program, and every statement of the procedures named. |
| `tracecmp.py` | The first procedure call where two runs part: two trace.txt files of tools/firsterr.py (an original build's and a decompiled build's), compared by procedure name, with what came before. |
| `tracecover.py` | Which procedures of a decompiled source did a traced run (tools/firsterr.py's trace.txt) never enter? What a comparison of two runs says nothing about. |
| `dumpdiag.py` | Insert a diagnostic Print # line after an anchor line in a VB source file (CRLF kept): for hand-made copies of an original and a decompiled program, to compare their state where two runs diverge… |
| `runcmp.py` | Run two builds of a program with the same arguments and compare the files they write. |
| `fixpoint.py` | Decompile, build, decompile again: is the second source the first? What differs once generated names are taken out shows what a round through the compiler changed: a statement lost shows here even… |
| `callcmp.py` | Do two builds make the same calls? Each procedure's calls, in order: the runtime's and the API's by name, the program's own procedures by name, methods by vtable offset. A statement the decompiler… |
| `execalls.py` | The same calls in the same procedures? An original EXE against its decompiled rebuild, no maps needed. |
| `execmp.py` | Do two builds of one source hold the same code? Procedure by procedure, addresses aside. |
| `fpcmp.py` | Do two builds compute with the FPU in the same way? Each procedure's floating point instructions, in order, by mnemonic and operand width: where they differ, the rebuilt source has another shape… |
| `bisectflag.py` | Which change made a difference? Decompile a program with one piece of the decompiler turned off at a time, each in its own copy of the sources, and report whether a text comes back in the output. |
| `cmpdec.py` | Decompile the same EXEs with two copies of the decompiler and compare: time taken and the files written. For checking that a change meant to be behaviour-neutral (a speed-up) leaves the output alone. |
| `proclines.py` | Procedures whose line count changed between two decompiled projects (a pass that loses code shows). |
| `optmatrix.py` | One small program compiled with each of VB6's compile options, to see what each leaves in the EXE: is vb6dec/detect.py right, and which options leave no mark. Shows VB6 windows while it builds. |

## Forms, controls and GUI programs

| tool | what it does |
| --- | --- |
| `dumpfrm.py` | Print the designer text (Begin VB.Form ... End) decoded for each form of an EXE, or for one form. |
| `cmpfrm.py` | Compare decoded form designer text with an original .frm (properties per control, order-insensitive, comments ignored). |
| `frmcmp.py` | Compare the designer part of decompiled forms with the real source, property by property. |
| `frmprobe.py` | What VB6 writes into an EXE for a form: build a .frm under Wine and trace its designer data record by record, property by property (opcode, bytes, what the decoder made of it). The oracle for… |
| `frxicon.py` | A .frx holding one form's Icon, for test projects: a 32x32 16-colour icon of two colours in a pattern of its own. |
| `ocxcheck.py` | Do the .frm properties recovered for an EXE's ActiveX controls (vb6dec/ocxprops.py, tools/ocxbag) give the controls back? Each control goes alone on a form with those properties; VB6 builds it… |
| `ucprops.py` | Where does VB6 keep a UserControl's design-time properties? Build the same small project with one property different each time, and list the bytes that differ from the plain build. |
| `mdlcheck.py` | Do the VB header's model object flags (vb6dec/detect.py MODEL_OBJECTS) say what a project's forms hold? Compares them with the Begin VB.x lines of the source a suite run left. |
| `proptab.py` | Dump pointer tables of a DLL (MSVBVM60's property and method tables, say) by public symbol name: each entry, the symbol it points to and its first bytes. |
| `guicmp.py` | Compare what two builds of a GUI program show after starting: run each under Wine (the VB6 prefix) in its own folder, wait, dump its windows with tools/wintree (class, position, size, state, text… |
| `guidiff.py` | Run two builds of a GUI program through the same steps from the same state, and compare everything they leave: the windows, the whole registry of the Wine prefix, the files in the program's folder. |
| `menucrawl.py` | Open every menu item in two builds of a GUI program, under Wine, each from the same data, and compare what comes up: windows, error log, data files. |
| `runshot.py` | Run a program under our Wine prefix for a while, then screenshot its windows and end it. |
| `shotcmp.py` | Compare the screenshots of two tools/runshot.py runs, window by window. |
| `vb6boxes.py` | Screenshots of the windows a stuck VB6 (or a program under Wine) shows on the desktop. |
| `w9xreg.py` | Minimal reader for Windows 9x registry files (CREG: SYSTEM.DAT/USER.DAT). |
| `wineprof.py` | Time the processes a Wine command starts (VB6.EXE, C2.EXE, LINK.EXE, the Wine services...): when each started, how long it lived, how much CPU it used. |

## Inside the pipeline (debugging one procedure or pass)

| tool | what it does |
| --- | --- |
| `callsites.py` | Print the instructions leading up to every direct call of TARGET inside a procedure. |
| `cfgdump.py` | The control flow nodes of a procedure, between two addresses, after the first pass (what decompile.py's second lift sees). |
| `dbg_ir.py` | Print the IR of one procedure after lift and after each early simplify pass, filtered to statements touching given local slots. |
| `dbgapicall.py` | What render sees for calls to one API: its raw name and the form of its arguments. |
| `dbgregion.py` | the blocks after simplify.run, with their successors. |
| `dislin.py` | Linear disassembly of an address range of a PE, read straight from the file. |
| `emptyhandlers.py` | On Error GoTo targets whose label has no statements after it. |
| `espdump.py` | The stack height the lift believes in, block by block: for a procedure with '<argN> in its output (the height went off somewhere and arguments are read from the wrong place). |
| `findstmt.py` | Statements of the first pass that contain a text, with their procedure and address. |
| `fp4trace.py` | Which simplify pass loses the "read or written as a Single" mark of a field (udt.fp_fields). |
| `globalwhy.py` | Why a module variable counts as a record of some size (decompile.global_struct_hints). |
| `iowidth.py` | Get and Put of a field with the wrong width: the compiled __vbaGet3/__vbaPut3 takes the variable's byte count, and the file stays in step only if the decompiled Type agrees. |
| `lateidwho.py` | Every late-bound call by DISPID whose member lateid_member cannot name: the procedure, the DISPID, the receiver and what is known of its interface. |
| `looptails.py` | The loops of a decompiled tree that aren't For loops, with what is before them and the last lines of their bodies: what stops a loop being recognised, at a glance. |
| `modtable.py` | The variable tables of each standard module: public and static block, entry by entry. |
| `ownercheck.py` | How well owners.guess places module procedures: lift a program that has a map, forget which module each module procedure is in, guess, and compare with the map. |
| `passtrace.py` | Follow statements through simplify.run, with the whole first pass done first (proc_returns, struct hints, parameter lists: what dbg_ir.py lacks). |
| `pinwhy.py` | Why is a frame slot a variable in a procedure that may resume (simplify.pin_statement_variables)? |
| `privscan.py` | Names from private programs in files that are to be handed out. A word counts as private when it is in the decompiled source of a program in --private and in none of --public, nor in the type… |
| `procof.py` | The procedure that uses a string literal (its error handler's name, say). |
| `proctime.py` | Where the decompiler spends its time: lift and simplify every procedure of an EXE once, then list the slowest procedures and the simplify passes by time. |
| `procva.py` | The address of a procedure by the name the decompiler gives it. |
| `pysample.py` | Statistical profile of a Python program: where its time goes, without cProfile's cost per call (which inflates small functions and generators several times over: ir.Expr.walk looked like 18% of a… |
| `recdump.py` | Dump the record descriptors of an EXE (records.py): header and raw bytes after each entry, to see formats records.read doesn't decode yet. |
| `recglobals.py` | Module variables declared with a record Type in decompiled source, and how the code uses them: as numbers (g = 0, For i = 1 To g, ReDim a(g), g + 1, F(g)...) or through fields (g.f8). A record… |
| `slotuse.py` | Every instruction of a procedure that names a frame slot, whatever vbdis.py stops at: the bytes are read straight from the file, up to the size given. |
| `tabledump.py` | Hex dump of a variable table or record descriptor from an executable. |
| `typecmp.py` | Compare a program's Types in its source with the decompiled ones, field by field. |
| `typewho.py` | Which procedures give a variable a type, and by which rule? Runs the first pass and the parameter lists as decompile.py does, then builds every procedure's Types (what writer.global_types reads)… |
| `udtgroup.py` | Why does a variable get a struct type? Runs the first pass with udt.group_structs instrumented and prints, each time it runs, the group the variable lands in: its members, each member's field… |
| `udtwho.py` | Who gave a variable its record type? Runs the first pass with every Lifter's udt map replaced by one that records, for the watched keys, each assignment: the procedure, the type, and the… |
| `unassigned.py` | Locals of a decompiled source that are read but never given a value: each is a store the decompiler lost, or a temporary it could not follow; they compile, and quietly read 0. |
| `vbdis.py` | Disassemble one procedure of a VB6 EXE, naming imported runtime calls. |
| `whoreturns.py` | Who made a procedure a Function: every place in the first pass that sets proc_returns for it. |
| `whyev.py` | Which line of types.Types gives a variable its evidence for a type. |

## Programs built with MinGW

`ocxbag/` loads ActiveX controls from the state an EXE keeps (used for .frm properties). `vb6fix/` is the
VB6.EXE patch for OCX projects under Wine, with `regtlb.exe`. `wintree/` dumps a program's windows and
`winpoke` drives them (typing, clicking, menus, file dialogs) for the GUI comparisons. Each folder has its
`build.sh`.
