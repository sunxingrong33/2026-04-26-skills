"""Build an offline, self-contained evidence explorer with RDKit structure SVGs."""
from __future__ import annotations

import argparse
import base64
import csv
import json
from dataclasses import asdict
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdFMCS
from rdkit.Chem.Draw import rdMolDraw2D

from .deltas import compute_program_deltas
from .narrate import build_facts
from .rules import evaluate, load_rules
from .schema import load_dataset
from .validate import pair_dataset, candidates

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "index.html"


def molecule_svg(smiles, highlighted=()):
    mol = Chem.MolFromSmiles(smiles)
    drawer = rdMolDraw2D.MolDraw2DSVG(560, 340)
    drawer.drawOptions().clearBackground = False
    drawer.drawOptions().setHighlightColour((0.96, 0.64, 0.28))
    drawer.DrawMolecule(mol, highlightAtoms=list(highlighted))
    drawer.FinishDrawing()
    return "data:image/svg+xml;base64," + base64.b64encode(drawer.GetDrawingText().encode()).decode()


def structure_pair(smiles_a, smiles_b):
    a, b = Chem.MolFromSmiles(smiles_a), Chem.MolFromSmiles(smiles_b)
    match = rdFMCS.FindMCS([a, b], timeout=2, ringMatchesRingOnly=True,
                          completeRingsOnly=False,
                          atomCompare=rdFMCS.AtomCompare.CompareElements,
                          bondCompare=rdFMCS.BondCompare.CompareOrder)
    query = Chem.MolFromSmarts(match.smartsString) if match.smartsString else None
    keep_a = set(a.GetSubstructMatch(query)) if query is not None else set()
    keep_b = set(b.GetSubstructMatch(query)) if query is not None else set()
    return {"from_svg": molecule_svg(smiles_a, set(range(a.GetNumAtoms())) - keep_a),
            "to_svg": molecule_svg(smiles_b, set(range(b.GetNumAtoms())) - keep_b),
            "timed_out": bool(match.canceled),
            "note": "橙色标示一次 MCS 匹配未覆盖的原子；匹配不唯一，未覆盖键级/立体变化，不代表已确认的药效团或因果关系。"}


def build_program(data: Path, comparisons):
    ds = load_dataset(data)
    meta = json.loads((data / "sources.json").read_text(encoding="utf-8"))
    pid = meta["plan"]["program_id"]
    compounds = []
    for member in ds.members_of(pid):
        c = ds.compounds[member.compound_id]
        compounds.append({"id": c.compound_id, "name": c.compound_name.split(" · ")[0],
            "full_name": c.compound_name, "generation": member.generation,
            "source": c.structure_source, "record_source": member.source_url,
            "citation": member.example_ref, "smiles": c.smiles,
            "inchikey": c.features.inchikey, "formula": c.features.formula,
            "features": c.features.numeric, "svg": molecule_svg(c.smiles),
            "measurements": [asdict(m) for m in ds.measurements_for(c.compound_id)]})
    with (data / "observations.csv").open(encoding="utf-8", newline="") as f:
        observations = list(csv.DictReader(f))
    pairs = []
    for a, b, label in comparisons:
        pair = pair_dataset(ds, pid, a, b)
        _, deltas = compute_program_deltas(pair, pid, run_mcs=False)
        delta = deltas[0]
        hyps = evaluate(delta, load_rules())
        facts = build_facts(ds.programs[pid].program_name, delta, hyps, pair.warnings)
        pairs.append({"from": a, "to": b, "label": label,
                      "features": [asdict(d) for d in delta.features.values() if d.delta],
                      "measures": [asdict(d) for d in delta.measures.values()],
                      "evidence": [asdict(e) for e in facts.evidence],
                      "narrative": candidates(facts, hyps),
                      "structure": structure_pair(ds.compounds[a].smiles, ds.compounds[b].smiles)})
    return {"id": pid, "meta": meta, "compounds": compounds, "comparisons": pairs,
            "observations": observations, "warnings": ds.warnings}


def build(out: Path):
    data = ROOT / "phase0" / "data" / "curated"
    programs = [build_program(data / "lorlatinib", [
        ("CHEMBL601719", "CHEMBL3128069", "参考分子 1 → 无环分子 6a"),
        ("CHEMBL3128069", "CHEMBL3286814", "无环 6a / 早期大环 7a 对照"),
        ("CHEMBL3286814", "CHEMBL3286830", "早期大环 7a / 候选 8k 对照"),
        ("CHEMBL3286820", "CHEMBL3286830", "同系列 8a / 8k 对照"),
        ("CHEMBL601719", "CHEMBL3286830", "参考分子 1 / 候选 8k 总体对照")]),
        build_program(data / "osimertinib", [
        ("CHEMBL2426279", "CHEMBL3353410", "独立留出：compound 9 / 8")])]
    validation = ROOT / "artifacts" / "validation" / "validation_report.json"
    payload = {"programs": programs, "validation": None}
    if validation.exists():
        payload["validation"] = json.loads(validation.read_text(encoding="utf-8"))
    raw = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__SAR_DATA__", raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "sar-explorer.html")
    args = ap.parse_args(argv)
    print(build(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
