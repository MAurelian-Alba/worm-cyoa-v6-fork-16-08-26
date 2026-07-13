# Project Split / Merge — Design

**Date:** 2026-07-13
**Status:** Approved (design)

## Problem

The ICYOA project file (`project-v17.json`, 46–58 MB) is a single large JSON
that contributors edit in the web UI and pass around over Discord. As it grows,
handing the whole file back and forth is unwieldy. We want to split it into one
**master** file (holding most of the content) plus several **auxiliary** files,
each carrying a rarely-changed section extracted from the master. Contributors
mostly edit the master and occasionally an auxiliary; each file must remain a
complete, web-UI-loadable project.

The build pipeline must be able to reassemble the full project from
master + auxiliaries, and must also accept an already-merged full file as input.

## Decisions (from brainstorming)

1. **Source of truth:** master + auxiliary files are canonical and committed to
   git. The full merged `project-v17.json` is a build artifact (the viewer input).
2. **File shape:** every split file (master and each aux) is a *full-skeleton*
   project — a complete copy of `pointTypes`, `groups`, `backpack`, and project
   settings, but only its own subset of `rows`. This keeps each file loadable in
   the web UI.
3. **Merge authority:** for the shared (non-row) sections, **master wins**. Aux
   copies of shared sections are dropped on merge. Metadata edits must be made in
   the master.
4. **Handling an already-merged input:** the merge step is idempotent by row
   presence. The split step **always overwrites** master + all aux files from the
   processed in-memory project, so canonical files are never stale. Git history is
   the recovery path if an overwrite is unwanted.
5. **Naming:** the `project.` namespace — `project.split` and `project.merge` —
   for both CLI tools and build steps. (Avoids colliding with the existing
   diff-based `merge` tool.)
6. **Anchor authority:** the anchor is authoritative for placement; merge always
   inserts rows at the anchor. Split warns if a row has drifted from its anchor.
7. **Anchor constraint:** anchors must reference **master-resident** rows (rows
   that are not themselves extracted). Enforced by lint.

## Descriptor — `split.yaml`

A single sidecar descriptor, loaded like `balance.yaml` (relative to the build
file's `work_dir`).

```yaml
version: 1
master: project-v17.master.json      # canonical master file (relative to descriptor)
files:
  - name: entity                     # logical id (used in logs)
    path: project-v17.entity.json    # canonical aux file
    segments:
      - rows: [i1p0, "...", jsvj]     # contiguous inclusive range
        anchor: { after: 114m }       #   equivalent to between: [114m, gra1]
      - rows: [06d7, iu9w]            # explicit ID list
        anchor: { before: PhysP--Title }
```

### Row selectors (per segment)

- `[A, "...", B]` — the literal `"..."` sentinel denotes the contiguous inclusive
  slice from row `A` to row `B`, as they appear in the source.
- `[a, b, c]` — exactly those IDs, in that order.

### Anchor forms

- `after: X` — insert immediately after row `X`.
- `before: Y` — insert immediately before row `Y`.
- `between: [X, Y]` — insert between `X` and `Y`; validates that `Y` immediately
  follows `X` in the master.
- `at_start: true` — insert at the head of `rows`.
- `at_end: true` — append to `rows`.

## Architecture

Mirrors the existing three-layer split used by `sort`/`balance`
(`ops/` + `tools/` + `build/steps/`).

### `cyoa/ops/filesplit.py` — pure functions, no I/O

- `parse_descriptor(data: dict) -> SplitSpec`
  Dataclasses: `SplitSpec`, `FileSpec`, `Segment`, `Anchor`.
- `merge_project(base: dict, aux_projects: dict[str, dict], spec: SplitSpec) -> MergeResult`
- `split_project(full: dict, spec: SplitSpec) -> SplitResult`
  Returns the master dict plus `{path: aux_dict}`.
- `validate_spec(project: dict, spec: SplitSpec) -> list[Issue]`
- Helpers: `resolve_selector(rows, selector)`, `resolve_anchor(rows, anchor)`,
  `skeleton(full, rows)` (copy of `full` with `rows` replaced).

### `cyoa/tools/filesplit_tools.py` — CLI wrappers

- `FileSplitTool` (`name = "project.split"`)
  - `--project <full.json>` input, `--config split.yaml`, `--write`, `--lint`.
  - Reads the full project, splits, writes master + aux (or reports under `--lint`
    / when `--write` is omitted).
- `FileMergeTool` (`name = "project.merge"`)
  - `--master <master.json>` (or full file) input, `--config split.yaml`,
    `--output <full.json>`, `--write`, `--lint`.
  - Reads base + aux files, merges, writes the full project.

Both extend `ToolBase` + `ProjectUtilsMixin`; registered in the `TOOLS` tuple in
`cyoa/tools/client.py`. Console output via `rich`.

### `cyoa/build/steps/filesplit.py` — build steps

- `MergeStep` (`step_type = "project.merge"`) — assembles `context.project` in
  place. Loads the descriptor and aux files from `context.work_dir`.
- `SplitStep` (`step_type = "project.split"`) — writes master + aux files from
  `context.project`.

Both reuse the ops functions. Registered in `cyoa/build/steps/__init__.py`.

## Merge algorithm (`project.merge`)

Assembles the full project in memory. Idempotent w.r.t. master-only vs.
already-merged input.

```
working = base project (context.project)
for each file in spec.files:
  aux = load aux file from disk            # full-skeleton project
  for each segment in file.segments:
    seg_ids = resolve_selector(aux.rows, segment.rows)
    if all seg_ids already present in working.rows:
        pass                               # already merged / full input: leave in place
    else:
        seg_rows = extract seg_ids from aux.rows, in order
        pos = resolve_anchor(working.rows, segment.anchor)
        insert seg_rows into working.rows at pos
```

Shared sections come from `base` (master wins). No files are written by this step.
Partial presence (some but not all of a segment's rows already present) is a lint
error.

## Split algorithm (`project.split`)

Persists canonical files from the full in-memory project. Overwrites master + all
aux files.

```
full = context.project
extracted = {}                             # file path -> list of rows
for each file, each segment:
  seg_rows = resolve+extract from full.rows (explicit IDs or slice A..B)
  validate anchor matches current position (warn on drift)
  extracted[file.path] += seg_rows
master_rows = [r for r in full.rows if r not in any extracted set]
write master file = skeleton(full, master_rows)
for each file: write file = skeleton(full, extracted[file.path])
```

`skeleton(full, rows)` returns a deep copy of `full` with `rows` replaced — every
written file is a complete, loadable project.

## Validation / lint

Run as a pre-flight in both steps and available via `--lint` on the CLI. Fails
fast with clear messages on:

- duplicate IDs across segments (a row assigned to more than one segment),
- overlapping ranges,
- a selector ID or range endpoint not found in the source,
- a range whose endpoints are not contiguous in the source,
- an anchor referencing a row that was itself extracted (anchors must be
  master-resident),
- an unknown anchor row,
- partial-presence during merge.

## `build.yaml` integration

Steps are placed by the maintainer wherever they belong in the pipeline. Typical
shape:

```yaml
input: "project-${version}.master.json"   # --copy-from lands the edited file here

steps:
  - name: "Assemble split files"
    uses: project.merge
    with: { config: split.yaml }
  # ... existing patches / media / balance / sort run on the full project ...
  - name: "Refresh canonical split files"
    uses: project.split
    with: { config: split.yaml }
  - name: "Save merged project"
    uses: save                            # writes full project-${version}.json for the viewer
```

### Contributor intake

- **Edited master or full file:** `--copy-from <download>` onto the input path, as
  today. `project.merge` presence-detection handles both cases.
- **Edited aux file:** drop the downloaded aux onto its `path` (rare, manual
  `cp`), then build normally. `project.merge` reads the updated aux; `project.split`
  rewrites all canonical files at the end.

## Constraints (recap)

1. Anchors must reference master-resident rows (enforced by lint).
2. Merge always places rows at the anchor, not their prior position; split warns
   on drift.
3. Shared-metadata edits must be made in the master; aux copies are dropped on
   merge.

## Out of scope

- Bidirectional live sync / conflict resolution between full and split forms.
- Splitting anything other than the top-level `rows` array (groups, pointTypes,
  and backpack are shared and master-owned).
- Marker-row / placeholder approaches (rejected in favor of the descriptor).
