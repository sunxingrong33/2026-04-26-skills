"""Regenerate the demo-scenario screenshots in docs/demo-scenarios/shots/.

    python -m pip install -r requirements-browser.txt   # Playwright; Pillow comes with RDKit
    python docs/demo-scenarios/capture.py

Runs the real workbench on a free local port, offline, with real curated data
only, against a fresh ledger database in a temporary folder (so the review
step can be shown without touching artifacts/). No external source is
queried: structure search covers the local ledger only, and no Google Patents
page is read. Scenarios 7 and 8 include the actual output of
``phase0.tools.demo`` and ``phase0.extract.demo`` rendered as terminal
screenshots. Set ``SAR_CHROMIUM`` to a Chromium executable if the one bundled
with Playwright is missing.
"""
import glob
import html
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

from phase0.ledger import access  # noqa: E402
from phase0.ledger.migrate import build  # noqa: E402
from phase0.ledger.store import LedgerStore  # noqa: E402
from phase0.sar import serve  # noqa: E402

LORLATINIB = 'C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21'  # WO2013132376A1 Example 2
FRAGMENT = 'Nc1ncccc1OCc1ccccc1'  # aminopyridine benzyl ether shared by both ALK families
EARLY, LATE = 'WO2011138751A2', 'WO2013132376A1'
STUDY = '/s/alk-pfizer/'
SCALE = 1.5
WIDTH = 1360


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


def shot(loc, name, max_h=1600):
    path = OUT / f'{name}.png'
    loc.screenshot(path=str(path))
    save(path, max_h)


UNSTICK = '.abbar,.addbar{position:static!important}'  # a sticky bar would land mid-page in a full-page shot


def viewport(pg, name, max_h=1600):
    """What the reader sees on arrival: the top of the page, header included."""
    pg.add_style_tag(content=UNSTICK)
    path = OUT / f'{name}.png'
    pg.screenshot(path=str(path), full_page=True)
    save(path, max_h)


def wait_text(pg, selector, text, timeout=30000):
    pg.wait_for_function('([s, t]) => (document.querySelector(s)?.textContent || "").includes(t)',
                         arg=[selector, text], timeout=timeout)


def workbench(b, base):
    pg = b.new_page(viewport={'width': WIDTH, 'height': 1000}, device_scale_factor=SCALE)
    errors = []
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.add_init_script("try { localStorage.setItem('sar-atlas-author', '演示用户'); } catch (e) {}")

    # 1 from a publication number to the study overview
    pg.goto(base + '/')
    pg.wait_for_selector('[data-study="alk-pfizer"]')
    pg.fill('#q', LATE)
    wait_text(pg, '#detect', '已在调研')
    viewport(pg, 's1-home', 1250)
    pg.click('#go')
    pg.wait_for_url('**' + STUDY + 'overview')
    wait_text(pg, '#next', '补测建议第 1 位')
    viewport(pg, 's1-overview', 2000)

    # 2 evidence table and a cross-family A/B comparison
    pg.goto(base + STUDY + 'evidence')
    pg.wait_for_selector(f'[data-compound="{LATE}:example:6"] img')
    pg.wait_for_load_state('networkidle')
    pg.check(f'[data-compound="{EARLY}:example:7"] input[type=checkbox]')
    pg.check(f'[data-compound="{LATE}:example:6"] input[type=checkbox]')
    pg.wait_for_load_state('networkidle')
    viewport(pg, 's2-evidence', 2300)
    pg.click('#to-compare')
    pg.wait_for_selector('#activity')
    shot(pg.locator('main'), 's2-compare', 2600)

    # 3 multi-property SAR for the project goal; 6f -> 6e inside one paper
    pg.goto(base + STUDY + 'analysis')
    card = pg.locator('.tf[data-transform="C[*:1]>>[H][*:1]"]')
    card.wait_for(timeout=30000)
    efflux = pg.locator('.prop-row[data-id="efflux"]')
    efflux.locator('.direction').select_option('range')
    efflux.locator('.range-high').fill('2.5')
    pg.click('#analyse')
    wait_text(pg, '#status', '设置已保存')
    card.wait_for(timeout=30000)
    pg.wait_for_load_state('networkidle')
    shot(pg.locator('.split-left > aside'), 's3-mapping', 2600)
    shot(card, 's3-tradeoff', 2200)
    shot(pg.locator('#followups'), 's3-followups', 1400)
    pg.fill('#sign-text', '去 N-甲基改善酶活性但外排变差，先补测 6f 细胞 IC50 再判断')
    pg.select_option('#sign-verdict', 'pending')
    pg.click('#sign-add')
    wait_text(pg, '#sign .status', '已署名')

    pg.goto(base + STUDY + 'compare')
    pg.wait_for_function('document.querySelectorAll("#pick-a option").length > 30')
    for slot, label in (('a', '洛拉替尼发现论文 · 6f'), ('b', '洛拉替尼发现论文 · 6e')):
        value = pg.eval_on_selector(f'#pick-{slot}', '(s, t) => [...s.options].find(o => o.textContent === t).value', label)
        pg.select_option(f'#pick-{slot}', value)
    pg.click('#run-compare')
    pg.wait_for_selector('#activity tr[data-assay="CHEMBL3293161"]')
    shot(pg.locator('#activity').locator('xpath=ancestor::div[contains(@class,"card")][1]'), 's3-compare-paper', 1600)

    # 4 structure search, local ledger only
    pg.goto(base + '/search?smiles=' + LORLATINIB.replace('#', '%23'))
    wait_text(pg, '#parsed', 'C21H19FN6O2')
    pg.check('input[value="similarity"]')
    pg.fill('#threshold', '50')
    pg.wait_for_load_state('networkidle')
    viewport(pg, 's4-search', 1700)
    pg.click('#run-search')
    pg.wait_for_selector('#coverage')
    pg.wait_for_load_state('networkidle')
    viewport(pg, 's4-similarity', 2400)
    pg.goto(base + '/search?' + 'smiles=' + FRAGMENT + '&method=substructure&run=1')
    pg.wait_for_selector('#coverage')
    pg.wait_for_load_state('networkidle')
    viewport(pg, 's4-substructure', 2000)

    # 5 timeline and R-group alignment
    pg.goto(base + STUDY + 'timeline')
    pg.wait_for_selector('#alignment')
    pg.wait_for_load_state('networkidle')
    pg.wait_for_timeout(300)  # citation arrow is drawn after layout
    viewport(pg, 's5-timeline', 2400)

    # 6 named review, then the report (A/B pair and signed note from above)
    pg.set_viewport_size({'width': WIDTH, 'height': 1240})  # the review layout fills the window; show the buttons
    pg.goto(base + '/review')
    pg.click(f'.qitem[data-id="{LATE}:example:2"]')
    wait_text(pg, '.detail', 'Example 2')
    for _ in range(pg.locator('.review-check').count()):
        pg.keyboard.press('y')
    pg.fill('#reviewer', '演示用户')
    pg.fill('#review-note', '与 PDF p.260 结构图及 Table 1 逐项一致')
    pg.wait_for_load_state('networkidle')
    viewport(pg, 's6-review', 1860)
    pg.set_viewport_size({'width': WIDTH, 'height': 1000})
    pg.click('#confirm')
    wait_text(pg, '#review-status', '已确认 Example 2')
    # the report's key comparison is the A/B pair last chosen in this tab: back to Example 7 / Example 6
    pg.goto(base + STUDY + f'compare?a={EARLY}:example:7&b={LATE}:example:6')
    pg.wait_for_function('document.querySelectorAll("#pick-a option").length > 30')
    pg.click('#run-compare')
    pg.wait_for_selector('#activity')
    pg.goto(base + STUDY + 'report')
    wait_text(pg, '[data-section="4"]', '补测建议')
    wait_text(pg, '[data-section="3"]', 'Example 7')
    pg.wait_for_load_state('networkidle')
    shot(pg.locator('article.report'), 's6-report', 4200)

    # 8 start from a PDF: hashing and identification only
    pdf = Path(tempfile.gettempdir()) / f'{EARLY}.pdf'
    pdf.write_bytes(b'%PDF-1.4\n% demo file: not the published patent PDF\n')
    pg.goto(base + '/upload')
    pg.set_input_files('#pdf-input', str(pdf))
    wait_text(pg, '[data-step=identify]', EARLY)
    viewport(pg, 's8-upload', 1300)
    pdf.unlink()
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
    ('s7-agent-tools', 'phase0.tools.demo', [('拒绝', 'bad'), ('重放', 'ok'), ('比值 = None', 'warn'), ('B/A = 0.185', 'warn')]),
    ('s8-extraction', 'phase0.extract.demo', [('注意：抽取器为模拟', 'warn'), ('mass_mismatch', 'bad'), ('未调用', 'ok'), ('评测：', 'ok')]),
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
    for old in OUT.glob('*.png'):
        old.unlink()
    serve.Handler.log_message = lambda *a: None
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / 'ledger.sqlite'
        LedgerStore(db).import_ledger(build(), 'demo-capture')
        os.environ[access.ENV] = str(db)
        server = serve.create_server(0, Path(tmp) / 'cache', ledger_db=db)
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
