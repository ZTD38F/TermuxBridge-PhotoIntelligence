#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
REPO="ZTD38F/TermuxBridge-PhotoIntelligence"
BRANCH="${PHOTO_INTELLIGENCE_CHANNEL:-stable}"
BRIDGE_ROOT="${TERMUXBRIDGE_ROOT:-$HOME/termux-mcp-bridge}"
ADDON_ROOT="${PHOTO_INTELLIGENCE_HOME:-$HOME/.termux-mcp-bridge/addons/photo-intelligence}"
RELEASES="$ADDON_ROOT/releases"
TMP="$(mktemp -d "$PREFIX/tmp/photo-intelligence.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
say(){ printf '[Photo Intelligence] %s\n' "$*"; }
fail(){ say "ERROR: $*" >&2; exit 1; }

[[ -d "$BRIDGE_ROOT" && -e "$BRIDGE_ROOT/current" ]] || fail "TermuxBridge is required first."
command -v pkg >/dev/null || fail "Run inside Termux."
bridge_version(){ grep -E '^BRIDGE_VERSION = ' "$BRIDGE_ROOT/current/bridge_server.py" 2>/dev/null | head -1 | sed -E 's/.*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/'; }
version_ge_1231(){ [[ "$(printf '%s\n' '1.2.31' "$1" | sort -V | head -1)" == "1.2.31" ]]; }
BV="$(bridge_version)"
if [[ -z "$BV" ]] || ! version_ge_1231 "$BV"; then
  say "Updating TermuxBridge to addon-compatible core..."
  [[ -x "$BRIDGE_ROOT/update_termux.sh" ]] || fail "TermuxBridge 1.2.31+ is required."
  bash "$BRIDGE_ROOT/update_termux.sh"
  BV="$(bridge_version)"
fi
[[ -n "$BV" ]] && version_ge_1231 "$BV" || fail "TermuxBridge 1.2.31+ is required."

say "Installing local dependencies..."
pkg install -y python python-pillow python-numpy python-onnxruntime tesseract ffmpeg exiftool curl git unzip termux-api >/dev/null

say "Ensuring OCR languages (eng, rus, lav)..."
TESSDATA="$PREFIX/share/tessdata"; mkdir -p "$TESSDATA"
for lang in eng rus lav; do
  if [[ ! -s "$TESSDATA/$lang.traineddata" ]]; then
    curl -fL --retry 3 --connect-timeout 15 "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$lang.traineddata" -o "$TESSDATA/.$lang.traineddata.tmp"
    mv "$TESSDATA/.$lang.traineddata.tmp" "$TESSDATA/$lang.traineddata"
  fi
done

say "Downloading addon source..."
ARCHIVE="$TMP/source.tar.gz"
if ! curl -fL --retry 3 --connect-timeout 20 "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$ARCHIVE"; then
  [[ "$BRANCH" == "stable" ]] || fail "Could not download branch $BRANCH"
  say "Stable channel is unavailable; using main bootstrap."
  BRANCH=main
  curl -fL --retry 3 --connect-timeout 20 "https://github.com/$REPO/archive/refs/heads/main.tar.gz" -o "$ARCHIVE"
fi
tar -xzf "$ARCHIVE" -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d -name 'TermuxBridge-PhotoIntelligence-*' | head -1)"
[[ -n "$SRC" && -f "$SRC/addon.json" ]] || fail "Downloaded addon is incomplete."
VERSION="$(python -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$SRC/addon.json")"
DEST="$RELEASES/$VERSION"
mkdir -p "$RELEASES" "$ADDON_ROOT/state" "$ADDON_ROOT/logs"
chmod 700 "$ADDON_ROOT" "$ADDON_ROOT/state" "$ADDON_ROOT/logs" "$RELEASES"
if [[ ! -d "$DEST" ]]; then
  rm -rf "$DEST.tmp"
  cp -a "$SRC" "$DEST.tmp"
  mv "$DEST.tmp" "$DEST"
fi
chmod +x "$DEST/install.sh" "$DEST/photo-intelligence" "$DEST/service.py" "$DEST/scripts/migrate_legacy.py" "$DEST/scripts/takeover_legacy.py"

if [[ "${PHOTO_INTELLIGENCE_SKIP_SEMANTIC_MODEL:-0}" != "1" ]]; then
  say "Installing local CLIP semantic model (~154 MB)..."
  MODEL_DIR="$ADDON_ROOT/models/clip-vit-base-patch32"
  mkdir -p "$MODEL_DIR"; chmod 700 "$ADDON_ROOT/models" "$MODEL_DIR" 2>/dev/null || true
  HF_REV="d15189d7028b43f1d3e65039190477f6af591c2a"
  download_exact(){
    local rel="$1" out="$2" expected="$3" tmp
    tmp="$out.tmp"
    if [[ -s "$out" && "$(wc -c < "$out")" == "$expected" ]]; then return 0; fi
    rm -f "$tmp"
    curl -fL --retry 4 --connect-timeout 20 "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/$HF_REV/$rel" -o "$tmp"
    [[ "$(wc -c < "$tmp")" == "$expected" ]] || fail "Semantic model asset size mismatch: $rel"
    mv "$tmp" "$out"; chmod 600 "$out"
  }
  download_exact "onnx/vision_model_quantized.onnx" "$MODEL_DIR/vision_model_quantized.onnx" 89117001
  download_exact "onnx/text_model_quantized.onnx" "$MODEL_DIR/text_model_quantized.onnx" 64504507
  download_exact "vocab.json" "$MODEL_DIR/vocab.json" 862328
  download_exact "merges.txt" "$MODEL_DIR/merges.txt" 524619
fi

say "Preparing optional Shizuku bridge..."
APK="$(pm path moe.shizuku.privileged.api 2>/dev/null | sed -n 's/^package://p' | head -1 || true)"
if [[ -n "$APK" && -r "$APK" ]]; then
  mkdir -p "$HOME/bin"
  unzip -p "$APK" assets/rish > "$HOME/bin/rish.tmp" || true
  unzip -p "$APK" assets/rish_shizuku.dex > "$HOME/bin/rish_shizuku.dex.tmp" || true
  if [[ -s "$HOME/bin/rish.tmp" && -s "$HOME/bin/rish_shizuku.dex.tmp" ]]; then
    sed 's/export RISH_APPLICATION_ID="PKG"/export RISH_APPLICATION_ID="com.termux"/' "$HOME/bin/rish.tmp" > "$HOME/bin/rish"
    mv "$HOME/bin/rish_shizuku.dex.tmp" "$HOME/bin/rish_shizuku.dex"
    rm -f "$HOME/bin/rish.tmp"
    chmod 500 "$HOME/bin/rish"; chmod 400 "$HOME/bin/rish_shizuku.dex"
  fi
fi

say "Activating release $VERSION..."
ln -sfn "$DEST" "$ADDON_ROOT/current.next"
mv -Tf "$ADDON_ROOT/current.next" "$ADDON_ROOT/current"
ln -sfn "$ADDON_ROOT/current/photo-intelligence" "$PREFIX/bin/photo-intelligence"

if [[ "${PHOTO_INTELLIGENCE_DEFER_START:-0}" != "1" ]]; then
  say "Stopping only legacy in-core photo workers for takeover..."
  TERMUXBRIDGE_ROOT="$BRIDGE_ROOT" python "$ADDON_ROOT/current/scripts/takeover_legacy.py"
fi

say "Importing existing gallery intelligence state when present..."
PHOTO_INTELLIGENCE_HOME="$ADDON_ROOT" PHOTO_INTELLIGENCE_CURRENT="$ADDON_ROOT/current" PYTHONPATH="$ADDON_ROOT/current/runtime" python "$ADDON_ROOT/current/scripts/migrate_legacy.py"

python -c 'import json,os,sys,time;from pathlib import Path;root=Path(sys.argv[1]);p=root/"state/install.json";tmp=p.with_name(".install.json.tmp");tmp.write_text(json.dumps({"version":sys.argv[2],"channel":sys.argv[3],"installed_at":time.time(),"author":"@dvkrum"},separators=(",",":"))+"\n");tmp.chmod(0o600);os.replace(tmp,p)' "$ADDON_ROOT" "$VERSION" "$BRANCH"

if [[ "${PHOTO_INTELLIGENCE_DEFER_START:-0}" != "1" ]]; then
  say "Reloading TermuxBridge so ChatGPT discovers the addon..."
  bash "$BRIDGE_ROOT/stop_bridge.sh" >/dev/null 2>&1 || true
  bash "$BRIDGE_ROOT/start_bridge.sh" >/dev/null
  for _ in $(seq 1 20); do
    curl -fsS --max-time 2 http://127.0.0.1:18780/api/status >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --max-time 3 http://127.0.0.1:18780/api/status >/dev/null || fail "Addon service/dashboard did not become healthy."
  if [[ "${PHOTO_INTELLIGENCE_NO_OPEN_UI:-0}" != "1" ]]; then
    am start -a android.intent.action.VIEW -d http://127.0.0.1:18780 >/dev/null 2>&1 || true
  fi
fi
say "Installed Photo Intelligence $VERSION. Dashboard: http://127.0.0.1:18780"
