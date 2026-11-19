#!/bin/sh
# The build half of guitest.sh: decompile, census and build a logging test copy,
# in a new directory WORK/N, without starting either program.
# usage: testbuild.sh EXE DATA_DIR WORK N HANDLER_SUB PROJECT.vbp FILE_TO_NUMBER...
# Environment: VB6DIR (VB98SP6).  The ready folder is WORK/N/b.
set -u
EXE=$1; DATA=$2; WORK=$3; N=$4; HANDLER=$5; VBP=$6; shift 6
HERE=$(cd "$(dirname "$0")" && pwd)
R=$WORK/$N
mkdir "$R" "$R/dec" "$R/b" || exit 1
python3 "$HERE/../decompile.py" "$EXE" -o "$R/dec" > "$R/dec.log" 2>&1
python3 "$HERE/vbcheck.py" "$R/dec" | tail -1
python3 "$HERE/errcensus.py" "$R/dec" "$R/census" --static -j 7 > "$R/census.txt" 2>&1
grep -a "procedure(s) emptied" "$R/census.txt"
python3 "$HERE/winetestfix.py" "$R/dec" "$R/census" "$R/census.txt" "$R/fix" > /dev/null
python3 "$HERE/errlines.py" "$R/fix" "$R/lines" 8 2>&1 | tail -1
python3 "$HERE/errlog.py" "$R/lines" "$R/log" "$HANDLER" "$@"
python3 "$HERE/vbmake.py" "$R/log/$VBP" | tail -1 | cut -c1-200
find "$DATA" -maxdepth 1 -type f ! -iname '*.pdf' ! -iname '*.exe' -exec cp -t "$R/b" {} +
cp "$R"/log/*.exe "$R/b/"
ls -la "$R"/b/*.exe
