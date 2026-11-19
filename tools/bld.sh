#!/bin/bash
# bld.sh EXE_MAP_OR_NOTHING: decompile EXE into a new DIR, vbcheck, build, show the first errors.
# usage: bld.sh EXE DIR
EXE=$1; D=$2
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir "$D" || exit 1
timeout 500 "$HERE/../.venv/bin/python" "$HERE/../decompile.py" "$EXE" -o "$D" > "$D.log" 2>&1 || { echo "decompile failed"; tail -3 "$D.log" | cut -c1-160; exit 1; }
python3 "$HERE/vbcheck.py" "$D" 2>&1 | sed "s#$D/##" > "$D.vbcheck"; tail -1 "$D.vbcheck"
timeout 280 python3 "$HERE/vbmake.py" "$D"/*.vbp > "$D.make" 2>&1
tail -2 "$D.make" | cut -c1-220 | sed 's#Z:\\tmp\\.*\\##'
for f in "$D"/*.log; do case "$f" in *make.log) ;; *) echo "$(basename "$f")"; iconv -f cp1252 -t utf8 -c "$f" | cut -c1-170 | head -6;; esac; done
ls "$D"/*.exe 2>/dev/null | sed 's#.*/##'
