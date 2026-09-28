"""The offline demos must keep working: they are what gets shown live."""
from phase0.extract.demo import run_demo as extract_demo
from phase0.tools.demo import run_demo as tools_demo


def test_tools_demo_shows_refusals_duplicate_block_and_faithful_replay(tmp_path):
    lines = []
    report = tools_demo(tmp_path, lines.append)
    text = '\n'.join(lines)
    assert '拒绝：新写入的记录只能是 proposed' in text
    assert "拒绝：参数无效：compund_id" in text
    assert '暂定 B/A = 0.185' in text and '存在重复观测' in text
    assert report['faithful'] is True and report['matched'] == report['calls'] == 8


def test_extract_demo_flags_the_injected_error_and_skips_l3():
    lines = []
    scored = extract_demo(lines.append)
    text = '\n'.join(lines)
    assert '模拟' in text and '| L3 PDF 图表识别 | 未调用' in text
    assert 'Example 6：low（mass_mismatch）' in text
    assert scored['counts']['errors'] == 1 and scored['counts']['unflagged_errors'] == 0
