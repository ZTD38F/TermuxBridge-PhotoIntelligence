# TermuxBridge Photo Intelligence

Local-first Android photo intelligence addon for **TermuxBridge**. It builds a persistent local memory of the photo library so ChatGPT can find and inspect photos quickly without re-reading the entire library on every request.

**Author:** `@dvkrum`

## Install

Prerequisite: TermuxBridge 1.2.31+ must already be installed and connected to ChatGPT.

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/ZTD38F/TermuxBridge-PhotoIntelligence/contents/install.sh?ref=stable' | bash
```

The installer updates an older TermuxBridge when possible, installs local dependencies, OCR languages and the quantized CLIP model, imports an existing legacy photo index if present, registers the addon, starts its services, reloads TermuxBridge and opens the local dashboard.

The dashboard is local-only: `http://127.0.0.1:18780`.

## What it does

- Persistent SQLite catalog of the accessible Android image library.
- Incremental scan of new/changed/removed images.
- Tesseract OCR in Russian, Latvian and English with FTS5 search.
- On-demand thumbnails/contact sheets for ChatGPT visual inspection.
- Local CLIP image embeddings through ONNX Runtime for natural-language visual search.
- Visual quality features, dominant-color search, screenshot detection and dHash.
- Persistent SHA-256 exact-duplicate indexing and dHash near-duplicate groups.
- Virtual categories without moving source files.
- Cleanup recommendations and reviewable plans; analysis never deletes files automatically.
- Optional Shizuku-backed read-only access to `/storage/emulated/0/Android/data`.
- Self-healing background workers, wake lock, resume after TermuxBridge/phone restart.
- Local dashboard for progress, search, gallery, categories, duplicate/cleanup results and health.

## Architecture

```text
ChatGPT
   │ MCP
   ▼
TermuxBridge 1.2.31+
   │ external addon ABI
   ▼
Photo Intelligence
   ├─ Library index / SQLite
   ├─ OCR / FTS5
   ├─ Visual features / dHash
   ├─ CLIP ONNX semantic index
   ├─ Exact + near duplicates
   ├─ Virtual organization
   ├─ Cleanup planning
   ├─ Shizuku source adapter
   └─ Local dashboard
```

TermuxBridge remains a generic bridge. Photo-specific code and state belong to this repository/addon.

## Read-only upgrade preflight

Before upgrading an installed addon, run the check against the candidate
source tree on the same Android device:

    python scripts/upgrade_preflight.py --candidate "$PWD"

The check uses read-only SQLite access and never touches source photos or
restarts services. It verifies addon identity, authorship, ABI, strict version
progression, minimum bridge version, schema compatibility, database integrity,
and free storage. Exit code 0 means these static preconditions pass; it
does not prove that an upgrade, service takeover, or rollback works.
Exit code 1 means preconditions are blocked. Do not perform a production
installation until staged migration and rollback tests pass.

## Permissions and privacy

Processing is local. Source photos are read-only during indexing and analysis. The semantic model and OCR run on-device. No photo is uploaded by the addon.

Normal Android shared storage works without Shizuku after Android grants Termux its storage permission. Shizuku is optional and only expands read access to `Android/data`; it is not a TermuxBridge dependency. Full `/data/data` access is not provided by ordinary Shizuku shell access and requires root.

## Cleanup safety

The intended mutation pipeline is:

`analyze → recommend → plan → explicit approval → quarantine → undo window → final delete`

The current analysis/planning APIs stop before destructive application. Source files are not removed automatically.

See `docs/ARCHITECTURE.md` and `docs/SAFETY.md`.

### Realtime pipeline

From v0.2.0, a unified scheduler coordinates scan → OCR → visual → duplicate → semantic processing. Local file events trigger near-real-time reconciliation when `inotifywait` is available, while periodic reconciliation guarantees eventual consistency. The dashboard can switch between Balanced, Fast and Unlimited processing profiles without restarting the addon.

### Hybrid Search 2.0

`gallery_hybrid_search` fuses metadata, OCR and local CLIP signals and explains why each image matched. Russian and Latvian visual queries receive local English CLIP variants; no translation API is used. `gallery_search_contact_sheet` returns a compact visual candidate sheet for ChatGPT reranking.
