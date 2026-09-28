"""Real-browser checks of the three main paths, fully offline.

Optional: needs ``requirements-browser.txt`` and a Chromium build. Skipped when
Playwright is not installed or no browser can be launched. Point
``SAR_CHROMIUM`` at a Chromium executable when the one bundled with the
installed Playwright version is missing; set ``SAR_REQUIRE_BROWSER=1`` (as the
CI browser job does) to fail instead of skip.

Network sources are replaced by fixtures: the patent page by the parser test
page (or the committed evidence packages for the cross-family case) and
ChEMBL by a fixed activity page.
"""
import glob
import os
import threading

import pytest

sync_api = pytest.importorskip('playwright.sync_api')

from phase0.ledger import access
from phase0.ledger.migrate import build
from phase0.ledger.store import LedgerStore
from phase0.sar import discovery, serve
from phase0.sar.patents import parse_patent
from phase0.tests.test_ledger_intake import DOCS, PAGE
from phase0.tests.test_lineage import EARLY, EXPECTED, LATE, document
from phase0.tests.test_patents import HTML

LORLATINIB = 'C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21'  # WO2013132376A1 Example 2
STRUCTURE_HITS = [
    {'molecule_chembl_id': 'CHEMBL9000001', 'pref_name': 'SAME', 'similarity': '100.0',
     'molecule_structures': {'canonical_smiles': LORLATINIB, 'standard_inchi_key': 'X'}},
    {'molecule_chembl_id': 'CHEMBL9000002', 'pref_name': 'UNRELATED', 'similarity': '71.0',
     'molecule_structures': {'canonical_smiles': 'CCOCC', 'standard_inchi_key': 'Y'}},
]
FIXTURE_ID = 'WO2013132376A1'  # the only publication the parser test page answers for
TARGETS = [{'target_chembl_id': 'CHEMBL4247', 'pref_name': 'ALK tyrosine kinase receptor',
            'organism': 'Homo sapiens', 'target_type': 'SINGLE PROTEIN', 'target_components': []}]


def _executables():
    if os.environ.get('SAR_CHROMIUM'):
        yield os.environ['SAR_CHROMIUM']
    yield None  # the build matching the installed Playwright
    yield from sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux/chrome'), reverse=True)


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        for path in _executables():
            try:
                b = p.chromium.launch(executable_path=path)
                break
            except Exception:
                continue
        else:
            if os.environ.get('SAR_REQUIRE_BROWSER'):
                pytest.fail('no launchable Chromium')
            pytest.skip('no launchable Chromium; set SAR_CHROMIUM')
        yield b
        b.close()


def fake_retrieve(withhold_citation=False):
    def retrieve(pid, cache):
        if pid not in EXPECTED:
            raise ValueError('fixture has no page for ' + pid)
        d = {**parse_patent(HTML, FIXTURE_ID), **document(pid)}  # page layout from the parser fixture
        if withhold_citation and pid == LATE:
            d['references'] = []
        d['source_snapshot'] = {**d['source_snapshot'], 'url': d['source_url'],
                                'retrieved_at': 'fixture', 'cache_hit': True}
        return d
    return retrieve


def fake_fetch(endpoint, params, cache):
    src = {'url': endpoint, 'sha256': 'f' * 64, 'retrieved_at': 'fixture', 'cache_hit': True}
    if endpoint == 'target/search':
        return {'targets': TARGETS, 'page_meta': {'total_count': 1}}, src
    if endpoint == 'activity':
        return {'activities': PAGE, 'page_meta': {'total_count': len(PAGE)}}, src
    if endpoint == 'document':
        return {'documents': DOCS}, src
    if endpoint.startswith(('similarity/', 'substructure/')):
        return {'molecules': STRUCTURE_HITS, 'page_meta': {'total_count': 40, 'next': '/next'}}, src
    raise AssertionError(endpoint)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Server on a free port with a fresh ledger; yields (base_url, store, set_retrieve)."""
    db = tmp_path / 'ledger.sqlite'
    store = LedgerStore(db)
    store.import_ledger(build(), 'browser-test')
    monkeypatch.setenv(access.ENV, str(db))
    monkeypatch.setattr(discovery, 'fetch', fake_fetch)
    monkeypatch.setattr(serve, 'retrieve', fake_retrieve())
    monkeypatch.setattr(serve.Handler, 'log_message', lambda *a: None)
    server = serve.create_server(0, tmp_path / 'cache', ledger_db=db)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (f'http://127.0.0.1:{server.server_port}', store,
           lambda **kw: monkeypatch.setattr(serve, 'retrieve', fake_retrieve(**kw)))
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    pg = context.new_page()
    pg.errors = []
    pg.on('pageerror', lambda e: pg.errors.append(str(e)))
    yield pg
    context.close()


def wait_status(pg, text):
    pg.wait_for_function('t => document.querySelector("#status").textContent.includes(t)', arg=text,
                         timeout=15000)
    return pg.text_content('#status')


def test_add_patent_and_chembl_page_to_ledger(app, page):
    base, store, _ = app
    page.goto(base + '/')
    page.fill('#publication', 'WO2013132376A1')
    page.click('#submit')
    add = page.get_by_role('button', name='将本专利及索引结构加入台账（待确认）')
    add.click()
    first = wait_status(page, '已加入台账')
    add.click()
    wait_status(page, '无新增')

    page.select_option('#input-mode', 'target')
    page.fill('#publication', 'ALK')
    page.click('#submit')
    page.get_by_role('button', name='查看此靶点的测量与分子').click()
    page.get_by_role('button', name='将本页测量加入台账（待确认）').click()
    chembl = wait_status(page, '测量')

    led = store.load()
    added = [o for o in led.observations if o.id.startswith('chembl:')]
    assert '待确认' in first and added
    assert {o.review.record_status for o in added} == {'proposed'}
    assert '拒绝' in chembl or '未加入' in chembl  # rows without value or structure are refused, not guessed
    assert page.errors == []


def test_cross_family_case_shows_edge_and_mass_check(app, page):
    base, _, _ = app
    page.goto(base + '/')
    page.click('#load-lineage')
    page.wait_for_selector('#lineage-content h3:has-text("可核查事实")', timeout=15000)
    lineage = page.text_content('#lineage-content')
    assert '他们可能在解决什么问题' in lineage and '仍缺少的证据' in lineage
    assert '未展示' not in lineage

    page.click('[data-tab="evidence"]')
    marks = page.locator('#evidence-cards .mass')
    assert marks.count() == 3
    assert all(t.startswith('质谱校验：一致') for t in marks.all_text_contents())
    assert set(page.eval_on_selector_all('#evidence-cards .mass', 'n => n.map(x => x.dataset.massStatus)')) \
        == {'consistent'}
    assert page.errors == []


def test_withheld_relation_says_which_check_failed(app, page):
    base, _, set_retrieve = app
    set_retrieve(withhold_citation=True)
    page.goto(base + '/')
    page.click('#load-lineage')
    withheld = page.locator('#lineage-content h3:has-text("未展示")')
    withheld.wait_for(timeout=15000)
    text = page.text_content('#lineage-content')
    assert f'{LATE} 的引用列表不含 {EARLY}' in text
    assert '可核查事实' not in text
    assert page.errors == []


def pick(pg, select, label):
    value = pg.eval_on_selector(select, '(s, t) => [...s.options].find(o => o.textContent.includes(t))?.value',
                                label)
    assert value, f'{label} not offered in {select}'
    pg.select_option(select, value)


def test_six_step_workflow_keeps_opposite_directions_apart(app, page):
    base, _, _ = app
    page.goto(base + '/evidence')
    page.wait_for_function('document.querySelectorAll("#pair-document option").length > 1')
    page.select_option('#pair-document', 'CHEMBL3286195')
    pick(page, '#pair-a', '/ 6f ·')
    pick(page, '#pair-b', '/ 6e ·')
    page.click('#align-pair')
    page.wait_for_function('document.querySelector("#pair-status").textContent.includes("结构分析已返回")')
    assert 'R 标签差异' in page.text_content('#alignment-result')

    page.click('#compare-pair')
    page.wait_for_function('document.querySelector("#comparison-result").textContent.includes("B/A")')
    assert '非改善倍数' in page.text_content('#comparison-result')

    page.click('#add-sar-pair')
    page.click('#run-sar-summary')
    page.wait_for_function('document.querySelector("#sar-status").textContent.includes("分析完成")')
    groups = {h.split(' · ')[1]: None for h in page.locator('#sar-summary-result h3').all_text_contents()}
    for card in page.locator('#sar-summary-result article').all():
        assay = card.locator('h3').text_content().split(' · ')[1]
        groups[assay] = card.text_content()
    assert '数值降低 1 / 升高 0' in groups['CHEMBL3293161']
    assert '数值降低 0 / 升高 1' in groups['CHEMBL3293391']

    page.click('#use-sar-a')
    page.select_option('#sar-assay', 'CHEMBL3293161')
    page.select_option('#sar-goal', 'lower')
    page.click('#run-sar-suggest')
    page.wait_for_selector('#sar-suggestions h3')
    lower = page.text_content('#sar-suggestions')
    page.select_option('#sar-assay', 'CHEMBL3293391')
    page.click('#run-sar-suggest')
    page.wait_for_function('t => document.querySelector("#sar-suggestions").textContent !== t', arg=lower)
    page.wait_for_selector('#sar-suggestions h3')
    other = page.text_content('#sar-suggestions')
    assert '待验证候选方向' in lower
    assert '待验证候选方向' not in other and '相反方向或未变的已选案例' in other
    assert page.errors == []


def test_similarity_search_shows_ranked_local_hits_and_flags_chembl_disagreement(app, page):
    base, _, _ = app
    page.goto(base + '/')
    page.select_option('#input-mode', 'smiles')
    page.select_option('#search-method', 'similarity')
    assert page.is_visible('#search-threshold')
    page.fill('#search-threshold', '90')
    page.fill('#publication', LORLATINIB + '.Cl')
    page.click('#submit')
    wait_status(page, '检索完成')
    text = page.text_content('#discovery-content')
    assert 'Tanimoto ≥ 90%' in text and '去除盐和溶剂' in text and '输入结构（标准化前）' in text
    hits = page.locator('.ledger-hits .panel')
    assert hits.count() == 2 and all('相似度 1.000' in t for t in hits.all_text_contents())

    page.get_by_role('button', name='查询 ChEMBL（发送标准化后的 SMILES）').click()
    wait_status(page, '检索完成')
    assert '共 40 个' in page.text_content('#discovery-content')
    assert '仅显示前 2 个' in page.text_content('#discovery-content')
    checks = page.eval_on_selector_all('.chembl-hit', 'n => n.map(x => x.dataset.localCheck)')
    assert checks == ['agrees', 'disagrees']
    assert page.errors == []


def test_surechembl_hits_lead_to_patent_loading(app, page, monkeypatch):
    from phase0.sar import surechembl
    from phase0.tests.test_surechembl import Server
    monkeypatch.setattr(surechembl, 'urlopen', Server())
    monkeypatch.setattr(surechembl, 'SLEEP', lambda seconds: None)
    base, _, _ = app
    page.goto(base + '/')
    page.select_option('#input-mode', 'smiles')
    page.select_option('#search-method', 'similarity')
    page.check('#surechembl-search')
    page.fill('#publication', LORLATINIB)
    page.click('#submit')
    wait_status(page, '检索完成')
    section = page.text_content('.surechembl')
    assert 'SureChEMBL 专利化学命中 · 共 4 个' in section and 'CC BY 4.0' in section
    assert '1 个命中的 SureChEMBL 相似度低于所选阈值' in section
    checks = page.eval_on_selector_all('.surechembl-hit', 'n => n.map(x => x.dataset.localCheck)')
    assert checks == ['agrees', 'agrees', 'disagrees']

    page.locator('.surechembl-hit').first.get_by_role('button', name='查看含此化合物的专利').click()
    wait_status(page, '检索完成')
    assert 'SureChEMBL 专利 · SCHEMBL200' in page.text_content('#discovery-content')
    page.get_by_role('button', name='核实并加载专利 WO2013132376A1').click()
    page.wait_for_selector('#evidence-cards .mass', timeout=15000)  # the curated patent opens with its cards
    assert page.errors == []


def test_pubchem_links_from_structure_to_curated_patent(app, page, monkeypatch):
    from phase0.sar import pubchem
    from phase0.tests.test_pubchem import Server
    monkeypatch.setattr(pubchem, 'urlopen', Server())
    base, _, _ = app
    page.goto(base + '/')
    page.select_option('#input-mode', 'smiles')
    page.fill('#publication', LORLATINIB)
    page.click('#submit')
    wait_status(page, '检索完成')
    page.get_by_role('button', name='查询检索结构的 PubChem 专利与文献（发送标准 InChIKey）').click()
    wait_status(page, '检索完成')
    text = page.text_content('#discovery-content')
    assert '关联专利 · 共 28 份（含已整理 WO2011138751A2、WO2013132376A1）' in text
    assert '关联 PubMed 文献 · 共 3 篇' in text and '不当作相互独立的佐证' in text
    flags = page.eval_on_selector_all('.pubchem-patent', 'n => n.map(x => x.dataset.curated)')
    assert flags[:3] == ['true', 'true', 'false'] and len(flags) == 20
    page.get_by_role('button', name='核实并加载专利 WO2013132376A1').click()
    page.wait_for_selector('#evidence-cards .mass', timeout=15000)
    assert page.errors == []
