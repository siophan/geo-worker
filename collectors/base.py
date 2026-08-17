"""采集器抽象契约 + 通用浏览器采集基类(worker 端自包含副本)。

与后端 backend/app/services/engine_collectors/base.py 保持同构;此处不依赖
任何后端/数据库代码,便于在 Mac mini 上独立运行。改动需两侧同步(或后续抽公共包)。
"""
from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EngineCitation:
    url: str
    title: str = ""
    mentions_brand: bool = False

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "mentions_brand": self.mentions_brand}


@dataclass
class EngineResult:
    ok: bool
    answer: str = ""
    citations: list[EngineCitation] = field(default_factory=list)
    error: str | None = None
    brand_mentioned: bool = False


class BaseEngineCollector(ABC):
    key: str = ""
    label: str = ""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def collect(self, prompt_text: str, brand_name: str = "") -> EngineResult:
        raise NotImplementedError

    def collect_batch(self, prompt_texts: list[str], brand_name: str = "") -> list[EngineResult]:
        return [self.collect(text, brand_name) for text in prompt_texts]


class BrowserChatCollector(BaseEngineCollector):
    """「web 对话页 + Playwright」类引擎的通用采集器(worker 端副本)。

    各引擎(DeepSeek/豆包/Kimi/文心/通义...)站点不同,但采集流程同构:
    带登录态打开对话页 → 在输入框填入提示词 → 提交 → 等流式回答稳定 → 抓回答+引用。
    子类只需声明一个 `defaults` 字典(站点 URL / cookie 域 / 各类选择器)。
    所有默认值均可经 config 热覆盖,便于对方页面改版后免改代码校准选择器。

    抗风控(模拟真人)约定:不再用 fill()+Enter 这种「机器味」操作,而是真实鼠标点击
    (带随机偏移,不总点正中心)+ 逐字打字(带随机字间延迟)+ 随机停顿/轮询,并抹掉
    navigator.webdriver 等自动化指纹。手法参考同机 automation 项目的发布模块。这些都可经
    config 热调。

    ⚠️ 只支持 CDP 模式:必须配 cdp_url 挂到已用 --remote-debugging-port 启动的真 Chrome。
    已删除 Playwright bundled(Chrome for Testing)兜底 —— 缺 cdp_url 直接返回 ok=False,
    绝不自己弹一个新浏览器。登录态复用真 Chrome 的 profile,无需再注入 cookie/storage_state。

    config / defaults 可用键:
    - url / input_selector / answer_selector / citation_selector
    - submit("enter"|"button") / submit_selector
    - settle_ms / timeout_ms
    - cdp_url: 挂到已运行真 Chrome 的 CDP 地址(如 http://127.0.0.1:9222),**必填**;
      走「连接真 Chrome」模式:指纹最真、复用其已登录 profile、只开关自己的标签、绝不关它
    - stealth: 是否注入反自动化指纹(默认 True)
    - type_delay_min/max: 逐字打字的字间随机延迟(ms)
    - punct_pause_min/max: 空格/标点后额外停顿(ms,真人在词/句边界会顿一下)
    - hesitate_prob: 打字中每个字触发一次较长"想一下"的概率(0~1)
    - hesitate_ms_min/max: 上述"想一下"的时长(ms)
    - think_ms_min/max: 关键动作前的"思考"随机停顿(ms)
    - click_hold_min/max: 鼠标按下到松开的随机保持(ms)
    - move_steps_min/max: 鼠标移到目标的插值步数(越多轨迹越平滑)
    - poll_min/max: 等回答时轮询的随机间隔(ms)
    - read_scroll_prob: 等回答期间每轮"阅读时"轻微滚动/挪鼠标的概率(0~1)
    """

    _BASE_DEFAULTS = {
        "url": "",
        "input_selector": "textarea",
        "submit": "enter",
        "submit_selector": "",
        "answer_selector": "div[class*='markdown']",
        "citation_selector": "a[href^='http']",
        "settle_ms": 2500,
        "timeout_ms": 90000,
        # cdp_url 必填:挂到已运行的真 Chrome(--remote-debugging-port);缺省会在 collect() 报错
        "cdp_url": "",
        # —— 抗风控/拟人参数(均可经 engine_<key>_config 热覆盖)——
        "stealth": True,        # 注入反自动化指纹(navigator.webdriver 等)
        "type_delay_min": 30,   # 逐字打字字间延迟下限(ms)
        "type_delay_max": 120,  # 逐字打字字间延迟上限(ms)
        "punct_pause_min": 120, # 空格/标点后额外停顿下限(ms,词/句边界顿一下)
        "punct_pause_max": 400, # 空格/标点后额外停顿上限(ms)
        "hesitate_prob": 0.03,  # 打字中每字触发一次较长"想一下"的概率
        "hesitate_ms_min": 300, # "想一下"时长下限(ms)
        "hesitate_ms_max": 900, # "想一下"时长上限(ms)
        "think_ms_min": 400,    # 关键动作前思考停顿下限(ms)
        "think_ms_max": 1200,   # 关键动作前思考停顿上限(ms)
        "click_hold_min": 50,   # 鼠标按下保持下限(ms)
        "click_hold_max": 150,  # 鼠标按下保持上限(ms)
        "move_steps_min": 8,    # 鼠标移到目标的插值步数下限(越多轨迹越平滑)
        "move_steps_max": 24,   # 鼠标移到目标的插值步数上限
        "poll_min": 300,        # 等回答轮询间隔下限(ms)
        "poll_max": 700,        # 等回答轮询间隔上限(ms)
        "read_scroll_prob": 0.15,  # 等回答期间每轮"阅读时"轻微滚动/挪鼠标的概率
    }

    # 子类覆盖:声明本引擎站点相关默认值(至少 url + 各类选择器)。
    defaults: dict = {}

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        # 会话内维护光标落点:鼠标弧线移动以「上次落点」为起点,保证 session 内轨迹连续
        # (真人光标不会每次凭空出现在目标上)。worker 串行采集,无并发写。
        self._cursor: tuple[float, float] | None = None

    def _opt(self, name: str):
        if name in self.config:
            return self.config[name]
        if name in self.defaults:
            return self.defaults[name]
        # 未知键回落 None(而非 KeyError),便于子类声明 _BASE_DEFAULTS 之外的自定义参数。
        return self._BASE_DEFAULTS.get(name)

    def collect(self, prompt_text: str, brand_name: str = "") -> EngineResult:
        # 只走 CDP:必须挂到已用 --remote-debugging-port 启动的真 Chrome。
        # 缺 cdp_url 直接报错,绝不回退 Playwright bundled 的 "Chrome for Testing"。
        # str() 兜底:CONFIG 里漏引号写成数字(如 {"cdp_url":9222})时不至于 .strip() 崩。
        cdp_url = str(self._opt("cdp_url") or "").strip()
        if not cdp_url:
            return EngineResult(
                ok=False,
                error=f"{self.label} 未配置 cdp_url:必须先用 --remote-debugging-port 启动真 Chrome,"
                f"再配 GEO_WORKER_CDP_URL(或该引擎的 cdp_url);已删除 bundled 兜底,不会自弹浏览器",
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return EngineResult(
                ok=False,
                error="Playwright 未安装,请先 `pip install playwright`",
            )

        stealth = bool(self._opt("stealth"))
        timeout_ms = int(self._opt("timeout_ms"))
        settle_ms = int(self._opt("settle_ms"))
        try:
            with sync_playwright() as pw:
                return self._collect_via_cdp(
                    pw, cdp_url, prompt_text, brand_name, settle_ms, timeout_ms, stealth
                )
        except Exception as exc:  # noqa: BLE001
            return EngineResult(ok=False, error=f"{self.label} 采集失败:{type(exc).__name__}:{exc}")

    def _collect_via_cdp(
        self, pw, cdp_url, prompt_text, brand_name, settle_ms, timeout_ms, stealth
    ) -> EngineResult:
        """模式2:挂到已在运行的真 Chrome(指纹最真,复用其已登录 profile)。

        Playwright 只通过 CDP 附着、不参与启动,故无任何自动化启动参数;复用真 Chrome
        的默认 context(new_context 会开一个无登录态的新上下文)。只开/关我们自己的标签,
        绝不 browser.close()——那会断开/影响用户的常驻 Chrome。
        """
        port = cdp_url.rsplit(":", 1)[-1]
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:  # noqa: BLE001 — 连不上就明确提示怎么起 Chrome
            return EngineResult(
                ok=False,
                error=f"{self.label} 连不上真 Chrome CDP({cdp_url}):{type(exc).__name__}:{exc};"
                f"请确认已用 --remote-debugging-port={port} 启动 Chrome 并登录该引擎",
            )
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        if stealth:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        page = context.new_page()
        try:
            page.set_default_timeout(timeout_ms)
            return self._drive(page, prompt_text, brand_name, settle_ms, timeout_ms)
        finally:
            page.close()  # 只关我们开的标签,保留用户常驻的真 Chrome

    def _drive(self, page, prompt_text, brand_name, settle_ms, timeout_ms) -> EngineResult:
        """CDP 标签页驱动:打开对话页 → 拟人输入提交 → 等回答 → 抓回答+引用。"""
        page.goto(self._opt("url"), wait_until="domcontentloaded")
        # 新 page 鼠标从原点起;清掉上个任务(已关闭 page)的陈旧落点,避免弧线起点错位
        self._cursor = None

        box = page.locator(self._opt("input_selector")).first
        box.wait_for(state="visible")
        self._think(page)                    # 页面加载后先"看一眼"再动手
        self._human_click(page, box)         # 真人鼠标点击聚焦输入框
        self._human_type(page, prompt_text)  # 逐字打字(带随机字间延迟)
        self._think(page)                    # 打完停顿再发送
        self._submit(page)

        answer = self._wait_answer(page, settle_ms, timeout_ms)
        citations = self._extract_citations(page, brand_name)

        if not answer.strip():
            return EngineResult(ok=False, error="未抓到回答文本(选择器可能需校准)")
        mentioned = bool(brand_name) and brand_name.lower() in answer.lower()
        return EngineResult(
            ok=True, answer=answer, citations=citations, brand_mentioned=mentioned
        )

    def _submit(self, page) -> None:
        if self._opt("submit") == "button":
            sel = self._opt("submit_selector")
            if sel:
                self._human_click(page, page.locator(sel).first)
                return
        page.keyboard.press("Enter")

    def _rand(self, lo_key: str, hi_key: str) -> float:
        """按 config 的 [lo, hi](ms)取一个随机值,hi<lo 时退化为 lo。"""
        lo = float(self._opt(lo_key))
        hi = float(self._opt(hi_key))
        return random.uniform(lo, hi) if hi > lo else lo

    def _think(self, page) -> None:
        """关键动作前的"思考"随机停顿,打散机器般规整的节奏。"""
        page.wait_for_timeout(self._rand("think_ms_min", "think_ms_max"))

    def _human_click(self, page, locator) -> None:
        """真人鼠标点击:滑到目标框内随机偏移点,按下→随机保持→松开。

        鼠标用 steps 插值移动(逐步派发 mousemove,形成轨迹),而非瞬移到目标——瞬间
        出现在按钮上是个弱自动化特征。取不到 bounding box(元素不可见/离屏)时回退到
        Playwright 原生 click,保证不失败。
        """
        box = None
        try:
            box = locator.bounding_box()
        except Exception:  # noqa: BLE001 — 拿不到坐标就回退
            box = None
        if not box:
            locator.click()
            self._cursor = None  # 走了原生 click,落点未知,别拿旧坐标画下一段弧
            return
        # 在按钮范围内随机偏移,避免总点正中心(automation 发布模块同款手法)
        x = box["x"] + box["width"] / 2 + random.uniform(-box["width"] / 3, box["width"] / 3)
        y = box["y"] + box["height"] / 2 + random.uniform(-box["height"] / 3, box["height"] / 3)
        self._move_curved(page, x, y)  # 带弧度地移过去,不是完美直线
        page.mouse.down()
        page.wait_for_timeout(self._rand("click_hold_min", "click_hold_max"))
        page.mouse.up()

    def _move_curved(self, page, x: float, y: float) -> None:
        """带弧度地把鼠标移到 (x, y):经过一个「垂直于移动方向」抖动的中间点,分两段插值。

        `page.mouse.move(x, y, steps=n)` 是匀速直线——两点间完美直线本身是弱自动化特征。
        真人是带弧度、常有轻微过冲的曲线。这里以上次落点为起点,构造一个偏离连线的控制点,
        走「起点→控制点→终点」两段,近似一条弧。首次没有历史落点则直接到位(无从画弧)。
        """
        steps = max(1, int(self._rand("move_steps_min", "move_steps_max")))
        start = self._cursor or (x, y)
        dx, dy = x - start[0], y - start[1]
        dist = math.hypot(dx, dy) or 1.0
        # 中点 + 垂直方向随机偏移(偏移量按移动距离缩放),形成弧线的控制点
        off = random.uniform(-0.2, 0.2) * dist
        mx = (start[0] + x) / 2 - dy / dist * off
        my = (start[1] + y) / 2 + dx / dist * off
        half = max(1, steps // 2)
        page.mouse.move(mx, my, steps=half)
        page.mouse.move(x, y, steps=max(1, steps - half))
        self._cursor = (x, y)

    def _human_type(self, page, text: str) -> None:
        """逐字打字,字间带「偏态」随机延迟。

        比 fill() 更像真人:fill() 是直接 DOM 赋值,不产生真实按键事件序列,易被风控识别。
        节奏刻意做成偏态而非均匀(均匀分布本身就是机器特征):大部分字很快,空格/标点/换行
        等词句边界后顿得更久,并以小概率插入一次较长的"想一下"。
        换行必须走 Shift+Enter:裸 keyboard.type("\\n") 会触发 Enter,而多数对话框 Enter=发送,
        会把带换行的提示词从换行处提前提交、丢失后半段。
        """
        hesitate_prob = float(self._opt("hesitate_prob"))
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for ch in normalized:
            if ch == "\n":
                page.keyboard.press("Shift+Enter")
            else:
                page.keyboard.type(ch)
            page.wait_for_timeout(self._key_delay(ch, hesitate_prob))

    # 词/句边界字符:真人在这些位置会顿一下(空格、中英文标点、换行)
    _PUNCT_PAUSE_CHARS = frozenset(" \t\n，。、,.!?！？；;：:")

    def _key_delay(self, ch: str, hesitate_prob: float) -> float:
        """单个字符打完后的停顿(ms):基础字间延迟 + 词句边界额外停顿 + 偶发"想一下"。"""
        delay = self._rand("type_delay_min", "type_delay_max")
        if ch in self._PUNCT_PAUSE_CHARS:
            delay += self._rand("punct_pause_min", "punct_pause_max")
        if hesitate_prob > 0 and random.random() < hesitate_prob:
            delay += self._rand("hesitate_ms_min", "hesitate_ms_max")
        return delay

    def _wait_answer(self, page, settle_ms: int, timeout_ms: int) -> str:
        import time

        sel = self._opt("answer_selector")
        deadline = time.monotonic() + timeout_ms / 1000
        last_text, stable_since = "", time.monotonic()
        while time.monotonic() < deadline:
            nodes = page.locator(sel)
            text = nodes.last.inner_text() if nodes.count() else ""
            if text != last_text:
                last_text, stable_since = text, time.monotonic()
            elif text and (time.monotonic() - stable_since) * 1000 >= settle_ms:
                break
            page.wait_for_timeout(self._rand("poll_min", "poll_max"))
            self._reading_fidget(page)
        return last_text

    def _reading_fidget(self, page) -> None:
        """等流式回答时模拟"人在看":小概率轻微滚动或挪一下鼠标,避免全程完全静止。

        全程零光标移动/零滚动地盯着流式输出,本身也是个弱自动化特征。滚动/挪鼠标失败
        (页面不支持/坐标异常)一律吞掉,绝不因"装样子"拖垮真正的采集。
        """
        prob = float(self._opt("read_scroll_prob"))
        if prob <= 0:
            return
        r = random.random()
        try:
            if r < prob:
                page.mouse.wheel(0, random.uniform(80, 320))  # 往下滚一点,像在读
            elif self._cursor and r < prob * 2:
                cx, cy = self._cursor
                self._move_curved(page, cx + random.uniform(-40, 40), cy + random.uniform(-30, 30))
        except Exception:  # noqa: BLE001 — 装样子的动作不允许影响采集
            pass

    def _extract_citations(self, page, brand_name: str) -> list[EngineCitation]:
        out, seen = [], set()
        try:
            links = page.locator(self._opt("citation_selector"))
            for i in range(min(links.count(), 50)):
                node = links.nth(i)
                href = (node.get_attribute("href") or "").strip()
                if not href.startswith("http") or href in seen:
                    continue
                seen.add(href)
                title = (node.inner_text() or "").strip()[:200]
                out.append(
                    EngineCitation(
                        url=href,
                        title=title,
                        mentions_brand=bool(brand_name) and brand_name.lower() in title.lower(),
                    )
                )
        except Exception:  # noqa: BLE001
            pass
        return out
