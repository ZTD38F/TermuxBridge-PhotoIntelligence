#!/data/data/com.termux/files/usr/bin/bash
# Fetch vetted upstream ONNX weights only; no original photos are accessed.
# Author: @dvkrum.
set -euo pipefail
umask 077
export PATH="/data/data/com.termux/files/usr/bin:/system/bin:$PATH"
dest="$HOME/.local/share/photo-intelligence-tools/face-models"
mkdir -p "$dest"
chmod 700 "$dest"
download() {
  local filename="$1" upstream="$2" digest="$3"
  local target="$dest/$filename"
  if [[ -L "$target" ]];then
    echo "Refusing model symlink: $filename" >&2;return 1
  fi
  if [[ -f "$target" ]];then
    test "$(sha256sum "$target" | cut -d' ' -f1)" = "$digest" || {
      echo "Existing model does not match pinned SHA256: $filename" >&2;return 1;
    }
    return
  fi
  local temp
  temp="$(mktemp "$dest/.model-XXXXXXXX")"
  trap 'rm -f "$temp"' RETURN
  curl -fLsS --retry 2 --connect-timeout 12 --max-time 110 "$upstream" -o "$temp"
  test "$(sha256sum "$temp" | cut -d' ' -f1)" = "$digest" || {
    echo "Downloaded face model hash mismatch: $filename" >&2;return 1;
  }
  chmod 600 "$temp"
  mv -n "$temp" "$target"
  trap - RETURN
}
download "face_detection_yunet_2023mar.onnx" \
  "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
  "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
download "face_recognition_sface_2021dec.onnx" \
  "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx" \
  "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
echo "Face ONNX models installed and SHA256 verified in private Termux storage."
