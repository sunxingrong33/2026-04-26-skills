"""Real-browser checks of the redesigned workbench (重构原型), fully offline.

Same requirements and skip rules as ``test_browser.py``, whose fixtures are reused.
"""
import pytest

pytest.importorskip('playwright.sync_api')

from phase0.tests.test_browser import LORLATINIB, app, browser, page  # noqa: F401  (fixtures)

EARLY, LATE = 'WO2011138751A2', 'WO2013132376A1'


def settle(pg, selector, timeout=20000):
    pg.wait_for_selector(selector, timeout=timeout)


def test_home_detects_input_and_opens_the_study(app, page):
    base, _, _ = app
    page.goto(base + '/')
    settle(page, '[data-study="alk-pfizer"]')
    page.fill('#q', LATE)
    page.wait_for_function('document.querySelector("#detect").dataset.kind === "识别为专利公开号"')
    assert '已在调研「ALK 大环系列 · 辉瑞」中' in page.text_content('#detect')
    page.fill('#q', LORLATINIB)
    page.wait_for_function('document.querySelector("#detect").textContent.includes("C21H19FN6O2")')
    page.fill('#q', LATE)
    page.wait_for_function('document.querySelector("#detect").dataset.kind === "识别为专利公开号"')
    page.click('#go')
    page.wait_for_url('**/s/alk-pfizer/overview')
    settle(page, '#next .next-item')
    text = page.text_content('main')
    assert '35 / 271' in text and '2010-05-04 至 2012-03-06' in text and '可核查事实 · 3' in text
    page.wait_for_function('document.querySelector("#next").textContent.includes("补测建议第 1 位：6f")')
    assert page.errors == []


def test_evidence_selection_leads_to_cross_family_compare_without_ratios(app, page):
    base, _, _ = app
    page.goto(base + '/s/alk-pfizer/evidence')
    settle(page, f'[data-compound="{EARLY}:example:7"]')
    assert page.locator(f'[data-compound="{LATE}:example:2"] .val.q').first.text_content().startswith('<0.200')
    assert page.locator(f'[data-compound="{EARLY}:example:6"] .val.miss').count() == 2
    assert set(page.eval_on_selector_all('[data-mass-status]', 'n => n.map(x => x.dataset.massStatus)')) == {'consistent'}
    page.check(f'[data-compound="{EARLY}:example:7"] input[type=checkbox]')
    page.check(f'[data-compound="{LATE}:example:6"] input[type=checkbox]')
    assert '跨文档 · 只并列原始值' in page.text_content('#abbar')
    page.click('#to-compare')
    settle(page, '#activity')
    assert '跨文档对照' in page.text_content('#scope-note')
    assert 'B/A' not in page.text_content('#activity') and 'B 为限定值，不比较' in page.text_content('#activity')
    assert page.errors == []


def test_same_paper_compare_shows_numeric_ratio(app, page):
    base, _, _ = app
    page.goto(base + '/s/alk-pfizer/compare')
    page.wait_for_function('document.querySelectorAll("#pick-a option").length > 30')
    for slot, label in (('a', '洛拉替尼发现论文 · 6f'), ('b', '洛拉替尼发现论文 · 6e')):
        value = page.eval_on_selector(f'#pick-{slot}', '(s, t) => [...s.options].find(o => o.textContent === t).value', label)
        page.select_option(f'#pick-{slot}', value)
    page.click('#run-compare')
    settle(page, '#activity tr[data-assay="CHEMBL3293161"]')
    assert 'B/A 0.127' in page.text_content('#activity tr[data-assay="CHEMBL3293161"]')
    assert '同一文档内' in page.text_content('#scope-note')
    assert page.errors == []


def test_analysis_tradeoff_and_signed_note_reach_the_report(app, page):
    base, _, _ = app
    page.goto(base + '/s/alk-pfizer/analysis')
    card = page.locator('.tf[data-transform="C[*:1]>>[H][*:1]"]')
    card.wait_for(timeout=30000)
    assert card.get_attribute('data-category') == 'tradeoff'
    grade = {p: card.locator(f'tr[data-property="{p}"] td').last.text_content()
             for p in ('cell_potency', 'efflux', 'enzyme_potency')}
    assert grade == {'cell_potency': '无可比数据', 'efflux': '较弱', 'enzyme_potency': '不一致'}
    pair = card.locator('[data-pair="6f>6e"]')
    assert '不利' in pair.locator('[data-property="efflux"]').first.text_content()
    assert page.locator('#followups li b').first.text_content() == '6f · 细胞活性'
    page.fill('#sign-text', '去 N-甲基改善酶活性但外排变差，先补测 6f 细胞 IC50')
    page.fill('#sign-author', '测试化学家')
    page.click('#sign-add')
    page.wait_for_function('document.querySelector("#sign .status").textContent.includes("已署名")')
    page.goto(base + '/s/alk-pfizer/report')
    settle(page, '[data-section="5"] .next-item')
    assert '先补测 6f 细胞 IC50' in page.text_content('[data-section="5"]')
    page.wait_for_function('document.querySelector("[data-section=\\"4\\"]").textContent.includes("补测建议")', timeout=30000)
    assert page.errors == []


def test_confirming_mapping_saves_it_to_the_study(app, page):
    base, _, _ = app
    page.goto(base + '/s/alk-pfizer/analysis')
    page.locator('.tf').first.wait_for(timeout=30000)
    efflux = page.locator('.prop-row[data-id="efflux"]')
    efflux.locator('.direction').select_option('range')
    efflux.locator('.range-high').fill('2.5')
    efflux.locator('.threshold').fill('3')
    page.click('#analyse')
    page.wait_for_function('document.querySelector("#status").textContent.includes("设置已保存")', timeout=30000)
    assert '已由你确认' in page.text_content('#mapping')
    page.goto(base + '/s/alk-pfizer/overview')
    page.wait_for_function('document.querySelector("main").textContent.includes("4 个性质")')
    assert page.errors == []


def test_review_queue_confirms_with_named_reviewer(app, page):
    base, store, _ = app
    page.goto(base + '/review')
    settle(page, f'.qitem[data-id="{EARLY}:example:1"]')
    page.click(f'.qitem[data-id="{LATE}:example:2"]')
    page.wait_for_function('document.querySelector(".detail").textContent.includes("Example 2")')
    assert page.is_disabled('#confirm')
    for _ in range(page.locator('.review-check').count()):
        page.keyboard.press('y')
    page.fill('#reviewer', '复核人甲')
    page.fill('#review-note', '与 PDF p.260 结构图及 Table 1 逐项一致')
    assert not page.is_disabled('#confirm')
    page.click('#confirm')
    page.wait_for_function('document.querySelector("#review-status").textContent.includes("已确认 Example 2")')
    led = store.load()
    comp = next(c for c in led.compounds if c.id == f'{LATE}:example:2')
    assert comp.review.record_status == 'confirmed' and comp.review.reviewer == '复核人甲'
    assert page.locator(f'.qitem[data-id="{LATE}:example:2"]').count() == 0
    assert page.errors == []


def test_structure_search_results_flag_study_membership(app, page):
    base, _, _ = app
    page.goto(base + '/search?smiles=' + LORLATINIB.replace('#', '%23'))
    settle(page, '#parsed')
    page.wait_for_function('document.querySelector("#parsed").textContent.includes("C21H19FN6O2")')
    page.check('input[value="similarity"]')
    page.fill('#threshold', '90')
    page.click('#run-search')
    settle(page, '#coverage')
    hits = page.locator('.hit[data-document]')
    assert hits.count() == 2
    assert all('已在「ALK 大环系列 · 辉瑞」' in t for t in hits.all_text_contents())
    assert '本地证据台账' in page.text_content('#coverage')
    assert page.errors == []


def test_upload_hashes_locally_and_says_extraction_is_not_wired(app, page, tmp_path):
    base, _, _ = app
    pdf = tmp_path / 'WO2099123456A1.pdf'
    pdf.write_bytes(b'%PDF-1.4\n% test\n')
    page.goto(base + '/upload')
    page.set_input_files('#pdf-input', str(pdf))
    page.wait_for_function('document.querySelector("[data-step=identify]").textContent.includes("WO2099123456A1")')
    assert 'SHA-256' in page.text_content('[data-step=read]')
    assert '未接入' in page.text_content('[data-step=extract]')
    assert page.errors == []
