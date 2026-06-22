# Row Balance Prefix Design

**Date:** 2026-06-22

## Problem

When `row.balance` creates new rows during rebalancing, those rows receive random IDs from `gen_id()`. The style tool (`styles.update`) uses explicit `row/<id>: { class: groupPage }` entries in the style YAML to apply styles, so every newly created row requires a manual style YAML edit. With dozens of groups each capable of growing, this is ongoing maintenance burden.

The style tool already supports glob matching (e.g. `obj/sep--*`, `obj/head--*`) via `fnmatch`. If all rows in a group share a predictable prefix, one wildcard rule covers them all — including any new pages created by future balance runs.

## Solution

Add an optional `prefix` field to group entries in `balance.yaml`. When present, `row.balance` ensures all rows in that group have IDs starting with the prefix. The style YAML can then use `row/{prefix}*` in a class `matching` list to cover all rows in the group automatically.

## Schema Change — `balance.yaml`

`prefix` is an optional string. Omitting it preserves existing behavior exactly.

```yaml
groups:
- title: "Tier 2 Powers"
  max_objects: 100
  prefix: "tier2--"
  rows: ["tier2--zg2f", "tier2--fxkg1a", "tier2--zr3ucz"]
```

## Logic Changes — `cyoa/ops/rows.py`

### `balance_groups(group)`

Before redistribution, when `prefix` is present:

- For each row in the group whose ID does **not** already start with `prefix`:
  - New ID = `{prefix}{old_id}` (prepend only, no suffix regeneration)
  - Update `row["id"]` in the project dict in place
  - Update the `row_ids` list in place (so `balance.yaml` write-back captures the rename)
- Rows already starting with `prefix`: left untouched.

Pass `prefix` (or `None`) down to `redistribute_to_rows()`.

### `redistribute_to_rows(project, row_ids, pages, title, template_row, prefix=None)`

When creating a new row, use:

```python
new_row["id"] = f"{prefix}{gen_id()}" if prefix else gen_id()
```

New rows get `{prefix}{gen_id()}` — a prefix plus a fresh random suffix, consistent with how other prefixed objects work (`sep--`, `head--`).

## Write-back

The `RowsBalanceTool.run()` already writes the updated config back to `balance.yaml` after each run. Renamed row IDs are captured automatically — no additional changes to the tool layer.

## Style YAML Migration

After the first `row.balance` run with a prefix, the user performs a one-time cleanup:

1. Remove stale explicit `row/<old_id>: { class: groupPage }` entries for the migrated group.
2. Add the prefix pattern to the class `matching` list:

```yaml
classes:
  groupPage:
    matching: ["row/tier2--*", "row/phys--*", ...]
```

From that point on, new pages created by `row.balance` match automatically with no further YAML edits.

## Scope Boundary

`row.balance` does **not** modify the style YAML. Style management remains a separate concern.

If a prefix changes (expected to be very rare), the user handles the necessary manual ID and style edits.

## Backwards Compatibility

Groups without a `prefix` field behave exactly as before. The change is purely additive.
