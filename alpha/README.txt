vb6dec
======

vb6dec turns a program compiled by Visual Basic 6 back into a VB6 project: forms, modules,
classes and code that you can open in VB6, read, change and build again.

This is an alpha version, so expect problems. Only use it on programs you are allowed to
decompile.

Source code, newer versions and bug reports: https://github.com/teepean/vb6decompiler


What you need
-------------
- Windows 10 or 11, 64-bit.
- Visual Basic 6 installed. vb6dec reads some of its files (VB6.OLB and others), and you need
  VB6 to build the result. If VB6 is not installed, copy its files to a folder and use --vb6-dir.
- Nothing else: Python comes with vb6dec, in the python folder.


How to use it
-------------
Unzip everything to a folder of your own, then drag the program's EXE onto vb6dec.bat.

Or from a command window:

  vb6dec PROGRAM.exe                  decompile it
  vb6dec PROGRAM.exe --detect         only say what it is, and do nothing else
  vb6dec PROGRAM.exe -o C:\NewFolder  write the result to a new folder of your choice
  vb6dec PROGRAM.exe --vb6-dir C:\X   VB6's files are copied to C:\X
  vb6dec PROGRAM.exe --ocx-dir C:\Y   the program's OCX files are in C:\Y
  vb6dec --version                    which version this is

First it prints what it detected: Visual Basic 6 or something else, native code or p-code,
and the flags it could read from the program. Then it says which of VB6's files it found.

The result goes to output\PROGRAM beside vb6dec.bat, and nothing is overwritten. Open the .vbp
file there in VB6, then use File > Make. A small program takes seconds; a very large one can
take ten minutes or more, using several GB of memory.


What to expect
--------------
- Comments and the names of local variables are not in a compiled program, so you get names
  like v24, a1 and Proc_401A30. Names of forms, controls and public members do come back.
- The code is meant to do the same as the original, not to look like what its author wrote.
- Forms are most complete when the OCX files the program uses are registered on your machine.
  Otherwise the report lists the controls affected. Use --ocx-dir to say where the OCXs are.
- Only native code from VB6 is supported. Programs made with VB5, compiled to p-code, or packed
  are detected and refused. They are not decompiled wrongly.
- ActiveX DLLs and OCXs have been tried much less than EXE programs.
- Most testing so far was done under Wine on Linux, and less on real Windows.


If something goes wrong
-----------------------
The output folder has a file called vb6dec-report.txt. Please open an issue at
https://github.com/teepean/vb6decompiler/issues, attach the report, and say:
- which program it was, or where to get it
- what happened: the first error VB6 gives, or what the rebuilt program does differently

Look before you send: the report, and any decompiled code you share, can contain text from
the program you decompiled. Issues are public, so share only what you are allowed to share.


License
-------
vb6dec is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 3. It comes with no warranty. See LICENSE.txt for the full text.
Its source code is at https://github.com/teepean/vb6decompiler


Other software included
-----------------------
Python, capstone and pefile, each under its own license: see python\LICENSE.txt and the
*.dist-info folders in app\lib.
