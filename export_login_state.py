"""一次性:在本机有头浏览器里手动登录某引擎,导出 storage_state 登录态。

用法:
    python export_login_state.py deepseek deepseek_state.json

流程:打开浏览器 → 你手动完成登录(含扫码/验证码)→ 回终端按回车 →
脚本把当前登录态(cookies + localStorage)写入指定 JSON 文件。
之后把该文件路径配到 GEO_ENGINE_DEEPSEEK_STORAGE_STATE 即可。
登录态会过期,过期后重跑本脚本刷新即可。
"""
import sys

from collectors import REGISTRY


def _engine_url(engine: str) -> str:
    """从采集器注册表取该引擎对话页地址,免去与采集器各自维护一份 URL。"""
    cls = REGISTRY.get(engine)
    if cls is None:
        return ""
    # defaults 里声明了站点 url(BrowserChatCollector 子类约定)。
    return getattr(cls, "defaults", {}).get("url", "")


def main() -> None:
    if len(sys.argv) != 3:
        print("用法: python export_login_state.py <engine> <out.json>")
        sys.exit(1)
    engine, out = sys.argv[1], sys.argv[2]
    url = _engine_url(engine)
    if not url:
        print(f"未知引擎 {engine},已知:{list(REGISTRY)}")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(url)
            print(f"\n请在弹出的浏览器里登录 {engine}({url})。")
            input("登录完成后回到这里按【回车】导出登录态... ")
            context.storage_state(path=out)
            print(f"已导出登录态 -> {out}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
