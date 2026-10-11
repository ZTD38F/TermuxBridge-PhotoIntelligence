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
