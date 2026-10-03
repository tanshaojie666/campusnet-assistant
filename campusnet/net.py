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
import urllib.error
import urllib.parse
import urllib.request

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


def clear_stale_dials():
    """清理卡住的 rasdial 进程。

    为什么需要：只要有一个 rasdial 卡住没退出，后面每次拨号都会立刻报
    **756「已经有一个拨号连接在进行中」**，而且会一直卡下去 ——
    表现就是"校园网怎么都连不回来"。拨号前先清一遍最省事。
    """
    from .util import list_processes, pids_of, terminate_pid
    procs = list_processes() or {}
    killed = 0
    for pid in pids_of(procs, "rasdial.exe"):
        if terminate_pid(pid):
            killed += 1
    return killed


def reset_ras():
    """重启 RasMan 服务，清掉"已经有一个拨号在进行中"（756）这种卡死状态。

    756 有时根本不是进程卡住 —— 而是拨号状态卡在 RasMan 服务里
    （实测：rasdial 进程数为 0，仍然次次报 756）。
    这种情况下只有重启该服务才能恢复。
    """
    from .util import run_cmd
    ps = ("try { Restart-Service RasMan -Force -ErrorAction Stop; 'ok' } "
          "catch { 'fail' }")
    code, out = run_cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                        timeout=120)
    ok = ("ok" in (out or "")) or code == 0
    if not ok:
        run_cmd(["sc", "stop", "RasMan"], timeout=60)
        time.sleep(3)
        run_cmd(["sc", "start", "RasMan"], timeout=60)
        ok = True
    time.sleep(4)
    return ok


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
def campus_link_state(mode, connection="", wifi_ssid="", cache=None, wired_cfg=None):
    """校园网链路是否已连上（wired / wireless / both）。

    wired 的判断依据由 wired_cfg.auth 决定：
      · 配了 PPPoE（connection + auth 里有 pppoe）→ 以拨号链路为准
      · 否则（dhcp / static / portal / client / lan）→ 以有线网卡是否拿到可用地址为准
    这样插到别的路由器上不会被误判成"连上校园网"。
    """
    now = time.time()
    if cache is not None and cache.get("t") and now - cache["t"] < 10:
        return cache.get("up", False), cache.get("desc", "")

    up, desc = False, ""
    want_wired = mode in ("wired", "both")
    want_wifi = mode in ("wireless", "both")

    if want_wired:
        up, desc = wired_link_state(wired_cfg or {}, connection, cache)
    if not up and want_wifi and wifi_ssid:
        got = wifi_connected_ssid()
        if got and got.lower() == wifi_ssid.lower():
            up, desc = True, "无线已连接(%s)" % got
        elif mode == "wireless":
            desc = "无线未连接%s" % ("（当前：%s）" % got if got else "")
    if mode in ("wired", "wireless") and not desc:
        desc = "有线未连接" if mode == "wired" else "无线未连接"

    if cache is not None:
        cache.update({"t": now, "up": up, "desc": desc})
    return up, desc


# ==========================================================================
# 有线接入：除了 PPPoE 拨号，还支持 DHCP / 静态 IP / 门户认证 / 专用客户端 / 802.1X
# ==========================================================================
def wired_auth_list(wired_cfg, connection=""):
    """取有线认证方式列表；没配就按有没有拨号连接推断。"""
    auths = [str(a).lower() for a in ((wired_cfg or {}).get("auth") or [])]
    if not auths:
        auths = ["pppoe"] if connection else ["dhcp"]
    return auths


# 虚拟网卡/隧道设备的关键字：它们长得像有线网卡，但不是插网线的那块
VIRTUAL_ADAPTER_HINTS = (
    "vethernet", "virtual", "vmware", "virtualbox", "hyper-v", "loopback", "bluetooth",
    "tap-", "tap_", "wsl", "npcap", "teredo", "isatap", "6to4", "wi-fi direct",
    "microsoft wi-fi", "pseudo", "tunnel", "隧道",
)


def wired_adapters(include_virtual=False):
    """本机有线网卡 [(名字, 管理状态, 连接状态), ...]，已连接/已启用的排在前面。"""
    out_list = []
    code, out = run_cmd(["netsh", "interface", "show", "interface"], timeout=30)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        admin, state, typ = parts[0], parts[1], parts[2]
        name = " ".join(parts[3:])
        low = name.lower()
        if not include_virtual and any(k in low for k in VIRTUAL_ADAPTER_HINTS):
            continue
        wired = any(k in typ for k in ("Dedicated", "专用")) or \
            any(k in name for k in ("以太网", "Ethernet", "本地连接", "Local Area"))
        if not wired or "WLAN" in name:
            continue
        out_list.append((name, admin, state))

    def _rank(item):
        _nm, admin, state = item
        enabled = ("已启用" in admin or "Enabled" in admin)
        connected = ("已连接" in state or "Connected" in state)
        return (0 if (enabled and connected) else 1 if enabled else 2)

    out_list.sort(key=_rank)
    return out_list


def pick_wired_adapter(wired_cfg=None):
    """选一块有线网卡：配置里指定了就用它，否则挑"已启用且已连接"的第一块。"""
    name = (wired_cfg or {}).get("adapter") or ""
    if name:
        return name
    for nm, admin, state in wired_adapters():
        enabled = ("已启用" in admin or "Enabled" in admin)
        connected = ("已连接" in state or "Connected" in state)
        if enabled and connected:
            return nm
    cands = wired_adapters()
    return cands[0][0] if cands else ""


def adapter_ip(name):
    """某个网卡当前的 IPv4（取不到返回 ""）。"""
    if not name:
        return ""
    code, out = run_cmd(["ipconfig"], timeout=30)
    inside = False
    for line in out.splitlines():
        if line.strip() and not line[:1].isspace():
            inside = name in line
            continue
        if inside:
            m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
            if m:
                ip = m.group(1)
                if ip != "0.0.0.0" and not ip.startswith("255."):
                    return ip
    return ""


def adapter_state(name):
    """(是否启用, 是否已连接)。"""
    for nm, admin, state in wired_adapters():
        if nm == name:
            return ("已启用" in admin or "Enabled" in admin), ("已连接" in state or "Connected" in state)
    return True, False


def set_adapter_dhcp(name, dns_auto=True):
    """设为自动获取 IP（DHCP）。"""
    ok = run_cmd(["netsh", "interface", "ipv4", "set", "address",
                  "name=%s" % name, "source=dhcp"], timeout=60)[0] == 0
    if dns_auto:
        run_cmd(["netsh", "interface", "ipv4", "set", "dnsservers",
                 "name=%s" % name, "source=dhcp"], timeout=60)
    return ok


def set_adapter_static(name, address, mask="255.255.255.0", gateway="", dns=None):
    """设为静态 IP。"""
    if not address:
        return False
    args = ["netsh", "interface", "ipv4", "set", "address", "name=%s" % name,
            "static", address, mask]
    if gateway:
        args.append(gateway)
    ok = run_cmd(args, timeout=60)[0] == 0
    dns = [d for d in (dns or []) if d]
    if dns:
        run_cmd(["netsh", "interface", "ipv4", "set", "dnsservers", "name=%s" % name,
                 "static", dns[0], "primary"], timeout=60)
        for i, server in enumerate(dns[1:], start=2):
            run_cmd(["netsh", "interface", "ipv4", "add", "dnsservers", "name=%s" % name,
                     server, "index=%d" % i], timeout=60)
    return ok


def set_adapter_disabled(name, disabled=True):
    """启用/禁用网卡（需要管理员权限）。

    为什么"断开校园网"要靠它：PPPoE 拨号是"骑"在物理网卡上的，
    而这个拨号连接常常不属于任何可枚举的 RAS 会话 ——
    `rasdial /disconnect` 会返回成功却什么都不做（实测就是这样：返回码 0，
    但 PPP 适配器还挂着 IP）。禁用它所依附的网卡则 PPP 链路必定断开，
    而手机热点 / 无线网不受影响。
    """
    if not name:
        return False
    state = "admin=disable" if disabled else "admin=enable"
    return run_cmd(["netsh", "interface", "set", "interface", "name=%s" % name, state],
                   timeout=60)[0] == 0


def adapter_enabled(name):
    """网卡当前是否处于"已启用"状态。"""
    for nm, admin, _state in wired_adapters(include_virtual=True):
        if nm == name:
            return ("已启用" in admin or "Enabled" in admin)
    return True


def disabled_wired_adapters():
    """当前处于"已禁用"状态的**物理**有线网卡名字列表。

    用途：为了断开校园网而被禁用的网卡，我们必须能恢复回来。
    如果"禁用了哪块"的记录丢了（实测发生过），就靠它兜底 ——
    否则会出现：网卡一直禁用 → 拨号一直报 756 → 校园网再也连不回来。
    """
    out = []
    for nm, admin, _state in wired_adapters():
        if not ("已启用" in admin or "Enabled" in admin):
            out.append(nm)
    return out


def enable_disabled_wired_adapters(log=None):
    """把所有被禁用的物理有线网卡重新启用，返回启用的名字列表。"""
    done = []
    for nm in disabled_wired_adapters():
        if set_adapter_disabled(nm, False):
            done.append(nm)
            if log:
                log("已重新启用有线网卡「%s」" % nm)
    return done


def restart_adapter(name):
    """重新启用网卡（拔插网线的软件等价操作），需要管理员权限。"""
    if not name:
        return False
    set_adapter_disabled(name, True)
    time.sleep(3)
    return set_adapter_disabled(name, False)


# --------------------------------------------------------------------------
# 门户认证（Captive Portal）
# --------------------------------------------------------------------------
# 这些地址会返回 204（无内容）。被门户劫持时，会返回 302 跳到登录页。
PORTAL_PROBE_URLS = [
    "http://connect.rom.miui.com/generate_204",
    "http://www.gstatic.com/generate_204",
    "http://connectivitycheck.platform.hicloud.com/generate_204",
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def portal_probe(url=None, timeout=6):
    """探测是否被门户劫持。

    返回 (是否已放行, 门户地址)：
      · 收到 204            → 已放行，门户地址为空
      · 收到 30x 且有跳转    → 未放行，返回跳转地址（= 登录页）
    """
    for probe in ([url] if url else PORTAL_PROBE_URLS):
        try:
            req = urllib.request.Request(probe, headers={"User-Agent": "Mozilla/5.0"})
            with _opener().open(req, timeout=timeout) as resp:
                if resp.status in (204, 200):
                    body = ""
                    try:
                        body = resp.read(2048).decode("utf-8", "replace")
                    except Exception:
                        pass
                    if resp.status == 204 or len(body) < 16:
                        return True, ""
                    return False, probe          # 200 但有内容 → 多半被门户替换了
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                return False, exc.headers.get("Location", "") or probe
            continue
        except Exception:
            continue
    return False, ""


def fill_template(text, username, password):
    """把模板里的占位符换成账号密码。"""
    out = text or ""
    for key in ("{username}", "{user}", "{account}", "{学号}", "{name}"):
        out = out.replace(key, username or "")
    for key in ("{password}", "{pass}", "{pwd}", "{密码}"):
        out = out.replace(key, password or "")
    return out


def parse_login_form(html):
    """从登录页里找出第一个含密码框的表单 → (action, method, {字段: 值})。"""
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html or "", re.I | re.S):
        attrs, body = m.group(1), m.group(2)
        has_pwd, fields = False, {}
        for im in re.finditer(r"<input\b([^>]*)>", body, re.I):
            a = im.group(1)
            nm = re.search(r'name\s*=\s*["\']?([^"\'\s>]+)', a, re.I)
            if not nm:
                continue
            val = re.search(r'value\s*=\s*["\']([^"\']*)["\']', a, re.I)
            typ = re.search(r'type\s*=\s*["\']?([^"\'\s>]+)', a, re.I)
            fields[nm.group(1)] = val.group(1) if val else ""
            if typ and typ.group(1).lower() == "password":
                has_pwd = True
        if has_pwd and fields:
            act = re.search(r'action\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
            meth = re.search(r'method\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
            return (act.group(1) if act else ""), \
                (meth.group(1).lower() if meth else "post"), fields
    return "", "", {}


# --------------------------------------------------------------------------
# 门户厂商预设：一键填好常见的字段名，剩下只要把地址换成你们学校门户的地址
# 注意：字段名各校可能不同，预设只是省去敲一遍，仍建议按 docs/WIRED.md
#       第四节的方法用 F12 抓一次真实登录请求核对。
# --------------------------------------------------------------------------
PORTAL_PRESETS = {
    "auto": {
        "label": "自动（先试这个）",
        "note": "自动打开登录页、找表单、填账号密码并提交。适合页面是普通 HTML 表单的门户。",
        "cfg": {"mode": "auto"},
    },
    "sangfor": {
        "label": "深信服 Sangfor",
        "note": "常见接口 /ac_portal/login.php。"
                "注意 auth_tag 常是每次不同的一次性令牌，"
                "固定模板可能不成功，这种情况请改用 script 先取令牌再提交。",
        "cfg": {"mode": "template", "method": "post",
                "url": "http://门户地址/ac_portal/login.php",
                "body": "opr=pwdLogin&userName={username}&pwd={password}&auth_tag=",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    },
    "ruijie": {
        "label": "锐捷 Ruijie",
        "note": "常见接口 /eportal/InterFace.do?method=login。"
                "service 和 queryString 通常要从登录页里取，可能需要用 script。",
        "cfg": {"mode": "template", "method": "post",
                "url": "http://门户地址/eportal/InterFace.do?method=login",
                "body": "userId={username}&password={password}&service=&queryString=",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    },
    "drcom": {
        "label": "城市热点 Dr.COM",
        "note": "各校自建地址差异很大（有的用 GET 带 0/1 参数）。"
                "先用自动；不行就照抓到的请求改地址和字段名。",
        "cfg": {"mode": "template", "method": "post",
                "url": "http://门户地址/drcom/login",
                "body": "username={username}&password={password}",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    },
    "h3c": {
        "label": "H3C",
        "note": "常见接口 /portal/login，字段 userid / passwd。",
        "cfg": {"mode": "template", "method": "post",
                "url": "http://门户地址/portal/login",
                "body": "userid={username}&passwd={password}",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
    },
}


def apply_portal_preset(portal_cfg, preset):
    """把厂商预设套到门户配置上（保留用户已填的账号密码）。"""
    item = PORTAL_PRESETS.get(str(preset or "").lower())
    out = dict(portal_cfg or {})
    if not item:
        return out, "未知预设"
    keep = {k: out.get(k) for k in ("username", "password_enc", "password_machine",
                                    "probe_url") if out.get(k)}
    out.update(item["cfg"])
    out.update(keep)
    return out, item["note"]


def portal_login(portal_cfg, username, password, log=None):
    """门户认证，三种方式：

      auto     —— 自动打开登录页、找出表单、填账号密码并提交（适合简单的门户）
      template —— 用自己填的请求模板提交（适合深信服 / 锐捷 / Dr.COM 这类）
      script   —— 直接跑一条命令（学校给了脚本或命令行工具时最省事）

    返回 (是否成功, 说明)。
    """
    def _log(msg):
        if log:
            log(msg)

    portal_cfg = portal_cfg or {}
    mode = str(portal_cfg.get("mode") or "auto").lower()

    online, page = portal_probe(portal_cfg.get("probe_url") or None)
    if online:
        return True, "门户已放行（探测通过）"
    if not page:
        page = portal_cfg.get("url") or ""
    _log("  检测到门户登录页：%s" % (page or "(没拿到)"))

    if mode == "script":
        cmd = portal_cfg.get("script") or ""
        if not cmd:
            return False, "没有配置脚本"
        cmd = fill_template(cmd, username, password)
        _log("  执行脚本：%s" % cmd[:80])
        code, out = run_cmd(cmd, timeout=120) if isinstance(cmd, str) else (1, "脚本格式不对")
        _log("  脚本退出码 %s %s" % (code, (out or "")[:120]))
        ok, _ = portal_probe(portal_cfg.get("probe_url") or None)
        return ok, ("脚本执行完毕，门户已放行" if ok else "脚本执行了但门户没放行")

    if mode == "template":
        url = portal_cfg.get("url") or page
        method = str(portal_cfg.get("method") or "post").lower()
        body = fill_template(portal_cfg.get("body") or
                             "username={username}&password={password}", username, password)
        headers = portal_cfg.get("headers") or {"Content-Type":
                                                "application/x-www-form-urlencoded"}
        if not url:
            return False, "没有配置门户地址"
        try:
            if method == "get":
                req = urllib.request.Request(url + ("&" if "?" in url else "?") + body,
                                             headers=headers)
            else:
                req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
            with _opener().open(req, timeout=20) as resp:
                text = resp.read(4096).decode("utf-8", "replace")
            _log("  门户返回：%s" % text[:120].replace("\n", " "))
        except Exception as exc:  # noqa: BLE001
            _log("  门户请求出错：%s" % exc)
            return False, "门户请求失败：%s" % exc
        time.sleep(2)
        ok, _ = portal_probe(portal_cfg.get("probe_url") or None)
        return ok, ("门户认证成功" if ok else "提交了，但还没放行（检查字段名/地址）")

    # ---- auto：自动填表 ----
    try:
        req = urllib.request.Request(page, headers={"User-Agent": "Mozilla/5.0"})
        with _opener().open(req, timeout=20) as resp:
            html = resp.read(200000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return False, "打不开登录页：%s" % exc
    action, method, fields = parse_login_form(html)
    if not fields:
        return False, "登录页里没找到可填的表单（多半是 JS 动态生成，请改用 template 或 script）"
    action = action or page
    if action.startswith("/") and page:
        base = urllib.parse.urlsplit(page)
        action = "%s://%s%s" % (base.scheme, base.netloc, action)
    for name in list(fields):
        low = name.lower()
        if any(k in low for k in ("user", "name", "account", "login", "学号", "账号")):
            if "pass" not in low:
                fields[name] = username or fields[name]
        elif any(k in low for k in ("pass", "pwd", "密码")):
            fields[name] = password or fields[name]
        elif low in ("0", "1", "domain", "domainname"):
            fields[name] = fields[name]
    _log("  表单字段：%s" % "、".join(fields.keys()))
    body = urllib.parse.urlencode(fields)
    try:
        if method == "get":
            url = action + ("&" if "?" in action else "?") + body
            with _opener().open(url, timeout=20) as resp:
                resp.read(4096)
        else:
            req = urllib.request.Request(action, data=body.encode("utf-8"),
                                         headers={"Content-Type":
                                                  "application/x-www-form-urlencoded",
                                                  "User-Agent": "Mozilla/5.0"})
            with _opener().open(req, timeout=20) as resp:
                resp.read(4096)
    except Exception as exc:  # noqa: BLE001
        _log("  提交表单出错：%s" % exc)
        return False, "提交失败：%s" % exc
    time.sleep(2)
    ok, _ = portal_probe(portal_cfg.get("probe_url") or None)
    return ok, ("自动填表认证成功" if ok else "自动提交了，但还没放行（多半需要 template/script）")


# --------------------------------------------------------------------------
# 802.1X（有线）
# --------------------------------------------------------------------------
def lan_8021x_interfaces():
    code, out = run_cmd(["netsh", "lan", "show", "interfaces"], timeout=30)
    conn = "已连接" in out or "Connected" in out or "已身份验证" in out
    return conn, out


def lan_8021x_connect(profile, interface=""):
    """连接一个已保存的有线 802.1X 配置（配置需要先在系统里建好）。"""
    if not profile:
        return False, "没有配置 802.1X 配置名"
    args = ["netsh", "lan", "connect", "name=%s" % profile]
    if interface:
        args.append("interface=%s" % interface)
    code, out = run_cmd(args, timeout=90)
    return code == 0, out


# --------------------------------------------------------------------------
# 有线接入总流程（非 PPPoE 部分）
# --------------------------------------------------------------------------
def wired_authenticate(wired_cfg, connection, username, password, log=None):
    """按配置依次执行：网卡/DHCP/静态IP → 门户认证 → 学校客户端 → 802.1X。

    PPPoE 由上层单独处理（它需要账号密码且有专门的错误码）。
    返回 (是否已连上, 说明)。
    """
    def _log(msg):
        if log:
            log(msg)

    wired_cfg = wired_cfg or {}
    auths = wired_auth_list(wired_cfg, connection)
    adapter = pick_wired_adapter(wired_cfg)
    if adapter:
        _log("  有线网卡：%s" % adapter)

    if "dhcp" in auths:
        ip = adapter_ip(adapter) if adapter else ""
        if not ip or ip.startswith("169.254."):
            _log("  设置为自动获取 IP（DHCP）…")
            set_adapter_dhcp(adapter)
            time.sleep(6)
        else:
            _log("  已自动获取地址：%s" % ip)

    if "static" in auths:
        st = wired_cfg.get("static") or {}
        if st.get("address"):
            cur = adapter_ip(adapter) if adapter else ""
            if cur != st.get("address"):
                _log("  设置静态 IP %s …" % st.get("address"))
                set_adapter_static(adapter, st.get("address"), st.get("mask") or "255.255.255.0",
                                   st.get("gateway") or "", st.get("dns") or [])
                time.sleep(4)

    if "restart" in auths and adapter:
        _log("  重新启用网卡（相当于拔插网线）…")
        restart_adapter(adapter)
        time.sleep(8)

    if "client" in auths:
        exe = wired_cfg.get("client_exe") or ""
        if exe and os.path.isfile(exe):
            from .util import create_no_window_flag, list_processes
            name = os.path.basename(exe).lower()
            if not (list_processes() or {}).get(name):
                _log("  启动学校认证客户端：%s" % os.path.basename(exe))
                import subprocess
                try:
                    subprocess.Popen([exe], close_fds=True,
                                     creationflags=create_no_window_flag())
                except Exception as exc:  # noqa: BLE001
                    _log("  客户端启动失败：%s" % exc)
                time.sleep(10)
        elif exe:
            _log("  找不到学校客户端：%s" % exe)

    if "lan" in auths:
        profile = wired_cfg.get("lan_profile") or ""
        ok, out = lan_8021x_connect(profile, adapter)
        _log("  802.1X 连接%s" % ("已发起" if ok else "失败"))
        time.sleep(6)

    if "portal" in auths:
        _log("  进行门户认证 …")
        ok, msg = portal_login(wired_cfg.get("portal") or {}, username, password, _log)
        _log("  " + msg)
        if ok:
            return True, msg

    up, desc = wired_link_state(wired_cfg, connection)
    return up, desc


def wired_bind_ip(wired_cfg):
    """有线网卡当前 IP —— 用来把探测钉在这条线路上，避免别的网络造成误判。"""
    adapter = pick_wired_adapter(wired_cfg)
    ip = adapter_ip(adapter) if adapter else ""
    return "" if ip.startswith("169.254.") else ip


def wired_link_state(wired_cfg, connection="", cache=None):
    """有线链路状态（PPPoE 以拨号链路为准，其余以网卡地址为准）。"""
    wired_cfg = wired_cfg or {}
    auths = wired_auth_list(wired_cfg, connection)

    if connection and "pppoe" in auths:
        up, ip = ppp_state_cached(connection, cache if cache is not None else {})
        return (True, "有线PPPoE已连接(%s)" % ip) if up else (False, "有线PPPoE未连接")

    adapter = pick_wired_adapter(wired_cfg)
    ip = adapter_ip(adapter) if adapter else ""
    if ip and not ip.startswith("169.254."):
        return True, "有线已获取地址(%s %s)" % (adapter, ip)
    return False, "有线未获取地址%s" % ("（%s）" % adapter if adapter else "")

