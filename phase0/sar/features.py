"""Deterministic molecular feature calculation.

Every number the product ever shows a chemist originates here or in deltas.py.
The LLM never computes a value -- it only renders values produced by this module
into prose. See README "分析层确定性" for why this boundary is load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors, rdCIPLabeler
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

MACROCYCLE_MIN_RING_SIZE = 12

# --- SMARTS library -------------------------------------------------------
# Kept as module constants so tests can assert against individual patterns.

_BASIC_AMINE = Chem.MolFromSmarts(
    "[NX3;!$(N[a]);!$(NC=[O,N,S]);!$(N[S,P]=O);!$(N[N,O]);!$(N#*);!$([N+]);!$(NC#N)]"
)
# Nitrogens whose basicity is materially suppressed by a beta heteroatom
# (morpholine) or an alpha/beta electron-withdrawing group (e.g. 4,4-difluoro-
# piperidine, alpha-CF3). Counted separately because "weakened basic centre"
# is a distinct medicinal-chemistry move from "removed basic centre".
_ATTENUATED_AMINE = Chem.MolFromSmarts(
    "[NX3;!$(N[a]);!$(NC=[O,N,S]);!$(N[S,P]=O);!$(N[N,O]);!$([N+])]"
    "[CX4][CX4;$([CX4][F,Cl,O,N,S]),$([CX4](F)F)]"
)
_ANILINE = Chem.MolFromSmarts("[NX3;!$(NC=[O,N,S]);!$(N[S,P]=O)][a]")

WARHEAD_SMARTS: dict[str, str] = {
    "acrylamide": "[CX3]=[CX3][CX3](=O)[NX3]",
    "propiolamide": "[CX2]#[CX2][CX3](=O)[NX3]",
    "cyanoacrylamide": "[CX3](C#N)=[CX3][CX3](=O)[NX3]",
    "chloroacetamide": "[Cl,Br,I][CX4H2][CX3](=O)[NX3]",
    "vinyl_sulfonamide": "[CX3]=[CX3][SX4](=O)(=O)[NX3]",
    "aldehyde_warhead": "[CX3H1](=O)[#6]",
    "boronic_acid": "[BX3]([OX2H1])[OX2H1]",
    "epoxide": "C1OC1",
    "sulfonyl_fluoride": "[SX4](=O)(=O)[F]",
    "nitrile_warhead": "[NX3,SX2H1][CX4][CX2]#[NX1]",
}
_WARHEADS = {k: Chem.MolFromSmarts(v) for k, v in WARHEAD_SMARTS.items()}

# sp3 carbon bearing at least one H, attached to an aromatic CARBON.
# Must be [c] not [a]: with [a] an N-methyl on a pyrazole counts as benzylic,
# which inflated crizotinib->lorlatinib from 1->3 to 2->6 and would have had the
# narrative layer describing an N-methylation as benzylic blocking.
_BENZYLIC_C = Chem.MolFromSmarts("[CX4;!H0][c]")
# sp3 C-H on an aromatic nitrogen -- an N-dealkylation soft spot, not a benzylic one
_N_ALKYL_C = Chem.MolFromSmarts("[CX4;!H0][n]")
# sp3 carbon bearing at least one H, alpha to O/N/S (classic CYP soft spot)
_ALPHA_HETERO_C = Chem.MolFromSmarts("[CX4;!H0][O,N,S]")

NUMERIC_FEATURES = (
    "mw",
    "clogp",
    "tpsa",
    "fsp3",
    "hbd",
    "hba",
    "rotb",
    "heavy_atoms",
    "ring_count",
    "aromatic_rings",
    "saturated_rings",
    "aromatic_n_count",
    "f_count",
    "cl_count",
    "halogen_count",
    "d_count",
    "basic_amine_count",
    "strong_basic_amine_count",
    "aniline_count",
    "benzylic_h_count",
    "n_alkyl_h_count",
    "alpha_heteroatom_h_count",
    "chiral_centers_defined",
    "chiral_centers_total",
    "max_ring_size",
)

BOOLEAN_FEATURES = (
    "has_macrocycle",
    "has_warhead",
    "has_strong_basic_center",
)

# Human-readable labels used in the FACTS block handed to the LLM.
FEATURE_LABELS: dict[str, str] = {
    "mw": "分子量",
    "clogp": "cLogP (Crippen)",
    "tpsa": "TPSA",
    "fsp3": "Fsp3",
    "hbd": "氢键给体数",
    "hba": "氢键受体数",
    "rotb": "可旋转键数",
    "heavy_atoms": "重原子数",
    "ring_count": "环数",
    "aromatic_rings": "芳环数",
    "saturated_rings": "饱和环数",
    "aromatic_n_count": "芳香氮原子数",
    "f_count": "氟原子数",
    "cl_count": "氯原子数",
    "halogen_count": "卤原子总数",
    "d_count": "氘原子数",
    "basic_amine_count": "脂肪胺数",
    "strong_basic_amine_count": "强碱性胺数",
    "aniline_count": "芳胺数",
    "benzylic_h_count": "苄位氢数",
    "n_alkyl_h_count": "芳氮 N-烷基氢数",
    "alpha_heteroatom_h_count": "α-杂原子位氢数",
    "chiral_centers_defined": "已定义手性中心数",
    "chiral_centers_total": "手性中心总数",
    "max_ring_size": "最大环尺寸",
    "has_macrocycle": "大环",
    "has_warhead": "共价弹头",
    "has_strong_basic_center": "强碱性中心",
}


class StructureError(ValueError):
    """Raised when a SMILES cannot be parsed or fails its formula gate."""


@dataclass
class FeatureSet:
    smiles: str
    canonical_smiles: str
    inchikey: str
    formula: str
    murcko_scaffold: str
    numeric: dict[str, float]
    boolean: dict[str, bool]
    warhead_types: list[str] = field(default_factory=list)

    def get(self, name: str) -> Any:
        if name in self.numeric:
            return self.numeric[name]
        return self.boolean.get(name)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("numeric")
        d.pop("boolean")
        d.update(self.numeric)
        d.update(self.boolean)
        return d


def _count_matched_hydrogens(mol: Chem.Mol, patt: Chem.Mol) -> int:
    """Total H count on the first atom of every match (deduplicated by atom)."""
    seen: set[int] = set()
    total = 0
    for match in mol.GetSubstructMatches(patt):
        idx = match[0]
        if idx in seen:
            continue
        seen.add(idx)
        total += mol.GetAtomWithIdx(idx).GetTotalNumHs()
    return total


def _max_ring_size(mol: Chem.Mol) -> int:
    rings = Chem.GetSymmSSSR(mol)
    return max((len(r) for r in rings), default=0)


def _deuterium_count(mol: Chem.Mol) -> int:
    n = 0
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 1 and atom.GetIsotope() == 2:
            n += 1
        # explicit [2H] written as isotope on H, or D attached implicitly
    return n


def compute_features(smiles: str, expected_formula: str | None = None) -> FeatureSet:
    """Compute the deterministic feature vector for one structure.

    ``expected_formula`` is the provenance gate: a hand-curated row must declare
    the molecular formula it believes it drew, and we refuse the row if RDKit
    disagrees. This catches the single most common curation error (a mis-drawn
    or mis-transcribed structure) before it ever reaches the narrative layer.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise StructureError(f"SMILES 无法解析: {smiles!r}")

    formula = rdMolDescriptors.CalcMolFormula(mol)
    if expected_formula:
        want = expected_formula.strip()
        if formula != want:
            raise StructureError(
                f"分子式校验失败: SMILES 算得 {formula}, CSV 声明 {want}. "
                "结构可能画错或声明写错 —— 两者都必须人工核对后才能进入管道。"
            )

    mol_h = Chem.AddHs(mol)
    try:
        rdCIPLabeler.AssignCIPLabels(mol)
    except Exception:  # pragma: no cover - CIP labeller is best-effort
        pass

    stereo_all = Chem.FindMolChiralCenters(
        mol, includeUnassigned=True, useLegacyImplementation=False
    )
    stereo_defined = [c for c in stereo_all if c[1] != "?"]

    numeric: dict[str, float] = {
        "mw": round(Descriptors.MolWt(mol), 2),
        "clogp": round(Crippen.MolLogP(mol), 2),
        "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 2),
        "fsp3": round(rdMolDescriptors.CalcFractionCSP3(mol), 3),
        "hbd": float(rdMolDescriptors.CalcNumHBD(mol)),
        "hba": float(rdMolDescriptors.CalcNumHBA(mol)),
        "rotb": float(rdMolDescriptors.CalcNumRotatableBonds(mol)),
        "heavy_atoms": float(mol.GetNumHeavyAtoms()),
        "ring_count": float(rdMolDescriptors.CalcNumRings(mol)),
        "aromatic_rings": float(rdMolDescriptors.CalcNumAromaticRings(mol)),
        "saturated_rings": float(rdMolDescriptors.CalcNumSaturatedRings(mol)),
        "aromatic_n_count": float(
            sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() == 7 and a.GetIsAromatic())
        ),
        "f_count": float(sum(1 for a in mol.GetAtoms() if a.GetSymbol() == "F")),
        "cl_count": float(sum(1 for a in mol.GetAtoms() if a.GetSymbol() == "Cl")),
        "halogen_count": float(
            sum(1 for a in mol.GetAtoms() if a.GetSymbol() in ("F", "Cl", "Br", "I"))
        ),
        "d_count": float(_deuterium_count(mol_h)),
        "basic_amine_count": float(len(mol.GetSubstructMatches(_BASIC_AMINE))),
        "aniline_count": float(len(mol.GetSubstructMatches(_ANILINE))),
        "benzylic_h_count": float(_count_matched_hydrogens(mol, _BENZYLIC_C)),
        "n_alkyl_h_count": float(_count_matched_hydrogens(mol, _N_ALKYL_C)),
        "alpha_heteroatom_h_count": float(_count_matched_hydrogens(mol, _ALPHA_HETERO_C)),
        "chiral_centers_defined": float(len(stereo_defined)),
        "chiral_centers_total": float(len(stereo_all)),
        "max_ring_size": float(_max_ring_size(mol)),
    }

    attenuated = {m[0] for m in mol.GetSubstructMatches(_ATTENUATED_AMINE)}
    basic_idx = {m[0] for m in mol.GetSubstructMatches(_BASIC_AMINE)}
    numeric["strong_basic_amine_count"] = float(len(basic_idx - attenuated))

    warheads = [name for name, patt in _WARHEADS.items() if mol.HasSubstructMatch(patt)]

    boolean = {
        "has_macrocycle": numeric["max_ring_size"] >= MACROCYCLE_MIN_RING_SIZE,
        "has_warhead": bool(warheads),
        "has_strong_basic_center": numeric["strong_basic_amine_count"] > 0,
    }

    scaffold = ""
    try:
        from rdkit.Chem.Scaffolds import MurckoScaffold

        scaffold = Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(mol))
    except Exception:  # pragma: no cover
        pass

    return FeatureSet(
        smiles=smiles,
        canonical_smiles=Chem.MolToSmiles(mol),
        inchikey=Chem.MolToInchiKey(mol),
        formula=formula,
        murcko_scaffold=scaffold,
        numeric=numeric,
        boolean=boolean,
        warhead_types=warheads,
    )
