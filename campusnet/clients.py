# -*- coding: utf-8 -*-
"""翻墙客户端适配器（可扩展）。

设计目标：**客户端可选**。绝大多数机场/代理客户端都是 mihomo(Clash.Meta) 内核，
都提供 HTTP 控制接口（external-controller），因此本项目只需要：

  1. 启动客户端 —— 两条路：
     · core  : 直接用客户端自带的内核 + 它自己的配置（推荐：不用开界面、不用管理员权限）
     · gui   : 启动它的图形界面（有些客户端只有界面能拉起来内核）
  2. 找到控制接口（配置里写死的 controller，或按内核进程监听的端口自动发现）
  3. 通过接口列节点 → 按地区关键词筛选 → 逐个真实测延迟 → 切到最快可用的那个
  4. 把系统代理指向它的混合端口

要支持新客户端，只要在配置的 clients 里加一条（见 docs/CLIENTS.md）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from .config import expand
from .util import (create_no_window_flag, is_admin, list_processes, pids_of,
                   run_cmd, set_system_proxy)

# 组名优先级：AI 类分组（ChatGPT 那类流量走它）+ 常见主分组
GROUP_PRIORITY = ["AI", "ChatGPT", "OpenAI", "Copilot", "Gemini", "Claude",
                  "节点选择", "选择", "Proxy", "PROXY", "代理", "GLOBAL", "E-IX", "MoJie"]


# --------------------------------------------------------------------------
# HTTP（绕开系统代理，否则会自己绕进代理里）
# --------------------------------------------------------------------------
def http_json(port, path, method="GET", body=None, timeout=6):
    url = "http://127.0.0.1:%d%s" % (int(port), path)
    req = urllib.request.Request(url, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace").strip()
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------
# 客户端定义
# --------------------------------------------------------------------------
def client_by_id(cfg, key):
    for c in cfg.get("clients") or []:
        if str(c.get("id")) == str(key):
            return c
    return None


def _first_existing(paths):
    """从候选路径里挑第一个存在的；都不存在就返回第一个（用于报错信息）。"""
    cleaned = [expand(p) for p in (paths or []) if p]
    for p in cleaned:
        if p and os.path.isfile(p):
            return p
    return cleaned[0] if cleaned else ""


def _first_dir(paths):
    cleaned = [expand(p) for p in (paths or []) if p]
    for p in cleaned:
        if p and os.path.isdir(p):
            return p
    return cleaned[0] if cleaned else ""


def client_paths(client):
    """展开环境变量并挑出真实存在的路径，返回 (gui, core, core_dir, core_config)。

    同时兼容旧写法（gui/core/core_dir 单值）和新写法（gui_paths/core_paths/core_dirs 候选列表）。
    """
    gui_list = client.get("gui_paths") or ([client.get("gui")] if client.get("gui") else [])
    core_list = client.get("core_paths") or ([client.get("core")] if client.get("core") else [])
    dir_list = client.get("core_dirs") or ([client.get("core_dir")] if client.get("core_dir") else [])
    return _first_existing(gui_list), _first_existing(core_list), _first_dir(dir_list), \
        client.get("core_config", "") or ""


def client_installed(client):
    gui, core, core_dir, core_cfg = client_paths(client)
    if core and core_dir and core_cfg:
        return os.path.isfile(core) and os.path.isfile(os.path.join(core_dir, core_cfg))
    return bool(gui) and os.path.isfile(gui)


def scan_clients(cfg):
    """扫描本机，返回 [(client, 是否装了, 说明)]。"""
    out = []
    for c in cfg.get("clients") or []:
        gui, core, core_dir, core_cfg = client_paths(c)
        detail = []
        if gui:
            detail.append("界面:" + ("有" if os.path.isfile(gui) else "无"))
        if core:
            detail.append("内核:" + ("有" if os.path.isfile(core) else "无"))
        if core_dir and core_cfg:
            detail.append("配置:" + ("有" if os.path.isfile(os.path.join(core_dir, core_cfg)) else "无"))
        out.append((c, client_installed(c), " ".join(detail)))
    return out


def core_pids(client):
    names = [str(x).lower() for x in (client.get("cores") or [])]
    if not names:
        names = [str(x).lower() for x in (client.get("kill") or [])]
    procs = list_processes() or {}
    pids = set()
    for name, ids in procs.items():
        if name in names:
            pids.update(ids)
    return pids


def find_controller(client):
    """找 mihomo 控制端口：先按配置，再按内核进程监听的端口自动发现。"""
    from .util import listening_ports

    fixed = str(client.get("controller") or "").strip()
    cands = []
    if fixed:
        m = fixed.split(":")
        if len(m) == 2 and m[1].isdigit():
            cands.append(int(m[1]))
    pids = core_pids(client)
    if pids:
        cands += [p for p in listening_ports(pids) if p not in cands]
    for port in cands:
        try:
            v = http_json(port, "/version", timeout=2)
            if isinstance(v, dict) and ("version" in v or "meta" in v):
                return port
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# 启动客户端
# --------------------------------------------------------------------------
def launch_client(client, log=None):
    """启动客户端，返回 (是否已发起启动, 说明)。"""
    def _log(m):
        if log:
            log(m)

    gui, core, core_dir, core_cfg = client_paths(client)
    if core and core_dir and core_cfg and os.path.isfile(core) \
            and os.path.isfile(os.path.join(core_dir, core_cfg)):
        _log("用 %s 自带内核直接启动（沿用它的节点和规则，无需界面/UAC）…" % client.get("name"))
        args = [core, "-d", core_dir, "-f", os.path.join(core_dir, core_cfg)]
        # 显式指定控制接口：有些客户端的配置文件里没这一项，而内核支持用
        # -ext-ctl 覆盖 —— 这样一定能拿到 API，不然就只能干等。
        ctl = str(client.get("controller") or "").strip()
        if ctl:
            args += ["-ext-ctl", ctl]
        try:
            subprocess.Popen(args, close_fds=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=create_no_window_flag())
            return True, "core"
        except Exception as exc:  # noqa: BLE001
            _log("  内核启动失败：%s" % exc)

    if not gui or not os.path.isfile(gui):
        return False, "找不到可执行文件"
    _log("启动 %s 界面…" % client.get("name"))
    try:
        subprocess.Popen([gui], close_fds=True)
        return True, "gui"
    except OSError as exc:
        # 740 = 需要提升权限
        if getattr(exc, "winerror", None) == 740:
            _log("  它需要管理员权限，改用提权启动（会弹一次 UAC）")
            import ctypes
            import ctypes.wintypes as wt
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
                                              wt.LPCWSTR, ctypes.c_int]
            rc = shell32.ShellExecuteW(None, "runas", gui, None, None, 1)
            return bool(rc and rc > 32), "gui-elevated"
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# --------------------------------------------------------------------------
# 选节点
# --------------------------------------------------------------------------
def is_target_region(name, hints):
    s = str(name).lower()
    return any(str(h).lower() in s for h in (hints or []))


def pick_node(port, region_hints, log=None, max_test=8):
    """列节点 → 按地区筛选 → 逐个真测延迟 → 切最快可用的那个。

    返回 (分组名, 节点名, 延迟ms) 或 None。
    """
    def _log(m):
        if log:
            log(m)

    data = http_json(port, "/proxies", timeout=10) or {}
    proxies = data.get("proxies") or {}
    sels = {k: v for k, v in proxies.items()
            if v.get("type") == "Selector" and (v.get("all") or [])}
    if not sels:
        return None

    prefer = [g for g in GROUP_PRIORITY if g in sels]
    for g in sels:
        if any(x in g for x in ("节点选择", "选择", "Proxy", "代理")) and g not in prefer:
            prefer.append(g)
    group = prefer[0] if prefer else list(sels)[0]

    nodes = [n for n in sels[group]["all"] if is_target_region(n, region_hints)]
    if not nodes:
        return None

    best = None
    for n in nodes[:max_test]:
        ms = 0
        try:
            q = urllib.parse.urlencode({"timeout": 3000,
                                        "url": "http://www.gstatic.com/generate_204"})
            d = http_json(port, "/proxies/%s/delay?%s" % (urllib.parse.quote(n), q), timeout=8)
            ms = int((d or {}).get("delay") or 0)
        except Exception:
            ms = 0
        _log("  测速 %s → %s" % (n, ("%d ms" % ms) if ms else "不通"))
        if ms > 0 and (best is None or ms < best[2]):
            best = (group, n, ms)
    if not best:
        return None

    # 把选中节点应用到所有相关分组（AI 类 + 主分组），保证 ChatGPT 那类流量也走它
    targets = [g for g in GROUP_PRIORITY if g in sels
               and best[1] in (sels[g].get("all") or [])]
    if best[0] not in targets:
        targets.append(best[0])
    for g in targets:
        try:
            http_json(port, "/proxies/%s" % urllib.parse.quote(g), method="PUT",
                      body={"name": best[1]})
        except Exception:
            pass
    _log("  已应用到分组：%s" % "、".join(targets))
    time.sleep(1)
    return best


def config_port(client, key="mixed-port"):
    """从客户端自己的配置里读端口（按行找，不依赖 YAML 解析）。

    有些内核是定制版、**根本没有控制接口**（例如 E-IX 的 mihomo 会把
    external-controller 和 -ext-ctl 都忽略掉），但代理端口是正常的 ——
    这种情况就靠这个函数拿到该用哪个端口。
    """
    _gui, _core, core_dir, core_cfg = client_paths(client)
    if not core_dir or not core_cfg:
        return 0
    path = os.path.join(core_dir, core_cfg)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"\s*[\"']?%s[\"']?\s*:\s*(\d+)" % re.escape(key), line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return 0


def probe_via_proxy(mixed_port, url="http://www.gstatic.com/generate_204", timeout=10):
    """通过这个代理端口真的发一个请求 —— 能返回 200/204 就说明代理链路通了。

    这比"端口在监听"可靠得多：端口在监听不等于节点可用。
    """
    if not mixed_port:
        return False
    proxy = "http://127.0.0.1:%d" % int(mixed_port)
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(url, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def enable_proxy_for(port, log=None):
    """把系统代理指向这个客户端的混合端口。"""
    mixed = 0
    try:
        mixed = int((http_json(port, "/configs", timeout=5) or {}).get("mixed-port") or 0)
    except Exception:
        mixed = 0
    if mixed and set_system_proxy(True, "127.0.0.1:%d" % mixed):
        if log:
            log("  已把系统代理指向 127.0.0.1:%d" % mixed)
        return mixed
    return 0


def ensure_client_ready(cfg, order, log=None, wait_seconds=None):
    """按顺序尝试客户端：启动 → 等控制接口 → 切到目标地区节点 → 设系统代理。

    注意：内核**首次启动可能要下载规则集**（配置里 rule-providers 指向上游 URL）。
    这段时间代理端口 7890-7893 已经能连，但控制接口还没开 —— 如果只等十几秒，
    就会被误判成"客户端起不来/需要在界面里手动连接"。所以默认等 180 秒，
    并每隔一段时间打一条进度日志。可用环境变量 CNA_CONTROLLER_WAIT 调整。

    返回 (是否成功, 客户端名, 节点名, client_id)。
    """
    def _log(m):
        if log:
            log(m)

    if wait_seconds is None:
        try:
            wait_seconds = int(os.environ.get("CNA_CONTROLLER_WAIT") or 180)
        except ValueError:
            wait_seconds = 180
    hints = (cfg.get("flip") or {}).get("region_hints") or []
    order = [k for k in (order or []) if client_by_id(cfg, k)]
    if not order:
        _log("没有勾选任何翻墙客户端 → 请到「翻墙模式」页把要用的客户端勾上。")
        return False, "", "", ""
    for key in order:
        c = client_by_id(cfg, key)
        if not c:
            continue
        if not client_installed(c):
            _log("跳过 %s（没装或路径不对）" % c.get("name"))
            continue
        if not core_pids(c):
            ok, how = launch_client(c, _log)
            if not ok:
                _log("  启动失败：%s" % how)
                continue
        port = None
        deadline = time.time() + wait_seconds
        next_tip = time.time() + 30
        while time.time() < deadline:
            time.sleep(2)
            port = find_controller(c)
            if port:
                break
            if time.time() >= next_tip:
                next_tip = time.time() + 30
                _log("  还在等 %s 的控制接口（首次启动要下载规则集，慢的话要一两分钟，"
                     "最多再等 %d 秒）…" % (c.get("name"), max(0, int(deadline - time.time()))))
        if not port:
            # 控制接口拿不到 → 退化成"直接用它自己的代理端口"。
            # 为什么需要这条：有些内核是定制版，会把 external-controller / -ext-ctl
            # 全部忽略（E-IX 就是这样），但代理功能完全正常。这时虽然没法自动挑节点，
            # 可它记住的那个节点通常是能用的 —— 能上网总比一点也不通强。
            mixed = config_port(c)
            if mixed and probe_via_proxy(mixed):
                if set_system_proxy(True, "127.0.0.1:%d" % mixed):
                    _log("  %s 没有控制接口（内核是定制版），但它自己的代理端口 %d 已经能上网"
                         " → 直接用它记住的节点（这种模式下无法自动换节点）。"
                         % (c.get("name"), mixed))
                    return True, "%s（自带节点）" % c.get("name"), "(客户端自己记住的节点)", key
            _log("  %s 的控制接口没出现，代理端口也不通（可在它界面里手动连一次），换下一个"
                 % c.get("name"))
            continue
        _log("  %s 控制接口 127.0.0.1:%d" % (c.get("name"), port))
        try:
            r = pick_node(port, hints, _log)
        except Exception as exc:  # noqa: BLE001
            _log("  切节点出错：%s" % exc)
            r = None
        if r:
            enable_proxy_for(port, _log)
            return True, c.get("name"), r[1], key
        _log("  %s 里没有可用的目标地区节点，换下一个" % c.get("name"))
    return False, "", "", ""
