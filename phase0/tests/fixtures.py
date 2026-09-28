"""Shared test structures.

SMILES are hand-drawn and cross-checked against published molecular formulas and
molecular weights. They are NOT database-verified -- see README §数据可信度.
Tests here assert on relative behaviour (A vs B), which stays valid even if an
individual structure needs a small correction after verification.

crizotinib and lorlatinib are checked against the curated evidence ledger by
InChIKey (test_structure_search); lorlatinib was corrected on 2026-09-28 (the
fluorine had been drawn on the wrong ring position -- same formula and mass).
"""

SMILES = {
    "crizotinib": "C[C@@H](Oc1cc(-c2cnn(C3CCNCC3)c2)cnc1N)c1c(Cl)ccc(F)c1Cl",
    "lorlatinib": "C[C@H]1Oc2cc(-c3c(C#N)n(C)nc3CN(C)C(=O)c3ccc(F)cc13)cnc2N",
    "ceritinib": "CC(C)Oc1cc(C2CCNCC2)c(C)cc1Nc1ncc(Cl)c(Nc2ccccc2S(=O)(=O)C(C)C)n1",
    "brigatinib": "COc1cc(N2CCC(N3CCN(C)CC3)CC2)ccc1Nc1ncc(Cl)c(Nc2ccccc2P(C)(C)=O)n1",
    "alectinib": "CCc1cc2c(cc1N1CCC(N3CCOCC3)CC1)[nH]c1c2C(=O)c2ccc(C#N)cc2C1(C)C",
}

FORMULAS = {
    "crizotinib": "C21H22Cl2FN5O",
    "lorlatinib": "C21H19FN6O2",
    "ceritinib": "C28H36ClN5O3S",
    "alectinib": "C30H34N4O2",
    "brigatinib": "C29H39ClN7O2P",
}

# Ground truth for core-hop detection: True means "same core family".
# crizotinib/lorlatinib share the aminopyridine + pyrazole pharmacophore;
# ceritinib/brigatinib are both 2,4-dianilinopyrimidines.
CORE_PAIRS = {
    ("crizotinib", "lorlatinib"): True,
    ("ceritinib", "brigatinib"): True,
    ("crizotinib", "ceritinib"): False,
    ("crizotinib", "alectinib"): False,
    ("crizotinib", "brigatinib"): False,
    ("ceritinib", "alectinib"): False,
    ("brigatinib", "lorlatinib"): False,
    ("alectinib", "lorlatinib"): False,
}

OSIMERTINIB = "COc1cc(N(C)CCN(C)C)c(NC(=O)C=C)cc1Nc1nccc(-c2cccc3ccccc23)n1"
