# 发布到 GitHub · PUBLISH

本文档说明如何把 **CampusNetAssistant** 发布（上传）到 GitHub。给两条路：

- **方式一：手动（推荐第一次用）** —— 装 Git，命令行走一遍 `init → add → commit → remote → push`。过程透明、出问题好排查。
- **方式二：半自动（脚本）** —— 项目自带 `scripts/publish_github.py`，用 GitHub REST API 通过本地代理创建仓库并上传文件。适合"我只想快点传上去"或者"这台机器没装 Git"。

> ⚠️ **上传前必读**：先确认没有把隐私文件提交上去。默认的 [`.gitignore`](.gitignore) 已经排除了 `config.json`、`*.log`、`credential*.xml` 等文件，但**你仍然要自己看一眼**下面这条命令的输出：
>
> ```bat
> git status --short
> git ls-files
> ```
>
> 里面**不应该**出现：学号/密码、`config.json`、任何 `运行日志.txt`、`credential*.xml`、含节点链接或订阅地址的截图。截图放 [`docs/images/`](docs/images/) 时请把学号、SSID、节点名打码。

---

## 0. 准备：先想清楚三件事

| 事项 | 建议 |
| --- | --- |
| 仓库名 | `CampusNetAssistant`（和目录名一致，省事） |
| 公开还是私有 | 想收 PR、想给别人用 → **Public**；只是自己备份 → Private |
| 默认分支名 | `main`（GitHub 现在的默认值，和本文档命令一致） |
| License | 仓库里已有 [`LICENSE`](LICENSE)（MIT，2026，CampusNetAssistant contributors），GitHub 会自动识别 |

---

## 方式一：手动发布（Git 命令行）

### 1. 安装 Git

1. 打开 <https://git-scm.com/download/win>，下载 64 位安装包。
2. 一路"下一步"装完（默认选项就行；编辑器选你顺手的，比如 VS Code 或 Notepad）。
3. 打开新的"命令提示符"，验证：

   ```bat
   git --version
   ```

   能打印版本号（如 `git version 2.45.0.windows.1`）就 OK。
   如果提示"不是内部或外部命令"，说明 PATH 没生效：**关掉再重开**命令行；仍不行就重新安装并选择 "Git from the command line and also from 3rd-party software"。

### 2. 配置身份（只做一次）

```bat
git config --global user.name  "你的名字"
git config --global user.email "你的邮箱@example.com"
git config --global init.defaultBranch main
```

> 这里的邮箱建议和你 GitHub 账号的邮箱一致，这样提交才会算在你头上。

### 3. 在项目目录初始化仓库

```bat
cd /d D:\CampusNetAssistant
git init
git add .
git status --short          :: 再确认一遍：没有隐私文件混进来
git commit -m "Initial commit: CampusNetAssistant 校园网助手"
```

如果 `git commit` 之后 `git branch` 显示的不是 `main`：

```bat
git branch -M main
```

### 4. 在 GitHub 网站上新建仓库

1. 登录 <https://github.com>，右上角 **+ → New repository**。
2. **Repository name** 填 `CampusNetAssistant`。
3. **不要**勾 "Add a README file"、**不要**勾 "Add .gitignore"、**不要**选 license —— 本地已经有了，勾了会产生冲突，第一次 push 会报 `rejected (fetch first)`。
4. 选 Public 或 Private，点 **Create repository**。
5. 创建完成后页面会显示仓库地址，形如 `https://github.com/<你的用户名>/CampusNetAssistant.git`。

### 5. 关联远程仓库并推送

```bat
git remote add origin https://github.com/<你的用户名>/CampusNetAssistant.git
git push -u origin main
```

第一次 push 会弹出**登录窗口**：

- 推荐用 **Git Credential Manager**（Git for Windows 自带）：点 "Sign in with your browser"，浏览器里授权一次，之后就不用再输了。
- 如果它要你输用户名和密码：**密码处必须填 Personal Access Token（PAT）**，GitHub 早就不接受账号密码了。生成方法见 [方式二的第 2 节](#2-生成一次性-personal-access-tokenpat)。

推完之后刷新仓库页面，应该能看到 README 被渲染出来。

### 6. 日常更新（改了文件之后）

```bat
git status                      :: 看改了啥
git add -A
git commit -m "docs: 补充客户端适配说明"
git push
```

### 7. 常见报错

| 报错 | 原因与解决 |
| --- | --- |
| `remote origin already exists` | 已经加过远程了。改地址用 `git remote set-url origin <新地址>` |
| `rejected ... fetch first` | 远程仓库不是空的（建仓时勾了 README / .gitignore）。先 `git pull --rebase origin main` 再 `git push` |
| `fatal: not a git repository` | 没在项目目录里，或忘了 `git init`；用 `cd /d 项目路径` 切换 |
| `Authentication failed` | 密码处填了账号密码而不是 PAT；或 PAT 过期/权限不足 |
| `Failed to connect ... timeout` | 网络问题。见下方"通过本地代理访问 GitHub" |
| 中文文件名显示成乱码 | 执行 `git config --global core.quotepath false` |
| 提示 `LF will be replaced by CRLF` | 正常警告，忽略即可 |

#### 通过本地代理访问 GitHub（网络不通时）

如果你平时靠本地代理上网（例如 Clash/mihomo 监听 `127.0.0.1:7893`），给 Git 单独配上代理：

```bat
git config --global http.proxy  http://127.0.0.1:7893
git config --global https.proxy http://127.0.0.1:7893

:: 不想用了就取消
git config --global --unset http.proxy
git config --global --unset https.proxy
```

> 端口要和你的客户端实际监听端口一致：在客户端里看"混合端口/HTTP 端口"，或用 `netstat -ano | findstr LISTENING` 对着内核进程 PID 找。

---

## 方式二：半自动发布（`scripts/publish_github.py`）

项目自带 [`scripts/publish_github.py`](scripts/publish_github.py)：它用 GitHub 的 **REST API** 帮你

1. 校验 Token、读取你的账号信息；
2. 创建（或复用）仓库；
3. 遍历项目文件，按 `.gitignore` 规则**跳过隐私文件和垃圾文件**；
4. 逐个上传（新建或更新），最后给出仓库网页地址。

**它是为"国内网络环境"设计的**：默认走本地代理 `http://127.0.0.1:7893`，不依赖 Git。

### 1. 前置条件

| 条件 | 说明 |
| --- | --- |
| Python 3.8+ | 和主程序一样，只用标准库 |
| 一个能用的本地代理 | 默认 `http://127.0.0.1:7893`。端口不一样就改（见下），或者关掉代理直连 |
| Personal Access Token | 见下一节，**一次性**用完可删 |

### 2. 生成一次性 Personal Access Token（PAT）

1. 浏览器打开 <https://github.com/settings/tokens>。
   （或：GitHub 右上角头像 → **Settings** → 左侧最底 **Developer settings** → **Personal access tokens**）
2. 选 **Tokens (classic)** → **Generate new token (classic)**。
   > 用 **Fine-grained tokens** 也可以，但必须给到 **Repository permissions → Contents: Read and write**，并允许访问你要发布的仓库；classic token 的配置更简单，第一次建议用它。
3. **Note** 随便写，例如 `publish CampusNetAssistant`。
4. **Expiration**：选 **7 days** 或 **30 days**（**别选 No expiration**，token 等同于你的账号权限）。
5. **Scopes**：**只勾一个 `repo`**（完整仓库读写权限）。
   - 只想发布**公开**仓库的话，`public_repo` 就够了；
   - 需要创建**私有**仓库，必须勾 `repo`。
   - 其它权限（`workflow`、`admin:org`、`delete_repo`…）**不要勾**。
6. 点 **Generate token**，页面会显示一串 `ghp_xxxxxxxx...`。
   **这串东西只显示一次，立刻复制**（存到临时文本里）。
7. 用完之后（发布成功、不需要再传了）：回到 <https://github.com/settings/tokens>，点该 token 右侧 **Delete** 删掉。token 一旦删除立即失效，不会影响已经上传的仓库。

> 🔐 Token 就是密码级凭据。**不要**写进 `config.json`、不要提交到仓库、不要贴进聊天记录或截图。本脚本不会把它保存到项目目录里的任何文件（若脚本提供了记住 Token 的选项，请自行确认其存储位置并谨慎使用）。

### 3. 运行

在项目根目录执行（**先看 `--help`**，参数名以脚本实际输出为准）：

```bat
python scripts\publish_github.py --help
```

典型用法：

```bat
:: 用默认代理 http://127.0.0.1:7893 发布（公开仓库）
python scripts\publish_github.py --token ghp_你的Token --repo CampusNetAssistant

:: 改代理端口（例如你的客户端监听 7890）
python scripts\publish_github.py --token ghp_你的Token --repo CampusNetAssistant --proxy http://127.0.0.1:7890

:: 不走代理（直连能通 GitHub API 时）
python scripts\publish_github.py --token ghp_你的Token --repo CampusNetAssistant --no-proxy

:: 创建私有仓库
python scripts\publish_github.py --token ghp_你的Token --repo CampusNetAssistant --private

:: 先预演：只列出会上传哪些文件，不真的上传
python scripts\publish_github.py --token ghp_你的Token --repo CampusNetAssistant --dry-run
```

> 上面是**示意用法**。脚本实际支持哪些开关请看 `--help` 的输出——不要照抄本节的参数名。**只要脚本提供 `--dry-run`（预演）就先用它**，确认文件清单里没有隐私文件再真传。

### 4. 脚本不上传哪些文件

脚本会按 [`.gitignore`](.gitignore) 的规则跳过：

- Python 垃圾：`__pycache__/`、`*.pyc`、`build/`、`dist/`、`.venv/`
- IDE / 系统：`.vscode/`、`.idea/`、`Thumbs.db`、`Desktop.ini`
- **隐私文件**：`config.json`、`*.log`、`运行日志.txt`、`开机拨号日志.txt`、`credential*.xml`
- Git 自己的目录：`.git/`

**仍然建议肉眼核对**脚本打印的文件清单，尤其是截图和示例文件。

### 5. 常见报错

| 现象 | 原因与解决 |
| --- | --- |
| `401 Unauthorized` | Token 不对/已过期/被删；或复制时带了空格或换行 |
| `403 Forbidden` + `rate limit` | 触发 API 频率限制，等一会儿再试（未认证请求限额更低） |
| `403` + `Resource not accessible` | Token 权限不足：勾 `repo`（私有仓库必须）；classic token 要重新生成 |
| `422 Unprocessable Entity` | 仓库名已存在且参数不允许覆盖，或名字含非法字符；换个名字或让脚本复用已有仓库 |
| 连不上 `api.github.com` / 超时 | 代理没开、端口写错、或代理规则没放行 `api.github.com`。先用 `curl -x http://127.0.0.1:7893 https://api.github.com/user` 之类的命令验证代理是否可用 |
| 单个文件上传失败 | GitHub Contents API 对单文件有大小限制（约 100 MB）；别把大压缩包、模型文件放进仓库 |
| 中文名文件乱码 | 尽量别用中文文件名；必要的文档用英文名（本项目文档均为英文文件名） |

### 6. 发布之后

1. 打开脚本打印的仓库地址，确认 README 正常渲染、文件齐全。
2. 补充仓库信息：**About**（一句话简介）、**Topics**（`windows`、`pppoe`、`campus-network`、`clash`、`mihomo`、`python`、`tkinter`）。建议的简介：
   > 校园网自动接入 + 代理/VPN 策略管理（Windows，纯 Python 标准库）
3. 确认 License 被识别为 MIT（仓库页右侧应显示 "MIT license"）。
4. 如果这是你要长期维护的仓库：回到 <https://github.com/settings/tokens> **删掉这次用的 Token**；以后改用 Git Credential Manager（见方式一）或重新签一个短期 token。
