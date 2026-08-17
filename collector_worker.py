"""智象 GEO 采集 worker(运行在 Mac mini 等真实桌面机)。

职责:向服务端注册 → 周期心跳 → 原子领取采集任务 → 本地 Playwright 模拟访问
对话采集 → 回传结果。服务端不主动连本机,本机只「出不进」,NAT 后亦可运行。

配置全部走环境变量(见 .env.example / README):
  GEO_SERVER_URL            服务端基址,默认 https://www.eleai.cc(生产写死,一般无需配置;
                            本地开发可覆盖为 http://127.0.0.1:9102)
  GEO_COLLECTOR_TOKEN       与服务端 collector_shared_secret 一致的共享密钥
  GEO_WORKER_CAPABILITIES   本机能服务的引擎,逗号分隔,如 "deepseek,kimi"
  GEO_WORKER_CDP_URL        【必填】所有引擎共用的真 Chrome CDP 地址,如 http://127.0.0.1:9222
                            (先用 --remote-debugging-port=9222 启动 Chrome 并登录各引擎)
  GEO_ENGINE_<KEY>_CONFIG          可选:JSON,热覆盖该引擎选择器/超时/cdp_url 等(免改代码)
  GEO_WORKER_ID_FILE        worker_id 持久化文件(默认 ./worker_id)

  <KEY> 为引擎大写名:DEEPSEEK / DOUBAO / KIMI / WENXIN / TONGYI。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import httpx

from collectors import REGISTRY

# 生产服务端写死为默认值:worker 机免配置、也不会再把示例域名带上线(曾致 DNS 解析失败)。
# 本地开发用 .env 的 GEO_SERVER_URL 覆盖(如 http://127.0.0.1:9102)。
SERVER = os.environ.get("GEO_SERVER_URL", "https://www.eleai.cc").rstrip("/")
TOKEN = os.environ.get("GEO_COLLECTOR_TOKEN", "")
CAPABILITIES = [c.strip() for c in os.environ.get("GEO_WORKER_CAPABILITIES", "").split(",") if c.strip()]
# 全局 CDP 地址:配一处即让所有引擎都挂到同一个真 Chrome(--remote-debugging-port),
# 不再各自弹 Playwright bundled 的 "Chrome for Testing"。per-engine 的
# GEO_ENGINE_<KEY>_CONFIG 里若显式给了 cdp_url 则以其为准(不被全局覆盖)。
CDP_URL = os.environ.get("GEO_WORKER_CDP_URL", "").strip()
WORKER_ID_FILE = Path(os.environ.get("GEO_WORKER_ID_FILE", "worker_id"))
IDLE_SLEEP_SEC = 5
ERROR_BACKOFF_SEC = 15
# 一次只领 1 条:批内串行采集,单条最坏 ~90s(见 collectors 的 timeout_ms),
# 若一次领多条,批内后续任务的租约(LEASE_TTL_SEC=180s)可能在轮到它前就过期
# 被服务端 reclaim,采完回传反被丢弃。领 1 条则每条都在租约内回传,稳妥。
CLAIM_BATCH = 1


def _fatal(msg: str) -> None:
    print(f"[worker] 致命错误:{msg}", file=sys.stderr)
    sys.exit(1)


def _worker_id() -> str:
    """持久化 worker_id:重启后保持同一身份(便于服务端识别/复用注册)。"""
    if WORKER_ID_FILE.exists():
        wid = WORKER_ID_FILE.read_text().strip()
        if wid:
            return wid
    wid = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    WORKER_ID_FILE.write_text(wid)
    return wid


def _engine_config(key: str) -> dict:
    """从环境变量装配某引擎的采集配置(CDP 地址 + 可选选择器/超时覆盖)。

    登录态不再从这里注入 —— CDP 模式复用真 Chrome profile 里已登录的会话。
    """
    cfg: dict = {}
    # 可选 JSON:热覆盖 url/选择器/超时/cdp_url 等(对齐后端 engine_<key>_config,页面改版免改代码)
    extra = os.environ.get(f"GEO_ENGINE_{key.upper()}_CONFIG", "")
    if extra:
        var = f"GEO_ENGINE_{key.upper()}_CONFIG"
        try:
            parsed = json.loads(extra)
        except json.JSONDecodeError as exc:
            parsed = None
            # 打出原始值:最常见的坑是 .env 里没用单引号裹住,source 时被 shell 吞掉双引号,
            # 值变成 {cdp_url:http://...} 这种非法 JSON。带上原始值一眼可辨。
            print(
                f"[worker] {var} 不是合法 JSON({exc});原始值={extra!r}。"
                f"提示:.env 里该值要用【单引号】整段裹住,避免 shell 吞掉里面的双引号",
                file=sys.stderr,
            )
        # 必须是 JSON 对象:非 dict(字符串/数组/标量)一律忽略,避免 cfg.update 抛错崩 worker。
        if isinstance(parsed, dict):
            cfg.update(parsed)
        elif parsed is not None:
            print(f"[worker] 忽略非 JSON 对象的 {var}(需为对象):{extra!r}", file=sys.stderr)
    # 全局 CDP 兜底:per-engine CONFIG 未显式给 cdp_url 时,统一用 GEO_WORKER_CDP_URL。
    if CDP_URL and "cdp_url" not in cfg:
        cfg["cdp_url"] = CDP_URL
    return cfg


def _build_collectors() -> dict:
    collectors = {}
    for key in CAPABILITIES:
        if key not in REGISTRY:
            print(f"[worker] 跳过未知引擎:{key}(REGISTRY 未登记)", file=sys.stderr)
            continue
        cfg = _engine_config(key)
        cdp = cfg.get("cdp_url")
        # 只支持 CDP:启动即校验。缺 cdp_url,或写成非字符串(JSON 漏引号如 {"cdp_url":9222})都
        # 直接 fatal,绝不带病起跑 —— 既杜绝之前「静默回退 bundled 弹 Chrome for Testing」的坑,
        # 也避免非字符串 cdp_url 在 .strip() 处抛未捕获的 AttributeError。
        if not isinstance(cdp, str) or not cdp.strip():
            _fatal(
                f"引擎 {key} 的 CDP 配置无效(cdp_url={cdp!r}):请设 GEO_WORKER_CDP_URL,"
                f"或在 GEO_ENGINE_{key.upper()}_CONFIG 里给【字符串】cdp_url(JSON 记得带引号)。"
                f"worker 已只支持挂真 Chrome(--remote-debugging-port),不再有 bundled 兜底"
            )
        collectors[key] = REGISTRY[key](cfg)
        print(f"[worker] {key} → CDP {cdp.strip()}", file=sys.stderr)
    if not collectors:
        _fatal("没有可用的采集器,请检查 GEO_WORKER_CAPABILITIES 与 GEO_WORKER_CDP_URL")
    return collectors


class Server:
    def __init__(self, base: str, token: str):
        self._client = httpx.Client(
            base_url=base, headers={"X-Collector-Token": token}, timeout=30
        )

    def post(self, path: str, body: dict, *, allow_conflict: bool = False) -> dict:
        resp = self._client.post(path, json=body)
        # 409 = 任务已被重派/租约过期,本地结果作废;调用方按 conflict 跳过即可,不算通信失败
        if allow_conflict and resp.status_code == 409:
            return {"ok": False, "status": "conflict"}
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    if not SERVER or not TOKEN:
        _fatal("缺少 GEO_COLLECTOR_TOKEN(或 GEO_SERVER_URL 被覆盖成了空值)")
    worker_id = _worker_id()
    collectors = _build_collectors()
    caps = list(collectors.keys())
    server = Server(SERVER, TOKEN)

    reg = server.post(
        "/api/collector/register",
        {"worker_id": worker_id, "hostname": socket.gethostname(), "capabilities": caps},
    )
    print(f"[worker] 已注册 {worker_id} capabilities={caps} -> {reg}")

    in_flight = 0
    while True:
        try:
            server.post("/api/collector/heartbeat", {"worker_id": worker_id, "in_flight": in_flight})
            tasks = server.post(
                "/api/collector/claim",
                {"worker_id": worker_id, "capabilities": caps, "max": CLAIM_BATCH},
            ).get("tasks", [])
            if not tasks:
                time.sleep(IDLE_SLEEP_SEC)
                continue

            for t in tasks:
                in_flight = len(tasks)
                collector = collectors.get(t["engine"])
                if collector is None:
                    server.post("/api/collector/result", {
                        "worker_id": worker_id, "task_id": t["task_id"], "ok": False,
                        "error": f"本机无 {t['engine']} 采集器",
                    }, allow_conflict=True)
                    continue
                print(f"[worker] 采集 task={t['task_id']} engine={t['engine']} prompt={t['prompt_text'][:24]!r}")
                r = collector.collect(t["prompt_text"], t.get("brand_name", ""))
                res = server.post("/api/collector/result", {
                    "worker_id": worker_id, "task_id": t["task_id"], "ok": r.ok,
                    "answer": r.answer, "brand_mentioned": r.brand_mentioned,
                    "citations": [c.to_dict() for c in r.citations], "error": r.error,
                }, allow_conflict=True)
                if res.get("status") == "conflict":
                    print(f"[worker]   -> 任务已被重派/租约过期,结果被拒,跳过 task={t['task_id']}")
                    continue
                print(f"[worker]   -> ok={r.ok} citations={len(r.citations)} err={r.error}")
            in_flight = 0
        except httpx.HTTPError as exc:
            print(f"[worker] 与服务端通信失败,{ERROR_BACKOFF_SEC}s 后重试:{exc}", file=sys.stderr)
            time.sleep(ERROR_BACKOFF_SEC)
        except KeyboardInterrupt:
            print("[worker] 收到中断,退出。")
            break


if __name__ == "__main__":
    main()
