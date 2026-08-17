"""豆包(doubao.com)对话采集器(worker 端副本)。

镜像 backend/app/services/engine_collectors/doubao.py。通用流程见基类
BrowserChatCollector,此处只声明站点默认值。
实测校准(2026-07-06,CDP 挂已登录真 Chrome):
- answer_selector `md-box-root` 核实无误(命中 1、.last=完整正文)。
- 提交必须点右下角蓝色发送键 div.send-btn-wrapper:豆包 textarea 里 Enter 不保险,故 submit=button。
- 引用是正文内联链接 <a class='...md-box-solid-color' href='link.wtturl.cn/?target=<编码URL>'>,
  真实域名被 URL 编码进 target、外包 wtturl 跳转壳;见 _extract_citations 覆盖(选 md-box-solid-color
  + 解壳)。另有「参考N篇资料」折叠面板结构不同,此覆盖暂不处理。
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from collectors.base import BrowserChatCollector, EngineCitation


class DoubaoCollector(BrowserChatCollector):
    key = "doubao"
    label = "豆包"

    defaults = {
        "url": "https://www.doubao.com/chat/",
        "input_selector": "textarea",
        # 提交走蓝色发送键:豆包 textarea 里 Enter 不保险,须点 send-btn-wrapper。
        "submit": "button",
        "submit_selector": "div[class*='send-btn-wrapper']",
        # 回答正文在豆包 markdown 组件 `md-box-root` 里(整套 --md-box-* 变量,稳定类名);
        # 内层 container-XXXXXX 是哈希 CSS Module 类,每次构建都变,不可用。已对真实 DOM 校准。
        "answer_selector": "div[class*='md-box-root']",
        # ⚠️ citation_selector 对豆包无效:引用是包了 wtturl 跳转壳的内联链接,见 _extract_citations。
        # 仅作基类兜底占位。
        "citation_selector": "a[class*='md-box-solid-color']",
    }

    @staticmethod
    def _unwrap(href: str) -> str:
        """豆包外链常包一层 link.wtturl.cn/?target=<urlencoded真实URL>,解出真实 URL。"""
        if "wtturl.cn" in href or "target=" in href:
            try:
                real = parse_qs(urlparse(href).query).get("target", [""])[0]
                if real:
                    return unquote(real)
            except Exception:  # noqa: BLE001
                pass
        return href

    def _extract_citations(self, page, brand_name: str) -> list[EngineCitation]:
        """豆包引用是答案正文里的内联链接 <a class='...md-box-solid-color' href=跳转壳>,
        真实 URL 藏在 link.wtturl.cn/?target= 参数里。实测(2026-07-06)一条联网答案抓到 8 条、
        去重 5 条真实 nike.com.cn 外链,全页无导航噪声。

        基类默认 a[href^=http] 存下来全是 wtturl.cn 跳转壳、丢了真实域名 —— 此覆盖按
        md-box-solid-color(link-XXXX 哈希类不可用)取链接并解壳还原真实 URL。
        """
        try:
            items = page.evaluate(
                r"""
                () => {
                  const res = [];
                  for (const a of document.querySelectorAll("a[class*='md-box-solid-color']")) {
                    res.push({ href: a.getAttribute('href') || '', text: (a.innerText || '').trim() });
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
            href = self._unwrap((it.get("href") or "").strip())
            if not href.startswith("http") or href in seen:
                continue
            seen.add(href)
            title = (it.get("text") or "").strip()[:200]
            out.append(
                EngineCitation(
                    url=href,
                    title=title,
                    mentions_brand=bool(brand_name) and brand_name.lower() in title.lower(),
                )
            )
        return out
