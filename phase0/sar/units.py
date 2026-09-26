"""Small, explicit unit vocabulary; never guess an unknown conversion."""
import math

CONCENTRATION_TO_NM = {"M": 1e9, "mM": 1e6, "uM": 1e3, "nM": 1, "pM": 1e-3}


def normalise(value: float, unit: str) -> tuple[float, str]:
    if not math.isfinite(value):
        raise ValueError("测量值必须是有限数值")
    unit = unit.strip().replace("µ", "u").replace("μ", "u")
    if unit in CONCENTRATION_TO_NM:
        return value * CONCENTRATION_TO_NM[unit], "nM"
    return value, unit


def assays_comparable(a: list[str], b: list[str]) -> bool:
    # One known identical protocol on each side, not merely a shared label
    # among multiple incompatible protocols. Missing labels are unknown.
    return len(a) == len(b) == 1 and bool(a[0]) and a == b
