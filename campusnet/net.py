# -*- coding: utf-8 -*-
"""网络与校园网接入。

支持两种校园网接入方式（可单选或都要）：
  wired    —— 有线 PPPoE 拨号（rasdial，等价于点“宽带连接→连接”）
  wireless —— 无线（netsh wlan connect 连接指定 SSID）

判定“校园网已连上”用的是**链路级事实**（PPPoE 有没有 IPv4 / 无线有没有连上该 SSID），
而不是“能不能上网” —— 这样代理软件没法伪造，也不会因为别的网络而误判。
"""
from __future__ import annotations

import os
import re
import socket
import time

from .util import run_cmd

# 探测目标：校园网 DNS 最可靠（很多校园网封 ICMP，所以一律用 TCP）
FALLBACK_PROBES = [["223.5.5.5", 443], ["180.76.76.76", 443], ["114.114.114.114", 53],
                   ["223.6.6.6", 443]]


# --------------------------------------------------------------------------
# 拨号电话簿
# --------------------------------------------------------------------------
def default_phonebook() -> str:
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Network\Connections\Pbk\rasphone.pbk")


def pppoe_connections(pbk=None):
    """从拨号电话簿里找出所有 PPPoE 连接名（自动适配本机）。"""
    pbk = pbk or default_phonebook()
    names = []
    if not os.path.isfile(pbk):
        return names
    try:
        with open(pbk, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except Exception:
        return names
    cur, block = None, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur and any(b.strip().upper() == "DEVICE=PPPOE" for b in block):
                names.append(cur)
            cur, block = s[1:-1], []
        elif cur is not None:
            block.append(line)
    if cur and any(b.strip().upper() == "DEVICE=PPPOE" for b in block):
        names.append(cur)
    return names


def ras_dial(entry, username=None, password=None, phonebook=None):
    """拨号。phonebook 用于指定电话簿 —— 以 SYSTEM 身份运行时必须指定，
    因为那时 %APPDATA% 指向的是系统配置目录，找不到用户的拨号条目。"""
    args = ["rasdial", entry]
    if username:
        args.append(username)
    if password:
        args.append(password)
    if phonebook:
        args.append("/PHONEBOOK:" + phonebook)
    return run_cmd(args, timeout=120)


def ras_hangup(entry, phonebook=None):
    args = ["rasdial", entry, "/disconnect"]
    if phonebook:
        args.append("/PHONEBOOK:" + phonebook)
    return run_cmd(args, timeout=60)


def friendly_error(code, text=""):
    table = {
        0: "成功", 5: "访问被拒绝：需要管理员权限",
        623: "找不到这个拨号连接（名字不对）", 629: "连接被远程计算机终止",
        651: "网卡报错：多半是网线没插好或网卡驱动异常", 676: "线路占线",
        678: "拨号无应答：PPPoE 服务器没反应（检查网线、墙上端口）",
        691: "账号或密码错误，或者账号被限制（欠费 / 同时在线设备数超了）",
        692: "硬件故障", 708: "账号已过期或被停用",
        720: "无法协商 PPP 参数：建议重建拨号连接", 756: "已经有一个拨号连接在进行中",
        769: "找不到网卡：网卡可能被禁用了", 797: "找不到 PPPoE 网卡",
        815: "宽带连接失败：检查网线 / 光猫 / 楼道交换机",
    }
    return "错误 %s：%s" % (code, table.get(code) or (text or "未知错误"))


# --------------------------------------------------------------------------
# 有线：PPPoE 状态
# --------------------------------------------------------------------------
def ppp_state(connection_name):
    """返回 (PPPoE 是否已连上, 它的 IPv4)。
    先试 CIM（最准），失败退回解析 ipconfig（不依赖 WMI，中文系统也适用）。"""
    if connection_name:
        try:
            ps = ("$a = Get-NetIPAddress -InterfaceAlias '%s' -AddressFamily IPv4 "
                  "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                  "if ($a) { $a.IPAddress }") % connection_name
            code, out = run_cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                timeout=30)
            for line in reversed(out.splitlines()):
                line = line.strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
                    return True, line
        except Exception:
            pass
    try:
        code, out = run_cmd(["ipconfig"], timeout=30)
        inside = False
        for line in out.splitlines():
            # 适配器标题行不带前导空格（“PPP adapter 宽带连接:” / “PPP 适配器 宽带连接:”）
            if line.strip() and not line[:1].isspace():
                inside = bool(connection_name) and (connection_name in line)
                continue
            if inside:
                m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
                if m:
                    ip = m.group(1)
                    if ip != "0.0.0.0" and not ip.startswith("255."):
                        return True, ip
    except Exception:
        pass
    return False, ""


def ppp_state_cached(connection_name, cache):
    """带缓存的 PPP 状态查询（避免每轮巡检都起一次 PowerShell）。"""
    now = time.time()
    if cache.get("ppp_t") and now - cache["ppp_t"] < 10:
        return cache.get("ppp_up", False), cache.get("ppp_ip", "")
    up, ip = ppp_state(connection_name)
    cache.update({"ppp_t": now, "ppp_up": up, "ppp_ip": ip})
    return up, ip


def ppp_dns_servers(connection_name):
    """取 PPP 网卡的 DNS（用作探测目标）。"""
    try:
        ps = ("$a = Get-DnsClientServerAddress -InterfaceAlias '%s' -AddressFamily IPv4 "
              "-ErrorAction SilentlyContinue; if ($a) { $a.ServerAddresses -join ',' }"
              ) % connection_name
        code, out = run_cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                            timeout=30)
        return [x.strip() for x in re.findall(r"\d+\.\d+\.\d+\.\d+", out)]
    except Exception:
        return []


# --------------------------------------------------------------------------
# 无线
# --------------------------------------------------------------------------
def wifi_interfaces():
    """返回无线网卡名列表。"""
    names = []
    try:
        import ctypes
        import ctypes.wintypes as wt

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                        ("Data4", ctypes.c_byte * 8)]

        class IP_ADAPTER_ADDRESSES(ctypes.Structure):
            pass

        # 简化：用 netsh 的接口名即可（避免复杂的 IP Helper 结构体）
    except Exception:
        pass
    code, out = run_cmd(["netsh", "interface", "show", "interface"], timeout=30)
    for line in out.splitlines():
        if "WLAN" in line or "无线" in line or "Wi-Fi" in line:
            parts = line.split()
            if parts:
                names.append(parts[-1])
    return names or ["WLAN"]


def wifi_connected_ssid():
    """当前连接的无线 SSID（未连接返回 ""）。"""
    code, out = run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=30)
    if code != 0:
        return ""
    ssid, state = "", ""
    for line in out.splitlines():
        if re.search(r"^\s*(SSID)\s*:", line, re.I) and "BSSID" not in line.upper():
            ssid = line.split(":", 1)[1].strip()
        if re.search(r"(State|状态)\s*:", line) and "Radio" not in line:
            state = line.split(":", 1)[1].strip().lower()
    if ssid and ("connected" in state or "已连接" in state or state == ""):
        return ssid
    return ""


def wifi_profiles():
    """已保存的无线配置名（去重）。"""
    code, out = run_cmd(["netsh", "wlan", "show", "profiles"], timeout=30)
    names = []
    for line in out.splitlines():
        low = line.lower()
        if "interface" in low or "接口" in line:
            continue
        if ":" in line or "：" in line:
            nm = re.split(r"[:：]", line, 1)[1].strip()
            if nm and nm not in names:
                names.append(nm)
    return names


def wifi_connect(ssid):
    """连接指定 SSID（需要该 SSID 已保存过）。"""
    if not ssid:
        return False, "未配置 SSID"
    code, out = run_cmd(["netsh", "wlan", "connect", "name=%s" % ssid], timeout=60)
    return code == 0, out


def apply_wifi_policy(policy):
    """off / manual（全部改成手动连接）/ disable（禁用无线网卡）。"""
    if policy == "disable":
        code, _ = run_cmd(["netsh", "interface", "set", "interface", "WLAN", "admin=disable"],
                          timeout=30)
        return (1 if code == 0 else 0), 1, ("已禁用无线网卡" if code == 0 else "禁用无线网卡失败")
    if policy != "manual":
        return 0, 0, ""
    names = wifi_profiles()
    ok = 0
    for nm in names:
        code, _ = run_cmd(["netsh", "wlan", "set", "profileparameter",
                           "name=%s" % nm, "connectionmode=manual"], timeout=30)
        if code == 0:
            ok += 1
    return ok, len(names), "已把 %d/%d 个无线配置改成手动连接" % (ok, len(names))


def wifi_profile_modes():
    """读无线配置里的连接模式（manual/auto），用于自检。"""
    modes = {}
    root = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                        r"Microsoft\Wlansvc\Profiles\Interfaces")
    try:
        import xml.etree.ElementTree as ET
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if not f.lower().endswith(".xml"):
                    continue
                try:
                    tree = ET.parse(os.path.join(dirpath, f))
                    r = tree.getroot()
                    name = r.findtext("name") or ""
                    mode = r.findtext("connectionMode") or ""
                    if name:
                        modes[name] = mode
                except Exception:
                    continue
    except Exception:
        pass
    return modes


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------
def tcp_probe(host, port, timeout=1.5, bind_ip=None):
    """能连上返回本机在连接里使用的 IP，否则 None。

    bind_ip 用来把探测**钉在校园网那条线路上** —— 否则接了别的网络（无线/别的出口）后
    可能从别处成功，让人误以为校园网已经连上。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        if bind_ip:
            sock.bind((bind_ip, 0))
        sock.connect((host, int(port)))
        return sock.getsockname()[0]
    except Exception:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


class Prober:
    """TCP 探测。ICMP 在很多校园网被封，所以不用 ping。"""

    def __init__(self, probes=None):
        self.probes = [tuple(p) for p in (probes or []) if len(p) == 2] or \
                      [tuple(p) for p in FALLBACK_PROBES]

    def check(self, bind_ip=None):
        """在线返回 (True, 本机IP, 判定依据)，离线返回 (False, "", "")。"""
        for host, port in self.probes:
            ip = tcp_probe(host, port, bind_ip=bind_ip)
            if ip:
                return True, ip, "TCP %s:%s" % (host, port)
        if bind_ip:
            return False, "", ""      # 钉住线路后不再吃“通用 DNS”兜底，避免假在线
        for name in ("www.baidu.com", "www.qq.com"):
            try:
                if socket.getaddrinfo(name, 80):
                    return True, "", "DNS %s" % name
            except Exception:
                pass
        return False, "", ""


def build_prober(cfg_campus, connection_name):
    """优先用配置里的探测目标；没配就用 PPP 网卡的 DNS + 公共兜底。"""
    probes = list(cfg_campus.get("probes") or [])
    if not probes and connection_name:
        probes = [[ip, 53] for ip in ppp_dns_servers(connection_name)[:2]]
    probes += [list(p) for p in FALLBACK_PROBES]
    uniq, seen = [], set()
    for p in probes:
        key = (str(p[0]), int(p[1]))
        if key not in seen:
            seen.add(key)
            uniq.append([key[0], key[1]])
    return Prober(uniq)


# --------------------------------------------------------------------------
# 统一判定
# --------------------------------------------------------------------------
def campus_link_state(mode, connection="", wifi_ssid="", cache=None):
    """校园网链路是否已连上（wired / wireless / both）。

    返回 (是否连上, 说明)。cache 用于避免每轮都起 PowerShell。
    """
    now = time.time()
    if cache is not None and cache.get("t") and now - cache["t"] < 10:
        return cache.get("up", False), cache.get("desc", "")

    up, desc = False, ""
    want_wired = mode in ("wired", "both")
    want_wifi = mode in ("wireless", "both")

    if want_wired and connection:
        ok, ip = ppp_state(connection)
        if ok:
            up, desc = True, "有线已连接(%s)" % ip
        elif mode == "wired":
            desc = "有线未连接"
    if not up and want_wifi and wifi_ssid:
        got = wifi_connected_ssid()
        if got and got.lower() == wifi_ssid.lower():
            up, desc = True, "无线已连接(%s)" % got
        elif mode == "wireless":
            desc = "无线未连接%s" % ("（当前：%s）" % got if got else "")

    if cache is not None:
        cache.update({"t": now, "up": up, "desc": desc})
    return up, desc
