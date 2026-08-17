"""worker 端采集器注册表(自包含,无数据库依赖)。

新增引擎:实现一个 BrowserChatCollector 子类(声明 url/选择器默认值),
在 REGISTRY 登记 key->类即可。需与后端 engine_collectors 注册表同步。
"""
# 必须保留:老 Mac 常见 Python 3.8,下面 REGISTRY 的 dict[str, type[...]] 注解
# 若无此行会在 import 时被求值,直接 TypeError 崩掉 worker。
from __future__ import annotations

from collectors.base import (
    BaseEngineCollector,
    BrowserChatCollector,
    EngineCitation,
    EngineResult,
)
from collectors.deepseek import DeepSeekCollector
from collectors.doubao import DoubaoCollector
from collectors.kimi import KimiCollector
from collectors.tongyi import TongyiCollector
from collectors.wenxin import WenxinCollector

REGISTRY: dict[str, type[BaseEngineCollector]] = {
    DeepSeekCollector.key: DeepSeekCollector,
    DoubaoCollector.key: DoubaoCollector,
    KimiCollector.key: KimiCollector,
    WenxinCollector.key: WenxinCollector,
    TongyiCollector.key: TongyiCollector,
}

__all__ = [
    "BaseEngineCollector",
    "BrowserChatCollector",
    "EngineCitation",
    "EngineResult",
    "DeepSeekCollector",
    "DoubaoCollector",
    "KimiCollector",
    "WenxinCollector",
    "TongyiCollector",
    "REGISTRY",
]
