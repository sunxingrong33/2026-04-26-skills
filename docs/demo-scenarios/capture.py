"""Regenerate the demo-scenario screenshots in docs/demo-scenarios/shots/.

    python -m pip install -r requirements-browser.txt   # Playwright; Pillow comes with RDKit
    python docs/demo-scenarios/capture.py

Runs the real workbench on a free local port, offline, with real curated data
only. No Google Patents page is read: patent metadata comes from the curated
evidence packages and the relation records, and the page title says so.
Scenarios 5 and 6 are the actual output of ``phase0.tools.demo`` and
``phase0.extract.demo`` rendered as terminal screenshots. Set ``SAR_CHROMIUM``
to a Chromium executable if the one bundled with Playwright is missing.
"""
import glob
import html
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / 'shots'
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from phase0.sar import serve  # noqa: E402
from phase0.sar.lineage import load_relations  # noqa: E402
from phase0.sar.patent_evidence import DATA, attach_evidence  # noqa: E402

LORLATINIB = 'C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21'  # WO2013132376A1 Example 2
FRAGMENT = 'Nc1ncccc1OCc1ccccc1'  # aminopyridine benzyl ether shared by both ALK families
SCALE = 1.5
REL = load_relations()[0]
BASIS = {(c.publication, c.check): c.expected for c in REL.basis}


def offline_patent(pid, cache):
    """Curated evidence only; nothing here comes from the live patent page."""
    pkg = json.loads((DATA / f'{pid}.json').read_text(encoding='utf-8'))
    url = f'https://patents.google.com/patent/{pid}/en'
    d = {'publication': pid, 'title': '（离线演示：未联网读取专利页面；以下为已整理证据包内容）',
         'assignee': '', 'assignees': [], 'inventors': [], 'abstract': '',
         'priority_date': BASIS.get((pid, 'priority_date')), 'filing_date': None, 'publication_date': None,
         'family_id': BASIS.get((pid, 'family_id')), 'family_members': [],
         'references': [REL.from_publication] if pid == REL.to_publication else [],
         'examples': [], 'structures': [], 'invalid_or_query_structures': [], 'structure_limit_reached': False,
         'limitations': ['离线演示：未读取专利页面，实施例标题与化学实体索引未提取。'], 'metadata_sources': {},
         'source_url': url, 'pdf_url': pkg['pdf_url'],
         'source_snapshot': {'url': url, 'sha256': pkg['source_html_sha256'],
                             'retrieved_at': '整理时快照（离线演示）', 'cache_hit': True}}
    attach_evidence(d)
    return d


def launch(p):
    candidates = [os.environ.get('SAR_CHROMIUM'), None,
                  *sorted(glob.glob('/opt/pw-browsers/chromium-*/chrome-linux/chrome'), reverse=True)]
    for path in dict.fromkeys(candidates):
        try:
            return p.chromium.launch(executable_path=path) if path else p.chromium.launch()
        except Exception:
            continue
    sys.exit('没有可启动的 Chromium；请运行 python -m playwright install chromium，或设置 SAR_CHROMIUM')


def save(path, max_h):
    im = Image.open(path)
    if im.size[1] > max_h:
        im.crop((0, 0, im.size[0], max_h)).save(path)
    print('saved', path.name, Image.open(path).size)


def shot(loc, name, max_h=1500):
    path = OUT / f'{name}.png'
    loc.screenshot(path=str(path))
    save(path, max_h)


def table_region(pg, heading, name):
    """The heading plus the table right after it."""
    box = pg.evaluate('''text => { const h = [...document.querySelectorAll('h3')].find(x => x.textContent.includes(text));
        let t = h.nextElementSibling; while (t && t.tagName !== 'TABLE') t = t.nextElementSibling;
        const a = h.getBoundingClientRect(), b = t.getBoundingClientRect();
        return {x: Math.min(a.left, b.left) + scrollX - 8, y: a.top + scrollY - 8,
                width: Math.max(a.right, b.right) - Math.min(a.left, b.left) + 16, height: b.bottom - a.top + 16}; }''',
        heading)
    path = OUT / f'{name}.png'
    pg.screenshot(path=str(path), clip=box, full_page=True)
    save(path, 10_000)


def status(pg, text):
    pg.wait_for_function('t => document.querySelector("#status").textContent.includes(t)', arg=text, timeout=20000)


def pick(pg, select, label):
    value = pg.eval_on_selector(select, '(s, t) => [...s.options].find(o => o.textContent.includes(t))?.value', label)
    pg.select_option(select, value)


def workbench(b, base):
    pg = b.new_page(viewport={'width': 1360, 'height': 1000}, device_scale_factor=SCALE)
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))

    # 1 patent evidence cards
    pg.goto(base + '/')
    pg.fill('#publication', 'WO2013132376A1')
    pg.click('#submit')
    pg.wait_for_selector('#evidence-cards .card')
    shot(pg.locator('#evidence'), 's1-evidence-cards', 1400)
    card = pg.locator('#evidence-cards .card').first
    card.locator('details summary').click()
    shot(card, 's1-card-detail', 2200)

    # 2 cross-family relation and comparison
    pg.click('#load-lineage')
    pg.wait_for_selector('#lineage-content h3:has-text("可核查事实")', timeout=20000)
    shot(pg.locator('#lineage-content'), 's2-lineage', 2000)
    pg.get_by_role('button', name='对照早期 Example 7 与大环 Example 6 →').click()
    pg.wait_for_selector('h3:has-text("跨专利测量并列")', timeout=20000)
    pg.add_style_tag(content='.selection-bar{display:none!important}')
    shot(pg.locator('#compare'), 's2-compare-structures', 1050)
    table_region(pg, '跨专利测量并列', 's2-compare-measurements')

    # 4 structure search on the local ledger
    pg.goto(base + '/')
    pg.select_option('#input-mode', 'smiles')
    pg.select_option('#search-method', 'similarity')
    pg.fill('#search-threshold', '50')
    pg.fill('#publication', LORLATINIB)
    pg.click('#submit')
    status(pg, '检索完成')
    shot(pg.locator('#discovery'), 's4-similarity', 1700)
    pg.select_option('#search-method', 'substructure')
    pg.fill('#publication', FRAGMENT)
    pg.click('#submit')
    status(pg, '检索完成')
    shot(pg.locator('#discovery'), 's4-substructure', 1500)

    # 3 six-step workflow
    pg.goto(base + '/evidence')
    pg.wait_for_function('document.querySelectorAll("#pair-document option").length > 1')
    pg.select_option('#pair-document', 'CHEMBL3286195')
    pick(pg, '#pair-a', '/ 6f ·')
    pick(pg, '#pair-b', '/ 6e ·')
    pg.click('#align-pair')
    pg.wait_for_function('document.querySelector("#pair-status").textContent.includes("结构分析已返回")')
    shot(pg.locator('#alignment'), 's3-1-alignment', 1500)
    pg.click('#compare-pair')
    pg.wait_for_function('document.querySelector("#comparison-result").textContent.includes("B/A")')
    shot(pg.locator('#comparability'), 's3-2-comparability', 1700)
    pg.click('#add-sar-pair')
    pg.click('#run-sar-summary')
    pg.wait_for_function('document.querySelector("#sar-status").textContent.includes("分析完成")')
    shot(pg.locator('#sar-summary'), 's3-3-summary', 1900)
    pg.click('#use-sar-a')
    pg.select_option('#sar-assay', 'CHEMBL3293161')
    pg.select_option('#sar-goal', 'lower')
    pg.click('#run-sar-suggest')
    pg.wait_for_selector('#sar-suggestions h3')
    shot(pg.locator('#sar-suggestions'), 's3-4-suggest-lower', 1400)
    before = pg.text_content('#sar-suggestions')
    pg.select_option('#sar-assay', 'CHEMBL3293391')
    pg.click('#run-sar-suggest')
    pg.wait_for_function('t => document.querySelector("#sar-suggestions").textContent !== t', arg=before)
    pg.wait_for_selector('#sar-suggestions h3')
    shot(pg.locator('#sar-suggestions'), 's3-5-suggest-other', 1400)
    pg.close()
    if errors:
        sys.exit(f'页面脚本错误：{errors}')


TERMINAL = '''<!doctype html><meta charset="utf-8"><style>
body{{margin:0;background:#1e2326}}
pre{{margin:0;padding:22px 26px;font:15px/1.65 "DejaVu Sans Mono","Noto Sans Mono CJK SC",monospace;color:#d8dee4;white-space:pre-wrap}}
.cmd{{color:#8fd19e}}.bad{{color:#ff9b87}}.ok{{color:#9fd3ff}}.warn{{color:#ffd479}}
</style><pre><span class="cmd">$ {cmd}</span>
{body}</pre>'''
DEMOS = [
    ('s5-agent-tools', 'phase0.tools.demo', [('拒绝', 'bad'), ('重放', 'ok'), ('比值 = None', 'warn'), ('B/A = 0.185', 'warn')]),
    ('s6-extraction', 'phase0.extract.demo', [('注意：抽取器为模拟', 'warn'), ('mass_mismatch', 'bad'), ('未调用', 'ok'), ('评测：', 'ok')]),
]


def terminals(b, tmp):
    for name, module, marks in DEMOS:
        out = subprocess.run([sys.executable, '-m', module], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        lines = [f'<span class="{next((c for k, c in marks if k in line), "")}">{html.escape(line)}</span>'
                 for line in out.rstrip().split('\n')]
        page = Path(tmp) / f'{name}.html'
        page.write_text(TERMINAL.format(cmd=f'python -m {module}', body='\n'.join(lines)), encoding='utf-8')
        pg = b.new_page(viewport={'width': 1180, 'height': 600}, device_scale_factor=SCALE)
        pg.goto(page.as_uri())
        shot(pg.locator('pre'), name, 10_000)
        pg.close()


def main():
    OUT.mkdir(exist_ok=True)
    serve.retrieve = offline_patent
    serve.Handler.log_message = lambda *a: None
    with tempfile.TemporaryDirectory() as tmp:
        server = serve.create_server(0, Path(tmp) / 'cache')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                b = launch(p)
                workbench(b, f'http://127.0.0.1:{server.server_port}')
                terminals(b, tmp)
                b.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
