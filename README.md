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


### Use without Shizuku / native-only policy (0.6.7)

The optional use_shizuku boolean (default true for backward compatibility)
can be set to false with gallery_settings_update or /api/settings.
When false, Shizuku/rish is never invoked by Photo Intelligence, even on
materialization or an explicit source probe. The Android/data source reports
DISABLED, and previously indexed rows are retained, not pruned. Local
DCIM/Pictures/Download OCR, visual and semantic indexing keep working.
This setting does not itself grant direct Android/data byte access to Termux.
Enable it again only if you consciously choose a different access policy.


### Missing photo recovery audit (read-only)

Use python scripts/gallery_recovery_audit.py --save-private to compare the
latest consistent pre-v067-* SQLite snapshot against the current catalog.
The audit reads SQLite with mode=ro, checks shared-storage metadata and
finds likely relocations by basename + size + modification time. It never
reads image bytes, deletes/renames photos, edits SQLite or uploads files.
An explicit --baseline and --current can select a different snapshot pair.

Detailed paths are written only to the private
~/.termux-mcp-bridge/addons/photo-intelligence/state/forensics/
directory with directory mode 0700 and file mode 0600. Terminal output
reports totals, not private filenames. A probable relocation is a strong
metadata match, not cryptographic proof of identical content.
The tool cannot reconstruct missing originals from a SQLite index.

### Preserving missing-image history (0.6.8)

Completed scans retain metadata for images removed from the active catalog
in the private SQLite gallery_missing_history table before pruning. The
read-only gallery_missing_history MCP tool shows historical missing paths,
counts and paths that later reappeared (RETURNED is not proof of identical
bytes). Shizuku/Android/data virtual rows are never pruned or marked missing
unless that source was actually scanned successfully. No image originals are
modified; old historical paths before installation remain in prior snapshots.


### High-resolution JPEG OCR (0.6.9)

50-64 MP camera JPEGs must be downscaled by Pillow JPEG decoder draft mode
before full bitmap decoding, then resized to max 1800x1800 for Tesseract.
Earlier versions rejected all JPEGs exceeding 45 million pixels and stored
ValueError without any text recognition. Decoder-level IDCT downscaling
avoids a 150+ MB full-resolution bitmap for typical 50 MP phone images.
Unsupported high-resolution formats still fail closed under the existing
pixel/memory guard. User originals remain unmodified.

### Offline person-photo album review (no face identity matching)

Use python scripts/gallery_album_review.py to build one local HTML report
with paginated generic person/people visual-tag candidates. The report
shows miniatures fetched exclusively from the already-running localhost
PhotoIntelligence /api/thumb endpoint; no original image pixels are embedded
in HTML or transmitted to any remote service. User can manually mark
"This is me", "Not me", "Already in album", export JSON decisions,
and import them later. No automatic gallery modifications.

For an existing local folder explicitly confirmed by user to correspond to
the album, pass --exclude-album 'Pictures/Gallery/owner/Я'.
This is an example folder, NOT a verified alias for the album "Давид".
For cloud albums, --exclude-manifest accepts an explicit trusted JSON
{"paths":["DCIM/Camera/example.jpg"]} with exact relative paths.
Never match by basename alone: different photos often share names.
Reports declare reference_album_visible and album_membership_known;
a missing album never silently counts as excluded.
Protected Android/data images are omitted when not readable by ordinary
Termux UID. Generic taxonomy is approximate and only covers previously
indexed accessible photos. This tool does not perform face recognition.

### Manual-feedback reuse and exact-copy hints (standalone optional tool)

Review decisions exported from the local HTML can be fed back without
uploading photos via gallery-album-review --reviews /path/to/photo-review-selections.json.
The export includes a per-album review scope to prevent cross-album labels
from being accepted inadvertently. The tool uses at most 512 MiB of
bounded read-only SHA-256 hashing and never copies source photo bytes
into its report. Only identical file bytes produce a duplicate-review hint;
equal size, filename, metadata, a similar face or CLIP proximity can never
transfer a review decision. It does not train any face-recognition model or
infer identity.

HTML retains manual review decisions in browser-local storage, and can
export them to JSON. Until user-reviewed decisions are exported, no
confirmed identity training data exist; the tool remains a generic
human-content review without learned identity. The --reviews flag is
opt-in and requires an exact matching review scope. Exact same file
bytes are explicitly distinguished from same person's face.
Existing addon runtime, gallery DB and photo files are unchanged.
