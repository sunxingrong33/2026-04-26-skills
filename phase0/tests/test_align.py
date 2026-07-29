"""Golden tests for core-hop detection.

This is the module that already produced one confident wrong answer (MCS with
completeRingsOnly=True called crizotinib->lorlatinib a core hop at coverage
0.23). Every retune must run these.
"""

import pytest
from rdkit import Chem

from phase0.sar.align import (
    CORE_HOP_JACCARD_THRESHOLD,
    align_generations,
    ring_systems,
)
from phase0.tests.fixtures import CORE_PAIRS, SMILES


def _mol(name):
    return Chem.MolFromSmiles(SMILES[name])


@pytest.mark.parametrize("pair,same_core", sorted(CORE_PAIRS.items()))
def test_core_hop_verdicts(pair, same_core):
    a, b = pair
    al = align_generations([_mol(a)], [_mol(b)], run_mcs=False)
    assert al is not None
    assert al.is_core_hop is (not same_core), (
        f"{a}/{b}: jaccard={al.ring_jaccard} verdict={al.label}, expected "
        f"{'retained' if same_core else 'core hop'}"
    )


def test_threshold_has_margin_on_the_golden_set():
    """A threshold that only just separates the classes is not a threshold."""
    retained, hopped = [], []
    for (a, b), same in CORE_PAIRS.items():
        al = align_generations([_mol(a)], [_mol(b)], run_mcs=False)
        (retained if same else hopped).append(al.ring_jaccard)
    assert min(retained) > max(hopped)
    assert min(retained) - max(hopped) >= 0.20
    assert max(hopped) < CORE_HOP_JACCARD_THRESHOLD < min(retained)


def test_macrocyclisation_does_not_read_as_a_core_hop():
    """The flagship case. Macrocycles are excluded from the ring set on purpose."""
    al = align_generations([_mol("crizotinib")], [_mol("lorlatinib")], run_mcs=False)
    assert al.is_core_hop is False
    assert al.lost == {"C1CCNCC1"}          # the piperidine goes away
    assert "c1cnnc1" in al.shared           # the pyrazole is retained
    assert "c1ccncc1" in al.shared          # so is the pyridine


def test_macrocycle_excluded_from_ring_set():
    rings = ring_systems(_mol("lorlatinib"))
    assert all(len(r) < 40 for r in rings)
    assert "C1CCNCC1" not in rings


def test_generic_ring_overlap_is_flagged():
    """Sharing only a benzene must not pass silently as core retention."""
    al = align_generations([_mol("brigatinib")], [_mol("lorlatinib")], run_mcs=False)
    assert al.shared == {"c1ccccc1"}
    assert al.shared_informative == set()
    assert al.notes, "expected a low-information warning"


def test_alignment_is_symmetric():
    fwd = align_generations([_mol("crizotinib")], [_mol("ceritinib")], run_mcs=False)
    rev = align_generations([_mol("ceritinib")], [_mol("crizotinib")], run_mcs=False)
    assert fwd.ring_jaccard == rev.ring_jaccard
    assert fwd.lost == rev.gained


def test_empty_generation_returns_none():
    assert align_generations([], [_mol("crizotinib")]) is None
