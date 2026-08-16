from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("project-v17.json")
OUT = Path("analysis/shard-powers-v17")

TIER_ROWS = {
    "Tier 1": [
        ("Mover", "tc7n"), ("Shaker", "wtn7"), ("Brute", "vs8q"),
        ("Master", "ghw4"), ("Tinker", "nwgn"), ("Blaster", "z0zb"),
        ("Thinker", "4x5f"), ("Striker", "xu5q"), ("Changer", "b7r5"),
        ("Trump", "bbyi"), ("Stranger", "hyzg"),
    ],
    "Tier 2": [("All", "zg2f")],
    "Tier 3": [("All", "e018")],
}

EXCLUDED_ROWS = {
    "Power Copy": ["jsch"],
    "Lucky Mutations": ["zj4c"],
    "Doll": ["wy6p", "xe94"],
    "Endbringer (Master)": ["o58j", "qd5r"],
    "Endbringer (Changer)": ["jmco", "ab0a"],
    "Upgrades": ["hd9l"],
    "Fusions": ["qldk"],
}

MEDIA_KEYS_NORMALIZED = {
    "image", "imageurl", "img", "src", "picture", "audio", "video",
    "thumbnail", "backgroundimage",
}


def strip_html(value: str) -> str:
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def text_safe(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = key.lower().replace("-", "").replace("_", "")
            if normalized in MEDIA_KEYS_NORMALIZED:
                continue
            if isinstance(child, str) and child.startswith(("data:image", "data:audio", "data:video")):
                continue
            result[key] = text_safe(child)
        return result
    if isinstance(value, list):
        return [text_safe(child) for child in value]
    return value


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source_bytes = SOURCE.read_bytes()
    project = json.loads(source_bytes.decode("utf-8"))
    rows = project.get("rows", [])
    point_types = {p.get("id"): p for p in project.get("pointTypes", [])}

    rows_by_id: dict[str, list[dict]] = defaultdict(list)
    row_positions: dict[int, int] = {}
    objects_by_id: dict[str, list[dict]] = defaultdict(list)

    for row_index, row in enumerate(rows):
        row_id = row.get("id", "")
        rows_by_id[row_id].append(row)
        row_positions[id(row)] = row_index
        for object_index, obj in enumerate(row.get("objects", [])):
            objects_by_id[obj.get("id", "")].append({
                "title": obj.get("title", ""),
                "row_id": row_id,
                "row_title": row.get("title", ""),
                "row_index": row_index,
                "object_index": object_index,
            })

    def resolve(object_id: str) -> dict:
        matches = objects_by_id.get(object_id, [])
        return {"id": object_id, "resolved": bool(matches), "matches": matches}

    def parse_conditions(items: list[dict] | None) -> tuple[list[str], list[str], list[dict]]:
        required_ids: list[str] = []
        incompatible_ids: list[str] = []
        complex_items: list[dict] = []
        for item in items or []:
            kind = item.get("type")
            is_required = item.get("required")
            if kind == "id" and item.get("reqId"):
                (required_ids if is_required else incompatible_ids).append(item["reqId"])
            elif kind == "or":
                refs = [x.get("req") for x in item.get("orRequired", []) if x.get("req")]
                complex_items.append({
                    "kind": "OR_REQUIRED" if is_required else "OR_CONDITION",
                    "ids": refs,
                    "resolved": [resolve(ref) for ref in refs],
                    "raw": text_safe(item),
                })
            else:
                complex_items.append({"kind": kind or "unknown", "raw": text_safe(item)})
        return required_ids, incompatible_ids, complex_items

    def cost_summary(scores: list[dict] | None) -> str:
        parts = []
        for score in scores or []:
            point_id = score.get("id", "")
            point_type = point_types.get(point_id, {})
            label = point_type.get("name") or point_type.get("afterText") or point_id
            conditional = " [conditional]" if score.get("requireds") else ""
            parts.append(f"{score.get('value', '')} {label}{conditional}".strip())
        return " | ".join(parts)

    def classification(title: str, fallback: str) -> str:
        match = re.match(r"^\s*[\(\[]([^\)\]]+)[\)\]]", title or "")
        return match.group(1).strip() if match else fallback

    records: list[dict] = []
    extraction_issues: list[dict] = []
    selected_row_ids: set[str] = set()

    for tier, configured_rows in TIER_ROWS.items():
        for class_group, row_id in configured_rows:
            selected_row_ids.add(row_id)
            matches = rows_by_id.get(row_id, [])
            if len(matches) != 1:
                extraction_issues.append({
                    "type": "row_resolution", "tier": tier, "row_id": row_id,
                    "expected_matches": 1, "actual_matches": len(matches),
                })
                continue

            row = matches[0]
            row_index = row_positions[id(row)]
            for object_index, obj in enumerate(row.get("objects", [])):
                title = obj.get("title", "")
                description_html = obj.get("text") or obj.get("description") or ""
                required_ids, incompatible_ids, complex_conditions = parse_conditions(obj.get("requireds"))

                scores = []
                for score in obj.get("scores", []) or []:
                    item = text_safe(score)
                    point_id = score.get("id")
                    point_type = point_types.get(point_id, {})
                    item["point_type"] = {
                        "id": point_id,
                        "name": point_type.get("name"),
                        "suffix": point_type.get("afterText"),
                        "starting_sum": point_type.get("startingSum"),
                    }
                    sr, si, sc = parse_conditions(score.get("requireds"))
                    item["resolved_required"] = [resolve(ref) for ref in sr]
                    item["resolved_incompatible"] = [resolve(ref) for ref in si]
                    item["complex_conditions"] = sc
                    scores.append(item)

                addons = []
                for addon_index, addon in enumerate(obj.get("addons", []) or []):
                    ar, ai, ac = parse_conditions(addon.get("requireds"))
                    addon_text = addon.get("text") or addon.get("description") or ""
                    addons.append({
                        "addon_index": addon_index,
                        "title": addon.get("title", ""),
                        "description_html": addon_text,
                        "description_plain": strip_html(addon_text),
                        "requirements_raw": text_safe(addon.get("requireds", [])),
                        "required": [resolve(ref) for ref in ar],
                        "incompatible": [resolve(ref) for ref in ai],
                        "complex_conditions": ac,
                        "scores": text_safe(addon.get("scores", [])),
                        "raw_text_safe": text_safe(addon),
                    })

                records.append({
                    "tier": tier,
                    "class_group": class_group,
                    "classification": classification(title, class_group),
                    "structural_path": (
                        f"Project V17/Powers/Shard/{tier}/{class_group}"
                        if tier == "Tier 1" else f"Project V17/Powers/Shard/{tier}"
                    ),
                    "row_id": row_id,
                    "row_title": row.get("title", ""),
                    "row_index": row_index,
                    "object_index": object_index,
                    "object_id": obj.get("id", ""),
                    "title": title,
                    "description_html": description_html,
                    "description_plain": strip_html(description_html),
                    "cost_summary": cost_summary(obj.get("scores")),
                    "scores": scores,
                    "requirements_raw": text_safe(obj.get("requireds", [])),
                    "required": [resolve(ref) for ref in required_ids],
                    "incompatible": [resolve(ref) for ref in incompatible_ids],
                    "complex_conditions": complex_conditions,
                    "addons": addons,
                    "raw_object_text_safe": text_safe(obj),
                })

    excluded_ids = {row_id for ids in EXCLUDED_ROWS.values() for row_id in ids}
    accidental = sorted(selected_row_ids & excluded_ids)
    if accidental:
        extraction_issues.append({"type": "excluded_row_selected", "row_ids": accidental})

    unresolved: list[dict] = []
    for record in records:
        for field in ("required", "incompatible"):
            for ref in record[field]:
                if not ref["resolved"]:
                    unresolved.append({
                        "source_id": record["object_id"], "source_title": record["title"],
                        "field": field, "target_id": ref["id"],
                    })
        for condition in record["complex_conditions"]:
            for ref in condition.get("resolved", []):
                if not ref["resolved"]:
                    unresolved.append({
                        "source_id": record["object_id"], "source_title": record["title"],
                        "field": "complex_condition", "target_id": ref["id"],
                    })

    anomalies: list[dict] = []
    id_counts = Counter(record["object_id"] for record in records)
    for record in records:
        if not record["object_id"]:
            anomalies.append({"type": "empty_object_id", "title": record["title"], "path": record["structural_path"]})
        if not record["title"]:
            anomalies.append({"type": "empty_title", "object_id": record["object_id"]})
        if not record["description_html"]:
            anomalies.append({"type": "empty_description", "object_id": record["object_id"], "title": record["title"]})
        if not record["scores"]:
            anomalies.append({"type": "no_score_entries", "object_id": record["object_id"], "title": record["title"]})
    for object_id, count in id_counts.items():
        if object_id and count > 1:
            anomalies.append({"type": "duplicate_id_within_corpus", "object_id": object_id, "count": count})

    counts_by_tier = Counter(record["tier"] for record in records)
    counts_by_row = Counter(record["row_id"] for record in records)
    counts_by_classification = Counter(record["classification"] for record in records)

    summary = {
        "source": {
            "repository": "MAurelian-Alba/worm-cyoa-v6-fork-16-08-26",
            "upstream_repository": "ltouroumov/worm-cyoa-v6-fork",
            "file": str(SOURCE),
            "size_bytes": len(source_bytes),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "scope": {
            "included": TIER_ROWS,
            "explicitly_excluded": EXCLUDED_ROWS,
            "selected_row_ids": sorted(selected_row_ids),
        },
        "counts": {
            "total_records": len(records),
            "by_tier": dict(sorted(counts_by_tier.items())),
            "by_row_id": dict(sorted(counts_by_row.items())),
            "by_classification": dict(sorted(counts_by_classification.items())),
        },
        "checks": {
            "all_expected_rows_resolved_once": not any(x["type"] == "row_resolution" for x in extraction_issues),
            "no_excluded_rows_selected": not accidental,
            "unique_nonempty_object_ids_within_corpus": not any(x["type"] == "duplicate_id_within_corpus" for x in anomalies),
            "unresolved_references_count": len(unresolved),
            "anomaly_count": len(anomalies),
        },
    }

    ambiguity_report = {
        "extraction_issues": extraction_issues,
        "unresolved_references": unresolved,
        "record_anomalies": anomalies,
        "notes": [
            "Rows are selected by IDs from cyoa/md_export/config/powers/shard.py, not by fuzzy title matching.",
            "Embedded addons are retained inside parent records and are not counted as separate base powers.",
            "Every score entry is retained; conditional score entries are marked in cost_summary.",
            "OR and non-ID conditions are preserved verbatim in complex_conditions.",
            "Media fields and embedded media data URLs are omitted; semantic text and conditions are retained.",
        ],
    }

    (OUT / "corpus.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "corpus.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    for tier in ("Tier 1", "Tier 2", "Tier 3"):
        filename = tier.lower().replace(" ", "-") + ".json"
        data = [record for record in records if record["tier"] == tier]
        (OUT / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "tier", "class_group", "classification", "structural_path", "row_id", "row_title",
        "row_index", "object_index", "object_id", "title", "description_plain", "description_html",
        "cost_summary", "required_titles", "required_ids", "incompatible_titles", "incompatible_ids",
        "complex_conditions_json", "scores_json", "addons_json", "requirements_raw_json",
    ]

    def ref_titles(refs: list[dict]) -> str:
        titles: list[str] = []
        for ref in refs:
            if ref["matches"]:
                titles.extend(match["title"] for match in ref["matches"])
            else:
                titles.append(f"[UNRESOLVED:{ref['id']}]")
        return " | ".join(titles)

    with (OUT / "corpus.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "tier": record["tier"], "class_group": record["class_group"],
                "classification": record["classification"], "structural_path": record["structural_path"],
                "row_id": record["row_id"], "row_title": record["row_title"],
                "row_index": record["row_index"], "object_index": record["object_index"],
                "object_id": record["object_id"], "title": record["title"],
                "description_plain": record["description_plain"], "description_html": record["description_html"],
                "cost_summary": record["cost_summary"], "required_titles": ref_titles(record["required"]),
                "required_ids": " | ".join(ref["id"] for ref in record["required"]),
                "incompatible_titles": ref_titles(record["incompatible"]),
                "incompatible_ids": " | ".join(ref["id"] for ref in record["incompatible"]),
                "complex_conditions_json": json.dumps(record["complex_conditions"], ensure_ascii=False),
                "scores_json": json.dumps(record["scores"], ensure_ascii=False),
                "addons_json": json.dumps(record["addons"], ensure_ascii=False),
                "requirements_raw_json": json.dumps(record["requirements_raw"], ensure_ascii=False),
            })

    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "ambiguities.json").write_text(json.dumps(ambiguity_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(
        "# Shard Powers Tier 1-3 corpus (Project V17)\n\n"
        f"Total records: **{len(records)}**  \n"
        f"Tier 1: **{counts_by_tier.get('Tier 1', 0)}**  \n"
        f"Tier 2: **{counts_by_tier.get('Tier 2', 0)}**  \n"
        f"Tier 3: **{counts_by_tier.get('Tier 3', 0)}**  \n"
        f"Unresolved references: **{len(unresolved)}**  \n"
        f"Audit anomalies: **{len(anomalies)}**\n\n"
        "Files: `corpus.json`, `corpus.jsonl`, `corpus.csv`, `tier-1.json`, `tier-2.json`, "
        "`tier-3.json`, `summary.json`, and `ambiguities.json`. Embedded addons remain nested and "
        "are not counted as separate powers. Media fields are omitted.\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
