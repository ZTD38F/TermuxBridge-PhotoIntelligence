# Architecture

## Separation of responsibilities

TermuxBridge owns transport, authentication, tunnel, generic jobs and the addon ABI. Photo Intelligence owns every photo-specific concern. The addon claims the `gallery_` MCP namespace; TermuxBridge disables its legacy gallery fallback when this addon is present.

## Data plane

The canonical catalog is a private SQLite database under `~/.termux-mcp-bridge/addons/photo-intelligence/state/gallery.sqlite3`. Image bytes remain at their original paths. `Android/data` entries use `shizuku://` virtual paths and are materialized one file at a time through a short-lived hidden exchange file that is removed immediately after use.

The database stores file metadata, OCR terminal state and FTS text, visual features/dHash, exact SHA-256 duplicate hashes, CLIP embeddings, audit information and non-destructive cleanup plans.

## Retrieval

`gallery_smart_search` merges metadata/OCR retrieval with CLIP semantic ranking. ChatGPT can then fetch only the strongest candidates as thumbnails, full images or contact sheets for visual reranking. This prevents the entire image library from being put into ChatGPT context.

## Background lifecycle

`service.py` supervises three children: the gallery worker, analysis worker and dashboard. The gallery worker launches/resumes full OCR and later performs five-minute incremental scans and OCR of new content. The analysis worker incrementally builds visual, duplicate and semantic indexes. TermuxBridge starts addon services at boot and isolates addon failures from core bridge transport.

## Compatibility

The public `gallery_*` MCP tool family and `gallery://` virtual URLs are retained so existing ChatGPT workflows can continue after migration from the old in-core implementation.

## Source isolation

From v0.1.3, local shared-storage work and Shizuku-backed Android/data work use independent runnable queues. If Shizuku is sleeping or disconnected, only Android/data is deferred. Local OCR, visual features, CLIP embeddings and duplicate hashing continue. Existing Android/data catalog rows are preserved during scans while that source is unavailable and resume automatically when Shizuku returns.

## Release transition cleanup

From v0.1.4, the one-click installer runs a scoped stale-worker reaper after the normal addon shutdown and before restart. It identifies only Photo Intelligence processes under the addon root and current Termux UID, tries SIGTERM first, then applies a bounded force-stop only after revalidating the same PID/start identity. This prevents orphan workers from an older release from holding advisory locks after an update.

## Unified pipeline scheduler (v0.2.0)

Photo Intelligence now uses one scheduler for metadata reconciliation, OCR, visual features, exact-duplicate hashing and CLIP embeddings. A lightweight source watcher only signals that local storage changed; it never reads image content. `inotifywait` is used when available, with bounded polling plus periodic full reconciliation as the fallback.

Processing profiles are centralized: `balanced`, `fast`, and `unlimited`. The scheduler is the single owner of battery/thermal policy, so individual engines do not apply contradictory thresholds. Every engine remains resumable from SQLite and source isolation remains in effect.

The local dashboard exposes source state, per-stage coverage, pipeline health, settings/profile changes, immediate reconciliation and search/cleanup/duplicate views.

## Schema migrations

SQLite changes are versioned through `PRAGMA user_version`. Before a schema version upgrade, a SQLite backup API snapshot is written under the addon's private `state/backups` directory; only the three newest schema-transition backups are retained.

## Hybrid Search 2.0 (v0.3.0)

Search uses weighted reciprocal-rank fusion across filename/path metadata, FTS5 OCR and local CLIP embeddings. Results include per-signal explanations instead of a single opaque score. RU/LV visual terms are expanded locally into an English CLIP query variant while the original text remains the canonical OCR/metadata query. Low-value sticker/cache/thumbnail assets are penalized for semantic-only matches but remain discoverable by direct metadata/OCR queries.

`gallery_search_contact_sheet` runs the hybrid ranker and renders top candidates as one labeled image, allowing ChatGPT to visually rerank a short candidate set without opening the entire library.
