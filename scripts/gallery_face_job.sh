#!/data/data/com.termux/files/usr/bin/bash
# Private local face-group indexer; unrestricted charge level, thermal guard remains.
# Images and gallery database are NEVER changed. @dvkrum
set -euo pipefail
umask 077
export PATH="/data/data/com.termux/files/usr/bin:/system/bin:$PATH"
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
state="$HOME/.termux-mcp-bridge/addons/photo-intelligence/state"
script="$HOME/.local/share/photo-intelligence-tools/gallery_face_groups.py"
# Verify temperature only; battery level and charging never block indexing.
power="$(termux-battery-status 2>/dev/null | python -c '
import json,sys
try:
 d=json.load(sys.stdin)
 temp=float(d.get("temperature",99))
 print("READY" if temp < 42 else "SKIP")
except (ValueError,TypeError,KeyError):
 print("SKIP")
' 2>/dev/null)" || power="SKIP"
if [[ "$power" != "READY" ]];then
  exit 0
fi
mkdir -p "$state"
chmod 700 "$state"
if [[ ! -f "$script" || -L "$script" ]];then exit 1;fi
exec 9>"$state/face_auto_index.lock"
if ! flock -n 9;then exit 0;fi
# Limit each scheduled work window and retain a short private job log.
log="$state/face_auto_index.log"
if [[ -f "$log" && $(stat -c %s "$log") -ge 200000 ]];then
    mv -f "$log" "$state/face_auto_index.previous.log"
fi
{
 date -Is
 /data/data/com.termux/files/usr/bin/python -B "$script" --index --batch-size 240 --max-seconds 105
 /data/data/com.termux/files/usr/bin/python -B "$script" --group
} >> "$log" 2>&1
