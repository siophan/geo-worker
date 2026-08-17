"""拟人化操作的单元测试(无需真 Playwright,mock 一个 fake page)。

跑法:cd worker && .venv/bin/python -m unittest test_humanize -v
只测纯逻辑:打字节奏、换行路由、鼠标弧线、阅读微动。真实浏览器行为不在此测。
"""
import random
import unittest

from collectors.base import BrowserChatCollector


class FakeKeyboard:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def type(self, ch):
        self.events.append(("type", ch))


class FakeMouse:
    def __init__(self):
        self.moves = []
        self.wheels = []

    def move(self, x, y, steps=1):
        self.moves.append((x, y, steps))

    def down(self):
        self.events_down = True

    def up(self):
        self.events_up = True

    def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()
        self.waits = []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


# 关掉一切随机延迟,让停顿计算可确定性断言(_rand 在 min==max 时退化为 min)
_NO_DELAY = {
    "type_delay_min": 0, "type_delay_max": 0,
    "punct_pause_min": 0, "punct_pause_max": 0,
    "hesitate_prob": 0, "hesitate_ms_min": 0, "hesitate_ms_max": 0,
}


class HumanTypeTest(unittest.TestCase):
    def test_newline_routes_to_shift_enter_and_normalizes(self):
        c = BrowserChatCollector(dict(_NO_DELAY))
        p = FakePage()
        c._human_type(p, "a\r\nb\rc\nd")  # \r\n 与裸 \r 都应归一成换行
        self.assertEqual(
            p.keyboard.events,
            [("type", "a"), ("press", "Shift+Enter"),
             ("type", "b"), ("press", "Shift+Enter"),
             ("type", "c"), ("press", "Shift+Enter"),
             ("type", "d")],
        )

    def test_one_wait_per_char(self):
        c = BrowserChatCollector(dict(_NO_DELAY))
        p = FakePage()
        c._human_type(p, "abc\nd")  # 归一后 5 个"字符事件"
        self.assertEqual(len(p.waits), 5)


class KeyDelayTest(unittest.TestCase):
    def _collector(self, **over):
        cfg = {
            "type_delay_min": 10, "type_delay_max": 10,
            "punct_pause_min": 50, "punct_pause_max": 50,
            "hesitate_ms_min": 100, "hesitate_ms_max": 100,
        }
        cfg.update(over)
        return BrowserChatCollector(cfg)

    def test_plain_char_is_base_delay(self):
        self.assertEqual(self._collector()._key_delay("a", 0.0), 10)

    def test_boundary_chars_get_extra_pause(self):
        c = self._collector()
        for ch in (" ", "，", "。", ",", ".", "?", "！", "\n"):
            self.assertEqual(c._key_delay(ch, 0.0), 60, f"char {ch!r} 应加词句边界停顿")

    def test_hesitation_adds_time_when_triggered(self):
        # prob=1 必触发 → 基础 10 + 想一下 100
        self.assertEqual(self._collector()._key_delay("a", 1.0), 110)

    def test_no_hesitation_when_prob_zero(self):
        self.assertEqual(self._collector()._key_delay("a", 0.0), 10)


class MoveCurvedTest(unittest.TestCase):
    def test_first_move_without_history_lands_on_target(self):
        c = BrowserChatCollector({"move_steps_min": 10, "move_steps_max": 10})
        p = FakePage()
        c._move_curved(p, 100, 200)
        self.assertEqual(c._cursor, (100, 200))
        self.assertEqual(len(p.mouse.moves), 2)          # 两段插值
        self.assertEqual(p.mouse.moves[-1][:2], (100, 200))  # 终点落在目标

    def test_fallback_click_clears_cursor(self):
        # 拿不到 bounding_box 走原生 click 时,应把落点标记为未知(None),不留旧坐标
        c = BrowserChatCollector({})
        c._cursor = (300, 400)

        class NoBoxLocator:
            def bounding_box(self):
                return None

            def click(self):
                self.clicked = True

        c._human_click(FakePage(), NoBoxLocator())
        self.assertIsNone(c._cursor)

    def test_path_deviates_from_straight_line(self):
        # 有历史落点时,中间控制点应偏离起终点连线(否则就是"完美直线"这个特征)
        c = BrowserChatCollector({"move_steps_min": 10, "move_steps_max": 10})
        deviated = False
        for _ in range(50):
            p = FakePage()
            c._cursor = (0, 0)
            c._move_curved(p, 100, 0)  # 直线时中点 y 恒为 0;弧线会把它推离 0
            if abs(p.mouse.moves[0][1]) > 1e-9:
                deviated = True
                break
        self.assertTrue(deviated, "50 次里应至少出现一次偏离直线的弧")


class ReadingFidgetTest(unittest.TestCase):
    def test_no_action_when_prob_zero(self):
        c = BrowserChatCollector({"read_scroll_prob": 0})
        p = FakePage()
        c._reading_fidget(p)
        self.assertEqual(p.mouse.wheels, [])
        self.assertEqual(p.mouse.moves, [])

    def test_scrolls_when_prob_one(self):
        c = BrowserChatCollector({"read_scroll_prob": 1})
        p = FakePage()
        c._reading_fidget(p)  # random()∈[0,1) 必 < 1 → 走滚动分支
        self.assertEqual(len(p.mouse.wheels), 1)

    def test_never_raises(self):
        random.seed(0)
        c = BrowserChatCollector({"read_scroll_prob": 0.5})
        c._cursor = (10, 10)
        for _ in range(200):
            c._reading_fidget(FakePage())  # 不抛异常即可


if __name__ == "__main__":
    unittest.main()
