# Safety and data integrity

- Source images are treated as read-only by scan, OCR, visual, semantic and duplicate analysis.
- SQLite migrations/imports use SQLite backup rather than copying a live database byte-for-byte.
- Legacy state is preserved during first migration for rollback.
- A scan with read errors does not mass-prune the existing index.
- Shizuku loss is a transient pause, not a permanent OCR failure.
- Temporary Shizuku exchange files are removed after each read and stale exchange files are cleaned on later reads.
- Cleanup APIs recommend and create plans only. They do not delete or move source files.
- Exact duplicates require SHA-256 equality. Near duplicates are explicitly labeled as visual similarity, not byte identity.
- TermuxBridge addon loading checks ownership, path containment, ABI, namespace and writable permission bits.
- `/data/data` is not claimed as accessible without root.
