#!/bin/bash
# rt.sh PROJECT.vbp WORKDIR [--nomap]: round trip one test (build, decompile, rebuild, compare out.txt)
cd "$(dirname "$0")/.."
rm -rf "$2"; timeout 900 .venv/bin/python tools/roundtrip.py ${3:-} "$1" "$2" 2>&1 | tail -5
