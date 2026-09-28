"""Matched molecular pairs over the evidence ledger (single-cut, with hydrogen replacement).

A pair is two compounds that share a constant part ("key", with one attachment
point ``[*:1]``) and differ only in the variable part. Hydrogen counts as a
variable part, so F <-> H or CH3 <-> H swaps are found. Following the usual
MMP practice the variable part must be small (``max_change`` heavy atoms) and
no larger than the constant part. When a pair is reachable through several
cuts, the one with the smallest change is kept.

Pairs are only structural. Whether their measurements can be compared is
decided later, per assay, by ``project_sar``.
"""
from collections import defaultdict

from rdkit import Chem, rdBase
from rdkit.Chem import rdMMPA

MAX_CHANGE = 12
H = '[H][*:1]'


def _canon(smiles):
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol, isomericSmiles=True) if mol is not None else None


def _heavy(smiles):
    if smiles == H:
        return 0
    mol = Chem.MolFromSmiles(smiles)
    return mol.GetNumHeavyAtoms() - 1  # minus the attachment point


def fragments(smiles, max_change=MAX_CHANGE):
    """(key, value) pairs for one structure: every single cut, plus H on every atom that carries one."""
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return set()
    out = set()
    for _core, chains in rdMMPA.FragmentMol(mol, maxCuts=1, resultsAsMols=False):
        a, b = chains.split('.')
        for key, value in ((a, b), (b, a)):
            key, value = _canon(key), _canon(value)
            if key and value and _heavy(value) <= min(max_change, _heavy(key)):
                out.add((key, value))
    for atom in mol.GetAtoms():
        if atom.GetTotalNumHs() == 0:
            continue
        rw = Chem.RWMol(mol)
        dummy = rw.AddAtom(Chem.Atom(0))
        rw.GetAtomWithIdx(dummy).SetAtomMapNum(1)
        rw.AddBond(atom.GetIdx(), dummy, Chem.BondType.SINGLE)
        target = rw.GetAtomWithIdx(atom.GetIdx())
        target.SetNoImplicit(True)
        target.SetNumExplicitHs(max(0, atom.GetTotalNumHs() - 1))
        try:
            Chem.SanitizeMol(rw)
        except Exception:
            continue
        out.add((Chem.MolToSmiles(rw, isomericSmiles=True), H))
    return out


def find_pairs(compounds, max_change=MAX_CHANGE):
    """Matched pairs among ``compounds`` (objects with ``id`` and ``smiles``).

    Returns dicts with the two compound ids, the constant part and the variable
    parts, oriented so the larger variable part is ``a`` (ties by SMILES), which
    keeps each transformation in one reading direction.
    """
    index = defaultdict(set)
    for c in compounds:
        for key, value in fragments(c.smiles, max_change):
            index[key].add((c.id, value))
    best = {}
    for key, members in index.items():
        members = sorted(members)
        for i, (x, vx) in enumerate(members):
            for y, vy in members[i + 1:]:
                if x == y or vx == vy:
                    continue
                (a, va), (b, vb) = sorted([(x, vx), (y, vy)], key=lambda t: (-_heavy(t[1]), t[1], t[0]))
                change = max(_heavy(va), _heavy(vb))
                pair = tuple(sorted((a, b)))
                current = best.get(pair)
                if current is None or (change, key) < (current['change'], current['key']):
                    best[pair] = {'a': a, 'b': b, 'key': key, 'from': va, 'to': vb, 'change': change}
    return sorted(best.values(), key=lambda p: (p['from'], p['to'], p['a'], p['b']))


def transform(pair):
    return f"{pair['from']}>>{pair['to']}"
