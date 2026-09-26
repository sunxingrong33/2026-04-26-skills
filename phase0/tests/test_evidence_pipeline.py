"""Offline contract checks for source ingestion and scientific validation."""
import csv
import json
from pathlib import Path

import pytest

from phase0.sar import ingest, validate
from phase0.sar.schema import load_dataset
from phase0.sar.deltas import compute_program_deltas

DATA = Path(__file__).parents[1] / "data"


@pytest.mark.parametrize("name,counts", [("lorlatinib", (29,247,224)), ("osimertinib", (2,49,38))])
def test_curated_source_accounting(name, counts):
    root = DATA / "curated" / name
    meta = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    ds = load_dataset(root)
    with (root / "observations.csv").open(encoding="utf-8", newline="") as f:
        obs = list(csv.DictReader(f))
    assert (len(ds.compounds), len(obs), len(ds.measurements)) == counts
    excluded = {str(r["activity_id"]) for r in meta["excluded_from_numeric_aggregation"]}
    assert len(obs) == len(ds.measurements) + len(excluded)
    assert all(o["activity_id"] in excluded for o in obs if o["standard_relation"] != "=")
    assert all(m.source.rsplit("/", 1)[-1].replace(".json", "") not in excluded for m in ds.measurements)
    assert all(m.priority_date is None for m in ds.memberships)


def test_logd_is_not_reported_as_ratio():
    ds = load_dataset(DATA / "curated" / "lorlatinib")
    pair = validate.pair_dataset(ds, "lorlatinib-literature", "CHEMBL3286820", "CHEMBL3286830")
    _, changes = compute_program_deltas(pair, "lorlatinib-literature", run_mcs=False)
    logd = next(d for key,d in changes[0].measures.items() if "logd" in key.lower())
    assert logd.comparable and logd.ratio is None


def test_ingestion_rejects_missing_plan_compound(tmp_path):
    plan = {"document_chembl_id": "CHEMBL1", "stages": [{"compound_keys": ["missing"]}]}
    with pytest.raises(ValueError, match="Missing planned compounds"):
        ingest.build_dataset({"document_chembl_id": "CHEMBL1"}, [], [], plan, tmp_path, [])


def test_pagination_loop_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "fetch_json", lambda *a: ({"activities": [], "page_meta": {
        "next": "activity.json?document_chembl_id=CHEMBL1&limit=1000"}}, {}))
    with pytest.raises(ValueError, match="Pagination loop"):
        ingest.collection("activity", "activities", "CHEMBL1", tmp_path, True)


def test_offline_fetch_never_silently_uses_network(tmp_path):
    with pytest.raises(FileNotFoundError, match="Offline snapshot missing"):
        ingest.fetch_json(ingest.API + "document/CHEMBL1.json", tmp_path, True)
    with pytest.raises(ValueError, match="Only the public"):
        ingest.fetch_json("https://example.com/", tmp_path, True)


def test_human_metric_requires_complete_attributed_review():
    rows = [{"review_id": "a"}, {"review_id": "b"}]
    review = {"a": {"supported": "no", "reviewer": "chemist", "rationale": "unsupported"}}
    assert validate.review_metric(rows, review, "supported", "no")["value"] is None
    review["b"] = {"supported": "yes", "reviewer": "chemist", "rationale": "source checked"}
    assert validate.review_metric(rows, review, "supported", "no")["value"] == .5


def test_validation_preserves_reviews_and_archives_stale_ids(tmp_path):
    result = validate.run(validate.DEFAULT_CASES, tmp_path)
    assert not result["release_ready"]
    assert all(c["human_objective_recall"]["value"] is None for c in result["cases"])
    path = tmp_path / "claims_review.csv"
    rows = list(validate.read_review(path).values())
    rows[0].update(supported="no", reviewer="test reviewer", rationale="test annotation")
    original_id = rows[0]["review_id"]
    ingest.write_csv(path, list(rows[0]), rows)
    validate.run(validate.DEFAULT_CASES, tmp_path)
    assert validate.read_review(path)[original_id]["reviewer"] == "test reviewer"
    rows[0]["review_id"] = "stale"
    ingest.write_csv(path, list(rows[0]), rows)
    validate.run(validate.DEFAULT_CASES, tmp_path)
    assert len(list(tmp_path.glob("claims_review.*.archived.csv"))) == 1


def test_changed_rules_cannot_reuse_holdout(monkeypatch, tmp_path):
    changed = tmp_path / "rules.yaml"
    changed.write_text("changed", encoding="utf-8")
    monkeypatch.setattr(validate, "DEFAULT_RULES_PATH", changed)
    with pytest.raises(ValueError, match="规则已改变"):
        validate.run(validate.DEFAULT_CASES, tmp_path / "out")


def test_cli_dry_run_with_legacy_console_encoding(tmp_path):
    import os
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "phase0.sar.cli", "--data",
        str(DATA / "curated" / "lorlatinib"), "--program", "lorlatinib-literature",
        "--dry-run", "--no-mcs", "--out", str(tmp_path)],
        env=dict(os.environ, PYTHONIOENCODING="gbk"), capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert len(list(tmp_path.glob("*.facts.json"))) == 3
