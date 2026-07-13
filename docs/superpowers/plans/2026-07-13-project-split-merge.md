# Project Split / Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the large ICYOA project JSON into a canonical master file plus auxiliary files (each a full loadable project) that the build pipeline can merge back and re-split, driven by a `split.yaml` descriptor.

**Architecture:** Three layers mirroring the existing `sort`/`balance` feature — pure ops functions in `cyoa/ops/filesplit.py`, CLI wrappers in `cyoa/tools/filesplit_tools.py` (`project.split` / `project.merge`), and build steps in `cyoa/build/steps/filesplit.py` (`project.merge` / `project.split`). The descriptor declares aux files, each with row segments (contiguous range or explicit ID list) and an authoritative insertion anchor.

**Tech Stack:** Python 3.14, `uv`, `pyyaml`, `rich`, `pytest` (added by this plan). Ruff (2-space indent, double quotes).

## Global Constraints

- Python `>=3.14,<3.15`; managed with `uv` (`uv run`, `uv add`).
- Indent: 2 spaces. Quotes: double. Formatter/linter: Ruff (`uvx ruff format cyoa/`, `uvx ruff check cyoa/`).
- Ops layer (`cyoa/ops/`) is pure: no I/O, no console output. Tools/steps do I/O.
- `project.merge` presence-detects per segment: rows absent → insert from aux; all present → leave in place (idempotent for already-merged input); partial → error.
- `project.split` overwrites master **and all** aux files every run. Master wins for shared sections (`pointTypes`, `groups`, `backpack`, settings).
- Anchors are authoritative for placement and must reference master-resident rows.
- Written JSON files match the `save` step style: `json.dump(..., indent=2, ensure_ascii=False)` plus a trailing newline.
- Namespace is `project.` (avoids the existing `merge` tool).

---

### Task 1: pytest setup + descriptor model & parsing

**Files:**
- Modify: `pyproject.toml` (add pytest dev dependency, via `uv add`)
- Create: `cyoa/ops/filesplit.py`
- Create: `tests/__init__.py`
- Create: `tests/ops/__init__.py`
- Create: `tests/ops/test_filesplit.py`

**Interfaces:**
- Produces:
  - `Anchor` dataclass: `after: str|None`, `before: str|None`, `between: tuple[str,str]|None`, `at_start: bool`, `at_end: bool`; classmethod `from_dict(d: dict) -> Anchor`.
  - `Segment` dataclass: `rows: list[str]`, `anchor: Anchor`.
  - `FileSpec` dataclass: `name: str`, `path: str`, `segments: list[Segment]`.
  - `SplitSpec` dataclass: `version: int`, `master: str`, `files: list[FileSpec]`.
  - `parse_descriptor(data: dict) -> SplitSpec` — raises `ValueError` on malformed input.

- [ ] **Step 1: Add pytest as a dev dependency**

Run: `uv add --dev pytest`
Expected: `pyproject.toml` gains a `[dependency-groups]`/dev `pytest` entry and `uv.lock` updates.

- [ ] **Step 2: Write the failing test**

Create `tests/__init__.py` (empty) and `tests/ops/__init__.py` (empty), then `tests/ops/test_filesplit.py`:

```python
from cyoa.ops.filesplit import (
  Anchor,
  FileSpec,
  Segment,
  SplitSpec,
  parse_descriptor,
)


def test_parse_descriptor_full():
  data = {
    "version": 1,
    "master": "project-v17.master.json",
    "files": [
      {
        "name": "entity",
        "path": "project-v17.entity.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}},
          {"rows": ["06d7", "iu9w"], "anchor": {"before": "PhysP--Title"}},
        ],
      }
    ],
  }
  spec = parse_descriptor(data)
  assert isinstance(spec, SplitSpec)
  assert spec.version == 1
  assert spec.master == "project-v17.master.json"
  assert len(spec.files) == 1
  f = spec.files[0]
  assert isinstance(f, FileSpec)
  assert f.name == "entity"
  assert f.path == "project-v17.entity.json"
  assert len(f.segments) == 2
  seg0 = f.segments[0]
  assert isinstance(seg0, Segment)
  assert seg0.rows == ["i1p0", "...", "jsvj"]
  assert seg0.anchor.after == "114m"
  assert f.segments[1].anchor.before == "PhysP--Title"


def test_anchor_between_and_boundaries():
  assert Anchor.from_dict({"between": ["a", "b"]}).between == ("a", "b")
  assert Anchor.from_dict({"at_start": True}).at_start is True
  assert Anchor.from_dict({"at_end": True}).at_end is True


def test_parse_descriptor_rejects_bad_version():
  import pytest

  with pytest.raises(ValueError):
    parse_descriptor({"version": 2, "master": "m.json", "files": []})


def test_parse_descriptor_rejects_multi_anchor():
  import pytest

  with pytest.raises(ValueError):
    Anchor.from_dict({"after": "a", "before": "b"})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `cyoa.ops.filesplit`.

- [ ] **Step 4: Write minimal implementation**

Create `cyoa/ops/filesplit.py`:

```python
"""File splitting/merging operations for CYOA project files.

Pure functions (no I/O, no console output) that split a full project into a
master file plus auxiliary files, and merge them back. Driven by a declarative
descriptor (see split.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass, field

RANGE_SENTINEL = "..."


@dataclass
class Anchor:
  """Where a segment's rows are inserted into the master, authoritatively."""

  after: str | None = None
  before: str | None = None
  between: tuple[str, str] | None = None
  at_start: bool = False
  at_end: bool = False

  @classmethod
  def from_dict(cls, data: dict) -> "Anchor":
    if not isinstance(data, dict):
      raise ValueError(f"anchor must be a mapping, got {type(data).__name__}")

    keys = {k for k, v in data.items() if v not in (None, False)}
    known = {"after", "before", "between", "at_start", "at_end"}
    unknown = keys - known
    if unknown:
      raise ValueError(f"unknown anchor key(s): {sorted(unknown)}")
    if len(keys) != 1:
      raise ValueError(
        f"anchor must specify exactly one of {sorted(known)}, got {sorted(keys)}"
      )

    between = None
    if "between" in data and data["between"]:
      pair = data["between"]
      if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError("anchor 'between' must be a [before, after] pair")
      between = (pair[0], pair[1])

    return cls(
      after=data.get("after"),
      before=data.get("before"),
      between=between,
      at_start=bool(data.get("at_start", False)),
      at_end=bool(data.get("at_end", False)),
    )


@dataclass
class Segment:
  """A set of rows extracted together and placed at one anchor."""

  rows: list[str]
  anchor: Anchor


@dataclass
class FileSpec:
  """One auxiliary file and the segments it owns."""

  name: str
  path: str
  segments: list[Segment] = field(default_factory=list)


@dataclass
class SplitSpec:
  """Parsed split.yaml descriptor."""

  version: int
  master: str
  files: list[FileSpec] = field(default_factory=list)


def parse_descriptor(data: dict) -> SplitSpec:
  """Parse and validate the structure of a split descriptor dict."""
  if not isinstance(data, dict):
    raise ValueError("descriptor must be a mapping")

  version = data.get("version")
  if version != 1:
    raise ValueError(f"unsupported descriptor version: {version!r} (expected 1)")

  master = data.get("master")
  if not isinstance(master, str) or not master:
    raise ValueError("descriptor must specify 'master' as a non-empty string")

  files_data = data.get("files", [])
  if not isinstance(files_data, list):
    raise ValueError("'files' must be a list")

  files: list[FileSpec] = []
  for i, fd in enumerate(files_data):
    if not isinstance(fd, dict):
      raise ValueError(f"files[{i}] must be a mapping")
    name = fd.get("name")
    if not isinstance(name, str) or not name:
      raise ValueError(f"files[{i}] must have a non-empty 'name'")
    path = fd.get("path")
    if not isinstance(path, str) or not path:
      raise ValueError(f"file '{name}' must have a non-empty 'path'")

    segments_data = fd.get("segments", [])
    if not isinstance(segments_data, list):
      raise ValueError(f"file '{name}' 'segments' must be a list")

    segments: list[Segment] = []
    for j, sd in enumerate(segments_data):
      if not isinstance(sd, dict):
        raise ValueError(f"file '{name}' segments[{j}] must be a mapping")
      rows = sd.get("rows")
      if not isinstance(rows, list) or not rows:
        raise ValueError(
          f"file '{name}' segments[{j}] must have a non-empty 'rows' list"
        )
      if not all(isinstance(r, str) for r in rows):
        raise ValueError(f"file '{name}' segments[{j}] 'rows' must be strings")
      anchor = Anchor.from_dict(sd.get("anchor", {}))
      segments.append(Segment(rows=rows, anchor=anchor))

    files.append(FileSpec(name=name, path=path, segments=segments))

  return SplitSpec(version=version, master=master, files=files)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add pyproject.toml uv.lock cyoa/ops/filesplit.py tests/
git commit -m "feat(filesplit): descriptor model and parsing + pytest setup"
```

---

### Task 2: selector, anchor, and skeleton resolution helpers

**Files:**
- Modify: `cyoa/ops/filesplit.py`
- Test: `tests/ops/test_filesplit.py`

**Interfaces:**
- Consumes: `Anchor`, `Segment`, `RANGE_SENTINEL` from Task 1.
- Produces:
  - `resolve_selector(rows: list[dict], selector: list[str]) -> list[str]` — ordered row IDs; `[A, "...", B]` → contiguous inclusive slice IDs; explicit list → the IDs (each must exist). Raises `ValueError`.
  - `resolve_anchor(rows: list[dict], anchor: Anchor) -> int` — insertion index. Raises `ValueError` if anchor rows missing or `between` pair not adjacent.
  - `skeleton(full: dict, rows: list[dict]) -> dict` — deep copy of `full` with `rows` replaced.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ops/test_filesplit.py`:

```python
from cyoa.ops.filesplit import resolve_anchor, resolve_selector, skeleton


def _rows(*ids):
  return [{"id": i, "title": i, "objects": []} for i in ids]


def test_resolve_selector_range_inclusive():
  rows = _rows("a", "b", "c", "d", "e")
  assert resolve_selector(rows, ["b", "...", "d"]) == ["b", "c", "d"]


def test_resolve_selector_explicit_list_preserves_order():
  rows = _rows("a", "b", "c", "d")
  assert resolve_selector(rows, ["c", "a"]) == ["c", "a"]


def test_resolve_selector_missing_id_raises():
  import pytest

  with pytest.raises(ValueError):
    resolve_selector(_rows("a", "b"), ["a", "zz"])


def test_resolve_selector_bad_range_shape_raises():
  import pytest

  with pytest.raises(ValueError):
    resolve_selector(_rows("a", "b", "c"), ["a", "...", "b", "...", "c"])


def test_resolve_anchor_after():
  rows = _rows("a", "b", "c")
  assert resolve_anchor(rows, Anchor(after="a")) == 1


def test_resolve_anchor_before():
  rows = _rows("a", "b", "c")
  assert resolve_anchor(rows, Anchor(before="c")) == 2


def test_resolve_anchor_between_adjacent():
  rows = _rows("a", "b", "c")
  assert resolve_anchor(rows, Anchor(between=("a", "b"))) == 1


def test_resolve_anchor_between_not_adjacent_raises():
  import pytest

  with pytest.raises(ValueError):
    resolve_anchor(_rows("a", "b", "c"), Anchor(between=("a", "c")))


def test_resolve_anchor_boundaries():
  rows = _rows("a", "b")
  assert resolve_anchor(rows, Anchor(at_start=True)) == 0
  assert resolve_anchor(rows, Anchor(at_end=True)) == 2


def test_resolve_anchor_missing_raises():
  import pytest

  with pytest.raises(ValueError):
    resolve_anchor(_rows("a"), Anchor(after="zz"))


def test_skeleton_replaces_rows_and_deep_copies():
  full = {
    "rows": _rows("a", "b", "c"),
    "pointTypes": [{"id": "rm"}],
    "groups": [{"id": "g1"}],
  }
  sk = skeleton(full, _rows("a"))
  assert [r["id"] for r in sk["rows"]] == ["a"]
  assert sk["pointTypes"] == full["pointTypes"]
  sk["pointTypes"][0]["id"] = "changed"
  assert full["pointTypes"][0]["id"] == "rm"  # deep copied
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: FAIL — `ImportError` for `resolve_anchor`, `resolve_selector`, `skeleton`.

- [ ] **Step 3: Implement the helpers**

Append to `cyoa/ops/filesplit.py`:

```python
import copy


def _index_of(rows: list[dict], row_id: str) -> int:
  for i, r in enumerate(rows):
    if r.get("id") == row_id:
      return i
  raise ValueError(f"row id {row_id!r} not found")


def resolve_selector(rows: list[dict], selector: list[str]) -> list[str]:
  """Resolve a segment selector to an ordered list of row IDs.

  ``[A, "...", B]`` is the contiguous inclusive slice from A to B as it appears
  in *rows*. Any other list is treated as explicit IDs (each must exist).
  """
  if RANGE_SENTINEL in selector:
    if len(selector) != 3 or selector[1] != RANGE_SENTINEL:
      raise ValueError(
        f"range selector must be [START, '{RANGE_SENTINEL}', END], got {selector!r}"
      )
    start_id, _, end_id = selector
    start = _index_of(rows, start_id)
    end = _index_of(rows, end_id)
    if start > end:
      raise ValueError(
        f"range selector start {start_id!r} comes after end {end_id!r}"
      )
    return [r["id"] for r in rows[start : end + 1]]

  # Explicit list: validate existence, preserve given order.
  existing = {r.get("id") for r in rows}
  for rid in selector:
    if rid not in existing:
      raise ValueError(f"row id {rid!r} not found")
  return list(selector)


def resolve_anchor(rows: list[dict], anchor: Anchor) -> int:
  """Return the insertion index into *rows* for an anchor."""
  if anchor.at_start:
    return 0
  if anchor.at_end:
    return len(rows)
  if anchor.after is not None:
    return _index_of(rows, anchor.after) + 1
  if anchor.before is not None:
    return _index_of(rows, anchor.before)
  if anchor.between is not None:
    left, right = anchor.between
    li = _index_of(rows, left)
    ri = _index_of(rows, right)
    if ri != li + 1:
      raise ValueError(
        f"anchor between [{left!r}, {right!r}] are not adjacent in the project"
      )
    return li + 1
  raise ValueError("anchor specifies no position")


def skeleton(full: dict, rows: list[dict]) -> dict:
  """Deep copy of *full* with its 'rows' replaced by *rows*."""
  result = copy.deepcopy(full)
  result["rows"] = rows
  return result
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/ops/filesplit.py tests/ops/test_filesplit.py
git commit -m "feat(filesplit): selector, anchor, and skeleton helpers"
```

---

### Task 3: `validate_spec` (lint)

**Files:**
- Modify: `cyoa/ops/filesplit.py`
- Test: `tests/ops/test_filesplit.py`

**Interfaces:**
- Consumes: `SplitSpec`, `resolve_selector` from earlier tasks.
- Produces: `validate_spec(project: dict, spec: SplitSpec) -> list[str]` — returns human-readable issue strings (empty = valid). Validates against a **full** project (all rows present). Checks: every selector resolves (endpoints exist / range contiguous); no row assigned to more than one segment; every anchor row exists and is **not** itself extracted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ops/test_filesplit.py`:

```python
from cyoa.ops.filesplit import validate_spec


def _full(*ids):
  return {
    "rows": _rows(*ids),
    "pointTypes": [{"id": "rm"}],
    "groups": [{"id": "g1"}],
    "backpack": [],
  }


def _spec(files):
  return parse_descriptor({"version": 1, "master": "m.json", "files": files})


def test_validate_spec_ok():
  project = _full("114m", "i1p0", "x1", "jsvj", "gra1", "PhysP--Title")
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}}
        ],
      }
    ]
  )
  assert validate_spec(project, spec) == []


def test_validate_spec_flags_duplicate_row_in_two_segments():
  project = _full("a", "b", "c", "d")
  spec = _spec(
    [
      {
        "name": "f",
        "path": "f.json",
        "segments": [
          {"rows": ["b"], "anchor": {"after": "a"}},
          {"rows": ["b"], "anchor": {"before": "d"}},
        ],
      }
    ]
  )
  issues = validate_spec(project, spec)
  assert any("b" in i and "more than one segment" in i for i in issues)


def test_validate_spec_flags_anchor_that_is_extracted():
  project = _full("a", "b", "c")
  spec = _spec(
    [
      {
        "name": "f",
        "path": "f.json",
        "segments": [{"rows": ["b"], "anchor": {"after": "b"}}],
      }
    ]
  )
  issues = validate_spec(project, spec)
  assert any("anchor" in i and "b" in i for i in issues)


def test_validate_spec_flags_unknown_selector_id():
  project = _full("a", "b")
  spec = _spec(
    [
      {
        "name": "f",
        "path": "f.json",
        "segments": [{"rows": ["zz"], "anchor": {"after": "a"}}],
      }
    ]
  )
  assert validate_spec(project, spec)  # non-empty
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ops/test_filesplit.py -k validate -v`
Expected: FAIL — `ImportError` for `validate_spec`.

- [ ] **Step 3: Implement `validate_spec`**

Append to `cyoa/ops/filesplit.py`:

```python
def _anchor_ids(anchor: Anchor) -> list[str]:
  ids: list[str] = []
  if anchor.after is not None:
    ids.append(anchor.after)
  if anchor.before is not None:
    ids.append(anchor.before)
  if anchor.between is not None:
    ids.extend(anchor.between)
  return ids


def validate_spec(project: dict, spec: SplitSpec) -> list[str]:
  """Validate a spec against a full project. Returns a list of issue strings."""
  issues: list[str] = []
  rows = project.get("rows", [])
  existing = {r.get("id") for r in rows}

  assigned: dict[str, str] = {}  # row id -> "file/segment" label
  extracted: set[str] = set()

  for file in spec.files:
    for si, segment in enumerate(file.segments):
      label = f"{file.name}/segment[{si}]"
      try:
        seg_ids = resolve_selector(rows, segment.rows)
      except ValueError as exc:
        issues.append(f"{label}: {exc}")
        continue

      for rid in seg_ids:
        if rid in assigned:
          issues.append(
            f"row {rid!r} is assigned to more than one segment "
            f"({assigned[rid]} and {label})"
          )
        else:
          assigned[rid] = label
        extracted.add(rid)

  # Anchors must exist and must be master-resident (not extracted).
  for file in spec.files:
    for si, segment in enumerate(file.segments):
      label = f"{file.name}/segment[{si}]"
      for aid in _anchor_ids(segment.anchor):
        if aid not in existing:
          issues.append(f"{label}: anchor row {aid!r} not found in project")
        elif aid in extracted:
          issues.append(
            f"{label}: anchor row {aid!r} is itself extracted "
            f"(anchors must be master-resident)"
          )

  return issues
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/ops/filesplit.py tests/ops/test_filesplit.py
git commit -m "feat(filesplit): validate_spec lint checks"
```

---

### Task 4: `merge_project`

**Files:**
- Modify: `cyoa/ops/filesplit.py`
- Test: `tests/ops/test_filesplit.py`

**Interfaces:**
- Consumes: `SplitSpec`, `resolve_selector`, `resolve_anchor` from earlier tasks.
- Produces:
  - `MergeResult` dataclass: `project: dict`, `inserted: list[str]` (segment labels inserted), `skipped: list[str]` (labels already present).
  - `merge_project(base: dict, aux_projects: dict[str, dict], spec: SplitSpec) -> MergeResult` — returns a new project (does not mutate `base`). `aux_projects` maps each aux `path` to its loaded project dict. Per segment: resolve IDs from the aux; if all present in base → skip; if none present → insert aux rows at anchor; if partial → raise `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ops/test_filesplit.py`:

```python
from cyoa.ops.filesplit import MergeResult, merge_project


def test_merge_inserts_absent_segment_at_anchor():
  master = _full("114m", "gra1", "PhysP--Title")
  aux = skeleton(master, _rows("i1p0", "x1", "jsvj"))
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}}
        ],
      }
    ]
  )
  result = merge_project(master, {"e.json": aux}, spec)
  assert isinstance(result, MergeResult)
  assert [r["id"] for r in result.project["rows"]] == [
    "114m",
    "i1p0",
    "x1",
    "jsvj",
    "gra1",
    "PhysP--Title",
  ]
  assert result.inserted and not result.skipped
  # base not mutated
  assert [r["id"] for r in master["rows"]] == ["114m", "gra1", "PhysP--Title"]


def test_merge_skips_already_present_segment():
  # base is already the full merged project
  full = _full("114m", "i1p0", "jsvj", "gra1")
  aux = skeleton(full, _rows("i1p0", "jsvj"))
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}}
        ],
      }
    ]
  )
  result = merge_project(full, {"e.json": aux}, spec)
  assert [r["id"] for r in result.project["rows"]] == [
    "114m",
    "i1p0",
    "jsvj",
    "gra1",
  ]
  assert result.skipped and not result.inserted


def test_merge_partial_presence_raises():
  import pytest

  base = _full("114m", "i1p0", "gra1")  # has i1p0 but not jsvj
  aux = skeleton(base, _rows("i1p0", "jsvj"))
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [{"rows": ["i1p0", "jsvj"], "anchor": {"after": "114m"}}],
      }
    ]
  )
  with pytest.raises(ValueError):
    merge_project(base, {"e.json": aux}, spec)


def test_merge_master_wins_shared_sections():
  master = _full("a", "b")
  master["pointTypes"] = [{"id": "rm", "suffix": "MASTER"}]
  aux = skeleton(master, _rows("x"))
  aux["pointTypes"] = [{"id": "rm", "suffix": "AUX"}]
  spec = _spec(
    [
      {
        "name": "f",
        "path": "f.json",
        "segments": [{"rows": ["x"], "anchor": {"after": "a"}}],
      }
    ]
  )
  result = merge_project(master, {"f.json": aux}, spec)
  assert result.project["pointTypes"][0]["suffix"] == "MASTER"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ops/test_filesplit.py -k merge -v`
Expected: FAIL — `ImportError` for `MergeResult`, `merge_project`.

- [ ] **Step 3: Implement `merge_project`**

Append to `cyoa/ops/filesplit.py`:

```python
@dataclass
class MergeResult:
  """Outcome of merging aux projects into a base project."""

  project: dict
  inserted: list[str] = field(default_factory=list)
  skipped: list[str] = field(default_factory=list)


def merge_project(
  base: dict, aux_projects: dict[str, dict], spec: SplitSpec
) -> MergeResult:
  """Assemble the full project from *base* + aux files.

  Returns a new project (base is not mutated). Idempotent by row presence:
  a segment already present in base is left in place; an absent segment is
  inserted at its anchor from the corresponding aux file.
  """
  working = copy.deepcopy(base)
  inserted: list[str] = []
  skipped: list[str] = []

  for file in spec.files:
    if file.path not in aux_projects:
      raise ValueError(f"aux project not provided for path {file.path!r}")
    aux_rows = aux_projects[file.path].get("rows", [])

    for si, segment in enumerate(file.segments):
      label = f"{file.name}/segment[{si}]"
      seg_ids = resolve_selector(aux_rows, segment.rows)

      present = {r.get("id") for r in working["rows"]}
      present_ids = [rid for rid in seg_ids if rid in present]

      if len(present_ids) == len(seg_ids):
        skipped.append(label)
        continue
      if present_ids:
        raise ValueError(
          f"{label}: partially present in base "
          f"({len(present_ids)}/{len(seg_ids)} rows) — ambiguous merge state"
        )

      by_id = {r.get("id"): r for r in aux_rows}
      seg_rows = [copy.deepcopy(by_id[rid]) for rid in seg_ids]
      pos = resolve_anchor(working["rows"], segment.anchor)
      working["rows"][pos:pos] = seg_rows
      inserted.append(label)

  return MergeResult(project=working, inserted=inserted, skipped=skipped)
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/ops/filesplit.py tests/ops/test_filesplit.py
git commit -m "feat(filesplit): merge_project assembly"
```

---

### Task 5: `split_project`

**Files:**
- Modify: `cyoa/ops/filesplit.py`
- Test: `tests/ops/test_filesplit.py`

**Interfaces:**
- Consumes: `SplitSpec`, `resolve_selector`, `resolve_anchor`, `skeleton` from earlier tasks.
- Produces:
  - `SplitResult` dataclass: `master: dict`, `aux: dict[str, dict]` (path → project), `drift_warnings: list[str]`.
  - `split_project(full: dict, spec: SplitSpec) -> SplitResult` — extracts each segment's rows from `full`, builds master (full minus all extracted) and each aux (skeleton + its rows), and emits a drift warning when a segment's current position does not match its anchor.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ops/test_filesplit.py`:

```python
from cyoa.ops.filesplit import SplitResult, split_project


def test_split_extracts_master_and_aux():
  # Row order matches the anchors: 06d7/iu9w sit before PhysP--Title.
  full = _full("114m", "i1p0", "x1", "jsvj", "gra1", "06d7", "iu9w", "PhysP--Title")
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}},
          {"rows": ["06d7", "iu9w"], "anchor": {"before": "PhysP--Title"}},
        ],
      }
    ]
  )
  result = split_project(full, spec)
  assert isinstance(result, SplitResult)
  assert [r["id"] for r in result.master["rows"]] == [
    "114m",
    "gra1",
    "PhysP--Title",
  ]
  assert [r["id"] for r in result.aux["e.json"]["rows"]] == [
    "i1p0",
    "x1",
    "jsvj",
    "06d7",
    "iu9w",
  ]
  # aux carries full shared skeleton
  assert result.aux["e.json"]["pointTypes"] == full["pointTypes"]


def test_split_round_trips_with_merge():
  # Row order matches the anchors so merge reproduces `full` exactly.
  full = _full("114m", "i1p0", "jsvj", "gra1", "06d7", "iu9w", "PhysP--Title")
  spec = _spec(
    [
      {
        "name": "entity",
        "path": "e.json",
        "segments": [
          {"rows": ["i1p0", "...", "jsvj"], "anchor": {"after": "114m"}},
          {"rows": ["06d7", "iu9w"], "anchor": {"before": "PhysP--Title"}},
        ],
      }
    ]
  )
  sr = split_project(full, spec)
  mr = merge_project(sr.master, sr.aux, spec)
  assert [r["id"] for r in mr.project["rows"]] == [
    r["id"] for r in full["rows"]
  ]


def test_split_warns_on_drift():
  # segment [c] declared after 'a', but c currently sits after 'b'
  full = _full("a", "b", "c", "d")
  spec = _spec(
    [
      {
        "name": "f",
        "path": "f.json",
        "segments": [{"rows": ["c"], "anchor": {"after": "a"}}],
      }
    ]
  )
  result = split_project(full, spec)
  assert result.drift_warnings
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ops/test_filesplit.py -k split_ -v`
Expected: FAIL — `ImportError` for `SplitResult`, `split_project`.

- [ ] **Step 3: Implement `split_project`**

Append to `cyoa/ops/filesplit.py`:

```python
@dataclass
class SplitResult:
  """Outcome of splitting a full project into master + aux files."""

  master: dict
  aux: dict[str, dict] = field(default_factory=dict)
  drift_warnings: list[str] = field(default_factory=list)


def split_project(full: dict, spec: SplitSpec) -> SplitResult:
  """Split *full* into a master project and one project per aux file.

  Every produced file is a full-skeleton project. A drift warning is emitted
  when a segment's rows do not currently sit at its declared anchor.
  """
  rows = full.get("rows", [])
  by_id = {r.get("id"): r for r in rows}
  index_of = {r.get("id"): i for i, r in enumerate(rows)}

  extracted_ids: set[str] = set()
  aux_rows: dict[str, list[dict]] = {}
  seg_records: list[tuple[str, "Anchor", str]] = []  # (label, anchor, first row id)

  # Pass 1: resolve every segment, collect extracted rows and their first index.
  for file in spec.files:
    file_rows: list[dict] = []
    for si, segment in enumerate(file.segments):
      label = f"{file.name}/segment[{si}]"
      seg_ids = resolve_selector(rows, segment.rows)
      seg_records.append((label, segment.anchor, seg_ids[0]))
      for rid in seg_ids:
        extracted_ids.add(rid)
        file_rows.append(copy.deepcopy(by_id[rid]))
    aux_rows[file.path] = file_rows

  master_rows = [
    copy.deepcopy(r) for r in rows if r.get("id") not in extracted_ids
  ]

  # Pass 2: drift = does the anchor position in the finished master match the
  # segment's current position (count of master-resident rows before it)?
  drift_warnings: list[str] = []
  for label, anchor, first_id in seg_records:
    kept_before = sum(
      1 for r in rows[: index_of[first_id]] if r.get("id") not in extracted_ids
    )
    try:
      anchor_pos = resolve_anchor(master_rows, anchor)
    except ValueError as exc:
      drift_warnings.append(f"{label}: anchor cannot be resolved in master: {exc}")
      continue
    if anchor_pos != kept_before:
      drift_warnings.append(
        f"{label}: rows are not at their anchor "
        f"(anchor index {anchor_pos}, current master position {kept_before})"
      )

  master = skeleton(full, master_rows)
  aux = {path: skeleton(full, rws) for path, rws in aux_rows.items()}

  return SplitResult(master=master, aux=aux, drift_warnings=drift_warnings)
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/ops/test_filesplit.py -v`
Expected: PASS (all ops tests, including the round-trip).

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/ops/filesplit.py tests/ops/test_filesplit.py
git commit -m "feat(filesplit): split_project with drift detection"
```

---

### Task 6: CLI tools `project.split` and `project.merge`

**Files:**
- Create: `cyoa/tools/filesplit_tools.py`
- Modify: `cyoa/tools/client.py:5-30` (import + register)
- Test: `tests/tools/__init__.py` (create empty), `tests/tools/test_filesplit_tools.py`

**Interfaces:**
- Consumes: all ops functions; `ToolBase`, `ProjectUtilsMixin`, `console` from `cyoa.tools.lib`; `parse_descriptor`, `validate_spec`, `merge_project`, `split_project`.
- Produces:
  - `FileSplitTool` (`name = "project.split"`), `FileMergeTool` (`name = "project.merge"`), `TOOLS = (FileSplitTool, FileMergeTool)`.
  - Shared JSON writer semantics: `indent=2, ensure_ascii=False`, trailing newline.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/__init__.py` (empty) and `tests/tools/test_filesplit_tools.py`:

```python
import json
from pathlib import Path

import yaml

from cyoa.tools.filesplit_tools import FileMergeTool, FileSplitTool


def _write_json(path: Path, data: dict):
  path.write_text(json.dumps(data), encoding="utf-8")


def _project(ids):
  return {
    "rows": [{"id": i, "title": i, "objects": []} for i in ids],
    "pointTypes": [{"id": "rm"}],
    "groups": [{"id": "g1"}],
    "backpack": [],
  }


def _descriptor(path: Path):
  path.write_text(
    yaml.safe_dump(
      {
        "version": 1,
        "master": "master.json",
        "files": [
          {
            "name": "entity",
            "path": "entity.json",
            "segments": [
              {"rows": ["b", "...", "c"], "anchor": {"after": "a"}}
            ],
          }
        ],
      }
    ),
    encoding="utf-8",
  )


def test_split_tool_writes_master_and_aux(tmp_path):
  full = tmp_path / "full.json"
  _write_json(full, _project(["a", "b", "c", "d"]))
  cfg = tmp_path / "split.yaml"
  _descriptor(cfg)

  args = type(
    "Args",
    (),
    {"project_file": full, "config": cfg, "write": True, "lint": False},
  )()
  FileSplitTool().run(args)

  master = json.loads((tmp_path / "master.json").read_text())
  aux = json.loads((tmp_path / "entity.json").read_text())
  assert [r["id"] for r in master["rows"]] == ["a", "d"]
  assert [r["id"] for r in aux["rows"]] == ["b", "c"]


def test_merge_tool_writes_full(tmp_path):
  _write_json(tmp_path / "master.json", _project(["a", "d"]))
  _write_json(tmp_path / "entity.json", _project(["b", "c"]))
  cfg = tmp_path / "split.yaml"
  _descriptor(cfg)
  out = tmp_path / "out.json"

  args = type(
    "Args",
    (),
    {
      "master_file": tmp_path / "master.json",
      "config": cfg,
      "output": out,
      "write": True,
      "lint": False,
    },
  )()
  FileMergeTool().run(args)

  merged = json.loads(out.read_text())
  assert [r["id"] for r in merged["rows"]] == ["a", "b", "c", "d"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/tools/test_filesplit_tools.py -v`
Expected: FAIL — `ModuleNotFoundError` for `cyoa.tools.filesplit_tools`.

- [ ] **Step 3: Implement the CLI tools**

Create `cyoa/tools/filesplit_tools.py`:

```python
"""CLI tools for splitting/merging a project across master + aux files."""

import json
from pathlib import Path

import yaml

from cyoa.ops.filesplit import (
  merge_project,
  parse_descriptor,
  split_project,
  validate_spec,
)
from cyoa.tools.lib import console, ProjectUtilsMixin, ToolBase


def _load_json(path: Path) -> dict:
  with path.open("r", encoding="utf-8") as fd:
    return json.load(fd)


def _write_json(path: Path, data: dict):
  with path.open("w", encoding="utf-8") as fd:
    json.dump(data, fd, indent=2, ensure_ascii=False)
    fd.write("\n")


def _load_spec(config: Path):
  with config.open("r", encoding="utf-8") as fd:
    return parse_descriptor(yaml.safe_load(fd))


class FileSplitTool(ToolBase, ProjectUtilsMixin):
  name = "project.split"

  @classmethod
  def setup_parser(cls, parent):
    parser = parent.add_parser(
      cls.name, help="Split a full project into master + aux files"
    )
    parser.add_argument("--project", dest="project_file", type=Path, required=True)
    parser.add_argument("--config", dest="config", type=Path, required=True)
    parser.add_argument("--write", dest="write", action="store_true")
    parser.add_argument("--lint", dest="lint", action="store_true")

  def run(self, args):
    spec = _load_spec(args.config)
    full = _load_json(args.project_file)

    issues = validate_spec(full, spec)
    for issue in issues:
      console.print(f"[red]lint:[/red] {issue}")
    if issues:
      console.print(f"[red]{len(issues)} lint issue(s); aborting.[/red]")
      return
    if args.lint:
      console.print("[green]Descriptor is valid.[/green]")
      return

    result = split_project(full, spec)
    for warning in result.drift_warnings:
      console.print(f"[yellow]drift:[/yellow] {warning}")

    work_dir = args.config.parent
    master_path = work_dir / spec.master
    console.print(
      f"master [b]{master_path.name}[/]: {len(result.master['rows'])} rows"
    )
    for path, aux in result.aux.items():
      console.print(f"aux [b]{path}[/]: {len(aux['rows'])} rows")

    if args.write:
      _write_json(master_path, result.master)
      for path, aux in result.aux.items():
        _write_json(work_dir / path, aux)
      console.print("[green]Wrote master + aux files.[/green]")
    else:
      console.print("[dim]Dry run (pass --write to save).[/dim]")


class FileMergeTool(ToolBase, ProjectUtilsMixin):
  name = "project.merge"

  @classmethod
  def setup_parser(cls, parent):
    parser = parent.add_parser(
      cls.name, help="Merge master + aux files into a full project"
    )
    parser.add_argument("--master", dest="master_file", type=Path, required=True)
    parser.add_argument("--config", dest="config", type=Path, required=True)
    parser.add_argument("--output", dest="output", type=Path, required=True)
    parser.add_argument("--write", dest="write", action="store_true")
    parser.add_argument("--lint", dest="lint", action="store_true")

  def run(self, args):
    spec = _load_spec(args.config)
    base = _load_json(args.master_file)
    work_dir = args.config.parent

    aux_projects = {
      file.path: _load_json(work_dir / file.path) for file in spec.files
    }

    if args.lint:
      console.print("[green]Descriptor loaded; aux files present.[/green]")
      return

    result = merge_project(base, aux_projects, spec)
    for label in result.inserted:
      console.print(f"[green]inserted[/green] {label}")
    for label in result.skipped:
      console.print(f"[dim]already present[/dim] {label}")
    console.print(f"merged: {len(result.project['rows'])} rows")

    if args.write:
      _write_json(args.output, result.project)
      console.print(f"[green]Wrote {args.output}[/green]")
    else:
      console.print("[dim]Dry run (pass --write to save).[/dim]")


TOOLS = (FileSplitTool, FileMergeTool)
```

- [ ] **Step 4: Register the tools in the client**

In `cyoa/tools/client.py`, add `filesplit_tools` to the imports from `cyoa.tools` (the `from cyoa.tools import (...)` block, alongside `merge_tools`):

```python
from cyoa.tools import (
  project_tools,
  row_tools,
  object_tools,
  media_tools,
  merge_tools,
  filesplit_tools,
  md_tools,
  style_tools,
  build,
  scripts,
  graph_tools,
)
```

And add to the `TOOLS` tuple (after `*merge_tools.TOOLS,`):

```python
  *merge_tools.TOOLS,
  *filesplit_tools.TOOLS,
```

- [ ] **Step 5: Run tests and smoke-test the CLI**

Run: `uv run pytest tests/tools/test_filesplit_tools.py -v`
Expected: PASS (2 tests).

Run: `uv run cyoa project.split -h` and `uv run cyoa project.merge -h`
Expected: both print help with their flags (no traceback), confirming registration.

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/tools/filesplit_tools.py cyoa/tools/client.py tests/tools/
git commit -m "feat(filesplit): project.split and project.merge CLI tools"
```

---

### Task 7: Build steps `project.merge` and `project.split`

**Files:**
- Create: `cyoa/build/steps/filesplit.py`
- Modify: `cyoa/build/steps/__init__.py` (import + register)
- Test: `tests/build/__init__.py` (create empty), `tests/build/test_filesplit_steps.py`

**Interfaces:**
- Consumes: `StepHandler`, `StepResult` from `cyoa.build.registry`; `BuildContext` shape (`context.project`, `context.work_dir`, `context.console`); ops `parse_descriptor`, `merge_project`, `split_project`, `validate_spec`.
- Produces: `MergeStep` (`step_type = "project.merge"`) and `SplitStep` (`step_type = "project.split"`), both reading `params["config"]` relative to `context.work_dir`. `MergeStep` mutates `context.project` in place (replaces its contents with the merged project). `SplitStep` writes master + aux files to disk from `context.project`.

- [ ] **Step 1: Write the failing test**

Create `tests/build/__init__.py` (empty) and `tests/build/test_filesplit_steps.py`:

```python
import json
from pathlib import Path

import yaml
from rich.console import Console

from cyoa.build.context import BuildContext
from cyoa.build.steps.filesplit import MergeStep, SplitStep


def _project(ids):
  return {
    "rows": [{"id": i, "title": i, "objects": []} for i in ids],
    "pointTypes": [{"id": "rm"}],
    "groups": [{"id": "g1"}],
    "backpack": [],
  }


def _descriptor(path: Path):
  path.write_text(
    yaml.safe_dump(
      {
        "version": 1,
        "master": "master.json",
        "files": [
          {
            "name": "entity",
            "path": "entity.json",
            "segments": [
              {"rows": ["b", "...", "c"], "anchor": {"after": "a"}}
            ],
          }
        ],
      }
    ),
    encoding="utf-8",
  )


def _context(tmp_path, project):
  return BuildContext(
    project=project,
    vars={},
    input_path=tmp_path / "master.json",
    work_dir=tmp_path,
    console=Console(),
  )


def test_merge_step_assembles_project(tmp_path):
  (tmp_path / "entity.json").write_text(
    json.dumps(_project(["b", "c"])), encoding="utf-8"
  )
  _descriptor(tmp_path / "split.yaml")
  ctx = _context(tmp_path, _project(["a", "d"]))

  result = MergeStep().execute(ctx, {"config": "split.yaml"})
  assert result.success
  assert [r["id"] for r in ctx.project["rows"]] == ["a", "b", "c", "d"]


def test_split_step_writes_files(tmp_path):
  _descriptor(tmp_path / "split.yaml")
  ctx = _context(tmp_path, _project(["a", "b", "c", "d"]))

  result = SplitStep().execute(ctx, {"config": "split.yaml"})
  assert result.success
  master = json.loads((tmp_path / "master.json").read_text())
  aux = json.loads((tmp_path / "entity.json").read_text())
  assert [r["id"] for r in master["rows"]] == ["a", "d"]
  assert [r["id"] for r in aux["rows"]] == ["b", "c"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/build/test_filesplit_steps.py -v`
Expected: FAIL — `ModuleNotFoundError` for `cyoa.build.steps.filesplit`.

- [ ] **Step 3: Implement the build steps**

Create `cyoa/build/steps/filesplit.py`:

```python
"""Project split/merge build steps."""

import json

import yaml

from cyoa.build.errors import BuildError
from cyoa.build.registry import StepHandler, StepResult
from cyoa.ops.filesplit import (
  merge_project,
  parse_descriptor,
  split_project,
  validate_spec,
)


def _load_spec(context, params):
  if "config" not in params:
    raise BuildError("step requires a 'config' parameter")
  config_path = context.work_dir / params["config"]
  with config_path.open("r", encoding="utf-8") as fd:
    return parse_descriptor(yaml.safe_load(fd))


def _write_json(path, data):
  with path.open("w", encoding="utf-8") as fd:
    json.dump(data, fd, indent=2, ensure_ascii=False)
    fd.write("\n")


class MergeStep(StepHandler):
  """Assemble the full project from master (context.project) + aux files."""

  step_type = "project.merge"

  def execute(self, context, params):
    spec = _load_spec(context, params)
    console = context.console

    aux_projects = {}
    for file in spec.files:
      aux_path = context.work_dir / file.path
      with aux_path.open("r", encoding="utf-8") as fd:
        aux_projects[file.path] = json.load(fd)

    result = merge_project(context.project, aux_projects, spec)

    # Replace context.project contents in place so later steps see the merge.
    context.project.clear()
    context.project.update(result.project)

    for label in result.inserted:
      console.print(f"[green]inserted[/green] {label}")
    for label in result.skipped:
      console.print(f"[dim]already present[/dim] {label}")

    return StepResult(
      success=True,
      message=(
        f"Merged {len(result.inserted)} segment(s), "
        f"skipped {len(result.skipped)} already present"
      ),
    )


class SplitStep(StepHandler):
  """Overwrite master + aux files from the full context.project."""

  step_type = "project.split"

  def execute(self, context, params):
    spec = _load_spec(context, params)
    console = context.console

    issues = validate_spec(context.project, spec)
    for issue in issues:
      console.print(f"[red]lint:[/red] {issue}")
    if issues:
      return StepResult(
        success=False, message=f"{len(issues)} lint issue(s) in descriptor"
      )

    result = split_project(context.project, spec)
    warnings = list(result.drift_warnings)
    for warning in warnings:
      console.print(f"[yellow]drift:[/yellow] {warning}")

    master_path = context.work_dir / spec.master
    _write_json(master_path, result.master)
    console.print(f"master {master_path.name}: {len(result.master['rows'])} rows")
    for path, aux in result.aux.items():
      _write_json(context.work_dir / path, aux)
      console.print(f"aux {path}: {len(aux['rows'])} rows")

    return StepResult(
      success=True,
      message=f"Wrote master + {len(result.aux)} aux file(s)",
      warnings=warnings,
    )
```

- [ ] **Step 4: Register the steps**

In `cyoa/build/steps/__init__.py`, add the import (alongside the other step imports):

```python
from cyoa.build.steps.filesplit import MergeStep, SplitStep
```

And add the registrations (alongside the other `registry.register(...)` calls):

```python
registry.register(MergeStep)
registry.register(SplitStep)
```

- [ ] **Step 5: Run tests and verify registration**

Run: `uv run pytest tests/build/test_filesplit_steps.py -v`
Expected: PASS (2 tests).

Run: `uv run cyoa build --dry-run -f build.yaml` (only if a `project.merge`/`project.split` step has been added to `build.yaml`; otherwise skip). To verify registration without editing `build.yaml`, run:
`uv run python -c "import cyoa.build.steps as s; from cyoa.build.registry import registry; print(registry.get('project.merge').__name__, registry.get('project.split').__name__)"`
Expected: prints `MergeStep SplitStep` (no `BuildError`).

- [ ] **Step 6: Run the full test suite, lint, and commit**

```bash
uv run pytest -v
uvx ruff format cyoa/ tests/
uvx ruff check cyoa/ tests/
git add cyoa/build/steps/filesplit.py cyoa/build/steps/__init__.py tests/build/
git commit -m "feat(filesplit): project.merge and project.split build steps"
```

---

## Notes for the integrator (not a task)

Wiring the steps into the real `build.yaml` and authoring the real `split.yaml`
descriptor (with the actual Entity row IDs and anchors) is a content/config
change left to the maintainer, since it depends on live row IDs. The typical
pipeline shape:

```yaml
input: "project-${version}.master.json"

steps:
  - name: "Assemble split files"
    uses: project.merge
    with: { config: split.yaml }
  # ... existing patches / media / balance / sort ...
  - name: "Refresh canonical split files"
    uses: project.split
    with: { config: split.yaml }
  - name: "Save merged project"
    uses: save
```
