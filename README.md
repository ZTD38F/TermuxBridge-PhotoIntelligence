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


### Automatic import of user-reviewed exports

By default the standalone gallery-album-review tool looks only for
photo-review-selections*.json directly in shared Download, choosing the
newest export with a valid schema and the exact matching local album scope.
This enables the next run to reuse manual decisions without typing the
file path. It never scans private Android/data, uses external apps, assigns
identity to unknown faces, uploads photos or automatically moves files.
--reviews sets an explicit export path and --no-auto-reviews disables
shared-Download discovery. All imported decisions remain human-reviewed,
and only byte-for-byte copies are suggested automatically. With no valid
export there is no trained facial identity and no matching outcome.

### Private live review on Android (instant durable saves)

Run gallery-album-review --serve --exclude-album 'Давид' only if
you intend to review generic people photos; album "Давид" is
currently NOT found in Termux local catalog, so this flag does
NOT exclude any existing Google Photos/Xiaomi album members.

Live mode uses an on-demand HTTP session bound exclusively to
127.0.0.1 on an OS-selected port with an unpredictable session URL.
Requests to save decisions must originate from that exact loopback
origin and carry the review-only custom header. Other websites,
cross-origin POSTs, missing session tokens and invalid input are
rejected. The server never trains or exports a biometric identity model.
The server exits after one hour of inactivity or six hours total.

On each deliberate tap (This is me / Not me / Already in album),
the browser sends ONLY image ID, selected decision and snapshot
fingerprint. Before saving, the server checks the original path,
size and modification time still match. It writes a restricted
0600 JSON file under Termux-private review_decisions (0700),
using interprocess locking, atomic replacement and fsync.
No SQLite/image/album original is modified. The mobile page reports
success only after server acknowledgement; failures show an error.

Manual JSON import is validated for the same album scope and saved
as one atomic batch. Browser filters show unreviewed, reviewed and
exact-copy hints, plus search by name/album. Only one page's
thumbnails are loaded at once. The workflow DOES NOT recognize
individual faces. It records only decisions the user confirms.
Google Photos/Xiaomi virtual album membership requires provider
listing or explicit user-supplied manifest.

In the live reviewer, manual marks can be corrected or cleared.
Clearing a mistaken mark removes its private saved decision atomically,
rather than converting the selection into a different class. The local
browser offers filters (unreviewed, reviewed, exact copies) and name/folder
search; the imported review file remains scoped to the exact album.
The persisted record is validated again on future runs against the current
photo path, catalog size and modification time, so a reused photo ID
cannot silently inherit an older decision.

### Offline face-based People albums — native Termux, no Google Photos required

Use the standalone face-group engine scripts/gallery_face_groups.py on
locally readable images. The separate private face_groups.sqlite3 catalog
holds SFace facial feature vectors and clustering links. Treat these
biometric vectors as sensitive private data: they never leave the device.
The database and derived face-cover previews use restrictive permissions.
Original images, main gallery DB and existing albums are never modified.

YuNet from OpenCV Zoo (MIT) performs face detection with landmarks; SFace
(Apache-2.0) produces 128D L2-normalized face features. Weights are
SHA-256 verified against pinned upstream binaries by
scripts/install_face_models.sh; model binaries are not committed to GitHub.
The code uses PIL, NumPy and onnxruntime, not opencv-python, Shizuku,
ADB, root or MT Manager.

Quick start:
    bash scripts/install_face_models.sh
    python scripts/gallery_face_groups.py --index --batch-size 200 --max-seconds 100
    python scripts/gallery_face_groups.py --group
    python scripts/gallery_face_groups.py --report
    python scripts/gallery_face_groups.py --serve

The People UI shows a representative, locally generated face crop for
each automatically formed cluster, and collapsible lists of original
photos. Names are only assigned by a user after visual confirmation:
    python scripts/gallery_face_groups.py --name-group 7 --name 'Давид'
The optional loopback-only interface has a random session token,
exact-origin POST checks and a limited session lifetime. Face covers
are only stored in private 0600 HTML files, or served on localhost.
No photos or face models/embeddings are uploaded externally.

Groups use a conservative cosine threshold of 0.57, shortlisting
using centroids and bounded complete-link validation. Some same-person
photos can be split, and different people might occasionally be
merged. It is not as exhaustively tested as Google Photos. Cloud
album membership is not inferred from local file paths.

Native Termux JobScheduler can resume in small batches of up to 240
photos and 105 seconds while charging only, with battery + thermal
guards. The scheduler is configured only for this user-requested
automatic workflow; it never needs network, never touches Android/data,
and may be delayed by Android battery/background restrictions.
A job can be stopped by cancelling its specific job ID (never
cancel all jobs, which could affect unrelated user automations).
\nRead-only progress: python scripts/gallery_face_groups.py --status.\n

### Automatic face-groups: conservative correctness and charging guard

Separate faces detected inside the same photo are never merged into one
group, regardless of embedding similarity. Clusters check representative
vectors from throughout the cluster instead of its oldest subset. The People
UI shows up to 1000 groups and lets you search by group name and number.

The optional install_face_schedule.sh registers JobScheduler ID 74061
for 15-minute charging-only sessions requiring storage and battery not low.
The job shell additionally fails closed unless current Android battery
telemetry confirms CHARGING or FULL, charge >=30%, temperature <40 C.
No recognition workloads run on an unplugged or low-charge phone.

On Termux:API 0.53.0 with Android 16, the JobScheduler pending-jobs
display may throw NullPointerException while formatting a null
NetworkRequest for jobs with network=none. Avoid running
termux-job-scheduler --pending to inspect this job. Instead observe the
read-only face index --status and face_auto_index.log after charging.
Registration acknowledgement alone does not prove future execution.

### Power disconnection during an automatic face-index job

Scheduled indexing passes --require-charging to both --index and --group.
On every job start and every 12 processed photos, the indexer checks
Termux battery telemetry. Indexing halts upon unplug, charge under 30%,
temperature at or above 40 C or unavailable telemetry. The scheduled
--group invocation checks power independently and skips regrouping after
unplug. Ordinary manual indexing (without --require-charging) retains the
legacy battery guard. --ignore-battery and --require-charging are mutually
exclusive; power checks cannot be bypassed accidentally for automatic jobs.
No original image files or gallery SQLite data are modified by this guard.


### Complete on-device face group browsing

The people/face groups viewer now lists all grouped faces instead of the
former silent limit of 1,000 groups. In live mode each group fetches its
photos **only when opened**, through a loopback, random-token protected
endpoint; it loads 48 thumbnails per page with an explicit "Load more"
button. This fixes the old cap of 18 face photos per group, while keeping
the mobile UI responsive and private. Maximum API page is 80; no embeddings
or original image bytes are included in API JSON. On-demand thumbnails are
served only from the existing local PhotoIntelligence server. A standalone
static HTML report still shows a documented small subset per group and
warns visibly if more photos are available. Cluster quality is approximate:
this does not prove the names or personal identities of people depicted.

### On-device automatic people gallery: unrestricted battery level

The face grouping job can run whether the phone is charging or
discharging, including at 10% charge and below, as explicitly requested
by the phone owner. The existing 15-minute Android JobScheduler entry
uses --charging false and --battery-not-low false. The scheduled worker
does not request --require-charging. This setting does not remove the
42°C temperature cutoff, data integrity protections, per-run CPU/time
limits, storage-space requirements or the Android/Termux permission
boundary. If the device is in Android Doze mode, JobScheduler may
still delay an interval; the application does not claim exact-time
continuous execution. Face groups are anonymous until manually named.


### People grouping: covers for all anonymous groups

The people-group page renders all indexed face groups, not just its first
100. Generating and embedding cropped portraits for thousands of groups
would increase CPU, RAM and HTML size, so high-quality face crops are
still limited to the first 100 while the remaining groups obtain a lazy
preview of an already indexed LOCAL photograph using the dashboard's
thumb endpoint. This fallback is labeled Foto gruppy, not
an isolated or verified face crop. No new photo files are created,
no originals altered, and no new network/biometric service is enabled.

For 1000+ groups, the search field and an optional 2+ photos filter are
placed ABOVE the people grid, so browsing no longer requires scrolling to
the bottom just to filter. The counter now distinguishes all groups from a
truncated result. Singletons remain visible unless the user explicitly
enables the filter.

### Automatic stale face-index reconciliation (Android)

The standalone face index is reconciled with the authoritative gallery
SQLite before every scheduled face job.
gallery-face-groups --reconcile previews cleanup and never writes.
gallery-face-groups --reconcile --apply removes only orphaned face_scans,
face_embeddings and face_clusters rows in private face-group SQLite.
It never edits original photos or gallery SQLite. Missing manually
named anchor faces block the operation. Unexpected source shrinkage
(>10% of face scans, >300 deletions, or empty gallery) fails closed:
the scheduled scan and grouping do not proceed on suspect data.
Groups are rebuilt by the existing scheduled operation, not another
background timer. No ADB, Shizuku, root or external intermediary.

Interrupted index writes may also leave face vectors without scan
rows; these are reconciled. Validate integrity and retained rollback
snapshot before an initial live cleanup. Person names are never
assigned automatically.
