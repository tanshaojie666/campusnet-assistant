# CampusNetAssistant · 校园网助手

[![Release](https://img.shields.io/github/v/release/tanshaojie666/campusnet-assistant?label=release)](https://github.com/tanshaojie666/campusnet-assistant/releases/latest)
[![CI](https://github.com/tanshaojie666/campusnet-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/tanshaojie666/campusnet-assistant/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

**English:** CampusNetAssistant is a dependency-free Windows utility (pure Python standard library + tkinter) that keeps your campus network connected, closes the proxy/VPN clients that break campus authentication, and — when you launch a program that needs it — automatically starts your chosen proxy client, picks the fastest working node by real latency testing, and turns on the system proxy. No third-party packages, no telemetry, passwords encrypted with Windows DPAPI.

> 📖 **完全不懂网络也能看懂** → 看 [**完整使用说明（大白话版）**](docs/使用说明.md)
> 里面有每个功能"干嘛用、什么时候需要、怎么开"，还有三个可以直接照抄的典型用法。

### Quick Start (English)

```text
1. Install Python 3.8+ for Windows (python.org, "Add python.exe to PATH").
2. Download / clone this project to any folder, e.g. D:\CampusNetAssistant.
3. Double-click CampusNetAssistant.pyw   (or run:  python -m campusnet)
4. In the window: pick your campus access mode, choose your dial-up
   connection (or Wi-Fi SSID), enter your account, click 保存设置 (Save),
   then 立即连接 (Connect now).
5. Optional: run an elevated command prompt and execute
   python -m campusnet --install-boot      to keep the link alive at boot
   and on the lock screen (runs as SYSTEM).
6. Stuck? Run:  python -m campusnet --selftest
   and:           python -m campusnet --scan
```

Everything runs locally. The only network calls are the connectivity probes you configure and the proxy client's own `127.0.0.1` control port.

---

## 目录

- [这个项目解决什么问题](#这个项目解决什么问题)
- [功能特性](#功能特性)
- [界面说明](#界面说明)
- [环境要求](#环境要求)
- [安装与快速开始](#安装与快速开始)
- [命令行用法](#命令行用法)
- [配置文件](#配置文件)
- [工作原理](#工作原理)
- [如何添加自己的翻墙客户端](#如何添加自己的翻墙客户端)
- [常见问题（FAQ）](#常见问题faq)
- [安全与隐私](#安全与隐私)
- [已知限制](#已知限制)
- [项目结构](#项目结构)
- [贡献指南](#贡献指南)
- [License](#license)

---

## 这个项目解决什么问题

在国内高校里，"上校园网"和"用代理/翻墙"这两件事天生打架，而且打架的方式很具体：

1. **校园网要拨号，而且只认自己的账号。** 大多数高校宿舍用的是 PPPoE 拨号（Windows 里的"宽带连接"），部分校区用 Wi-Fi + 网页门户认证。手动点一次不难，难的是它**半夜断线**、**锁屏后会掉**、**开机时你还没登录 Windows 它压根不拨**。
2. **代理/VPN 客户端会抢网络。** 很多 Clash/mihomo 系客户端在开机时会自动启动、写系统代理、甚至抢 DNS。校园网认证流量被塞进代理隧道 → 认证失败 → 网断了，而你还在奇怪"为什么网页打不开"。
3. **无线网卡会自己乱连。** Windows 默认"自动连接"所有保存过的 Wi-Fi，笔记本从宿舍走到教学楼，就自动跳到别的 SSID 上去了，校园网连接随之断掉。
4. **翻墙客户端要手动开、手动选节点。** 想用的时候先开客户端、等它加载、点开节点列表、试几个节点看看哪个能通——这套动作每天重复好几遍。

CampusNetAssistant 把这些串成一条自动化链路：**该连的自动连、该断的自动重拨、该关的代理自动关、该开的代理在你打开目标程序那一刻自动开好并切到最快的可用节点。**

## 功能特性

| 功能 | 说明 | 是否默认开启 |
| --- | --- | --- |
| 校园网接入方式可选 | 有线 / 无线（连接指定 SSID）/ 两者都要 | 用户选择 |
| **有线认证方式可选（可多选）** | **PPPoE 拨号**、**自动获取 IP(DHCP)**、**静态 IP**、**Web 门户认证**（深信服/锐捷/Dr.COM，支持自动填表 / 请求模板 / 执行脚本三种方式）、**学校专用客户端**、**有线 802.1X**，还能选择"先重启网卡再认证"。按勾选顺序依次执行 → 详见 [docs/WIRED.md](docs/WIRED.md) | 用户选择 |
| **无线也能过门户认证** | 网页（门户）认证是**有线无线共用**的一份设置：无线连上 SSID 但上不了网时会自动尝试认证（覆盖"校园 WiFi 连上后弹登录页"这种常见场景） | 用户选择 |
| 断线自动重拨 | 按设定间隔探测连通性，判定掉线后自动重连（默认 **10 秒**一次；链路硬断线时**立刻重拨**，不等最小间隔） | 开启 |
| **没在翻墙就自动连回校园网** | 断开校园网之后，如果一直没在使用代理（判定依据：翻墙标记 + 关闭名单进程），就自动把校园网连回来 —— 为翻墙而断开时特别省心。默认关闭（手动断开应保持断开），可在界面勾选 | 用户选择 |
| **跟着 VPN 走** | 以"有没有在用代理"为准自动切网络：翻墙中 → 断开校园网 + 自动连上你指定的无线（手机热点）；**VPN 一关 → 10 秒内切回校园网**。保证随时都有网 | 用户选择 |
| **功能开关** | 每个自动行为都能单独关掉：断线自动重拨、拨号前关代理/VPN、更新后自动重启、点关闭收进托盘、只允许开一个程序，以及检测间隔（5~30 秒） | 全部开启，可关 |
| 连上校园网自动关闭代理/VPN | 名单可配置；触发时机为**接入前**先关一遍，**连上之后再检查一遍** | 用户选择 |
| 无线策略 | 把本机**所有已保存的 Wi-Fi 配置**改成"手动连接"（或直接禁用 WLAN 网卡），防止自动连到别的网络 | 用户选择 |
| 翻墙模式 | **未连校园网**时，检测到指定程序启动 → 启动所选代理客户端 → 通过 mihomo/Clash 控制接口按地区筛选节点 → **逐个真实测延迟** → 切到最快可用的那个 → 设置系统代理 | 用户选择 |
| 客户端适配器可扩展 | 内置 6 个 mihomo/Clash 系客户端定义（E-IX、魔戒、FlClash、FlyingBird、万达云、Clash Verge），每个都给了多个候选路径自动探测；也支持在配置里自定义（界面 exe、内核 exe、配置目录、控制端口、进程名单） | 内置 + 自定义 |
| **翻墙客户端可勾选** | 界面列出所有客户端，**☑ 允许使用 / ☐ 不参与**（只勾 E-IX 就只用 E-IX，不会退到别的客户端）；列表顺序 = 尝试顺序；每行显示"已装/未装"，支持一键"只留勾选的" | 用户选择 |
| 触发程序可配置 | 界面上可增删：选 exe 或手输进程名；浏览器类按**窗口标题关键词**判断（如 ChatGPT） | 可增删 |
| 系统级守护 | 注册计划任务 `CampusNetAssistant-Boot`，以 **SYSTEM** 身份在开机 / 锁屏 / 未登录时工作（另加每 5 分钟触发一次，失败自动重启 3 次） | 可选安装 |
| 系统托盘 | 点关闭按钮 = 隐藏到托盘（纯 `ctypes` 实现，无第三方库），双击托盘图标恢复窗口 | 开启 |
| 开机自启 | 登录后自动打开界面/进入守护 | 用户选择 |
| 环境自检与扫描 | `--selftest` 排查环境问题（网卡、认证方式、链路状态、客户端、守护）；`--scan` 扫描本机 PPPoE 连接、无线配置、已装代理客户端 | 命令行 |
| 门户认证厂商预设 | 界面里直接选「深信服 / 锐捷 / Dr.COM / H3C」，自动填好常见请求地址与字段名；另有「测试门户认证」按钮，不用等掉线就能试一次 | 内置 |
| 配置导出 / 导入 | 一键把设置导出成 JSON，换电脑或分享给同学（密码是本机账户加密的密文，换机需重填） | 界面按钮 |
| 单元测试与 CI | 29 个单元测试（模板替换 / 登录页解析 / 认证方式推断 / 配置合并 / 地区匹配 / 电话簿解析 / 规则兼容…），不联网、不改系统设置；GitHub Actions 每次提交自动跑 | 开发用 |

> 所有功能都在本机完成，不依赖任何服务器，也不上传任何数据。
> **有线接入方式的完整说明（怎么判断自己是哪种、门户认证怎么抓请求、厂商字段参考、排查清单）见 [docs/WIRED.md](docs/WIRED.md)；版本变化见 [CHANGELOG.md](CHANGELOG.md)。**

## 界面说明

> **截图占位**：截图统一放在 [`docs/images/`](docs/images/) 下，文件名规划见该目录的说明。下面先用文字描述界面元素。

主窗口是一个普通 tkinter 窗口，自上而下分为四块：

**① 状态区**（顶部，一眼看清现在什么情况）

| 显示项 | 含义 |
| --- | --- |
| 校园网连接状态 | 当前是"已连接 / 未连接 / 拨号中 / 冷却中" |
| 是否已联网 | 探测结论：通 / 不通 |
| 探测依据 | 本次结论是怎么得出的（比如"TCP 202.117.112.13:53 成功"或"全部探测目标无响应"） |
| 当前生效策略 | 现在实际在跑哪一套：仅校园网 / 已关闭代理 / 翻墙模式已接管 |

**② 三个"可选"控件**（本工具的核心配置）

1. **校园网接入方式** —— 有线 / 无线 / 两者都要。
   - 选"有线"：填"宽带连接"名称 + 账号密码。
   - 选"无线"：填要连接的 SSID。
   - 选"两者都要"：先有线后无线，任一成功即可。
2. **翻墙客户端优先级列表** —— 内置客户端清单，每个前面有勾选框，可勾选启用，也可上下调整顺序。顺序 = 尝试顺序：第一个能启动成功的就用它。
3. **翻墙触发程序名单** —— 可增删。点"添加"可以从磁盘选一个 exe，也可以直接手输进程名（如 `telegram.exe`）。浏览器类程序（Chrome/Edge 等）无法靠进程名区分你在看什么网站，因此对它们按**窗口标题关键词**判断，例如 `chatgpt`、`openai`、`claude`。

**③ 可选开关**

- 连上校园网就关闭代理/VPN（旁边是**名单管理**：增删要关闭的进程名）
- 无线改成手动连接
- 翻墙模式
- 开机自启

**④ 按钮**

| 按钮 | 作用 |
| --- | --- |
| 立即连接 | 立刻执行一次接入流程 |
| 断开 | 断开当前连接，并写入"先别拨"标记，进入冷却期 |
| 保存设置 | 把界面上的选择写进配置文件 |
| 查看日志 | 打开运行日志 |
| 立即检查 | 立刻跑一次状态检查（不等待下一个周期） |
| 退出程序 | 真正退出（关闭按钮只是隐藏到托盘） |

**托盘行为**：关闭按钮 = 隐藏到系统托盘；双击托盘图标恢复窗口；托盘菜单通常提供"显示/连接/断开/退出"等项。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（x64 或 ARM64 均可，只要有 Windows 自带的拨号与无线组件） |
| Python | 3.8 及以上，安装时勾选 **Add python.exe to PATH** |
| 第三方库 | **不需要**。界面用标准库 `tkinter`，其余用 `ctypes` / `winreg` / `urllib` 等标准库 |
| tkinter | Python 官方 Windows 安装包自带；若用精简版/Microsoft Store 版可能缺失，用 `python -c "import tkinter"` 验证 |
| 权限 | 日常使用普通用户即可；**拨号、修改无线配置、注册计划任务需要管理员**（见 FAQ） |
| 磁盘 | 随便放，几 MB |

## 安装与快速开始

### 方式零：下载免安装版（**不想装 Python 就用这个**）

到 [**Releases 页面**](../../releases/latest) 下载 `校园网助手-免安装版.zip`（约 18 MB），
解压后双击 **`① 启动校园网助手.cmd`** 即可使用 —— **包里自带精简版 Python，无需安装任何东西**。
包内附 `先看我-怎么用.txt`，同学/同事照着做两分钟就能配好。

> 免安装版只在本机运行，不联网安装依赖；解压到任意目录都行（路径含中文也没问题）。

### 方式一：下载源码压缩包（适合已装 Python 的人）

1. 在 GitHub 仓库页面点 **Code → Download ZIP**，解压到一个**路径不含中文**的目录，例如 `D:\CampusNetAssistant`。
2. 确认已安装 Python 3.9+。打开"命令提示符"输入 `python --version`，能打印版本即可。
3. 双击目录里的 **`CampusNetAssistant.pyw`**（`.pyw` 双击不会弹出黑色命令行窗口）。
4. 在界面里完成首次配置：
   - 选接入方式（有线 / 无线 / 两者）；
   - **有线**：在下拉框选你的拨号连接，并**勾选有线认证方式**（PPPoE 拨号 / 自动获取 IP /
     静态 IP / Web 门户认证 / 学校客户端 / 802.1X，可多选；不确定就同时勾 PPPoE 和 DHCP）；
     详解见 [docs/WIRED.md](docs/WIRED.md)；
   - **无线**：填 SSID；
   - 勾上"连上校园网就关闭代理/VPN"，并在名单里加上你常用的代理客户端；
   - 点 **保存设置**（也可以点「导出设置…」把配置存成文件，换电脑时「导入设置…」）。
5. 点 **立即连接** 验证能上；上不去就双击 `② 自检` 或用命令行跑一次自检（见下）。

### 方式二：命令行 / git

```bat
git clone https://github.com/<你的账号>/CampusNetAssistant.git
cd CampusNetAssistant
python -m campusnet            :: 打开图形界面
python -m campusnet --scan     :: 先看一眼本机有啥连接、啥无线网、装了什么代理客户端
```

### 方式三：自己打包免安装版（分发给同学）

把本机 Python 的精简副本和项目放一起即可：

```bat
robocopy C:\Python3xx .\python /E /XD Doc Lib\test Lib\idlelib Lib\ensurepip Lib\site-packages
```

再写一个 `启动.cmd` 调用 `python\pythonw.exe CampusNetAssistant.pyw`（注意 `.cmd` 内容保持纯 ASCII）。
本项目的 Releases 附件就是这么打出来的。

### 第一次使用建议跑这两条

```bat
python -m campusnet --scan        :: 本机有什么：PPPoE 连接名、已保存的 Wi-Fi、已装的代理客户端
python -m campusnet --selftest    :: 环境自检：网卡、认证方式、链路状态、代理客户端、系统级守护
```

### 让它在开机 / 锁屏时也工作

以**管理员**身份打开"命令提示符"（或 Windows 终端），在项目目录执行：

```bat
python -m campusnet --install-boot
```

它会注册一个以 **SYSTEM** 身份运行的计划任务 `CampusNetAssistant-Boot`：**开机时**启动守护进程，并额外**每 5 分钟**触发一次（失败自动重试 3 次，电池供电时也照常运行），因此**未登录 / 锁屏**状态下也能继续工作（这样就不会出现"锁屏掉线、登录后才发现网没了"）。

安装过程做了这几件事，出问题时可对照排查：

1. 检查管理员权限；
2. 把用户配置转成系统级配置（密码用**机器范围 DPAPI** 重新加密，并自检能否加密成功）；
3. 给 `%ProgramData%\CampusNetAssistant\` 目录授权普通用户**只读**（方便你看日志）；
4. 依次尝试三种方式注册计划任务：PowerShell `ScheduledTasks` 模块 → `schtasks /XML` → `schtasks /RU SYSTEM /SC ONSTART`，任一成功即可；
5. 结束旧实例、启动新守护，并**等它写心跳**（最多 60 秒）；没等到会打印日志尾部让你定位。

> 有的下载包里附带了辅助脚本 `scripts\install-boot.cmd`（右键 → 以管理员身份运行），作用和上面的命令一样；没有的话直接用命令即可。

卸载：

```bat
python -m campusnet --uninstall-boot
```

## 命令行用法

```bat
python -m campusnet --selftest              :: 环境自检，排查问题用
python -m campusnet --scan                  :: 扫描本机：PPPoE 连接、无线配置、已装代理客户端
python -m campusnet                        :: 打开图形界面
python -m campusnet --boot                  :: 系统级守护（由计划任务以 SYSTEM 调用）
python -m campusnet --install-boot          :: 注册开机/锁屏守护任务（需管理员）
python -m campusnet --uninstall-boot        :: 卸载
python -m campusnet --set-account 账号 密码  :: 保存有线拨号账号密码
```

| 命令 | 作用 | 需要管理员 | 备注 |
| --- | --- | --- | --- |
| `--selftest` | 环境自检 | 否（部分检查项降级） | 第一次装完、出问题时先跑它；输出会写进日志 |
| `--scan` | 扫描本机现状 | 否 | 列出拨号连接、Wi-Fi 配置、检测到的代理客户端及其核心/端口 |
| （无参数） | 打开图形界面 | 否 | 与双击 `CampusNetAssistant.pyw` 等价 |
| `--boot` | 系统级守护循环 | 由计划任务以 SYSTEM 运行 | 一般**不需要手敲**；手工测试时也应在管理员下运行 |
| `--install-boot` | 注册计划任务 | **是** | 开机触发 + SYSTEM 身份，保证未登录/锁屏也在拨号 |
| `--uninstall-boot` | 删除计划任务 | **是** | 会先结束正在跑的守护进程 |
| `--set-account 账号 密码` | 保存拨号账号密码 | 否 | 密码用 DPAPI 加密后写入配置，**不存明文**（见下） |

> 命令行的参数名和可用性在版本之间可能微调；以 `python -m campusnet --help` 的实际输出为准。

## 配置文件

### 文件位置

| 用途 | 路径 | 加密方式 |
| --- | --- | --- |
| 用户配置 | `%LOCALAPPDATA%\CampusNetAssistant\config.json` | 密码字段 `password_enc` 用 **Windows DPAPI（当前用户范围）** 加密 |
| 实时规则 | `%LOCALAPPDATA%\CampusNetAssistant\rules.json` | 明文。界面上改开关/名单时写这里，守护进程**立即生效**，不需要管理员、不需要重启 |
| 用户日志 | `%LOCALAPPDATA%\CampusNetAssistant\运行日志.txt` | 明文文本 |
| 系统级配置 | `%ProgramData%\CampusNetAssistant\config.json` | 密码字段 `password_machine` 用 **机器范围 DPAPI** 加密，只有本机（含 SYSTEM）能解密 |
| 开机守护日志 | `%ProgramData%\CampusNetAssistant\开机拨号日志.txt` | 明文文本 |
| 守护心跳 | `%ProgramData%\CampusNetAssistant\heartbeat.txt` | 明文，守护进程每次检查后重写 |
| 安装日志 | `%ProgramData%\CampusNetAssistant\安装日志.txt` | 明文，`--install-boot` 的详细过程 |
| 运行期临时文件 | `%LOCALAPPDATA%\CampusNetAssistant\pause.until`、`show.request` 等 | 明文。`pause.until` 是"先别拨"的冷却标记（点"断开"时写入） |

为什么要有两份配置？因为 **SYSTEM 账户没有你的用户配置文件**：它去读 `%LOCALAPPDATA%` 会跑到系统自己的目录里，什么也读不到。所以系统级守护用 `%ProgramData%` 下的一份，密码用机器范围 DPAPI 加密，这样守护进程在**你还没登录**时也能拿到账号去拨号。

> 小提示：Windows 里把 `%LOCALAPPDATA%` 粘到资源管理器地址栏就能直接打开这个目录。
>
> 环境变量 `CNG_HOME` / `CNG_BOOT_DIR` 可以临时把这两个目录挪到别处（调试用）。

### 配置示例

```json
{
  "campus": {"mode": "wired", "connection": "宽带连接", "wifi_ssid": "", "account": "学号", "interval": 15},
  "guard": {"kill_proxies": true, "kill_processes": ["flclash.exe", "mojie-windows-amd64.exe"],
            "wifi_policy": "manual"},
  "flip": {"enabled": true, "order": ["eix", "mojie"], "apps": ["telegram.exe", "codex.exe", "chrome.exe"],
           "region_hints": ["美国", "United States", "🇺🇸", "Los Angeles", "San Jose"],
           "title_hints": ["chatgpt", "openai", "claude"]},
  "clients": [
    {"id": "eix", "name": "E-IX", "kind": "mihomo",
     "gui": "E:\\E-IX\\eix_client.exe",
     "core": "E:\\E-IX\\mihomo.exe",
     "core_dir": "%APPDATA%\\usfoo\\E-IX", "core_config": "profile.yaml",
     "controller": "127.0.0.1:9090",
     "kill": ["eix_client.exe", "mihomo.exe"]}
  ]
}
```

（JSON 里反斜杠要写两个 `\\`；`%APPDATA%` 这类环境变量可以直接写在路径里，程序会展开。）

### 字段说明

**`campus` —— 校园网接入**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `mode` | 字符串 | `wired` = 只走有线 PPPoE；`wireless` = 只连无线 SSID；`both` = 两者都要（先有线，失败再无线） |
| `connection` | 字符串 | 有线拨号连接名，就是"网络连接"里那个"宽带连接"的名字；拨号时用 `rasdial "名字" 账号 密码`。**留空会自动探测**（从拨号电话簿 `rasphone.pbk` 里找出所有 `DEVICE=PPPOE` 的条目） |
| `wifi_ssid` | 字符串 | 要连接的无线网络名（SSID）。该网络必须已经在本机保存过（连过一次） |
| `account` | 字符串 | 拨号账号（学号 / 工号，视学校而定） |
| `password_enc` | 字符串 | **不要手写**。用界面或 `--set-account` 保存时，程序用当前用户范围 DPAPI 加密后写进这里；你打开 `config.json` 只会看到一段 Base64 密文 |
| `interval` | 整数（秒） | 每隔多少秒检查一次连接状态，默认 15 |
| `probes` | 数组 | 自定义探测目标，形如 `[["202.117.112.13", 53], ["223.5.5.5", 443]]`（host + TCP 端口）。**留空 = 自动**：优先用这条 PPPoE 连接协商到的校园网 DNS，再补上公共 DNS 兜底 |

**`guard` —— 守护与"关代理"策略**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kill_proxies` | 布尔 | 是否启用"连上校园网就关闭代理/VPN" |
| `kill_processes` | 字符串数组 | 要关闭的**进程名**（不是路径），如 `flclash.exe`、`mojie-windows-amd64.exe`。写成路径无效；大小写不敏感 |
| `wifi_policy` | 字符串 | `off` = 不做任何修改（默认）；`manual` = 把本机所有已保存的 Wi-Fi 配置改成"手动连接"；`disable` = 直接禁用 WLAN 网卡（最彻底，但要用 Wi-Fi 时得自己去启用） |

**`flip` —— 翻墙模式**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enabled` | 布尔 | 是否启用翻墙模式（未连校园网时，检测到触发程序就自动开代理） |
| `order` | 字符串数组 | 客户端优先级顺序，元素是 `clients[].id`。列表里第一个能启动成功的就用它 |
| `apps` | 字符串数组 | 触发程序名单。**exe 类**填进程名（`telegram.exe`）；**浏览器类**（`chrome.exe`、`msedge.exe`、`firefox.exe`、`brave.exe`）靠窗口标题关键词判断 |
| `region_hints` | 字符串数组 | 目标地区节点的**匹配关键词**（大小写不敏感）。节点名里出现任意一个就算候选，如 `美国`、`United States`、`🇺🇸`、`Los Angeles`、`San Jose` |
| `title_hints` | 字符串数组 | 窗口标题关键词（小写）。浏览器窗口标题里出现这些词，才认为你真的在访问目标站点，如 `chatgpt`、`openai`、`claude` |
| `auto_close` | 布尔 | 触发程序**全部退出后**是否自动关掉代理客户端（默认 `false`，即让它继续跑） |

**`clients[]` —— 客户端适配器定义**（详见 [`docs/CLIENTS.md`](docs/CLIENTS.md)）

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 唯一标识，`flip.order` 引用它，如 `eix`、`mojie` |
| `name` | ✅ | 显示名，如 `E-IX`、`魔戒` |
| `kind` | ✅ | 客户端类型。`mihomo` 表示 mihomo/Clash 内核、带 HTTP 控制接口 |
| `gui` | 可选 | 客户端主程序（界面）完整路径。用于"通过界面启动"这条路 |
| `core` | 可选 | 内核 exe 完整路径。填了就优先"直接用内核 + 你自己的配置"启动（推荐，通常**不需要 UAC**） |
| `core_dir` | 可选 | 内核的**工作目录**，支持 `%APPDATA%` 等环境变量 |
| `core_config` | 可选 | 要加载的配置文件（相对 `core_dir`），如 `profile.yaml`、`config.yaml` |
| `controller` | 可选 | 控制接口地址，如 `127.0.0.1:9090`。**留空则自动发现**（扫内核进程监听的端口） |
| `cores` | 可选 | 该客户端的**内核进程名**列表，如 `["mihomo.exe"]`。用于判断"内核是不是已经在跑"和自动发现控制端口；不填则退回用 `kill` 里的进程名 |
| `kill` | 可选 | 该客户端自己的进程名列表，用于连上校园网后把它整个关干净 |
| `admin` | 可选 | 该客户端启动是否需要管理员/UAC。视客户端而定 |

**`ui` —— 界面选项**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `minimize_to_tray` | 布尔 | 点关闭按钮时是否隐藏到系统托盘（默认 `true`）。改成 `false` 则关闭 = 退出程序 |

**关于实时规则 `rules.json`**：开关类选项（关代理、名单、无线策略、翻墙模式）会同时写进 `rules.json`，守护进程读它并**立即生效**——所以你在界面上改一下，不需要重新安装计划任务或重启守护。

## 工作原理

### 一、校园网接入

- **有线**：调用系统自带的 `rasdial "连接名" 账号 密码` 完成 PPPoE 拨号。成功/失败用 `rasdial` 的**退出码**判定，错误码会被翻译成人话写进日志（见 FAQ）。如果没填连接名，会尝试自动探测本机的拨号连接。
- **无线**：调用 `netsh wlan connect name=<SSID>` 连接已保存的无线网络。前提是这个 SSID 在本机连过一次（有配置文件）。
- **两者都要**：先走有线，失败或超时后走无线；任一成功即视为校园网已接入。
- 连接完成后做一次**连通性探测**（见"探测方式"）确认真的通了，而不是"命令返回成功但网还是死的"。
- 以 SYSTEM 身份拨号时会**显式指定拨号电话簿**（`rasphone.pbk` 的完整路径）——因为那时 `%APPDATA%` 指向的是系统配置目录，不指定就找不到你的拨号条目。

**探测方式**：用 **TCP 连接**（三次握手成功即视为通），默认目标优先取这条 PPPoE 连接协商到的**校园网 DNS**（取其 DNS 服务器地址 + 53 端口），再追加公共兜底目标（`223.5.5.5:443`、`180.76.76.76:443`、`114.114.114.114:53`、`223.6.6.6:443`）。全部失败才算掉线，并在日志里写明**探测依据**。**不用 ping**，原因见 [FAQ Q2](#q2为什么用-tcp-探测而不是-ping)。

### 二、断线守护

- 主循环每隔 `campus.interval` 秒（默认 15 秒）跑一次状态检查：探测连通性 → 判断是否掉线 → 掉线就重连。
- 你点 **断开** 时，程序会写一个"先别拨"的标记（`pause.until`）**暂停自动拨号，直到你点「立即连接」**。
- **系统级守护**（`--boot`，由计划任务以 SYSTEM 调用）负责在**开机时、锁屏时、你还没登录时**保持链路。它每次检查后会写 `heartbeat.txt`；界面进程读到 **90 秒内**的新心跳就认为守护在跑，并把状态显示出来。界面与守护通过 `rules.json` 共享开关，不会互相抢拨号。
- 需要以 SYSTEM 运行的原因很直接：**PPPoE 拨号是机器级资源**，只在你的登录会话里拨号，一旦锁屏/切换用户/注销就会断。

#### 断开为什么不能只靠 `rasdial /disconnect`

这是实测踩出来的坑：**校园网这个 PPPoE 连接不属于任何可枚举的 RAS 会话**——
`rasdial`（不带参数）显示"没有连接"，`RasEnumConnections` 返回 **632**，
而 `rasdial /disconnect` 会**返回成功却什么都不做**（PPP 适配器仍挂着 IP）。
所以界面程序断不开它，连 SYSTEM 守护也断不开它。

程序现在用**两步断开**：

1. 先 `rasdial /disconnect`（对能正常断的情况照常断）；
2. 4 秒后如果链路仍在，就**禁用它所依附的有线网卡**（`netsh interface set interface ... admin=disable`）。
   PPPoE 骑在这块网卡上，禁用后**必定断开**；而**手机热点 / 无线网完全不受影响**。

配套的两个细节：

- **记住禁用了哪块网卡**（`disabled_adapter.txt`）：网卡一禁用，"自动挑选有线网卡"可能挑到另一块
  （例如同样处于禁用状态的"本地连接"），恢复时就会启用错的那块。
- **开机时清掉上次的断开状态**：用系统运行时长（`GetTickCount64`）区分"刚开机"与
  "计划任务每 5 分钟的重启"。于是**开机 / 锁屏界面照样连校园网**，
  只有你主动点「断开」才保持断开，点「立即连接」就恢复（自动重新启用网卡并拨号）。

#### 界面与守护之间的信号文件

| 文件 | 作用 |
| --- | --- |
| `pause.until` | 暂停自动拨号（点「断开」时写；点「立即连接」或开机时清） |
| `disconnect.request` | 请求守护执行断开（界面没权限断 SYSTEM 建立的拨号） |
| `disabled_adapter.txt` | 记录"为断开而禁用了哪块网卡"，供恢复时精确启用 |
| `flip.active` | 翻墙模式正在用代理（带时间戳）→ 守护**不关**这个代理；时间戳过期自动失效，进程被强杀也不会留下永久豁免 |

### 三、关闭代理 / VPN

- 触发时机有两个：**拨号前先关一遍**（防止代理抢在认证之前改写网络栈），**连上校园网之后再检查一遍**（防止客户端在你拨号期间自己启动了）。
- 关闭过程是"先礼后兵"，顺序固定：
  1. 先**礼貌关闭**（`taskkill /IM <进程名> /T`，**不带** `/F`）——很多客户端收到正常退出请求时会顺手把系统代理还原；
  2. 等 3 秒；
  3. 还活着的（包括把它自己拉起来的 service/helper 进程）再**强制结束**（`taskkill /F /T`，失败则按 PID 直接终止）；
  4. 最后**清理残留的系统代理设置**（把所有用户会话里的 `ProxyEnable` 关掉），否则会出现"代理进程没了，浏览器却还连着已经不存在的 127.0.0.1:7890"。
- 名单来自 `guard.kill_processes`，界面里有对应的名单管理。只关你明确列出的进程——像 Watt Toolkit（Steam++）、普通游戏加速器这类**不冲突**的工具，不要写进名单。

### 四、无线策略

- 打开"无线改成手动连接"后，程序会枚举本机所有已保存的 Wi-Fi 配置，逐个把连接模式设为**手动**（等价于 `netsh wlan set profileparameter name=<名字> connectionmode=manual`）。
- 效果：Windows 不再自动往任意一个保存过的 SSID 上跳，只有本程序（或你手动）发起连接才连。
- 代价：**别的网络也不会自动连了**，包括你家里的 Wi-Fi。不想要这个行为就把开关关掉。

### 五、翻墙模式：节点筛选

前提条件：**当前没有连上校园网**（连着校园网时不该抢隧道），并且 `flip.enabled` 为真。

1. **等触发**。程序监视进程列表和窗口标题：
   - 名单里是普通 exe 的（`telegram.exe`、`codex.exe`…）→ 看进程是否启动，命中即触发；
   - 名单里含**浏览器**（`chrome.exe`、`msedge.exe`、`firefox.exe`、`brave.exe`）→ 不看浏览器进程本身（它一直开着），改看**窗口标题**里有没有 `title_hints` 的关键词。所以你可以只在打开 ChatGPT 时才让代理上线，平时浏览校园网不受影响。
2. **启动客户端**。按 `flip.order` 顺序尝试：客户端没装（路径不对）就跳过；内核进程已经在跑就直接复用。需要启动时，**优先**用"内核 + 你自己的配置"直接拉起（执行 `core -d <core_dir> -f <core_dir>\<core_config>`，无窗口、免 UAC）；内核路径没配或起不来才退回启动界面；界面启动若报"需要提升权限"（Windows 错误 740），会自动改用 `runas` 提权（**会弹一次 UAC**）。第一个成功起来的胜出。
3. **找到控制接口**。先试配置里的 `clients[].controller`，再用该客户端内核进程**实际监听的端口**补齐候选，逐个访问 `GET http://127.0.0.1:<端口>/version`——返回含 `version`/`meta` 的 JSON 才算数。接口可能不是立刻就有，程序会**最多等约 20 秒**（10 轮 × 2 秒）；一直不出现就换下一个客户端，并在日志里写明"它可能需要在界面里手动连接"。
4. **筛节点**。拉取 `/proxies`，只在**类型为 Selector 且有节点列表**的策略组里挑：优先 AI 相关分组（`AI`、`ChatGPT`、`OpenAI`、`Copilot`、`Gemini`、`Claude`）和常见主分组（`节点选择`、`选择`、`Proxy`、`代理`、`GLOBAL` 等），再按 `region_hints`（如"美国 / United States / 🇺🇸 / Los Angeles / San Jose"）筛节点名。
5. **真实测延迟**。对候选节点**逐个**调用延迟测试接口（`/proxies/<节点名>/delay?timeout=3000&url=http://www.gstatic.com/generate_204`，节点名做 URL 编码），**只有真返回正延迟才算可用**；一次最多测 8 个候选，日志里逐个打印"测速 xxx → 123 ms / 不通"。不看配置文件里的静态延迟——那个数字经常是假的。
6. **切换**。选**延迟最低的那个可用节点**，并把它应用到**所有包含该节点的相关分组**（AI 类分组 + 主分组都切过去），这样 ChatGPT 那类走独立分组的流量也跟着走同一个节点。
7. **设置系统代理**。读 `/configs` 里的 `mixed-port`，写入 Windows 系统代理（当前用户 `Internet Settings` 的 `ProxyServer` = `127.0.0.1:<mixed-port>`、`ProxyEnable` = 1，并设置 `ProxyOverride` 跳过 localhost）。**如果客户端没开混合端口（读到的值为 0），系统代理不会被修改**，此时只有本身就支持代理的程序能走。翻墙模式退出时会还原系统代理。
8. **收尾**。翻墙条件不再成立（触发程序退出、或校园网重新连上）时，按策略关闭客户端并还原系统代理；`flip.auto_close` 控制"触发程序都退出了要不要顺手把客户端也关了"。

> 程序访问控制接口时会**主动绕开系统代理**（用空的 ProxyHandler）——否则请求会绕进它自己刚设好的代理里，形成死循环。

> **探测方式**：连通性探测用 **TCP 连接**（例如向校园网 DNS 的 53 端口、公共 DNS 的 443 端口发起连接），**不用 ping**。原因见 FAQ。

## 如何添加自己的翻墙客户端

内置的客户端定义不一定覆盖你用的那款。**完全不需要改代码** —— 在 `config.json` 的 `clients` 数组里加一项，然后在 `flip.order` 里写上它的 `id` 即可。

最短可用示例：

```json
{
  "id": "myclient",
  "name": "我的客户端",
  "kind": "mihomo",
  "gui": "D:\\MyClient\\MyClient.exe",
  "core": "D:\\MyClient\\resources\\static\\clash\\mycore.exe",
  "core_dir": "%APPDATA%\\MyClient",
  "core_config": "config.yaml",
  "controller": "127.0.0.1:9090",
  "kill": ["myclient.exe", "mycore.exe"],
  "admin": false
}
```

**逐字段解释、三种接入方式、怎么找控制端口、以及 E-IX / 魔戒两个真实完整示例，见 [`docs/CLIENTS.md`](docs/CLIENTS.md)。**

一句话版本：

- 想在任务栏里看到客户端界面 → 填 `gui`；
- 想安静地在后台跑、不弹 UAC → 填 `core` + `core_dir` + `core_config`（**推荐**）；
- 不知道端口 → 留空 `controller`，程序会自己去内核监听的端口里找。

## 常见问题（FAQ）

### Q1：`rasdial` 报错码是什么意思？

| 错误码 | 含义 | 怎么办 |
| --- | --- | --- |
| 0 | 成功 | —— |
| 5 | 访问被拒绝：需要管理员权限 | 用管理员身份运行 |
| 623 | 找不到这个拨号连接（名字不对） | 用 `--scan` 看真实连接名，或先手动建一个"宽带连接" |
| 629 | 连接被远程计算机终止 | 稍后重试；反复出现问网络中心 |
| 651 | 网卡报错：多半是网线没插好或网卡驱动异常 | 检查网线/墙上端口；更新网卡驱动 |
| 676 | 线路占线 | 稍后重试 |
| 678 | 拨号无应答：PPPoE 服务器没反应 | 检查网线、墙上端口、楼道交换机；确认端口没被禁用 |
| 691 | 账号或密码错误，或者账号被限制（欠费 / 同时在线设备数超了） | 核对账号密码；退出其它设备上的登录；确认没欠费 |
| 692 | 硬件故障 | 换网线/端口，检查网卡 |
| 708 | 账号已过期或被停用 | 联系网络中心 |
| 720 | 无法协商 PPP 参数 | 系统的拨号组件有问题，删除并**重建"宽带连接"** |
| 756 | 已经有一个拨号连接在进行中 | 等它结束，或先"断开"再重连 |
| 769 | 找不到网卡：网卡可能被禁用了 | 在"网络连接"里启用网卡 |
| 797 | 找不到 PPPoE 网卡 | 检查"WAN Miniport (PPPOE)"设备是否正常 |
| 815 | 宽带连接失败 | 检查网线 / 光猫 / 楼道交换机 |

### Q2：为什么用 TCP 探测，而不是 ping？

很多校园网**封禁 ICMP**（ping 的回包），所以你 `ping` 不通不代表网没通——用 ping 判断会导致程序疯狂重拨，把账号打进"频繁认证"限流里。所以本工具用 **TCP 连接**（三次握手成功即视为通）来判定，并且在日志里写明**探测依据**，让你能看清结论是从哪来的。

### Q3：为什么有些操作需要管理员？

因为这些是机器级操作：PPPoE 拨号与网络适配器状态、`netsh wlan` 修改无线配置、注册/删除以 SYSTEM 身份运行的计划任务、结束由高权限启动的代理/VPN 进程。日常"看状态、改设置"不需要管理员。

### Q4：为什么密码要用 DPAPI 加密？

因为 `config.json` 是纯文本，直接写明文等于把学号密码摊在硬盘上——任何能读这个文件的程序（包括备份软件、同步网盘、误发的压缩包）都能拿到。DPAPI 是 Windows 自带的加密机制：

- 加密后**只有对应的账户（或本机 SYSTEM）能解密**，别的机器、别的账户拿到文件也解不开；
- 密钥由 Windows 管理，不需要你再记一个主密码；
- 密文是**不可逆**的，程序本身也没法把明文"反推"出来给别人看。

代价：换电脑或重装系统后，密码解不开，需要重新用 `--set-account` 或界面再存一次。

### Q5：为什么关上代理客户端了，它还会自己回来？

两种情况：① 客户端有**服务/守护进程**（名字通常带 `service`、`helper`、`core`），你只关了界面，服务把界面又拉起来了——把服务进程名也加进关闭名单；② 客户端被注册成了**开机启动项或计划任务**，请在客户端自己的设置里关掉"开机自启"。

### Q6：翻墙模式为什么没反应？

按顺序排查：

1. 现在是不是**还连着校园网**？连着时翻墙模式不接管（这是设计）。
2. 触发程序是不是在名单里？浏览器要看**窗口标题**，标题里要有 `title_hints` 的关键词。
3. 客户端能不能起来？到 `--scan` 的输出里看有没有检测到你的客户端。
4. 控制接口通不通？浏览器打开 `http://127.0.0.1:<端口>/version`，能返回 JSON 才说明接口可用。
5. 有没有匹配到节点？节点名里必须含 `region_hints` 里的关键词（比如机场把节点叫"HK-01"就不会被当成美国节点）。

### Q7：无线改成手动之后，我家里的 Wi-Fi 也不自动连了？

是的，这是全局设置的效果。不需要就把开关关掉，程序不会恢复"自动"，需要你在 Windows 设置里把想要的网络改回"自动连接"。

### Q8：托盘图标不见了 / 点了没反应？

Windows 资源管理器重启后，托盘图标可能消失。重新打开一次程序即可。另外检查系统托盘设置里有没有把这个图标折叠隐藏了。

### Q9：安全软件报警怎么办？

本程序会做"结束进程""注册计划任务"这类敏感动作，纯脚本打包/未签名的工具被启发式规则误报是常见现象。你可以：核对源码（全部是标准库、无混淆、无联网上传）、把项目目录加入白名单，或从源码直接运行而不打包。**请只从本仓库或你信任的来源获取本工具。**

### Q10：界面没反应 / 卡住了？

拨号、无线、延迟测试这类操作可能耗时数秒到数十秒。如果长时间无响应，先看日志（**查看日志**按钮），再跑 `python -m campusnet --selftest` 定位。

## 安全与隐私

- **不上传任何数据。** 程序没有账号体系、不含统计 SDK、不向任何服务器发送你的信息。
- **网络访问范围很小：** ① 你自己配置的连通性探测目标（默认是校园网 DNS 和公共 DNS 的 TCP 端口）；② 本机 `127.0.0.1` 上代理客户端的控制接口。除此之外不主动外连。
- **密码本地加密，不存明文。** 用户配置用当前用户范围 DPAPI，系统级配置用机器范围 DPAPI。
- **只读你自己的配置。** 不改动客户端自身的配置文件内容；`clients[].core_config` 只是告诉它"加载哪个文件"。
- **日志会记录诊断信息**（连接名、SSID、时间和错误码），**密码会被脱敏**。日志在你自己的目录下，随你处置。
- **可完整审计**：纯 Python 标准库，无第三方依赖、无二进制内核，读一遍源码即可确认上面的每一条。
- **卸载干净**：`--uninstall-boot` 删除计划任务；配置、日志、密钥材料都在上述固定目录里，删掉那几个目录即彻底清除。

## 已知限制

- **无线门户认证（Web Portal）不代做。** 很多学校的 Wi-Fi 连上之后要弹网页输账号。本工具只负责"连上这个 SSID"，门户登录需要你自己提供登录方式（先手动登录一次，或用你学校提供的客户端）。不同学校的门户实现千差万别，无法通用。
- **不同学校的 PPPoE 参数不同。** 连接名、是否需要服务名、是否绑定 MAC、是否限制多设备在线，各校不同。本工具用的是 Windows 标准拨号，特殊的私有客户端协议不支持。
- **客户端控制接口因版本而异。** mihomo/Clash 系的 API 大体一致（`/version`、`/proxies`、`/proxies/<名字>/delay`），但**具体客户端可能改名、改端口、关掉接口或加认证**。遇到不支持的情况，请在配置里补上正确的 `controller`，或按 `docs/CLIENTS.md` 提 PR。
- **不保证所有客户端都能"免 UAC"启动。** 有的客户端内核必须以管理员身份运行（TUN 模式），此时 `admin: true` 且系统会弹 UAC；能否绕过取决于客户端本身，**视客户端而定**。
- **改无线为手动是全局操作**，会影响所有已保存的 Wi-Fi，见 [Q7](#q7无线改成手动之后我家里的-wi-fi-也不自动连了)。
- **系统级守护需要管理员安装**，且在没有安装计划任务时，只有你登录后程序才工作。
- **仅支持 Windows。** 依赖 `rasdial`、`netsh wlan`、DPAPI、计划任务、消息托盘等 Windows 专有组件，不打算移植到 macOS/Linux。
- **节点测延迟是"当下的"。** 网络质量随时变化，程序选的是**测的那一刻**最快的可用节点，不保证一直最快。

## 项目结构

```text
CampusNetAssistant/
├─ CampusNetAssistant.pyw     双击启动（无控制台窗口）
├─ campusnet/                主程序包（python -m campusnet）
│  ├─ __main__.py            命令行入口：--selftest / --scan / --boot / --install-boot ...
│  ├─ config.py              配置、默认值、DPAPI 加密、系统级配置构建
│  ├─ net.py                 网络与校园网接入（PPPoE / DHCP / 静态IP / 门户 / 802.1X）
│  ├─ clients.py             翻墙客户端适配器（mihomo/Clash 控制接口）
│  ├─ rules.py               关代理/VPN、翻墙触发判断
│  ├─ guard.py               系统级守护（SYSTEM 常驻）+ 自检/扫描
│  ├─ installer.py           计划任务安装/卸载
│  ├─ gui.py                 图形界面（tkinter）
│  └─ util.py                进程/DPAPI/netstat/托盘图标等系统工具
├─ tests/
│  └─ test_core.py           29 个单元测试（不联网、不改系统设置）
├─ .github/workflows/ci.yml  CI：语法检查 + 单元测试（Windows / Python 3.9 & 3.12）
├─ scripts/
│  ├─ install-boot.cmd       安装系统级守护（自动提权）
│  ├─ uninstall-boot.cmd     卸载
│  ├─ feature_check.py       全功能检查（88 项，真跑不模拟，见下）
│  ├─ check_names.py         静态自查：用了但没定义/没导入的名字（CI 里跑）
│  └─ publish_github.py      半自动发布脚本（见 PUBLISH.md，支持 --release 发版）
├─ docs/
│  ├─ 使用说明.md            完整使用说明（大白话版，每个功能都讲到）
│  ├─ WIRED.md               有线接入方式详解（重点看这个）
│  ├─ CLIENTS.md             如何添加翻墙客户端
│  └─ images/                界面截图（占位）
├─ README.md
├─ CHANGELOG.md
├─ PUBLISH.md
├─ LICENSE
└─ .gitignore
```

### 开发与测试

```bat
python -m unittest discover -s tests -v      :: 跑单元测试
python -m compileall -q campusnet            :: 语法检查
```

## 贡献指南

欢迎 PR，尤其是下面这几类"别人替不了你"的补充：

- **新的客户端适配器**：把你正在用的 mihomo/Clash 系客户端定义整理成一条 `clients[]` 配置（或直接改内置清单），并附上**你是怎么找到控制端口的**。请参考 [`docs/CLIENTS.md`](docs/CLIENTS.md)。
- **校园网适配经验**：你学校的接入方式（PPPoE 参数、门户认证流程、是否封 ICMP、多设备限制），写进文档或作为兼容性代码。有线门户认证的厂商字段参考见 [`docs/WIRED.md`](docs/WIRED.md)。
- **错误码补充**：`rasdial` / `netsh wlan` 的其它报错及人话解释。
- **测试**：新加的纯逻辑函数请顺手补一个 `tests/test_core.py` 里的用例。
- **文档改进**：错别字、说反了的地方、你踩过的坑。

提 PR 前请确认：

1. **不引入第三方依赖。** 本项目坚持"只用 Python 标准库 + Windows 自带组件"，这是它能在任何一台校园机器上跑起来的原因。
2. **不提交隐私文件。** `config.json`、`*.log`、`credential*.xml` 等已在 `.gitignore` 里，别用 `git add -f` 塞进来。也请检查你的截图和日志里有没有学号、密码、SSID、节点链接。
3. **敏感操作要写日志。** 会改系统状态的步骤（拨号、杀进程、改无线、注册任务、改系统代理）都要留下可诊断的记录。
4. **说明测试环境**：Windows 版本、Python 版本、学校、客户端名称+版本。

发现 bug 请提 issue，并附上 `--selftest` 和 `--scan` 的输出（**提交前先把学号、密码、节点信息打码**）。

## License

MIT License —— 见 [LICENSE](LICENSE)。Copyright (c) 2026 CampusNetAssistant contributors.
