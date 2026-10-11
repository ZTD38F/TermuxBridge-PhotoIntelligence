# Safe cleanup and quarantine

Author: `@dvkrum`

Photo Intelligence never turns a cleanup recommendation directly into a permanent delete. The state machine is:

`PROPOSED → QUARANTINED → RESTORED` or `PROPOSED → QUARANTINED → PURGED`.

A plan snapshots image ID, relative path, size and mtime. Before quarantine every file is revalidated against that snapshot. Any changed, missing, symlinked or escaping path aborts the whole preflight before the first move.

Quarantine is inside shared storage at `.photo-intelligence-quarantine/<plan-id>/...`; the scanner ignores this tree. Quarantine and restore require both `approved=true` and an exact `confirm_plan_id`. The dashboard additionally requires the operator to type the plan ID. TermuxBridge marks quarantine, restore and purge as consequential/destructive tools.

The default undo window is 72 hours and can be configured per plan from 1 to 720 hours. Final purge is rejected until the window expires. Purge is irreversible. If a purge is partial, the plan remains `PURGE_PARTIAL` and may safely retry only still-quarantined actions.

`Android/data` entries are intentionally excluded from mutation even when readable through Shizuku. Cleanup analysis may recommend them, but quarantine refuses such plans. This avoids trying to mutate another application's private external-storage tree.

Source-file moves and SQLite updates use rollback-oriented ordering. OCR/FTS state for a quarantined image is explicitly removed before the image row disappears, while visual/semantic/duplicate state uses foreign-key cascades. Restore queues a fresh reconciliation scan so restored media is reindexed from its current bytes.
