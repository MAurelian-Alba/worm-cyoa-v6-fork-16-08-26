# Row Balance Prefix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `prefix` field to `balance.yaml` groups so that `row.balance` assigns predictable prefixed IDs to all rows in a group, enabling glob-based style matching in the style YAML.

**Architecture:** Two small changes to `cyoa/ops/rows.py`: `redistribute_to_rows()` gains a `prefix` kwarg used when generating IDs for new rows; `balance_groups()` reads `prefix` from the group dict and renames any existing rows that lack it before redistribution. The tool layer (`cyoa/tools/row_tools.py`) needs no changes — it already passes the full group dict to `balance_groups()`.

**Tech Stack:** Python 3.13, `uv`. No test suite exists in this project — verify via manual CLI invocation.

## Global Constraints

- 2-space indentation, double quotes (Ruff enforced)
- No new dependencies
- Backwards compatible: groups without `prefix` must behave exactly as before

---

### Task 1: Add `prefix` parameter to `redistribute_to_rows()`

**Files:**
- Modify: `cyoa/ops/rows.py` — `redistribute_to_rows()` function (lines ~180–242)

**Interfaces:**
- Produces: `redistribute_to_rows(project, row_ids, pages, title, template_row, prefix=None)` — new optional keyword argument consumed by Task 2

- [ ] **Step 1: Add `prefix` to the function signature**

In `cyoa/ops/rows.py`, update the signature of `redistribute_to_rows`:

```python
def redistribute_to_rows(
  project: dict,
  row_ids: list[str],
  pages: list[list[dict]],
  title: str,
  template_row: dict,
  prefix: str | None = None,
) -> RedistributeResult:
```

- [ ] **Step 2: Use `prefix` when generating IDs for new rows**

In the "Create missing rows" block inside `redistribute_to_rows`, replace:

```python
new_row["id"] = gen_id()
```

with:

```python
new_row["id"] = f"{prefix}{gen_id()}" if prefix else gen_id()
```

- [ ] **Step 3: Verify no regression without a prefix**

Run a balance pass on the existing config (no prefix fields):

```bash
uv run cyoa row.balance --project project-v17.json --config balance.yaml
```

Expected: normal output, no errors, all groups report "Already balanced, skipping." (or redistribution as before).

- [ ] **Step 4: Lint**

```bash
uvx ruff check cyoa/ops/rows.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add cyoa/ops/rows.py
git commit -m "feat(rows): add prefix param to redistribute_to_rows"
```

---

### Task 2: Rename existing rows and pass `prefix` in `balance_groups()`

**Files:**
- Modify: `cyoa/ops/rows.py` — `balance_groups()` function (lines ~245–316)

**Interfaces:**
- Consumes: `redistribute_to_rows(..., prefix=None)` from Task 1

- [ ] **Step 1: Read prefix and rename existing rows before the resolve loop**

In `balance_groups()`, the current opening reads:

```python
  title = group["title"]
  max_objects = group["max_objects"]
  row_ids = group["rows"]

  # Resolve existing physical rows
  rows = []
```

Replace with:

```python
  title = group["title"]
  max_objects = group["max_objects"]
  row_ids = group["rows"]
  prefix = group.get("prefix")

  if prefix:
    for i, row_id in enumerate(row_ids):
      if not row_id.startswith(prefix):
        row = find_first(project["rows"], lambda r, rid=row_id: r["id"] == rid)
        if row is None:
          raise KeyError(f"Row {row_id!r} not found in group {title!r}")
        new_id = f"{prefix}{row_id}"
        row["id"] = new_id
        row_ids[i] = new_id

  # Resolve existing physical rows
  rows = []
```

`row_ids` is a direct reference to `group["rows"]`, so mutating it in place is enough — the config write-back at the end of `RowsBalanceTool.run()` captures the new IDs automatically.

- [ ] **Step 2: Pass `prefix` to `redistribute_to_rows`**

Find the `redistribute_to_rows` call at the bottom of `balance_groups()`:

```python
  redistribute_result = redistribute_to_rows(
    project, row_ids, pages, title, template_row
  )
```

Replace with:

```python
  redistribute_result = redistribute_to_rows(
    project, row_ids, pages, title, template_row, prefix=prefix
  )
```

- [ ] **Step 3: Add a prefix to one group in `balance.yaml` for manual testing**

Edit `balance.yaml` and add `prefix: "tier2--"` to the "Tier 2 Powers" group:

```yaml
- title: Tier 2 Powers
  max_objects: 100
  prefix: "tier2--"
  rows:
  - zg2f
  - fxkg1a
  - zr3ucz
```

- [ ] **Step 4: Run balance and verify rename**

```bash
uv run cyoa row.balance --project project-v17.json --config balance.yaml
```

Expected console output for the "Tier 2 Powers" group:
- Total objects count (unchanged)
- Row assignment lines showing IDs `tier2--zg2f`, `tier2--fxkg1a`, `tier2--zr3ucz`

Expected `balance.yaml` after run — the rows list for "Tier 2 Powers":

```yaml
  rows:
  - tier2--zg2f
  - tier2--fxkg1a
  - tier2--zr3ucz
```

- [ ] **Step 5: Verify idempotence**

Run the same command a second time immediately:

```bash
uv run cyoa row.balance --project project-v17.json --config balance.yaml
```

Expected: "Already balanced, skipping." for the "Tier 2 Powers" group — rows already have the prefix and objects are already distributed.

- [ ] **Step 6: Verify groups without prefix are unaffected**

Check another group in the console output (e.g. "Tier 0 / Lesser Powers") — it should show its existing IDs unchanged and behave as before.

- [ ] **Step 7: Lint**

```bash
uvx ruff check cyoa/ops/rows.py
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add cyoa/ops/rows.py balance.yaml
git commit -m "feat(rows): rename existing rows to prefix in balance_groups"
```

---

## Post-Implementation: Style YAML Migration (manual, per group)

After running `row.balance` with prefixes for all desired groups, update `gold_morning.style.yaml` once per group:

1. Remove the explicit `row/<old_id>: { class: groupPage }` entries for rows that now have the prefix.
2. Add the prefix pattern to the `groupPage` class `matching` list:

```yaml
classes:
  groupPage:
    normalize: true
    matching:
      - "row/tier2--*"
      - "row/phys--*"
      # ... one entry per prefix
```

From that point on, new pages created by `row.balance` match automatically.
