#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
export PATH="/data/data/com.termux/files/usr/bin:/system/bin:$PATH"
job="$HOME/.local/share/photo-intelligence-tools/gallery_face_job.sh"
[[ -f "$job" && ! -L "$job" ]] || exit 1
exec termux-job-scheduler --script "$job" --job-id 74061 --period-ms 900000 --network none --battery-not-low true --charging true --storage-not-low true --persisted true
