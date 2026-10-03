"""Reported MS value versus the mass computed from a transcribed structure.

A cheap first gate for structure transcription: a wrong atom count shows up as
a mass mismatch. A match proves nothing about identity -- isomers, regio- and
stereo-errors keep the same formula -- so a passing check is never a
confirmation, only the absence of one kind of error.

Tolerance follows the precision the source reported:

* integer (nominal, low-resolution LCMS)  +-0.6 Da; still rejects a one-H error
* one or two decimals                      +-0.3 Da
* three or more decimals (HRMS)            +-10 ppm

When the declared ion does not fit, common alternatives are tried and reported
as ``alternative_match`` for a human to look at, never silently accepted.

    python -m phase0.sar.mass_check      # check the committed patent evidence cards
"""
import json
import re
import sys

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors

PROTON = 1.007276
SODIUM = 22.989218
ISOTOPE_M2 = {'Cl': 1.997050, 'Br': 1.997953}
IONS = {
    '[M+H]+': lambda m: m + PROTON,
    '[M+Na]+': lambda m: m + SODIUM,
    '[M-H]-': lambda m: m - PROTON,
    '[M+2H]2+': lambda m: (m + 2 * PROTON) / 2,
    'M': lambda m: m,
}
NOTE = '质量一致只说明分子式层面相符；同分异构、区域或立体化学错误无法由此发现。'


def _decimals(reported):
    text = reported if isinstance(reported, str) else repr(reported)
    match = re.search(r'\d+(?:\.(\d+))?', text)
    if not match:
        return None, None
    return float(match.group(0)), len(match.group(1) or '')


def tolerance(value, decimals):
    if decimals == 0:
        return 0.6, 'Da', 'nominal'
    if decimals <= 2:
        return 0.3, 'Da', 'low'
    return value * 10e-6, 'ppm', 'high'


def check_mass(smiles, reported, ion='[M+H]+'):
    """Compare a reported m/z (number or text such as '358.2') with the structure."""
    base = {'ion': ion, 'reported': reported, 'note': NOTE}
    if reported in (None, ''):
        return {**base, 'status': 'no_report'}
    value, decimals = _decimals(reported)
    if value is None:
        return {**base, 'status': 'unparsed_report'}
    _ = rdBase.BlockLogs()
    mol = Chem.MolFromSmiles(smiles or '')
    if mol is None:
        return {**base, 'status': 'invalid_structure'}
    if ion not in IONS:
        raise ValueError(f'未知离子类型：{ion}')
    mono = Descriptors.ExactMolWt(mol)
    tol, kind, resolution = tolerance(value, decimals)
    expected = IONS[ion](mono)
    delta = value - expected
    result = {**base, 'status': 'consistent' if abs(delta) <= tol else 'inconsistent',
              'formula': rdMolDescriptors.CalcMolFormula(mol), 'resolution': resolution,
              'computed': round(expected, 4), 'delta': round(delta, 4),
              'tolerance': round(tol, 4), 'tolerance_unit': 'Da',
              'tolerance_rule': '10 ppm' if kind == 'ppm' else f'{tol} Da', 'alternatives': []}
    if result['status'] == 'consistent':
        return result
    symbols = {a.GetSymbol() for a in mol.GetAtoms()}
    candidates = [(name, f(mono)) for name, f in IONS.items() if name != ion]
    candidates += [(f'{ion} 的 {el} 同位素峰（M+2）', expected + shift)
                   for el, shift in ISOTOPE_M2.items() if el in symbols]
    for name, mz in candidates:
        if abs(value - mz) <= tolerance(mz, decimals)[0]:
            result['alternatives'].append({'ion': name, 'computed': round(mz, 4), 'delta': round(value - mz, 4)})
    if result['alternatives']:
        result['status'] = 'alternative_match'
    return result


def summarise(check):
    """One-line reader-facing label: level is ok / warn / none, never 'confirmed'."""
    status = check['status']
    if status == 'no_report':
        return {'level': 'none', 'text': '质谱校验：原文未报告质谱值，未校验'}
    if status == 'unparsed_report':
        return {'level': 'warn', 'text': f"质谱校验：无法解析原文质谱值“{check['reported']}”，需人工核对"}
    if status == 'invalid_structure':
        return {'level': 'warn', 'text': '质谱校验：转录结构无法解析，需人工核对'}
    detail = (f"原文 {check['ion']} {check['reported']}，结构计算 {check['computed']:.2f}，"
              f"差 {check['delta']:+.2f}（容差 {check['tolerance_rule']}）")
    if status == 'consistent':
        return {'level': 'ok', 'text': f'质谱校验：一致 · {detail}'}
    if status == 'alternative_match':
        ions = '、'.join(a['ion'] for a in check['alternatives'])
        return {'level': 'warn', 'text': f'质谱校验：与声明离子不符，但与 {ions} 相符，需人工核对 · {detail}'}
    return {'level': 'warn', 'text': f'质谱校验：不一致，转录结构可能有误，需人工核对 · {detail}'}


def flags_for(check):
    """Extraction flags implied by a mass check (empty when nothing to report)."""
    return {'inconsistent': ['mass_mismatch'], 'alternative_match': ['mass_alternative_ion'],
            'invalid_structure': ['invalid_structure']}.get(check['status'], [])


def flag_prediction(doc, ion='[M+H]+'):
    """Add mass flags to predicted compounds that report a mass; returns the checks."""
    checks = {}
    for c in doc.get('compounds', []):
        check = check_mass(c.get('smiles'), c.get('reported_mass'), ion)
        checks[str(c['example'])] = check
        flags = flags_for(check)
        if flags:
            c['flags'] = sorted(set(c.get('flags', [])) | set(flags))
    return checks


def main(argv=None):
    from .patent_evidence import DATA
    bad = 0
    for path in sorted(DATA.glob('*.json')):
        package = json.loads(path.read_text(encoding='utf-8'))
        for card in package['cards']:
            r = check_mass(card['smiles'], card.get('reported_lcms_m_plus_h'))
            bad += r['status'] != 'consistent'
            detail = (f"报告 {r['reported']} · 计算 {r['computed']} · 差 {r['delta']:+} "
                      f"（{r['resolution']}，容差 {r['tolerance_rule']}）") if 'computed' in r else ''
            print(f"{package['publication']} Example {card['example']}: {r['status']} {detail}")
    print(NOTE)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
