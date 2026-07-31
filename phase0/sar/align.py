"""Cross-generation scaffold comparison.

DESIGN NOTE -- why this is not the MCS approach it started as.

The obvious implementation (and the one in the original plan) is: run rdFMCS
between adjacent generations and call it a core hop when coverage < 0.60. We
tried that and it does not work. Measured on the crizotinib/ceritinib/
brigatinib/alectinib/lorlatinib set (8 pairs, known chemotypes):

  * whole-molecule MCS, best of 24 parameter combinations -> margin -0.03
    (no threshold separates "same core" from "core hop")
  * Murcko-scaffold MCS                                   -> margin -0.48
  * ring-system Jaccard (this module)                     -> margin +0.35

Two reasons MCS fails here. First, whole-molecule coverage is dominated by
peripheral substituents, so it answers a question about the molecule rather than
about the core. Second, ``completeRingsOnly=True`` is actively wrong for the one
event the product most wants to catch: after macrocyclisation the old ring atoms
also belong to the macrocycle, so requiring complete rings drags the macrocycle
into the match and it collapses (crizotinib/lorlatinib -> 7 atoms, coverage
0.23, i.e. a confident *false* core-hop call on the flagship example).

So core-hop detection is keyed on ring-system overlap, which is also how a
chemist states it ("kept the aminopyridine and the pyrazole, dropped the
piperidine"). MCS is still computed, but only as descriptive colour -- no rule
depends on it.

CALIBRATION CAVEAT: the 0.55 threshold is fitted on 8 pairs with 2 positives.
That is enough to reject MCS, not enough to trust the number. It must be
re-fitted on the §5 benchmark before anyone reads a core-hop call as a finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdFMCS

# Rings at or above this size are macrocycles; they are excluded from the ring
# set and handled by the has_macrocycle feature instead, so that forming a
# macrocycle does not by itself look like a core hop.
MACROCYCLE_MIN_RING_SIZE = 12

CORE_HOP_JACCARD_THRESHOLD = 0.55
MCS_TIMEOUT_SECONDS = 10

# Rings so common that sharing them says nothing about core identity. Reported
# separately so the evidence text can distinguish "both contain a benzene" from
# "both retain the aminopyridine".
GENERIC_RINGS = {"c1ccccc1", "C1CCNCC1", "C1CCCCC1", "C1CCOCC1", "C1COCCN1", "C1CNCCN1"}


def _normalise_ring(sub: Chem.Mol) -> str:
    """Canonical SMILES of a ring skeleton, independent of its substitution state.

    Without this an N-methylpyrazole yields ``c1cnnc1`` while a free NH pyrazole
    yields ``c1cn[nH]c1``, so methylating a ring NH reads as losing one ring
    system and gaining another -- deflating the core-hop Jaccard for what is
    actually the same core. Ring identity here means the ring skeleton (atoms +
    aromaticity); what hangs off it is the substituent layer's business.
    """
    rw = Chem.RWMol(sub)
    for atom in rw.GetAtoms():
        atom.SetNumExplicitHs(0)
        atom.SetNoImplicit(True)
        atom.SetFormalCharge(0)
        atom.SetIsotope(0)
    return Chem.MolToSmiles(rw)


def ring_systems(mol: Chem.Mol) -> set[str]:
    """Canonical skeletons of each non-macrocyclic SSSR ring."""
    out: set[str] = set()
    for ring in Chem.GetSymmSSSR(mol):
        idx = set(ring)
        if len(idx) >= MACROCYCLE_MIN_RING_SIZE:
            continue
        bonds = [
            b.GetIdx()
            for b in mol.GetBonds()
            if b.GetBeginAtomIdx() in idx and b.GetEndAtomIdx() in idx
        ]
        if not bonds:
            continue
        try:
            out.add(_normalise_ring(Chem.PathToSubmol(mol, bonds)))
        except Exception:  # pragma: no cover - degenerate fragments
            continue
    return out


@dataclass
class Alignment:
    rings_from: set[str]
    rings_to: set[str]
    shared: set[str]
    lost: set[str]
    gained: set[str]
    ring_jaccard: float
    ring_containment: float
    shared_informative: set[str]
    is_core_hop: bool
    mcs_smarts: str = ""
    mcs_num_atoms: int = 0
    mcs_coverage: float = 0.0
    mcs_timed_out: bool = False
    n_from: int = 0
    n_to: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.is_core_hop:
            return "骨架跃迁事件 (core hopping)"
        return "母核保留"

    def describe(self) -> str:
        keep = "、".join(sorted(self.shared)) or "无"
        lost = "、".join(sorted(self.lost)) or "无"
        gained = "、".join(sorted(self.gained)) or "无"
        return f"保留环系: {keep}; 消失: {lost}; 新增: {gained}"


def consensus_ring_set(mols: list[Chem.Mol], min_fraction: float = 0.5) -> set[str]:
    """Rings present in at least ``min_fraction`` of a generation's compounds.

    Not the union. A generation that explores five different solubilising groups
    has a large ring union, which deflates the Jaccard against the next
    generation and biases towards *false* core-hop calls precisely where the data
    is richest -- the opposite of what you want. Requiring a ring to appear in
    half the generation keeps the peripheral exploration out and leaves what the
    generation actually treats as its core.

    Falls back to the union when nothing clears the threshold, so a generation of
    entirely dissimilar compounds still yields something rather than nothing.
    """
    if not mols:
        return set()
    counts: dict[str, int] = {}
    for m in mols:
        for ring in ring_systems(m):
            counts[ring] = counts.get(ring, 0) + 1
    need = max(1, len(mols) * min_fraction)
    consensus = {r for r, c in counts.items() if c >= need}
    return consensus or set(counts)


def align_generations(
    mols_from: list[Chem.Mol],
    mols_to: list[Chem.Mol],
    run_mcs: bool = True,
    timeout: int = MCS_TIMEOUT_SECONDS,
) -> Alignment | None:
    a = [m for m in mols_from if m is not None]
    b = [m for m in mols_to if m is not None]
    if not a or not b:
        return None

    ra, rb = consensus_ring_set(a), consensus_ring_set(b)
    shared = ra & rb
    union = ra | rb
    jaccard = len(shared) / len(union) if union else 0.0
    containment = len(shared) / min(len(ra), len(rb)) if ra and rb else 0.0
    informative = shared - GENERIC_RINGS

    notes: list[str] = []
    if shared and not informative:
        notes.append(
            "共有环系全部是苯环/哌啶这类通用环，母核判定的信息量很低，建议人工复核"
        )

    mcs_smarts, mcs_atoms, mcs_cov, timed_out = "", 0, 0.0, False
    if run_mcs:
        result = rdFMCS.FindMCS(
            a + b,
            atomCompare=rdFMCS.AtomCompare.CompareAny,
            bondCompare=rdFMCS.BondCompare.CompareAny,
            ringMatchesRingOnly=True,
            completeRingsOnly=False,
            timeout=timeout,
        )
        timed_out = bool(result.canceled)
        mcs_smarts = result.smartsString
        mcs_atoms = result.numAtoms
        smallest = min(m.GetNumHeavyAtoms() for m in a + b)
        mcs_cov = round(mcs_atoms / smallest, 3) if smallest else 0.0

    return Alignment(
        rings_from=ra,
        rings_to=rb,
        shared=shared,
        lost=ra - rb,
        gained=rb - ra,
        ring_jaccard=round(jaccard, 3),
        ring_containment=round(containment, 3),
        shared_informative=informative,
        is_core_hop=jaccard < CORE_HOP_JACCARD_THRESHOLD,
        mcs_smarts=mcs_smarts,
        mcs_num_atoms=mcs_atoms,
        mcs_coverage=mcs_cov,
        mcs_timed_out=timed_out,
        n_from=len(a),
        n_to=len(b),
        notes=notes,
    )
