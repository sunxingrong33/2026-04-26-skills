"""Tests for the multi-measurement path and the consensus ring set."""

import pytest
from rdkit import Chem

from phase0.sar.align import align_generations, consensus_ring_set
from phase0.sar.deltas import compute_program_deltas
from phase0.sar.narrate import build_facts
from phase0.sar.rules import evaluate, load_rules
from phase0.sar.schema import load_dataset
from phase0.tests.fixtures import SMILES

HEADERS = {
    "programs.csv": "program_id,program_name,assignee,target,kind,note\n",
    "compounds.csv": (
        "compound_id,compound_name,smiles,expected_formula,"
        "structure_source,structure_provenance,note\n"
    ),
    "program_members.csv": (
        "program_id,compound_id,generation,patent_number,priority_date,example_ref,"
        "source_url,source_kind,activity_type,activity_value_nm,activity_assay,"
        "activity_source,activity_provenance,note\n"
    ),
    "measurements.csv": "compound_id,measure_type,value,unit,assay,source,provenance,note\n",
}


def _write(tmp_path, compounds, members, measures=None):
    (tmp_path / "programs.csv").write_text(
        HEADERS["programs.csv"] + "p,P,A,T,single_assignee,\n", encoding="utf-8"
    )
    (tmp_path / "compounds.csv").write_text(
        HEADERS["compounds.csv"] + "".join(compounds), encoding="utf-8"
    )
    (tmp_path / "program_members.csv").write_text(
        HEADERS["program_members.csv"] + "".join(members), encoding="utf-8"
    )
    if measures is not None:
        (tmp_path / "measurements.csv").write_text(
            HEADERS["measurements.csv"] + "".join(measures), encoding="utf-8"
        )
    return tmp_path


def _cmp(cid, smiles):
    return f"{cid},{cid},{smiles},,src,unverified,\n"


def _mem(cid, gen, act=""):
    return f"p,{cid},{gen},WO1,2010-01-01,ex1,http://x,patent,IC50,{act},A1,s,unverified,\n"


def test_measurements_are_optional(tmp_path):
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 2)])
    ds = load_dataset(d)
    assert ds.measurements == []
    _, deltas = compute_program_deltas(ds, "p", run_mcs=False)
    assert deltas[0].measures == {}


def test_measurement_delta_and_ratio(tmp_path):
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 2)],
               ["a,efflux,8,,MDCK,s,unverified,\n", "b,efflux,1.1,,MDCK,s,unverified,\n"])
    ds = load_dataset(d)
    _, deltas = compute_program_deltas(ds, "p", run_mcs=False)
    md = deltas[0].measures["efflux"]
    assert md.val_from == 8 and md.val_to == 1.1
    assert md.ratio == 0.138
    assert md.comparable is True


def test_measurement_cross_assay_flagged(tmp_path):
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 2)],
               ["a,ic50,24,nM,LabA,s,unverified,\n", "b,ic50,1.3,nM,LabB,s,unverified,\n"])
    ds = load_dataset(d)
    _, deltas = compute_program_deltas(ds, "p", run_mcs=False)
    assert deltas[0].measures["ic50"].comparable is False


def test_mixed_units_are_rejected(tmp_path):
    """Silently aggregating nM with uM would produce a confident wrong median."""
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["ceritinib"]),
                _cmp("c", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 1), _mem("c", 2)],
               ["a,ic50,24,nM,L,s,unverified,\n", "b,ic50,0.02,uM,L,s,unverified,\n"])
    ds = load_dataset(d)
    with pytest.raises(ValueError, match="混用了单位"):
        compute_program_deltas(ds, "p", run_mcs=False)


def test_measurements_become_citable_evidence(tmp_path):
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 2)],
               ["a,efflux,8,,MDCK,s,unverified,\n", "b,efflux,1.1,,MDCK,s,unverified,\n"])
    ds = load_dataset(d)
    _, deltas = compute_program_deltas(ds, "p", run_mcs=False)
    facts = build_facts("p", deltas[0], evaluate(deltas[0], load_rules()))
    assert any("efflux" in e.text for e in facts.evidence)
    assert any("measure:efflux" in e.features for e in facts.evidence)


def test_unverified_measurements_warn(tmp_path):
    d = _write(tmp_path,
               [_cmp("a", SMILES["crizotinib"]), _cmp("b", SMILES["lorlatinib"])],
               [_mem("a", 1), _mem("b", 2)],
               ["a,efflux,8,,MDCK,s,unverified,\n"])
    ds = load_dataset(d)
    assert any("measurements.csv" in w for w in ds.warnings)


# --- consensus ring set ----------------------------------------------------

def test_consensus_excludes_minority_peripheral_rings():
    """A ring in 1 of 3 compounds is exploration, not core."""
    mols = [Chem.MolFromSmiles(SMILES[n]) for n in ("crizotinib", "crizotinib", "alectinib")]
    rings = consensus_ring_set(mols)
    assert "c1cnnc1" in rings                # pyrazole: in 2 of 3
    assert "C1COCCN1" not in rings           # alectinib's morpholine: 1 of 3


def test_consensus_falls_back_to_union_when_nothing_shared():
    mols = [Chem.MolFromSmiles(SMILES[n]) for n in ("crizotinib", "alectinib")]
    rings = consensus_ring_set(mols, min_fraction=0.9)
    assert rings, "must not return empty"


def test_diverse_generation_does_not_deflate_into_a_false_core_hop():
    """Regression: unioning ring sets made rich generations look like core hops."""
    diverse = [Chem.MolFromSmiles(SMILES[n]) for n in ("crizotinib", "ceritinib")]
    single = [Chem.MolFromSmiles(SMILES["crizotinib"])]
    al = align_generations(diverse, single, run_mcs=False)
    assert al.is_core_hop is False


def test_single_compound_generations_unchanged_by_consensus():
    a = [Chem.MolFromSmiles(SMILES["crizotinib"])]
    b = [Chem.MolFromSmiles(SMILES["lorlatinib"])]
    al = align_generations(a, b, run_mcs=False)
    assert al.ring_jaccard == 0.75
