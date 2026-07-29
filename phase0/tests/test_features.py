import pytest

from phase0.sar.features import StructureError, compute_features
from phase0.tests.fixtures import FORMULAS, OSIMERTINIB, SMILES


@pytest.mark.parametrize("name", sorted(SMILES))
def test_declared_formula_matches_structure(name):
    feats = compute_features(SMILES[name], FORMULAS[name])
    assert feats.formula == FORMULAS[name]


def test_formula_gate_rejects_wrong_declaration():
    with pytest.raises(StructureError, match="分子式校验失败"):
        compute_features(SMILES["crizotinib"], "C21H22Cl2FN5O2")


def test_unparseable_smiles_rejected():
    with pytest.raises(StructureError, match="无法解析"):
        compute_features("this is not a smiles")


def test_macrocycle_detection():
    assert compute_features(SMILES["lorlatinib"]).boolean["has_macrocycle"] is True
    assert compute_features(SMILES["crizotinib"]).boolean["has_macrocycle"] is False
    assert compute_features(SMILES["lorlatinib"]).numeric["max_ring_size"] == 12


def test_benzylic_count_excludes_n_methyl_on_aromatic_nitrogen():
    """An N-methyl on a pyrazole is an N-dealkylation site, not a benzylic one.

    Regression test: the original SMARTS used [a] instead of [c] and counted
    lorlatinib's pyrazole N-CH3 as three benzylic hydrogens.
    """
    lorl = compute_features(SMILES["lorlatinib"])
    assert lorl.numeric["benzylic_h_count"] == 3
    assert lorl.numeric["n_alkyl_h_count"] == 3


def test_basic_amine_counting():
    criz = compute_features(SMILES["crizotinib"])
    lorl = compute_features(SMILES["lorlatinib"])
    # crizotinib's piperidine NH is a strong base; lorlatinib has none
    assert criz.numeric["strong_basic_amine_count"] == 1
    assert criz.boolean["has_strong_basic_center"] is True
    assert lorl.numeric["strong_basic_amine_count"] == 0
    assert lorl.boolean["has_strong_basic_center"] is False


def test_amide_nitrogen_is_not_counted_as_basic():
    lorl = compute_features(SMILES["lorlatinib"])
    assert lorl.numeric["basic_amine_count"] == 0


def test_morpholine_is_attenuated_not_strong():
    """Alectinib has a piperidine (strong) and a morpholine (attenuated)."""
    alec = compute_features(SMILES["alectinib"])
    assert alec.numeric["basic_amine_count"] > alec.numeric["strong_basic_amine_count"]


def test_warhead_detection():
    osi = compute_features(OSIMERTINIB)
    assert osi.boolean["has_warhead"] is True
    assert "acrylamide" in osi.warhead_types
    assert compute_features(SMILES["crizotinib"]).boolean["has_warhead"] is False


def test_stereocentre_assignment():
    criz = compute_features(SMILES["crizotinib"])
    assert criz.numeric["chiral_centers_defined"] == 1
    assert compute_features(SMILES["ceritinib"]).numeric["chiral_centers_defined"] == 0


def test_hbd_drops_from_crizotinib_to_lorlatinib():
    """The load-bearing fact behind the P-gp/CNS narrative."""
    assert compute_features(SMILES["crizotinib"]).numeric["hbd"] == 2
    assert compute_features(SMILES["lorlatinib"]).numeric["hbd"] == 1


def test_tpsa_rises_from_crizotinib_to_lorlatinib():
    """Guards the rule-library decision to key P-gp on HBD/basicity, not TPSA."""
    criz = compute_features(SMILES["crizotinib"]).numeric["tpsa"]
    lorl = compute_features(SMILES["lorlatinib"]).numeric["tpsa"]
    assert lorl > criz
