#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
export PATH="/data/data/com.termux/files/usr/bin:/system/bin:$PATH"
state="$HOME/.termux-mcp-bridge/addons/photo-intelligence/state"
script="$HOME/.local/share/photo-intelligence-tools/gallery_face_groups.py"
echo "Face indexing status (read-only; avoids Termux:API pending-jobs crash):"
python -B "$script" --status
echo "Last automated job output:"
if [[ -f "$state/face_auto_index.log" ]];then
 tail -n 4 "$state/face_auto_index.log"
else
 echo "No executed face-index job has been recorded."
fi
