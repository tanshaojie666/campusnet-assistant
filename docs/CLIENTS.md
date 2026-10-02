# 添加你自己的翻墙客户端 · docs/CLIENTS.md

本文档面向想把自己正在用的代理/VPN 客户端接进 CampusNetAssistant 的人。**只要你用的客户端是 mihomo/Clash 系，通常不用改一行代码** —— 在配置文件的 `clients` 数组里加一条定义，再把它加进 `flip.order` 就行。

- 只想让它能跑起来 → 看 [§1 客户端需要满足什么](#1-客户端需要满足什么) 和 [§3 三种接入方式](#3-三种接入方式)
- 想搞懂每个字段 → 看 [§2 字段逐项解释](#2-字段逐项解释)
- 卡在"端口是多少" → 看 [§4 如何找到控制端口](#4-如何找到控制端口)
- 想照抄 → 看 [§5 两个真实示例（E-IX 与魔戒）](#5-两个真实示例e-ix-与魔戒)
- 想知道内核 exe 一般叫什么 → 看 [§6 常见客户端内核名参考](#6-常见客户端内核名参考)

---

## 1. 客户端需要满足什么

### 硬性要求（缺一不可）

| 要求 | 为什么需要 | 怎么判断 |
| --- | --- | --- |
| **mihomo / Clash 系内核** | 本工具靠 Clash 系的 HTTP 控制接口（`/version`、`/proxies`、`/proxies/<名字>/delay`）来筛节点、测延迟、切节点 | 客户端安装目录里能找到 `mihomo.exe` / `clash*.exe` / `<品牌>Core.exe`；或者在任务管理器里能看到这类内核进程 |
| **有可访问的控制接口** | 同上。没有控制接口就没法自动测延迟、自动选节点 | 配置里能读到 `external-controller: 127.0.0.1:xxxx`，或程序能自动发现内核监听的端口 |
| **能提供系统级代理** | 翻墙模式最后要把"系统代理"打开，让浏览器、Telegram 这类程序也能走；只开 TUN 而没有混合端口的话，本工具至少要能识别出可用的代理端口 | 客户端设置里有"混合端口 / Mixed Port / HTTP Port"，或者有"系统代理 / TUN 模式"开关 |
| **账号和节点已经配好** | 本工具**不代管订阅、不代填机场账号**，它只负责"开客户端 + 选节点 + 开系统代理"，节点和规则仍然来自客户端自己的配置 | 你先手动打开客户端，能正常上外网，就说明这一条满足 |

### 加分项

- **支持"内核 + 配置文件"直接启动**：不弹界面、不开 UAC，适合后台静默运行（见 [§3.2](#32-core直接用自带内核自己的配置推荐)）。
- **进程名稳定且可列举**：方便"连上校园网就把它关干净"（`kill` 字段）。
- **有独立的 helper/service 进程**：这类进程会互相拉起，`kill` 里要一并写上。

### 不支持的

- 自有私有协议、没有本地控制接口的客户端（例如很多"一键加速器"）。这类只能走 `gui` 启动这条路，**无法自动测延迟和选节点**——这种情况下本工具最多只能帮你"把客户端打开 + 打开系统代理"，选节点得你自己在客户端里点。这属于**视客户端而定**，不是 bug。
- 手机上那种订阅式小工具、以及 macOS/Linux 客户端（本项目只支持 Windows）。

---

## 2. 字段逐项解释

每个客户端是 `config.json` 里 `clients` 数组的一个对象：

```json
{
  "id": "eix",
  "name": "E-IX",
  "kind": "mihomo",
  "gui": "E:\\E-IX\\eix_client.exe",
  "core": "E:\\E-IX\\mihomo.exe",
  "core_dir": "%APPDATA%\\usfoo\\E-IX",
  "core_config": "profile.yaml",
  "controller": "127.0.0.1:9090",
  "kill": ["eix_client.exe", "mihomo.exe"],
  "admin": false
}
```

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `id` | 字符串 | ✅ | 唯一标识，只用于程序内部和 `flip.order` 引用。**建议全小写英文、无空格**，如 `eix`、`mojie`、`flclash`。不要包含中文或斜杠 |
| `name` | 字符串 | ✅ | 界面上显示的名字，随便写中文，如 `E-IX`、`魔戒`、`FlClash` |
| `kind` | 字符串 | ✅ | 客户端类型。目前用 `mihomo` 表示 **mihomo/Clash 系**（有 HTTP 控制接口）。以后若支持别的类型会新增取值；不认识的值会被忽略 |
| `gui` | 字符串（路径） | 可选 | 客户端**主程序/界面**的完整路径。走"启动界面"这条路时用它。用双反斜杠 `\\` 或正斜杠 `/` |
| `core` | 字符串（路径） | 可选 | **内核 exe**的完整路径。填了它，程序就优先"直接用内核 + 你自己的配置"启动（[§3.2](#32-core直接用自带内核自己的配置推荐)，推荐）。留空则只能走 `gui` |
| `core_dir` | 字符串（路径） | 可选（用 `core` 时必需） | 内核的**工作目录**。mihomo 按"工作目录"解析相对路径，所以这里要指向**配置所在的那个目录**。支持 `%APPDATA%`、`%LOCALAPPDATA%`、`%USERPROFILE%` 等环境变量 |
| `core_config` | 字符串（文件名/相对路径） | 可选（用 `core` 时必需） | 要加载的配置文件，相对 `core_dir`。常见是 `config.yaml`、`profile.yaml`、`config.yml` |
| `controller` | 字符串 `主机:端口` | 可选 | 控制接口地址，如 `127.0.0.1:9090`。**留空 = 自动发现**：程序会扫内核进程实际监听的端口，用 `/version` 验证。填错会导致"客户端起来了但选不了节点" |
| `kill` | 字符串数组 | 可选 | 这个客户端**自己的进程名**列表（不是路径），用于"连上校园网就关掉它"。记得把 `service`、`helper`、内核进程都写进来 |
| `admin` | 布尔 | 可选（默认 `false`） | 该客户端**是否需要管理员权限**启动。`true` 时会走提权路径（程序中转，可能弹一次 UAC）。**视客户端而定**：TUN 模式的内核往往需要，纯用户态代理通常不需要 |

### 关于路径的写法

- JSON 里反斜杠是转义符，**必须写两个**：`"E:\\E-IX\\mihomo.exe"`。
- 也可以写成 `"E:/E-IX/mihomo.exe"`（正斜杠在 Windows 的 JSON 里是合法的）。
- 尽量用环境变量代替用户名：`"%APPDATA%\\usfoo\\E-IX"` 比 `"C:\\Users\\张三\\AppData\\Roaming\\usfoo\\E-IX"` 更好，换电脑/改用户名不用改配置。
- 路径**不要以反斜杠结尾**。

### 关于 `kill` 与"关闭名单"的区别

- `clients[].kill` 是**这个客户端专属**的进程名，翻墙模式需要"关掉它"时会用。
- `guard.kill_processes` 是**连上校园网时要关闭的全部代理/VPN 进程**，与客户端定义无关。**建议把每个客户端的 `kill` 内容也同步加进 `guard.kill_processes`**，否则校园网认证时它可能还在抢网络。
- 两者都只写**进程名**（`mihomo.exe`），写完整路径无效。

---

## 3. 三种接入方式

程序启动一个客户端时，按下面的优先级尝试；哪条能成就走哪条。

### 3.1 `gui`：启动客户端界面

- **做法**：直接运行 `gui` 指向的 exe，等它把内核拉起来。
- **优点**：最通用，什么都不用研究；用户能看到界面，方便手动处理订阅过期之类的弹窗。
- **缺点**：会占一个窗口；部分客户端启动会弹 **UAC**；界面加载期间节点还没就绪，程序需要等一段时间再去找控制接口。
- **适用**：不支持"内核 + 配置"直启的客户端；或者你想看着它跑。

```json
{"id": "flclash", "name": "FlClash", "kind": "mihomo",
 "gui": "E:\\FlClash\\FlClash.exe",
 "kill": ["flclash.exe", "flclashcore.exe", "flclashhelperservice.exe"],
 "admin": false}
```

### 3.2 `core`：直接用自带内核 + 自己的配置（推荐）

- **做法**：用 `core`（内核 exe）+ `core_dir`（工作目录）+ `core_config`（配置文件）**直接启动内核进程**，不启动界面。
- **优点**：**通常不需要 UAC**（内核以当前用户身份跑）；启动快、无窗口；用的**还是客户端自己的节点、订阅、规则**，只是换了个启动方式。
- **缺点**：得先找到内核 exe 和配置文件在哪；界面上看不到状态（要看日志）；**部分客户端会检测"内核被外部启动"并把它杀掉**，此时只能退回 `gui`——视客户端而定。
- **要点**：
  1. `core_dir` 必须是**配置文件所在目录**（mihomo 的相对路径都从这里算）；
  2. 配置里需要有一个可用的 `external-controller`（没有的话本工具就没法测延迟/切节点）；
  3. 有些客户端的配置文件散在子目录（如 `resources\`、`profiles\`），要写清相对路径；
  4. 内核 exe 有可能**不在**客户端主目录，而在 `resources\static\clash\` 之类的子目录里。

```json
{"id": "eix", "name": "E-IX", "kind": "mihomo",
 "core": "E:\\E-IX\\mihomo.exe",
 "core_dir": "%APPDATA%\\usfoo\\E-IX",
 "core_config": "profile.yaml",
 "controller": "127.0.0.1:9090",
 "kill": ["eix_client.exe", "mihomo.exe"]}
```

### 3.3 自动发现控制端口

- **做法**：`controller` **留空**时，程序会：
  1. 找到该客户端内核进程的 **PID**；
  2. 从 `netstat -ano` 的输出里挑出这些 PID **正在 LISTEN** 的本地端口；
  3. 对每个端口访问 `http://127.0.0.1:<端口>/version`，**返回 mihomo/Clash 版本 JSON 的那个**就是控制接口。
- **优点**：不用去翻配置；端口随机的客户端（每次启动换端口）也能用。
- **缺点**：慢一点；如果内核同时监听多个 HTTP 端口（例如同时开了一个 web 面板），可能误判；如果客户端设置了 `secret`（访问密码），请求会返回 401 —— 这种情况下自动发现会失败。
- **建议**：知道自己端口就**直接写死** `controller`（快且稳定），不知道就留空让程序找。

```json
{"id": "mojie", "name": "魔戒", "kind": "mihomo",
 "gui": "E:\\mojie\\魔戒.exe",
 "core": "E:\\mojie\\resources\\static\\clash\\mojie-windows-amd64.exe",
 "core_dir": "%APPDATA%\\mojie", "core_config": "config.yaml",
 "kill": ["魔戒.exe", "mojie-service.exe", "mojie-windows-amd64.exe"],
 "admin": true}
```

---

## 4. 如何找到控制端口

有三种办法，从最简单的开始试。

### 办法一：翻客户端自己的配置文件（最快）

在客户端的配置目录里找 `config.yaml` / `profile.yaml` / `*.yml`，搜 `external-controller`：

```yaml
# mihomo / Clash 配置片段
mixed-port: 7890
external-controller: 127.0.0.1:9090
# secret: "xxxxx"        # 如果这一行存在，控制接口需要密码
external-ui: ui
```

那么 `controller` 就填 `127.0.0.1:9090`。

**常见配置文件位置**（不同客户端不一样，用 `--scan` 或资源管理器搜）：

| 位置 | 说明 |
| --- | --- |
| `%APPDATA%\<品牌>` | 最常见的用户配置目录，如 `%APPDATA%\usfoo\E-IX`、`%APPDATA%\mojie` |
| `%LOCALAPPDATA%\<品牌>` | 有些客户端放这里 |
| 客户端安装目录下的 `resources\`、`profiles\`、`data\` | 便携版常见 |
| `%USERPROFILE%\.config\mihomo` | mihomo 原版的默认位置 |

### 办法二：用 netstat 反查内核监听的端口

1. 打开客户端，确认它在正常代理。
2. 找到内核进程的 PID：任务管理器 → **详细信息** 标签 → 找到 `mihomo.exe` / `xxxCore.exe`，记下 **PID**。
   （命令行也可以：`tasklist | findstr /i mihomo`）
3. 列出该 PID 正在监听的端口：

   ```bat
   netstat -ano | findstr LISTENING | findstr <PID>
   ```

   典型输出：

   ```text
   TCP    127.0.0.1:9090    0.0.0.0:0    LISTENING    12345
   TCP    127.0.0.1:7890    0.0.0.0:0    LISTENING    12345
   ```

4. 其中一个是**控制接口**（HTTP API），另一个通常是**混合/HTTP 代理端口**。哪个是哪个，用下一步验证。

### 办法三：验证（必做）

浏览器直接访问：

```text
http://127.0.0.1:9090/version
```

- 返回类似 `{"version":"1.18.x","meta":true,"premium":true}` 的 JSON → **这就是控制接口**，填进 `controller`。
- 返回 `401 Unauthorized` → 端口对了，但这个客户端设了 `secret`。当前版本**视实现而定**是否支持带密码访问；不行的话请在客户端里关掉 secret / 换个客户端，或在 issue 里说明。
- 返回 404 / 乱码 / 连接被重置 → 这个端口是代理端口或别的服务，换下一个端口试。

**顺手验证另外两个接口**（确认节点能被筛出来、延迟能测）：

```text
http://127.0.0.1:9090/proxies          节点列表与策略组（看节点名长什么样）
http://127.0.0.1:9090/proxies/节点名/delay?timeout=5000&url=http%3A%2F%2Fwww.gstatic.com%2Fgenerate_204
```

> 第二个接口会**真的发起一次连接**去测延迟，所以它返回的数值 == "这个节点现在能不能用"。
> 如果节点名里有中文/emoji/空格，URL 里要做 **百分号编码**，程序会自己处理。

### 用程序帮你找

```bat
python -m campusnet --scan
```

它会列出本机检测到的代理客户端、核心进程名、监听的端口，以及 `/version` 的探测结果，是排查"到底端口是多少"最省事的办法。

---

## 5. 两个真实示例（E-IX 与魔戒）

下面两个例子覆盖了两种典型形态：**配置在 AppData 的独立目录**（E-IX）和 **内核藏在安装目录深处的 resources 里**（魔戒）。

### 5.1 E-IX（`core` 直启 + 固定端口）

**背景**：E-IX 的界面是 `eix_client.exe`，但它的内核就是一个标准的 `mihomo.exe`，放在安装目录根下；节点配置则在用户的 AppData 里。它的 `profile.yaml` **自带 `external-controller: 127.0.0.1:9090`**，所以我们不需要界面，直接用它的内核 + 它自己的配置启动，既免 UAC 又快。

**探测过程**：

1. 在安装目录看到 `E:\E-IX\` 下有 `eix_client.exe`（界面）和 `mihomo.exe`（内核） → `gui` 和 `core` 都有了。
2. 打开客户端一次，确认能正常代理。到 `%APPDATA%` 下找到 `usfoo\E-IX\`，里面有 `profile.yaml` → 这就是 `core_dir` 和 `core_config`。
3. 在 `profile.yaml` 里搜到 `external-controller: 127.0.0.1:9090` → `controller` 填 `127.0.0.1:9090`。
4. 浏览器访问 `http://127.0.0.1:9090/version` 返回 mihomo 版本 JSON → 验证通过。
5. 任务管理器里看到两个相关进程：`eix_client.exe`、`mihomo.exe` → `kill` 写这两个。

**配置**：

```json
{
  "id": "eix",
  "name": "E-IX",
  "kind": "mihomo",
  "gui": "E:\\E-IX\\eix_client.exe",
  "core": "E:\\E-IX\\mihomo.exe",
  "core_dir": "%APPDATA%\\usfoo\\E-IX",
  "core_config": "profile.yaml",
  "controller": "127.0.0.1:9090",
  "kill": ["eix_client.exe", "mihomo.exe"],
  "admin": false
}
```

**为什么 `admin: false`**：内核以当前用户身份就能跑，只有在需要用 TUN 模式（虚拟网卡）时才可能需要管理员 —— **视客户端配置而定**。如果你发现直启后 TUN 起不来、日志报权限错误，再改成 `true`。

### 5.2 魔戒（内核在 `resources\static\clash\`，端口 18606）

**背景**：魔戒的界面叫 `魔戒.exe`，但它的内核**不在安装目录根下**，而在 `E:\mojie\resources\static\clash\mojie-windows-amd64.exe`；同时它还有一个 `mojie-service.exe` 服务进程，会把内核和界面拉起来。用户配置在 `%APPDATA%\mojie\config.yaml`，控制端口是 **18606**（不是常见的 9090）。

**探测过程**：

1. 在 `E:\mojie\` 下看到：`魔戒.exe`（界面）、`mojie-service.exe`（服务）、`resources\static\clash\mojie-windows-amd64.exe`（内核）。
2. 打开客户端，确认能代理。任务管理器里 `mojie-windows-amd64.exe` 的 PID 记下来。
3. `netstat -ano | findstr LISTENING | findstr <PID>` → 看到 `127.0.0.1:18606` 等端口。
4. 浏览器访问 `http://127.0.0.1:18606/version` → 返回 mihomo/Clash 版本 JSON，确认为控制接口。
5. 配置文件在 `%APPDATA%\mojie\config.yaml`，里面能看到 `external-controller: 127.0.0.1:18606`。
6. 三个进程名都写进 `kill`，因为**只关界面的话，`mojie-service.exe` 会把内核重新拉起来**。

**配置**：

```json
{
  "id": "mojie",
  "name": "魔戒",
  "kind": "mihomo",
  "gui": "E:\\mojie\\魔戒.exe",
  "core": "E:\\mojie\\resources\\static\\clash\\mojie-windows-amd64.exe",
  "core_dir": "%APPDATA%\\mojie",
  "core_config": "config.yaml",
  "controller": "127.0.0.1:18606",
  "kill": ["魔戒.exe", "mojie-service.exe", "mojie-windows-amd64.exe"],
  "admin": true
}
```

**说明**：

- `admin: true` 表示这个客户端的内核/服务通常需要管理员权限。填 `true` 时程序会走提权路径；如果你的环境里直启内核并不需要 UAC，可以改成 `false` 试试（**视客户端版本而定**）。
- 中文 exe 名（`魔戒.exe`）可以直接写，程序内部按宽字符处理；但 `kill` 里的进程名要和任务管理器里看到的**完全一致**。
- 如果 `mojie-service.exe` 是 Windows 服务（在"服务"里能看到），仅结束进程可能不够，还需要在客户端设置里关掉"开机自启/服务守护"。

### 5.3 从这两个例子能学到什么

| 观察 | 结论 |
| --- | --- |
| 内核 exe 不一定在主目录 | 全盘搜一下 `mihomo.exe`、`*Core.exe` 再决定 `core` |
| 控制端口不一定是 9090 | 一定要用 `/version` 验证，别猜 |
| 服务进程会把客户端拉回来 | `kill` 要把 `service`/`helper` 写全 |
| 配置目录和安装目录常常是两回事 | `core_dir` 指向**配置文件**所在目录，不是安装目录 |

---

## 6. 常见客户端内核名参考

> 下表是**参考**，不同版本、不同安装方式（安装版/便携版）可能不一样。**以你机器上实际存在的文件为准** —— 用 `--scan`，或在资源管理器里搜 `*.exe` 看名字。

| 客户端 | 界面进程（`gui`） | 内核进程（`core` / `kill`） | 备注 |
| --- | --- | --- | --- |
| **FlClash** | `FlClash.exe` | `FlClashCore.exe`，另有 `FlClashHelperService.exe` | 界面用 Flutter，助手服务会把内核拉起，`kill` 三个都要写 |
| **FlyingBird（飞鸟）** | `FlyingBird.exe` | `FlyingBirdCore.exe`，另有 `FlyingBirdHelperService.exe` | 结构同 FlClash 系 |
| **wandacloud（万达云）** | `wandacloud.exe` | `wandacloudCore.exe`，另有 `wandacloudHelperService.exe` | 内核控制接口支持情况**视版本而定** |
| **E-IX** | `eix_client.exe` | `mihomo.exe`（安装目录根下） | 配置在 `%APPDATA%\usfoo\E-IX\profile.yaml`，控制端口常见 9090 |
| **魔戒** | `魔戒.exe` | `mojie-windows-amd64.exe`（在 `resources\static\clash\`）+ `mojie-service.exe` | 控制端口常见 18606 |
| **mihomo（原版）** | 无（命令行程序） | `mihomo.exe` / `mihomo-windows-amd64.exe` | 最标准，直接用 `-d <配置目录> -f <配置>` 启动 |
| **Clash Verge / Clash Verge Rev** | `clash-verge.exe` | `verge-mihomo.exe`（新版）/ `clash-meta.exe` / `clash.exe` | 内核一般在应用目录或 `.config` 下；控制端口见它生成的运行时配置 |
| **Mihomo Party** | `mihomo-party.exe` | `mihomo-party-core.exe` / `mihomo.exe` | 内核名随版本变化，**视版本而定** |
| **Clash for Windows**（已停更） | `Clash for Windows.exe` | `clash-win64.exe` / `clash.exe` | 老版本仍在用，控制端口默认 9090 |

**其它线索**：

- 名字里带 `Helper`、`Service`、`Daemon` 的进程，多半是**守护/辅助进程**，会互相拉起 —— `kill` 里必须写。
- 内核 exe 常见命名规律：`mihomo*.exe`、`clash*.exe`、`<品牌拼音>Core.exe`、`<品牌>-windows-amd64.exe`。
- 内核 exe 常见位置：安装目录根、`resources\`、`resources\static\clash\`、`%LOCALAPPDATA%\<品牌>\`。

---

## 7. 接入后的自检清单

加完配置后按顺序验证：

1. **JSON 合法**：`python -m json.tool "%LOCALAPPDATA%\CampusNetAssistant\config.json"` 不报错（注意路径里的反斜杠要写两个）。
2. **路径存在**：在资源管理器里把 `gui`、`core`、`core_dir`、`core_config` 逐个打开确认。程序不会替你猜路径。
3. **端口能通**：浏览器打开 `http://127.0.0.1:<端口>/version`。
4. **顺序正确**：`flip.order` 里写的是 `id`，**不是** `name`。写错了这个客户端永远不会被选中。
5. **没连校园网**：翻墙模式只在"未连接校园网"时接管；连着校园网时它不会启动客户端（这是设计）。
6. **看日志**：`%LOCALAPPDATA%\CampusNetAssistant\运行日志.txt`，搜客户端 `id`，能看到"启动 → 找端口 → 筛节点 → 测延迟 → 切换 → 设置系统代理"每一步的结果和失败原因。
7. **手动复现一次**：把 `flip.apps` 临时改成一个你能马上启动的程序（比如 `notepad.exe`），触发一次，确认整条链路能走通，再换回你真正要用的程序。

## 8. 提交 PR 补充内置客户端

欢迎把你验证通过的客户端定义贡献回项目：

1. 按 [§5](#5-两个真实示例e-ix-与魔戒) 的格式整理：**完整字段 + 你是怎么探测出端口和内核路径的**（这段最重要，别人能照做）。
2. 在 `README.md` 的功能表/内置清单、本文件的 [§6](#6-常见客户端内核名参考) 表格里补一行。
3. 说明你的测试环境：Windows 版本、客户端版本、安装方式（安装版/便携版）、是否需要管理员。
4. **不要**附上你的订阅链接、节点名、账号或带个人信息的截图。

> 提醒：内置定义里出现的路径只是**示例**（很多人装在 `E:\` 下）。最终以用户自己的 `config.json` 为准，所以文档里要写清"怎么找到这些路径"，而不是只给一条写死的路径。
