"""DeepSeek 对话采集器(worker 端副本)。

镜像 backend/app/services/engine_collectors/deepseek.py;唯一差别是从本地
collectors.base 导入。通用流程见基类 BrowserChatCollector,此处只声明站点默认值。
"""
from __future__ import annotations

from collectors.base import BrowserChatCollector


class DeepSeekCollector(BrowserChatCollector):
    key = "deepseek"
    label = "DeepSeek"

    defaults = {
        "url": "https://chat.deepseek.com/",
        "input_selector": "textarea",
        "answer_selector": "div[class*='markdown'], .ds-markdown",
        "citation_selector": "a[href^='http']",
    }
