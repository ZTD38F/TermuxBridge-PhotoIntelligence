# Visual Intelligence 2.0

Author: `@dvkrum`

Photo Intelligence 0.6 adds a local zero-shot taxonomy layer on top of the existing normalized CLIP image embeddings. The taxonomy stage does not decode the source image again: it compares already-stored 512-dimensional image vectors against cached CLIP text vectors for a bounded generic label set.

Current labels cover documents, receipts, screenshots, clothing, food/drink, animals, people presence, rooms, buildings, vehicles, landscapes, products/packages, electronics, books, artwork, memes, charts and plants.

The `person` and `people` labels mean only that an image is visually similar to prompts describing one or more people. **No face recognition, identity assignment, name inference, face embeddings, or biometric identity profile is created.**

Taxonomy results are stored locally in SQLite and are versioned independently from the underlying CLIP model. A taxonomy-version change causes only the inexpensive label stage to refresh; existing CLIP image embeddings remain reusable.

Tools:
- `gallery_taxonomy_status`
- `gallery_taxonomy_index`
- `gallery_taxonomy_labels`
- `gallery_taxonomy_get`
- `gallery_taxonomy_search`

Schema version 4 adds `gallery_taxonomy`. Normal migration backup rules apply before schema changes.
