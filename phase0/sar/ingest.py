"""Bounded ChEMBL document ingestion with immutable evidence snapshots.

Only public read-only endpoints are used. No automatic chronology, patent
clustering or compound-example matching is inferred from database order.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .features import compute_features

API = "https://www.ebi.ac.uk/chembl/api/data/"


def fetch_json(url: str, cache: Path, offline: bool = False) -> tuple[dict, dict]:
    if not url.startswith(API):
        raise ValueError("Only the public ChEMBL API is supported")
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists():
        raw = path.read_bytes()
    elif offline:
        raise FileNotFoundError(f"Offline snapshot missing: {url}")
    else:
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={"User-Agent": "SAR-evidence-prototype/0.2"}), timeout=45) as response:
                    raw = response.read()
                json.loads(raw)
                path.write_bytes(raw)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    return json.loads(raw), {"url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                             "cache_file": path.name,
                             "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}


def collection(endpoint: str, key: str, doc: str, cache: Path, offline: bool):
    url = API + endpoint + ".json?" + urlencode({"document_chembl_id": doc, "limit": 1000})
    rows, sources, visited = [], [], set()
    while url:
        if url in visited or len(visited) >= 100:
            raise ValueError("Pagination loop or document exceeds page limit")
        visited.add(url)
        payload, source = fetch_json(url, cache, offline)
        rows.extend(payload[key])
        sources.append(source)
        next_page = payload["page_meta"]["next"]
        url = urljoin(API, next_page) if next_page else None
    return rows, sources


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(document: dict, records: list[dict], activities: list[dict],
                  plan: dict, out: Path, sources: list[dict]) -> dict:
    doc = document["document_chembl_id"]
    if plan["document_chembl_id"] != doc:
        raise ValueError("Plan/document mismatch")
    by_molecule: dict[str, str] = {}
    for a in activities:
        if a.get("canonical_smiles"):
            by_molecule[a["molecule_chembl_id"]] = a["canonical_smiles"]
    stages = plan["stages"]
    compounds, members, measurements, observations, exclusions = [], [], [], [], []
    selected = {}
    for r in records:
        key = r["compound_key"].split(",")[0].strip()
        matching = [s for s in stages if key in s["compound_keys"]]
        if not matching:
            continue
        if len(matching) != 1:
            raise ValueError(f"Ambiguous stage for {key}")
        cid = r["molecule_chembl_id"]
        if cid in selected:
            raise ValueError(f"Duplicate molecule in plan: {cid}")
        if cid not in by_molecule:
            raise ValueError(f"No structure for {key}")
        feats = compute_features(by_molecule[cid])
        record_url = API + f"compound_record/{r['record_id']}.json"
        compounds.append(dict(compound_id=cid, compound_name=f"{key} · {r.get('compound_name') or cid}",
            smiles=by_molecule[cid], expected_formula=feats.formula,
            structure_source=API + f"molecule/{cid}.json", structure_provenance="chembl", note="ChEMBL structure; formula computed, not an independent identity check"))
        stage = matching[0]
        selected[cid] = r
        members.append(dict(program_id=plan["program_id"], compound_id=cid,
            generation=stage["generation"], patent_number="", priority_date="",
            example_ref=f"{doc}; paper compound {r['compound_key']}; record {r['record_id']}",
            source_url=record_url, source_kind="paper", activity_type="", activity_value_nm="",
            activity_assay="", activity_source="", activity_provenance="chembl",
            note=stage["label"]))
    expected_keys = {key for s in stages for key in s["compound_keys"]}
    found_keys = {r["compound_key"].split(",")[0].strip() for r in selected.values()}
    if found_keys != expected_keys:
        raise ValueError(f"Missing planned compounds: {sorted(expected_keys - found_keys)}")
    seen_activities = set()
    for a in activities:
        cid = a["molecule_chembl_id"]
        if cid not in selected or a["activity_id"] in seen_activities:
            continue
        seen_activities.add(a["activity_id"])
        source_url = API + f"activity/{a['activity_id']}.json"
        observations.append({k: a.get(k) for k in (
            "activity_id", "record_id", "molecule_chembl_id", "assay_chembl_id", "assay_description",
            "standard_type", "standard_relation", "standard_value", "standard_units", "data_validity_comment")}
            | {"source_url": source_url})
        reason = None
        if a.get("standard_relation") != "=":
            reason = "censored_or_missing_relation"
        elif a.get("standard_value") is None:
            reason = "missing_value"
        elif a.get("data_validity_comment"):
            reason = "data_validity_comment"
        if reason:
            exclusions.append({"activity_id": a["activity_id"], "reason": reason})
            continue
        # Endpoint identity includes exact assay; different protocols never
        # become the same metric just because they are both called IC50.
        assay = a["assay_chembl_id"]
        mtype = plan.get("assay_labels", {}).get(assay, f"{assay}:{a['standard_type']}")
        measurements.append(dict(compound_id=cid, measure_type=mtype,
            value=a["standard_value"], unit=a.get("standard_units") or "",
            assay=assay, source=source_url, provenance="chembl",
            note=a["assay_description"]))
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "programs.csv", ["program_id", "program_name", "assignee", "target", "kind", "note"],
              [dict(program_id=plan["program_id"], program_name=plan["program_name"], assignee=plan["assignee"],
                    target=plan["target"], kind="literature_series", note=plan["ordering_note"])])
    write_csv(out / "compounds.csv", ["compound_id", "compound_name", "smiles", "expected_formula", "structure_source", "structure_provenance", "note"], compounds)
    write_csv(out / "program_members.csv", ["program_id", "compound_id", "generation", "patent_number", "priority_date", "example_ref", "source_url", "source_kind", "activity_type", "activity_value_nm", "activity_assay", "activity_source", "activity_provenance", "note"], members)
    write_csv(out / "measurements.csv", ["compound_id", "measure_type", "value", "unit", "assay", "source", "provenance", "note"], measurements)
    write_csv(out / "observations.csv", list(observations[0]) if observations else ["activity_id"], observations)
    metadata = {
        "document_chembl_id": doc, "doi": document.get("doi"), "pubmed_id": document.get("pubmed_id"),
        "year": document.get("year"), "built_at": datetime.now(timezone.utc).isoformat(),
        "structure_count": len(compounds), "observation_count": len(observations),
        "exact_measurement_count": len(measurements), "excluded_from_numeric_aggregation": exclusions,
        "source_snapshots": sources, "plan": plan,
        "limitations": [
            "结构、编号与数值来自 ChEMBL 对原始论文的整理，尚未逐项对照论文图表进行人工复核。",
            "阶段由已声明的论文系列分组构成，不代表逐个化合物的真实研发先后或专利优先权时间。",
            "不等式/缺失/质量标记数据保留在 observations.csv，不当作精确值参与中位数；精确值子集可能有选择偏差。",
            "分子式由导入结构计算，仅验证内部一致性，不能代替原文结构身份核验。",
        ],
    }
    (out / "sources.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="artifacts/chembl-cache")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    doc = plan["document_chembl_id"]
    if not re.fullmatch(r"CHEMBL\d+", doc):
        raise ValueError("Invalid ChEMBL document ID")
    cache = Path(args.cache)
    document, source = fetch_json(API + f"document/{doc}.json", cache, args.offline)
    records, sr = collection("compound_record", "compound_records", doc, cache, args.offline)
    activities, sa = collection("activity", "activities", doc, cache, args.offline)
    summary = build_dataset(document, records, activities, plan, Path(args.out), [source] + sr + sa)
    print(json.dumps({k: summary[k] for k in ("document_chembl_id", "structure_count", "observation_count", "exact_measurement_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
