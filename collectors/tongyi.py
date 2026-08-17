"""通义千问(www.qianwen.com,原 tongyi)对话采集器(worker 端副本)。

镜像 backend/app/services/engine_collectors/tongyi.py。通用流程见基类
BrowserChatCollector,此处只声明站点默认值。
实测校准(2026-07-06,CDP 挂真 Chrome):
- 域名已由 tongyi.aliyun.com 迁到 **www.qianwen.com**(tongyi 域名会 302 过去,直配新域名更稳)。
- 提交按 Enter 即可(发送按钮无稳定 class)。
- answer_selector 用 div[class*='markdown'](.last=完整正文);原 answerItem 现命中 0、已删。
- 引用是正文内联 markdown 链接 a.qk-md-link(href=来源URL、文本=「来源 - 标题」),全页无导航噪声,
  citation_selector 收紧到 qk-md-link 后基类默认提取即可,无需覆盖。
"""
from __future__ import annotations

from collectors.base import BrowserChatCollector


class TongyiCollector(BrowserChatCollector):
    key = "tongyi"
    label = "通义千问"

    defaults = {
        "url": "https://www.qianwen.com/",
        "input_selector": "textarea, div[contenteditable='true']",
        # 正文取 div[class*='markdown'] 的 .last(整段完整答案)。原 answerItem 系旧版猜测、
        # 现命中 0,已删。qianwen.com 把整条答案放在一个 markdown 容器里,.last 即完整正文。
        "answer_selector": "div[class*='markdown']",
        # 引用是正文内联 markdown 链接 a.qk-md-link,href=来源URL、文本=「来源 - 标题」;
        # 全页无导航噪声,基类默认 _extract_citations 直接可用(title 取链接文本)。
        "citation_selector": "a[class*='qk-md-link']",
    }
