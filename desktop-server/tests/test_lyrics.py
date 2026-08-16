"""
LRC 解析与手动歌词逻辑单元测试
运行: python -m unittest discover -s tests -v  (在 desktop-server/ 目录下)
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.lyrics import parse_lrc
from modules.content import ContentManager


class TestParseLrc(unittest.TestCase):
    """LRC 时间轴解析边界测试"""

    def test_empty_input(self):
        self.assertEqual(parse_lrc(""), [])
        self.assertEqual(parse_lrc(None), [])

    def test_single_line(self):
        result = parse_lrc("[00:05.00]第一行歌词")
        self.assertEqual(result, [{"time": 5.0, "text": "第一行歌词"}])

    def test_lines_sorted_by_time(self):
        text = "[00:10.00]第二行\n[00:05.00]第一行\n[00:00.00]开始"
        result = parse_lrc(text)
        self.assertEqual([l["time"] for l in result], [0.0, 5.0, 10.0])
        self.assertEqual([l["text"] for l in result], ["开始", "第一行", "第二行"])

    def test_milliseconds_precision(self):
        result = parse_lrc("[00:01.123]带毫秒")
        self.assertAlmostEqual(result[0]["time"], 1.123, places=3)

    def test_minute_rollover(self):
        result = parse_lrc("[01:30.50]一分半")
        self.assertAlmostEqual(result[0]["time"], 90.5, places=2)

    def test_multiple_tags_same_line(self):
        result = parse_lrc("[00:01.00][00:02.00]重复标签")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["time"], 1.0)
        self.assertEqual(result[1]["time"], 2.0)

    def test_single_digit_ms_not_matched(self):
        # "[00:01.5]" 毫秒仅 1 位, 不满足 \d{2,3} → 整行跳过 (与现有解析行为一致)
        text = "[00:01.5]单毫秒行\n[00:02.00]正常行"
        result = parse_lrc(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "正常行")

    def test_lines_without_timestamp_ignored(self):
        text = "没有时间戳的行\n[00:05.00]正常行"
        result = parse_lrc(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "正常行")

    def test_broken_timestamp_ignored(self):
        text = "[00:xx.00]坏时间戳\n[00:05.00]好行"
        result = parse_lrc(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "好行")

    def test_blank_lines_skipped(self):
        text = "\n\n[00:01.00]行1\n\n[00:02.00]行2\n"
        result = parse_lrc(text)
        self.assertEqual(len(result), 2)


def _make_content_manager() -> ContentManager:
    """跳过 __init__ 构造最小可测实例 (不启动 MediaWatcher/PS 子进程)"""
    cm = ContentManager.__new__(ContentManager)
    cm.notification_queue = []
    cm._lock = MagicMock()  # 简单替换锁, 避免真实锁依赖
    cm._lock.__enter__ = MagicMock(return_value=None)
    cm._lock.__exit__ = MagicMock(return_value=False)
    cm.manual_lines = []
    cm.manual_parsed_lines = []
    cm.manual_index = 0
    cm._manual_start_time = 0.0
    cm.auto_mode = False
    cm._song_version = 0
    return cm


class TestManualLyrics(unittest.TestCase):
    """set_lyrics_text 的手动歌词解析逻辑 (LRC / 纯文本)"""

    def test_plain_text_generates_fixed_intervals(self):
        cm = _make_content_manager()
        cm.set_lyrics_text("第一行\n第二行\n第三行")
        self.assertEqual(
            cm.manual_parsed_lines,
            [
                {"time": 0.0, "text": "第一行"},
                {"time": 5.0, "text": "第二行"},
                {"time": 10.0, "text": "第三行"},
            ],
        )
        self.assertEqual(len(cm.manual_lines), 3)
        self.assertFalse(cm.auto_mode)
        self.assertEqual(cm._song_version, 1)

    def test_lrc_text_parsed_with_timestamps(self):
        cm = _make_content_manager()
        cm.set_lyrics_text("[00:00.00]开始\n[00:03.50]下一句")
        self.assertEqual(
            cm.manual_parsed_lines,
            [
                {"time": 0.0, "text": "开始"},
                {"time": 3.5, "text": "下一句"},
            ],
        )

    def test_empty_text_no_crash(self):
        cm = _make_content_manager()
        cm.set_lyrics_text("")
        self.assertEqual(cm.manual_parsed_lines, [])
        self.assertEqual(cm.manual_lines, [])


if __name__ == "__main__":
    unittest.main()
