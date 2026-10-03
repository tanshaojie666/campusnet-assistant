# 有线接入方式详解 · docs/WIRED.md

校园网的有线接入**不止 PPPoE 一种**。本文把国内高校常见的几种都讲清楚：
每种方式是什么样、怎么判断自己属于哪种、在这个项目里怎么配。

> 快速判断：**插上网线后，系统里会不会多出一个"宽带连接"？**
> - 会，而且要你点"连接"输账号密码 → **PPPoE**
> - 不会，网卡直接就有 IP，但打开网页会跳登录页 → **DHCP + 门户认证**
> - 不会，学校给了你固定 IP/网关 → **静态 IP**
> - 必须装学校发的软件才能上网 → **学校专用客户端**
> - 插上就弹身份验证（提示"正在验证身份"）→ **802.1X**

- [一、PPPoE 拨号](#一pppoe-拨号)
- [二、自动获取 IP（DHCP）](#二自动获取-ipdhcp)
- [三、静态 IP](#三静态-ip)
- [四、Web 门户认证](#四web-门户认证captive-portal重点)
- [五、学校专用认证客户端](#五学校专用认证客户端)
- [六、有线 802.1X](#六有线-8021x)
- [七、组合与执行顺序](#七组合与执行顺序)
- [八、排查清单](#八排查清单)

---

## 配置在哪

界面：**校园网** 分页 → 「有线怎么认证（可多选）」勾选方式 → 「有线详细设置…」填细节。
配置文件：`%LOCALAPPDATA%\CampusNetAssistant\config.json` → `campus.wired`

```json
{
  "campus": {
    "mode": "wired",
    "connection": "宽带连接",
    "account": "学号",
    "wired": {
      "auth": ["pppoe"],
      "adapter": "",
      "static": {"address": "", "mask": "255.255.255.0", "gateway": "", "dns": []},
      "client_exe": "",
      "lan_profile": "",
      "portal": {"mode": "auto", "url": "", "method": "post", "body": "",
                 "script": "", "username": "", "password_enc": "", "probe_url": ""}
    }
  }
}
```

`auth` 是**数组**，可以多选，程序按数组顺序依次执行（见[第七节](#七组合与执行顺序)）。

---

## 一、PPPoE 拨号

**什么样**：最经典。Windows 里有一个"宽带连接"，点它要输账号密码。
对应 `rasdial`，等价于手动拨号。

**配置**

```json
{"auth": ["pppoe"], "connection": "宽带连接"}
```

- `connection` 必须和 Windows 里那个拨号连接名**完全一致**（`--scan` 会帮你列出来）
- 账号密码填在校友网分页的"账号/密码"，也可以 `python -m campusnet --set-account 学号 密码`

**错误码**（程序会翻译成中文写进日志）：

| 码 | 含义 | 通常怎么办 |
|---|---|---|
| 691 | 账号或密码错、欠费、同时在线设备超限 | 核对密码；去营业厅/自助页查是否超限 |
| 678 | 拨号无应答 | 网线、墙上端口、楼道交换机 |
| 651 | 网卡报错 | 网线没插好、网卡驱动异常 |
| 769 | 找不到网卡 | 网卡被禁用 |
| 720 | 无法协商 PPP 参数 | 删掉重建"宽带连接" |

**这个项目额外做的事**：拨号**之前**先关掉代理/VPN（很多校园网在代理开着的时候拨不上），
连上**之后**再复查关闭一次。

---

## 二、自动获取 IP（DHCP）

**什么样**：插上网线，网卡自己就拿到 IP（`169.254.x.x` 之外的地址），
不需要拨号。可能直接能上网，也可能还要过门户认证（那就是方式四）。

**配置**

```json
{"auth": ["dhcp"]}
```

**程序做的事**

1. 看有线网卡有没有可用 IP；
2. 没有（或拿到 `169.254.x.x` 自动私有地址）就执行
   `netsh interface ipv4 set address name=<网卡> source=dhcp`
   并把 DNS 也设为自动获取；
3. 等 6 秒后复查。

**判定"连上校园网"的依据**：有线网卡拿到了可用 IP。这是**链路级事实**，代理软件伪造不了。

---

## 三、静态 IP

**什么样**：学校给你一个固定 IP、子网掩码、网关、DNS，自己填进网卡属性。

**配置**

```json
{
  "auth": ["static"],
  "static": {"address": "10.20.30.40", "mask": "255.255.255.0",
             "gateway": "10.20.30.1", "dns": ["202.117.112.13", "202.117.112.14"]}
}
```

**程序做的事**：当前 IP 和配置不一致时执行

```
netsh interface ipv4 set address name=<网卡> static <IP> <掩码> <网关>
netsh interface ipv4 set dnsservers name=<网卡> static <DNS1> primary
```

**注意**：填错会直接断网（网关填错时连局域网都不通）。
建议先在 Windows 网卡属性里手动配通一次，确认参数无误再交给程序。

---

## 四、Web 门户认证（Captive Portal）★重点

> **先记住一件事**：门户认证在界面里是**独立的一项设置**（「网页（门户）认证设置…」按钮），
> **有线和无线共用**。也就是说：
> - 有线走 DHCP / 静态 IP 之后被门户拦住 → 会用它；
> - **无线连上指定 SSID 却上不了网** → 也会用它（校园 WiFi 绝大多数都是这种情况）。
>
> 配置位置：`campus.portal`（早期版本存在 `campus.wired.portal`，程序会自动兼容沿用）。

**什么样**：拿到 IP 了，但打开任何网页都会跳到学校的登录页，输学号密码后才放行。
国内常见厂商：**深信服 Sangfor、锐捷 Ruijie、城市热点 Dr.COM、H3C**。

**原理**：门户通过"劫持 HTTP 请求"实现 —— 你请求任何 http 页面，
它返回 302 跳转到登录页。所以判断"是否已放行"的方法很标准：
请求一个**必定返回 204 的探测地址**，收到 204 就是通了，收到 302 就是被劫持。

本项目内置的探测地址（可自定义 `probe_url`）：

```
http://connect.rom.miui.com/generate_204
http://www.gstatic.com/generate_204
http://connectivitycheck.platform.hicloud.com/generate_204
```

### 三种登录方式

**① `auto` —— 自动填表（先试这个）**

程序会：拿到跳转地址 → 下载登录页 → 找出含密码框的表单 →
自动把账号密码填进名字像 username/password 的字段 → 提交 → 复查是否放行。

适合：登录页是**服务端渲染的普通 HTML 表单**。
不适合：登录页是 JS 动态渲染、或提交前要算签名（`auth_tag`）的。

**② `template` —— 按模板提交（最通用，兼容各家厂商）**

你自己填请求地址和内容，程序只负责替换 `{username}` / `{password}` 并发送。

```json
{
  "mode": "template",
  "url": "http://10.0.0.1/ac_portal/login.php",
  "method": "post",
  "body": "opr=pwdLogin&userName={username}&pwd={password}&auth_tag=xxx",
  "headers": {"Content-Type": "application/x-www-form-urlencoded"}
}
```

**怎么拿到正确的地址和字段？**（最可靠的办法）

1. 在浏览器里手动登录一次门户；
2. 按 `F12` 打开开发者工具 → **Network（网络）** → 勾选 `Preserve log`；
3. 在登录页重新登录一次；
4. 找到那条**提交登录**的请求（一般是 POST，名字像 `login`），
   点它 → 看 **Request URL** 和 **Form Data / Payload**；
5. 把 URL 填进 `url`，把 Payload 里的账号密码换成 `{username}` `{password}`，
   原样填进 `body`。

> 有些门户提交前要从页面里取一个一次性令牌（深信服的 `auth_tag`、锐捷的 `service` 等），
> 这种**不能**用固定模板，需要改用 `script` —— 让脚本每次先取令牌再提交。

**③ `script` —— 执行命令（最后的兜底，也最省事）**

学校给了脚本、或者有现成的命令行工具（很多学校有），直接让它跑：

```json
{"mode": "script", "script": "python C:\\portal\\login.py {username} {password}"}
```

程序会在需要认证时执行它，然后复查是否放行。退出码和输出都会写进日志。

### 厂商参考（字段名**因学校而异**，务必按上面的方法抓一次）

| 厂商 | 常见接口 | 常见字段 |
|---|---|---|
| 深信服 Sangfor | `POST /ac_portal/login.php` | `opr=pwdLogin`、`userName`、`pwd`、`auth_tag`（每次不同） |
| 锐捷 Ruijie | `POST /eportal/InterFace.do?method=login` | `userId`、`password`、`service`、`queryString` |
| 城市热点 Dr.COM | 各校自建，常见 `POST /drcom/login` 或带 `0/1` 参数的 GET | `username`/`password`、`ip`、`mac` |
| H3C | `POST /portal/login` | `userid`、`passwd` |

---

## 五、学校专用认证客户端

**什么样**：学校只认自己发的软件（Dr.COM 客户端、赛尔、iNode、
各种"校园网认证客户端"），不开它就不给网。

**配置**

```json
{"auth": ["client"], "client_exe": "C:\\Program Files\\DrCom\\DrMain.exe"}
```

**程序做的事**：检测该进程没在跑就启动它（不抢焦点），等 10 秒，再复查网络。

**局限**：程序只能**把它拉起来**，客户端内部的点击/登录还是它自己完成的
（绝大多数这类客户端会记住账号并自动登录）。
如果学校客户端必须手动点"登录"，就把它加进**开机自启**，
本项目负责保证它一直在运行。

---

## 六、有线 802.1X

**什么样**：插上网线后系统提示"正在验证身份"，需要证书或账号密码。
Windows 自带支持，本项目用 `netsh lan` 触发连接。

**先在系统里建好配置**（一次性）：

1. 控制面板 → 网络和共享中心 → 更改适配器设置；
2. 右键有线网卡 → 属性 → **身份验证** 选项卡；
3. 勾选「启用 IEEE 802.1X 身份验证」，选择你的认证方式（PEAP / EAP-TLS 等），
   点「设置」填账号密码或选证书，确定保存。

**配置**

```json
{"auth": ["lan"], "lan_profile": "你的配置名", "adapter": "以太网"}
```

程序在需要时执行 `netsh lan connect name=<配置名> interface=<网卡>`。

> 注意：`netsh lan` 需要管理员权限；系统级守护本身就以 SYSTEM 运行，所以没问题。

---

## 七、组合与执行顺序

`auth` 数组就是执行顺序。几个常见组合：

**宿舍 PPPoE + 偶尔插别人家的网线（最稳）**

```json
{"auth": ["pppoe"], "connection": "宽带连接"}
```

**实验室：静态 IP + 门户认证**

```json
{
  "auth": ["static", "portal"],
  "static": {"address": "10.20.30.40", "mask": "255.255.255.0", "gateway": "10.20.30.1"},
  "portal": {"mode": "auto"}
}
```

**网口经常卡住：先重启网卡再 DHCP**

```json
{"auth": ["restart", "dhcp"]}
```

**不确定是 DHCP 还是静态：两个都勾**

程序会先按 DHCP 试，拿不到地址再按静态配。这样两种环境都能用。

> **判定"连上校园网"的依据**：配置里第一项如果是 `pppoe`，就以拨号链路为准；
> 否则以有线网卡是否拿到可用 IP 为准。之所以这样区分，是为了避免
> "笔记本插到家里路由器上拿到 IP"被误判成"连上校园网"。

---

## 八、排查清单

按顺序做，基本能定位问题：

```bat
python -m campusnet --selftest      :: 看第 1、2 节：网卡、认证方式、链路状态
python -m campusnet --scan          :: 列出拨号连接 / 无线配置 / 已装客户端
```

| 现象 | 先看什么 |
|---|---|
| 网卡列表是空的 | 网线没插、网卡被禁用、或网卡是虚拟网卡（`vEthernet`）——在详细设置里手动指定网卡名 |
| DHCP 拿不到 IP | `ipconfig /release` 后重试；或勾选 `restart` 让程序先重启网卡 |
| 一直显示"未获取地址" | 网线/端口问题；或需要先过门户（勾 `portal`） |
| 门户认证一直不成功 | 改用 `template`，按第四节的方法抓一次真实请求；再不行用 `script` |
| 认证成功但很快又断 | 学校限制单设备/有闲置踢线策略；调小 `interval`（默认 15 秒） |
| 静态 IP 配完完全没网 | 网关填错；先在系统里手动配通再交给程序 |
| 需要管理员权限 | 网卡/IP/802.1X 的修改都需要；界面里的操作会弹一次 UAC，系统级守护本身已是 SYSTEM |

**抓门户请求的替代办法**：在浏览器里登录成功后，看
`F12 → Network` 里那条登录请求，右键 → Copy → **Copy as cURL**，
把里面的 URL 和 `--data-raw` 内容照抄成 `template` 配置即可。
