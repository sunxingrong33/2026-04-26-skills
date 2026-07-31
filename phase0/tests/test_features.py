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


def test_nihonium_typo_is_rejected():
    """[Nh] parses as element 113 instead of failing -- the gate must catch it.

    Regression: a curated row wrote [Nh] intending an exocyclic amine N-H (the
    nitrogen of a Boc-protected aminopyridine). RDKit read it as nihonium and
    silently produced a 902 Da structure whose every descriptor was garbage.
    The correct writing there is a plain N; [nH] is for ring aromatic nitrogen.
    """
    with pytest.raises(StructureError, match="非常规元素"):
        compute_features("C(C1NN=C(I)C=1CCCOC1=CC=C(F)C=C1COC1=C([Nh]C)N=CC(I)=C1)#N")


def test_ordinary_elements_pass():
    for name in ("crizotinib", "brigatinib", "ceritinib"):
        compute_features(SMILES[name])


def test_amide_count_recognises_a_real_amide():
    """Regression: constraining the amide N excluded every amide (lorlatinib -> 0)."""
    assert compute_features(SMILES["lorlatinib"]).numeric["amide_count"] == 1


def test_amide_count_excludes_urea_ester_and_carbamate():
    assert compute_features("CNC(=O)NC").numeric["amide_count"] == 0
    assert compute_features("CNC(=O)NC").numeric["urea_count"] == 1
    assert compute_features("CNC(=O)OC").numeric["amide_count"] == 0
    assert compute_features("CNC(=O)OC").numeric["carbamate_count"] == 1
    assert compute_features("CC(=O)OCC").numeric["amide_count"] == 0
    assert compute_features("CC(=O)OCC").numeric["ester_count"] == 1


def test_ether_count_excludes_ester_oxygen():
    assert compute_features("CC(=O)OCC").numeric["ether_count"] == 0
    assert compute_features(SMILES["crizotinib"]).numeric["ether_count"] == 1


def test_linker_swap_is_visible_as_a_feature_delta():
    """Ether-linked macrocycle -> amide-linked + nitrile, the change descriptors miss."""
    early = compute_features("Nc1ncc2cc1OCc1cc(F)ccc1OCCCCc1cnn(C)c21").numeric
    lorl = compute_features(SMILES["lorlatinib"]).numeric
    assert early["ether_count"] - lorl["ether_count"] == 1
    assert lorl["amide_count"] - early["amide_count"] == 1
    assert lorl["nitrile_count"] - early["nitrile_count"] == 1


BOC_PRECURSOR = (
    "C(C1NN=C(I)C=1CCCOC1=CC=C(F)C=C1COC1=C(NC(=O)OC(C)(C)C)N=CC(I)=C1)#N"
)


def test_synthetic_handles_flag_a_protected_coupling_precursor():
    """The [Nh] row, once corrected, is a valid molecule that passes every other
    gate: right elements, and it keeps the programme's core rings. Only the Boc
    and the aryl iodides distinguish it from a tested analogue."""
    feats = compute_features(BOC_PRECURSOR)
    assert "Boc 保护基" in feats.synthetic_handles
    assert "芳基碘" in feats.synthetic_handles


def test_tested_compounds_carry_no_synthetic_handles():
    for name in ("crizotinib", "lorlatinib", "ceritinib", "alectinib", "brigatinib"):
        assert compute_features(SMILES[name]).synthetic_handles == [], name
