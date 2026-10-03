# 贡献指南

感谢愿意帮忙！这个项目最需要的是**别人替不了的实践经验** —— 你学校的接入方式、你在用的代理客户端。

## 最受欢迎的四类贡献

### 1. 新的翻墙客户端适配器
把你正在用的 mihomo / Clash 系客户端整理成一条配置，并说明**你是怎么找到控制端口的**。
参考 [`docs/CLIENTS.md`](docs/CLIENTS.md)。最省事的做法是在 issue 里贴：

```
客户端名：
界面 exe 路径：
内核 exe 路径（如果有）：
配置目录 + 配置文件名（如果有）：
控制端口（或"没找到"）：
```

### 2. 校园网适配经验
- 你们学校的接入方式（PPPoE？DHCP？门户？客户端？802.1X？）
- 门户认证的厂商和**真实请求**（URL + 字段名，注意打码账号密码）
- 是否封 ICMP、是否限制多设备、闲置多久踢线

这些会写进 [`docs/WIRED.md`](docs/WIRED.md) 的厂商参考表，帮到后面所有人。

### 3. 错误码与排查经验
`rasdial` / `netsh wlan` / `netsh lan` 的其它报错，以及它的人话解释和解决办法。

### 4. 测试
新增的纯逻辑函数请顺手在 [`tests/test_core.py`](tests/test_core.py) 里补一个用例。

## 开发环境

只需要 Python 3.9+，**没有任何第三方依赖**（界面用标准库 tkinter）。

```bat
git clone https://github.com/<你的账号>/campusnet-assistant.git
cd campusnet-assistant
python -m campusnet                 :: 打开界面
python -m unittest discover -s tests -v   :: 跑测试
python -m compileall -q campusnet   :: 语法检查
```

调试时可以用环境变量把配置目录挪到临时位置，不碰你本机的真实配置：

```bat
set CNA_HOME=D:\tmp\cna-test
set CNA_BOOT_DIR=D:\tmp\cna-test\boot
python -m campusnet --selftest
```

## 代码约定

- **只用标准库**。要引入第三方依赖的 PR 基本不会被合并（这是本项目的核心卖点）。
- 中文注释与中文日志是本项目的风格，请保持。
- 面向用户的文字要"人话"：不要只说 `netsh` 命令，要说明**为什么**、**失败了怎么办**。
- 改动面向用户的行为时，请同时更新 `README.md`、`CHANGELOG.md`，必要时更新 `docs/`。
- `.cmd` 文件内容**保持纯 ASCII**：中文在 `.cmd` 里可能被 `cmd.exe` 按错误代码页解读而报错。
- 提交信息用中文或英文都行，说清楚"改了什么、为什么"。

## 提交 PR

1. Fork 本仓库，在自己的分支上改；
2. 跑一遍 `python -m unittest discover -s tests -v`，确保全绿；
3. 在 PR 描述里写：**动机**（解决什么问题）、**验证方式**（你怎么确认它能用）、
   如果涉及新客户端/新校园网，附上 `--selftest` 的输出（**记得去掉账号和密码**）。

## 安全与隐私

- **绝对不要**在 issue / PR / 截图里贴出真实的学号、密码、订阅链接、token。
- `--selftest` 的输出里含账号名（不含密码），贴出来前请自行打码。
- 发现安全问题的报告方式见 [`SECURITY.md`](SECURITY.md)。
