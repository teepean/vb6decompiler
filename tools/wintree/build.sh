#!/bin/sh
# Build wintree.exe (no C runtime).
cd "$(dirname "$0")" && i686-w64-mingw32-gcc -O2 -Wall -nostdlib -Wl,-e,_start@0 \
    -o wintree.exe wintree.c -lkernel32 -luser32 -lshell32
i686-w64-mingw32-gcc -O2 -Wall -nostartfiles -Wl,-e,_start@0 -mwindows -o winpoke.exe winpoke.c \
    -lshell32 -lkernel32 -luser32
i686-w64-mingw32-gcc -O2 -Wall -shared -o winpokedll.dll winpokedll.c -lkernel32 -luser32
