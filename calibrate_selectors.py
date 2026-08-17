"""对着真 Chrome(CDP)校准某引擎的答案/引用选择器。

采集报「未抓到回答文本(选择器可能需校准)」时用它:连上你手动起的真 Chrome,
打开该引擎对话页、填入提示词并提交、等回答稳定,然后 dump 出**真实 DOM** 里
最像「回答正文」的候选容器(按文本长度、带 class/data-testid)和所有外链,
据此挑出正确的 answer_selector / citation_selector 填进
GEO_ENGINE_<KEY>_CONFIG(或改采集器 defaults)。

前提:worker 机上已用 --remote-debugging-port 起了真 Chrome 并登录该引擎。

用法:
    python calibrate_selectors.py doubao \\
        --cdp http://127.0.0.1:9222 \\
        --prompt "购买防风防水夹克和户外运动装备时,如何评价 耐克"

engine 取值:deepseek / doubao / kimi / wenxin / tongyi(URL 与当前默认选择器取自采集器注册表)。
"""
from __future__ import annotations

import argparse
import sys
import time

from collectors import REGISTRY

# 页内脚本:枚举「文本量可观、且没有某个子节点几乎独占其全部文本」的最紧凑容器,
# 按文本长度倒序返回。回答正文通常就在里面靠前几个,带可辨识的 class/testid。
_DUMP_BLOCKS = r"""
() => {
  const res = [];
  for (const el of document.body.querySelectorAll('*')) {
    const t = (el.innerText || '').trim();
    if (t.length < 40) continue;
    let childMax = 0;
    for (const c of el.children) {
      const ct = (c.innerText || '').trim().length;
      if (ct > childMax) childMax = ct;
    }
    if (childMax >= t.length * 0.9) continue;  // 子节点几乎独占全部文本 -> 不是最紧凑的包裹层
    res.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 160),
      testid: el.getAttribute('data-testid') || '',
      len: t.length,
      text: t.slice(0, 120).replace(/\s+/g, ' '),
    });
  }
  res.sort((a, b) => b.len - a.len);
  return res.slice(0, 12);
}
"""

_DUMP_LINKS = r"""
() => {
  const seen = new Set(), out = [];
  for (const a of document.querySelectorAll("a[href^='http']")) {
    const href = a.getAttribute('href');
    if (!href || seen.has(href)) continue;
    seen.add(href);
    out.push({ href, text: (a.innerText || '').trim().slice(0, 80) });
    if (out.length >= 30) break;
  }
  return out;
}
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="校准某引擎的答案/引用选择器(挂 CDP 真 Chrome)")
    ap.add_argument("engine", help="引擎 key:deepseek/doubao/kimi/wenxin/tongyi")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="真 Chrome 的 CDP 地址")
    ap.add_argument("--prompt", required=True, help="用于触发一条回答的提示词")
    ap.add_argument("--wait", type=int, default=40, help="等回答稳定的最长秒数(默认 40)")
    args = ap.parse_args()

    if args.engine not in REGISTRY:
        sys.exit(f"未知引擎 {args.engine};可选:{', '.join(REGISTRY)}")
    collector = REGISTRY[args.engine]({})
    url = collector._opt("url")
    input_sel = collector._opt("input_selector")
    answer_sel = collector._opt("answer_selector")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as exc:  # noqa: BLE001
            port = args.cdp.rsplit(":", 1)[-1]
            sys.exit(
                f"连不上真 Chrome CDP({args.cdp}):{type(exc).__name__}:{exc}\n"
                f"请确认已用 --remote-debugging-port={port} 启动 Chrome 并登录 {args.engine}"
            )
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            print(f"[calibrate] 打开 {url}", flush=True)
            page.set_default_timeout(30000)
            page.goto(url, wait_until="domcontentloaded")

            box = page.locator(input_sel).first
            box.wait_for(state="visible")
            box.click()
            box.fill(args.prompt)
            page.keyboard.press("Enter")
            print(f"[calibrate] 已提交提示词,等回答稳定(≤{args.wait}s)…", flush=True)

            # 轮询:任意候选块文本连续 ~3s 不再增长即认为稳定
            deadline = time.monotonic() + args.wait
            last_total, stable_since = -1, time.monotonic()
            while time.monotonic() < deadline:
                blocks = page.evaluate(_DUMP_BLOCKS)
                total = sum(b["len"] for b in blocks[:3])
                if total != last_total:
                    last_total, stable_since = total, time.monotonic()
                elif total > 0 and (time.monotonic() - stable_since) >= 3:
                    break
                page.wait_for_timeout(500)

            blocks = page.evaluate(_DUMP_BLOCKS)
            links = page.evaluate(_DUMP_LINKS)
            cur_hits = page.locator(answer_sel).count()

            print("\n===== 当前 answer_selector 命中情况 =====", flush=True)
            print(f"selector: {answer_sel!r} → 命中 {cur_hits} 个节点"
                  f"{'(空,难怪抓不到)' if cur_hits == 0 else ''}")

            print("\n===== 候选「回答正文」容器(按文本长度倒序)=====", flush=True)
            for i, b in enumerate(blocks, 1):
                sel_hint = (
                    f"div[data-testid='{b['testid']}']" if b["testid"]
                    else (f"{b['tag']}[class*='{b['cls'].split()[0]}']" if b["cls"].split() else b["tag"])
                )
                print(f"[{i}] <{b['tag']}> len={b['len']} testid={b['testid']!r}\n"
                      f"    class={b['cls']!r}\n"
                      f"    选择器猜测: {sel_hint}\n"
                      f"    文本: {b['text']}", flush=True)

            print("\n===== 页面外链(候选 citation_selector 数据)=====", flush=True)
            for l in links[:15]:
                print(f"  {l['href']}  «{l['text']}»", flush=True)
            if not links:
                print("  (无外链;豆包这类可能引用在展开面板里,需另找)", flush=True)

            print("\n提示:挑出含完整回答的那一项,把它的『选择器猜测』填进\n"
                  f"  GEO_ENGINE_{args.engine.upper()}_CONFIG='{{\"answer_selector\":\"...\"}}'\n"
                  "验证无误后再固化到采集器 defaults。", flush=True)
        finally:
            page.close()  # 只关自己开的标签,保留你的真 Chrome


if __name__ == "__main__":
    main()
