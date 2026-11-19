#!/bin/sh
# Build ocxbag.exe (no C runtime start-up; memcpy from msvcrt).
cd "$(dirname "$0")" && i686-w64-mingw32-gcc -O2 -Wall -nostdlib -Wl,-e,_start@0 \
    -o ocxbag.exe ocxbag.c -lkernel32 -luser32 -lole32 -loleaut32 -lshell32 -luuid -lmsvcrt
