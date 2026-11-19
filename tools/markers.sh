#!/bin/bash
# markers.sh DIR: count the constructs a decompiled project still has unresolved
cd "$1" || exit 1
echo "files: $(ls *.bas *.cls *.frm 2>/dev/null | wc -l), lines: $(cat *.bas *.cls *.frm 2>/dev/null | wc -l)"
for pat in "' ?asm" "DerefAry" "global_[0-9A-F]* + " " And 0$" " Or 255$\| Or -1$\| Or 65535$" "Unknown\|unknown" "vtbl_" "__vba\|rtc[A-Z]" "^ *GoTo L" "\bAs T_[0-9]"; do
    printf "  %-40s %6d\n" "$pat" "$(cat *.bas *.cls *.frm 2>/dev/null | grep -c -- "$pat")"
done
grep -oh "' ?asm [a-z]*" *.bas *.cls *.frm 2>/dev/null | sort | uniq -c | sort -rn | head -5
