#!/bin/sh
# Build vb6fix.dll and regtlb.exe (no C runtime).
cd "$(dirname "$0")" && i686-w64-mingw32-gcc -shared -O2 -Wall -nostdlib \
    -Wl,-e,_DllMain@12 -o vb6fix.dll vb6fix.c -lkernel32 -loleaut32 -luser32 -luuid
i686-w64-mingw32-gcc -O2 -Wall -nostdlib -Wl,-e,_start@0 -mwindows -o regtlb.exe regtlb.c \
    -lkernel32 -loleaut32 -lshell32
