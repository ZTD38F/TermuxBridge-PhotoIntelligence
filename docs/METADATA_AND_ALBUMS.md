# Metadata intelligence, tags and smart albums

Author: `@dvkrum`

## Metadata index

Photo Intelligence 0.5 adds a persistent local metadata index for image dimensions, EXIF capture time, camera make/model, EXIF orientation and optional GPS fields. The source photo is opened read-only and is revalidated by size/mtime before a metadata record is committed.

Metadata work is incremental and resumable. It participates in the unified pipeline scheduler and can be enabled/disabled independently through `auto_metadata`.

Exact GPS coordinates are privacy-sensitive. They may exist in the private SQLite database when an image contains GPS EXIF data, but the default metadata API returns only `gps_included: false` and never emits latitude/longitude. Coordinates are returned only when a caller explicitly requests `include_gps=true`.

## Virtual tags

Tags are stored only in the private Photo Intelligence database. Adding or removing a tag does not rename, move or modify a media file. Tags are automatically removed by SQLite foreign-key cascade when an indexed image disappears.

## Smart albums

Smart albums are query definitions, not filesystem folders. Built-in albums include Recent 30 days, Screenshots, Camera, With GPS and Large files. A user may create custom albums using bounded criteria such as path/name/album substring, capture-date range, size, dimensions, camera make/model, GPS presence, orientation, source, MIME prefix and required tags.

Deleting a custom smart album deletes only its rule. Built-in albums cannot be deleted. Source files are never moved by smart-album operations.

## Schema migration

Schema version 3 adds `gallery_metadata_index`, `gallery_smart_albums` and `gallery_tags`. Before the first migration from an earlier schema, Photo Intelligence creates a private SQLite backup using SQLite's backup API. Only the three most recent schema-migration backups are retained.
