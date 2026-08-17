# 智象 GEO 采集 worker(Mac mini 等真实桌面机)

把「模拟访问 AI 引擎对话」的采集放到真实桌面机上跑:住宅 IP + 真实设备指纹,
绕开云机房 IP 被风控的问题。worker 主动向服务端拉任务、本地 Playwright 采集、
回传结果;服务端不主动连本机,本机只「出不进」,NAT/家庭网络后即可运行。

采集全程**模拟真人**(真实鼠标轨迹点击、逐字打字带随机延迟、随机停顿、抹自动化指纹),
默认开启无需配置。

浏览器**只支持一种模式:CDP 挂真 Chrome** —— worker 通过 CDP 挂到你手动起的真 Google
Chrome 上驱动,指纹和真人手点无异、复用真实已登录 profile、免注入登录态,反风控最强
(见第二节)。缺 CDP 配置时 worker 启动即报错退出,**绝不自弹 Playwright 的
「Chrome for Testing」**(旧的 bundled Chromium 兜底已删除)。

架构与字段设计见仓库 [`docs/distributed-collector-design.md`](../docs/distributed-collector-design.md)。

## 一、安装(在 Mac mini 上)

```bash
# 部署仓库(本目录的发布副本):git@github.com:siophan/geo-worker.git
# 约定:所有 worker 机统一部署在 ~/Documents/geo-worker
git clone git@github.com:siophan/geo-worker.git ~/Documents/geo-worker && cd ~/Documents/geo-worker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> 机器上没配 GitHub SSH key 的话,用 HTTPS 地址 clone:
> `https://github.com/siophan/geo-worker.git`。日后升级:`git pull` 再重跑
> `pip install -r requirements.txt` 即可。

> **无需 `playwright install chromium`**:worker 只走 CDP 挂真 Chrome,从不启动
> Playwright 自带浏览器,下载浏览器内核纯属浪费(老 macOS 上还可能直接失败)。
>
> **老 Mac(旧 macOS / 旧 Python)**:系统自带 python3 哪怕是 3.8/3.9 也能跑——
> requirements 未锁死 playwright 版本,pip 会自动装本机 Python 能用的最高版本,
> CDP 模式下旧版功能完全一致。无需为此升级系统。

## 二、准备真 Chrome(CDP 模式,唯一模式)

1. 在本机手动起一个**专用 profile** 的真 Chrome,带远程调试端口,并保持常驻:
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --remote-debugging-port=9222 --user-data-dir="$HOME/geo-chrome-profile"
   ```
2. 在这个 Chrome 里**手动登录**要服务的各引擎(chat.deepseek.com 等,可扫码/过验证码);
   登录态留在该 profile,过期了再登一次即可,**无需导出 storage_state**。
3. 给 worker 配 CDP 地址(见第三节),它就会挂到这个 Chrome 上驱动、只开关自己的标签、
   绝不关你的 Chrome。

> 运维提示:这个真 Chrome 要**一直开着**;它挂了/掉登录,采集会明确报错停摆,重开重登即可。
> 可把它加进「登录项」开机自启,或用一个 LaunchAgent 拉起。
>
> ⚠️ 除 DeepSeek 外其余引擎的页面选择器为初始猜测,首次真机联调需对照线上 DOM 校准
> (`GEO_ENGINE_<KEY>_CONFIG` 热覆盖,免改代码)。
> 注:`export_login_state.py` 是旧 bundled 模式的登录态导出工具,CDP 模式下已用不到。

## 三、配置

```bash
cp .env.example .env      # 按注释填写;或直接 export 这些环境变量
```
关键项:`GEO_COLLECTOR_TOKEN`(与服务端 `collector_shared_secret` 一致)、
`GEO_WORKER_CAPABILITIES`(本机服务哪些引擎),以及 **CDP 地址**。
`GEO_SERVER_URL` 已内置默认生产地址 `https://www.eleai.cc`,worker 机无需配置,
本地开发联调时才在 .env 里覆盖:
- **全局(推荐)**:`GEO_WORKER_CDP_URL=http://127.0.0.1:9222`,所有引擎共用;
- **按引擎覆盖**:`GEO_ENGINE_<KEY>_CONFIG='{"cdp_url":"http://127.0.0.1:9223"}'`(优先于全局)。

> 缺 CDP 地址、或把 `cdp_url` 写成数字(JSON 漏引号,如 `{"cdp_url":9222}`),worker 启动即
> 明确报错退出,不会带病起跑。

## 四、跑起来

```bash
# 前台试跑(先确认能注册、领到任务)
set -a && source .env && set +a
python collector_worker.py
```

后台保活(开机自启 + 崩溃重启)用 launchd:

```bash
# 编辑 com.zhixiang.collector.plist 里的绝对路径与环境变量
cp com.zhixiang.collector.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zhixiang.collector.plist
tail -f ~/Documents/geo-worker/worker.log
```
> CDP 挂的真 Chrome 需登录用户的图形会话(且要常驻),故 worker 用**用户级 LaunchAgent**,
> 不要用 LaunchDaemon;确保那个带 `--remote-debugging-port` 的 Chrome 也随会话拉起。

## 五、横向扩展(多台机器)

再加一台 Mac mini,重复以上步骤即可——它会以不同 `worker_id` 注册。任务按
`engine` 能力路由,所以可以:
- **按引擎分机**:A 机只配 `deepseek`、B 机只配 `doubao`,隔离风险;
- **同引擎多机分担**:多台都配 `deepseek` 但**各用不同账号**,提升吞吐。

服务端用 `FOR UPDATE SKIP LOCKED` 保证同一任务不会被两台机器重复领取;某台掉线后,
其已领未回传的任务到租约期自动回滚,由其他机器接手。

## 六、服务端侧开关

- 在后端 `.env` 配 `collector_shared_secret`(留空则 `/api/collector/*` 全部 503,
  即「未启用采集」)。
- 在运营后台/`system_settings` 把 `engines_enabled` 设为 `["deepseek"]` 等,诊断时
  才会派发对应引擎的采集任务。

## 维护提示

- **部署仓库**:`siophan/geo-worker` 是主仓库 `worker/` 目录的发布副本(剔除 .env、
  登录态、缓存等本机文件)。代码改动一律先在主仓库完成,再同步推送到部署仓库;
  worker 机只从部署仓库 `git pull`,不要在 worker 机上直接改代码。
- `collectors/` 源自后端 `backend/app/services/engine_collectors/` 的自包含副本(去掉了
  数据库依赖),但**两侧已分叉**:worker 端只走 CDP(本目录),后端那份仍是简化的 bundled
  实现,且生产采集只跑 worker。选择器/流程改动仍需注意同步,后续应抽成共享包消除重复。
- 各家 web 改版会导致选择器失效;`collect()` 任何异常都返回 `ok=False` 不崩进程,
  服务端会记录失败原因并按重试上限处理。
