"""Kimi(www.kimi.com)对话采集器(worker 端副本)。

镜像 backend/app/services/engine_collectors/kimi.py。通用流程见基类
BrowserChatCollector,此处只声明站点默认值。
实测校准(2026-07-06,CDP 挂已登录真 Chrome):
- 域名已由 kimi.moonshot.cn 迁到 **www.kimi.com**(旧域名会跳转,直接配新域名更稳)。
- 提交必须**点发送按钮**(div.send-button-container):kimi.com 按 Enter 不发送,故 submit=button;
  基类默认 Enter 会让提示词根本发不出去、全程「未抓到回答文本」。
- answer_selector 用 segment-content(其 .last 是最新一轮完整答案);markdown 会被拆成多个子块,
  .last 只抓到最后一块(表格片段)而截断。
- 引用是 <a class='pua-ref-cite-tag' href=来源URL data-site-name=来源名>,见 _extract_citations 覆盖。
"""
from __future__ import annotations

from collectors.base import BrowserChatCollector, EngineCitation


class KimiCollector(BrowserChatCollector):
    key = "kimi"
    label = "Kimi"

    defaults = {
        "url": "https://www.kimi.com/",
        "input_selector": "div[contenteditable='true'], textarea",
        # 提交走发送按钮:kimi.com 按 Enter 不发送,须点右下角 send-button-container。
        "submit": "button",
        "submit_selector": "div[class*='send-button']",
        # 正文取 segment-content 的 .last(最新一轮完整答案);markdown 会被拆成多个子块,
        # .last 只抓到最后一块(表格片段)而截断。联网型答案该块会带「搜索网页 <关键词>」前缀,
        # 但仍含完整正文、非空,可接受。
        "answer_selector": "div[class*='segment-content']",
        # ⚠️ citation_selector 对 kimi 无效:真实引用是 pua-ref-cite-tag,见 _extract_citations。
        # 仅作基类兜底占位。
        "citation_selector": "a[class*='pua-ref-cite-tag']",
    }

    def _extract_citations(self, page, brand_name: str) -> list[EngineCitation]:
        """Kimi 联网引用是 <a class='pua-ref-cite-tag' href=来源URL data-site-name=来源名>,
        内层文本为空、来源名在 data-site-name 属性。实测(2026-07-06)一条联网答案抓到 14 条
        真实外链(t3/nike/whowhatwear/taobao/什么值得买...),0 噪声。

        基类默认 a[href^=http] 会把侧边栏导航(新建会话/插件/招聘/协议等 10+ 条)当引用抓进来、
        真实来源反被淹没 —— 此覆盖只取 pua-ref-cite-tag,title 用 data-site-name。
        """
        try:
            items = page.evaluate(
                r"""
                () => {
                  const res = [];
                  for (const a of document.querySelectorAll("a[class*='pua-ref-cite-tag']")) {
                    const link = a.getAttribute('href') || '';
                    const site = a.getAttribute('data-site-name') || '';
                    if (link) res.push({ link, title: site });
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
