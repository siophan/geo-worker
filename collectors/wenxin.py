"""文心一言(chat.baidu.com)对话采集器(worker 端副本)。

镜像 backend/app/services/engine_collectors/wenxin.py。通用流程见基类
BrowserChatCollector,此处只声明站点默认值。
answer_selector 已对**实时 DOM** 核实(2026-07-06,ai-markdown 命中 1 个、正文干净);
如页面再改版可经 config 热覆盖。
⚠️ 「未抓到回答文本」多半是**匿名自动化触发百度图形验证码**(跳 wappass.baidu.com/captcha,
回答不渲染),而非选择器错。走 CDP 挂已登录真 Chrome 的 profile 可显著降低被拦概率。
"""
from __future__ import annotations

import re

from collectors.base import BrowserChatCollector, EngineCitation


class WenxinCollector(BrowserChatCollector):
    key = "wenxin"
    label = "文心一言"

    defaults = {
        "url": "https://chat.baidu.com/",
        "input_selector": "div[contenteditable='true'], textarea",
        # 回答正文在 `ai-markdown` 块(内含 marklang 内层节点,百度 COS 设计系统,稳定类名);
        # 实测(2026-07-06 实时 DOM):ai-markdown 命中 1 个,.last.inner_text() 即干净正文,
        # 与内层 marklang 文本一致。不能用 [class*='answer'] —— 会匹配 answer-menu /
        # answer-ask-container 等,.last 会抓到底部追问建议而非正文;下划线+哈希类
        # (_result-container_p8c64_6 等)是 CSS Module,每次构建都变,不可用。
        "answer_selector": "div[class*='ai-markdown']",
        # ⚠️ citation_selector 对文心无效:引用不是 <a href>,见下方 _extract_citations 覆盖。
        # 仅作基类兜底占位,实际引用走 data-long-press-ext-info 提取。
        "citation_selector": "a[href^='http']",
    }

    def _extract_citations(self, page, brand_name: str) -> list[EngineCitation]:
        """文心引用**不是** <a href>,而是「共参考N篇资料 → 搜索全球N篇资料」面板里的
        <li data-long-press-menu='link'>,URL/标题藏在其 data-long-press-ext-info JSON
        ({"link","linkTitle"})。实测(2026-07-06):这 35 条 <li> 无论面板是否可视都在
        DOM 里、属性已填充,故直接整页读取即可;仍 best-effort 点开面板以防个别版本懒渲染。

        基类默认按 a[href^=http] 取 href 对文心只会抓到页脚「查看使用规则」这条噪声,
        真实来源 0 条 —— 这就是引用一直为空的根因,此覆盖专治。
        """
        item_sel = "li[data-long-press-menu='link']"
        # 仅当一条都读不到时才 best-effort 点开「共参考N篇资料」补渲染。
        # 实测:折叠/展开只是 CSS 隐藏、不卸载 <li>,故常态下 li 已在 DOM、无需点击;
        # 加此守卫避免无谓点击(徒增延迟、偶发误点),也不会把已展开的面板点回折叠。
        try:
            present = page.locator(item_sel).count()
        except Exception:  # noqa: BLE001
            present = 0
        if present == 0:
            try:
                toggle = page.get_by_text(re.compile(r"共参考\d+篇资料")).first
                if toggle.count():
                    toggle.click(timeout=3000)
                    page.wait_for_timeout(600)
            except Exception:  # noqa: BLE001 — 展开只是保险,拿不到不影响下面直接读 DOM
                pass

        try:
            items = page.evaluate(
                r"""
                () => {
                  const res = [];
                  for (const li of document.querySelectorAll("li[data-long-press-menu='link']")) {
                    const raw = li.getAttribute('data-long-press-ext-info') || '';
                    let link = '', title = '';
                    try { const o = JSON.parse(raw); link = o.link || ''; title = o.linkTitle || ''; }
                    catch (e) {}
                    if (!title) title = (li.innerText || '').replace(/^\s*\d+[.、]\s*/, '').trim();
                    if (link) res.push({ link, title });
                  }
                  return res;
                }
                """
            )
        except Exception:  # noqa: BLE001
            items = []

        out: list[EngineCitation] = []
        seen: set[str] = set()
        for it in items or []:
            href = (it.get("link") or "").strip()
            if not href.startswith("http") or href in seen:
                continue
            seen.add(href)
            title = (it.get("title") or "").strip()[:200]
            out.append(
                EngineCitation(
                    url=href,
                    title=title,
                    mentions_brand=bool(brand_name) and brand_name.lower() in title.lower(),
                )
            )
        return out
