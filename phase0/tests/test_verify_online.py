"""The online check script, driven offline: fixture pages stand in for Google Patents and ChEMBL."""
import io
import json
from urllib.parse import urlsplit

from phase0.sar import discovery, patents
from phase0.tests.test_ledger_intake import DOCS, PAGE
from phase0.tests.test_patents import HTML
from phase0.tools.verify_online import verify

FIXTURE_ID = 'WO2013132376A1'  # the only publication the parser test page answers for
TARGETS = [{'target_chembl_id': 'CHEMBL4247', 'pref_name': 'ALK tyrosine kinase receptor',
            'organism': 'Homo sapiens', 'target_type': 'SINGLE PROTEIN', 'target_components': []}]


def fake_urlopen(calls):
    def urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        path = urlsplit(url).path
        if 'patents.google.com' in url:
            body = HTML.encode()
        elif path.endswith('/target/search.json'):
            body = json.dumps({'targets': TARGETS, 'page_meta': {'total_count': 1}}).encode()
        elif path.endswith('/activity.json'):
            body = json.dumps({'activities': PAGE, 'page_meta': {'total_count': len(PAGE)}}).encode()
        elif path.endswith('/document.json'):
            body = json.dumps({'documents': DOCS}).encode()
        else:
            raise AssertionError(url)
        return io.BytesIO(body)
    return urlopen


def run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(patents, 'urlopen', fake_urlopen(calls))
    monkeypatch.setattr(discovery, 'urlopen', fake_urlopen(calls))
    report = verify(tmp_path / 'out', publications=(FIXTURE_ID,))
    return report, {c['check']: c for c in report['checks']}, calls


def test_full_path_passes_with_changed_page_as_warning(tmp_path, monkeypatch):
    report, checks, calls = run(tmp_path, monkeypatch)
    assert report['counts']['fail'] == 0, report['checks']
    # The fixture page is not the page the evidence package was checked against: cards pause.
    assert checks[f'专利 {FIXTURE_ID}']['status'] == 'warn'
    assert '来源快照已变化' in checks[f'专利 {FIXTURE_ID}']['detail']
    assert checks['靶点检索']['status'] == 'pass'
    assert checks['测量加入台账']['refused']  # rows without value or structure are refused, not guessed
    assert checks['重复加入']['status'] == 'pass'
    assert checks['新增记录状态']['detail'].endswith("状态 ['proposed']（只能是 proposed）")
    assert checks['断网重放']['status'] == 'pass'
    assert report['verdict'] == '通过（有注意项）'
    assert any('patents.google.com' in u for u in calls) and any('ebi.ac.uk' in u for u in calls)
    md = (tmp_path / 'out' / 'report.md').read_text(encoding='utf-8')
    assert '| 断网重放 | 通过 |' in md


def test_unreachable_sources_fail_and_replay_does_not_hide_it(tmp_path, monkeypatch):
    from urllib.error import URLError

    def down(*a, **k):
        raise URLError('blocked')
    monkeypatch.setattr(patents, 'urlopen', down)
    monkeypatch.setattr(discovery, 'urlopen', down)
    report = verify(tmp_path / 'out', publications=(FIXTURE_ID,))
    checks = {c['check']: c for c in report['checks']}
    assert report['verdict'] == '失败'
    assert checks[f'专利 {FIXTURE_ID}']['status'] == 'fail'
    assert checks['断网重放']['status'] == 'warn'
    assert all(c['seconds'] is not None for c in report['checks'])
