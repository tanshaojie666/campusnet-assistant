# -*- coding: utf-8 -*-
"""配置与默认值。

- 用户配置：%LOCALAPPDATA%\\CampusNetAssistant\\config.json
- 实时规则：%LOCALAPPDATA%\\CampusNetAssistant\\rules.json（界面改开关立即生效，不用提权）
- 系统级配置：%ProgramData%\\CampusNetAssistant\\config.json（机器范围加密，SYSTEM 才能解）
"""
from __future__ import annotations

import os
import time

from .util import dpapi_decrypt, dpapi_encrypt, json_dump, json_load

APP_ID = "CampusNetAssistant"
APP_NAME = "校园网助手"
APP_TITLE = "CampusNetAssistant"

HOME_DIR = os.environ.get("CNA_HOME") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_ID)
CONFIG_FILE = os.path.join(HOME_DIR, "config.json")
RULES_FILE = os.path.join(HOME_DIR, "rules.json")
LOG_FILE = os.path.join(HOME_DIR, "运行日志.txt")
PAUSE_FILE = os.path.join(HOME_DIR, "pause.until")
SHOW_REQUEST = os.path.join(HOME_DIR, "show.request")

PROGRAM_DATA = os.environ.get("ProgramData") or r"C:\ProgramData"
BOOT_DIR = os.environ.get("CNA_BOOT_DIR") or os.path.join(PROGRAM_DATA, APP_ID)
BOOT_CONFIG = os.path.join(BOOT_DIR, "config.json")
BOOT_LOG = os.path.join(BOOT_DIR, "开机拨号日志.txt")
BOOT_HEARTBEAT = os.path.join(BOOT_DIR, "heartbeat.txt")
BOOT_STOP = os.path.join(BOOT_DIR, "stop.flag")
INSTALL_LOG = os.path.join(BOOT_DIR, "安装日志.txt")
BOOT_TASK = "CampusNetAssistant-Boot"
HEARTBEAT_FRESH = 90          # 心跳多少秒内算“守护在跑”

# 内置客户端定义：每项给多个候选路径，程序会自动挑存在的那个；
# 路径里可以用 %APPDATA% / %LOCALAPPDATA% 这类环境变量。全部都能在配置里改。
_LOCAL = r"%LOCALAPPDATA%\Programs"
DEFAULT_CLIENTS = [
    {"id": "eix", "name": "E-IX", "kind": "mihomo",
     "gui_paths": [r"E:\E-IX\eix_client.exe", r"D:\E-IX\eix_client.exe",
                    _LOCAL + r"\E-IX\eix_client.exe",
                    r"C:\Program Files\E-IX\eix_client.exe"],
     "core_paths": [r"E:\E-IX\mihomo.exe", r"D:\E-IX\mihomo.exe"],
     "core_dirs": [r"%APPDATA%\usfoo\E-IX", r"%APPDATA%\E-IX", r"%APPDATA%\eix"],
     "core_config": "profile.yaml",
     "controller": "127.0.0.1:9090",
     "kill": ["eix_client.exe", "mihomo.exe"]},
    {"id": "mojie", "name": "魔戒", "kind": "mihomo",
     "gui_paths": [r"E:\mojie\魔戒.exe", r"D:\mojie\魔戒.exe"],
     "core_paths": [r"E:\mojie\resources\static\clash\mojie-windows-amd64.exe",
                    r"D:\mojie\resources\static\clash\mojie-windows-amd64.exe"],
     "core_dirs": [r"%APPDATA%\mojie"],
     "core_config": "config.yaml",
     "controller": "127.0.0.1:18606",
     "kill": ["魔戒.exe", "mojie-service.exe", "mojie-windows-amd64.exe"]},
    {"id": "flclash", "name": "FlClash", "kind": "mihomo",
     "gui_paths": [r"E:\FlClash\FlClash.exe", r"D:\FlClash\FlClash.exe",
                   _LOCAL + r"\FlClash\FlClash.exe"],
     "core_paths": [], "core_dirs": [], "core_config": "",
     "controller": "",
     "kill": ["flclash.exe", "flclashcore.exe", "flclashhelperservice.exe"]},
    {"id": "flyingbird", "name": "FlyingBird", "kind": "mihomo",
     "gui_paths": [r"E:\FlyingBird\FlyingBird.exe", r"D:\FlyingBird\FlyingBird.exe",
                   _LOCAL + r"\FlyingBird\FlyingBird.exe"],
     "core_paths": [], "core_dirs": [], "core_config": "",
     "controller": "",
     "kill": ["flyingbird.exe", "flyingbirdcore.exe", "flyingbirdhelperservice.exe"]},
    {"id": "wandacloud", "name": "万达云", "kind": "mihomo",
     "gui_paths": [r"E:\wandacloud\wandacloud.exe", r"D:\wandacloud\wandacloud.exe",
                   _LOCAL + r"\wandacloud\wandacloud.exe"],
     "core_paths": [], "core_dirs": [], "core_config": "",
     "controller": "",
     "kill": ["wandacloud.exe", "wandacloudcore.exe", "wandacloudhelperservice.exe"]},
    # 通用兜底：Clash Verge / Mihomo Party 这类，路径对不上时在配置里改一下即可
    {"id": "clash-verge", "name": "Clash Verge", "kind": "mihomo",
     "gui_paths": [_LOCAL + r"\Clash Verge\Clash Verge.exe",
                   r"C:\Program Files\Clash Verge\Clash Verge.exe"],
     "core_paths": [], "core_dirs": [], "core_config": "",
     "controller": "",
     "kill": ["clash-verge.exe", "verge-mihomo.exe", "clash-verge-service.exe"]},
]

DEFAULT_CONFIG = {
    # 程序自身的开关（都在界面「功能开关」里能改）
    "app": {
        "auto_update": True,      # 程序文件更新后自动重启生效
        "tray": True,             # 点关闭只是收进托盘，程序继续后台跑
        "single_instance": True,  # 同一时间只允许开一个
    },
    "campus": {
        "mode": "wired",                 # wired=有线 / wireless=无线 / both
        "connection": "",                # PPPoE 连接名，留空自动探测
        "wifi_ssid": "",                 # 无线 SSID（wireless/both 时使用）
        "account": "",
        "password_enc": "",
        "interval": 10,
        "probes": [],                    # 探测目标 [[host, port], ...]，留空自动用 PPP 的 DNS
        # Web 门户认证（有线、无线共用；很多学校的 Wi-Fi 连上后也要过网页认证）
        "portal": {
            "mode": "auto",              # auto=自动填表 / template=按模板提交 / script=跑命令
            "url": "",                   # 门户地址（留空自动从跳转里取）
            "method": "post",            # post / get
            "body": "",                  # 提交内容模板，支持 {username}/{password}
            "headers": {},               # 自定义请求头
            "probe_url": "",             # 判断是否已放行的地址（留空用内置的）
            "script": "",                # mode=script 时执行的命令
            "username": "",              # 门户账号（留空用校园网账号）
            "password_enc": "",          # 门户密码（留空用校园网密码）
            "preset": "",                # 上次选的厂商预设（仅界面用，便于回显）
        },
        # 有线接入方式：可多选，按这个顺序执行
        #   pppoe   PPPoE 拨号（宿舍网口最常见）
        #   dhcp    自动获取 IP（插上网线就有 IP）
        #   static  静态 IP（学校分配固定 IP/网关/DNS）
        #   portal  Web 门户认证（深信服 / 锐捷 / Dr.COM 这类登录页）
        #   client  学校专用认证客户端
        #   lan     有线 802.1X（Windows 自带）
        #   restart 先把网卡重启一遍（拔插网线的软件版，网口卡住时有用）
        "wired": {
            "auth": [],                  # 留空自动推断：配了 connection → ["pppoe"]，否则 ["dhcp"]
            "adapter": "",               # 有线网卡名，留空自动选第一块
            "static": {"address": "", "mask": "255.255.255.0", "gateway": "", "dns": []},
            "client_exe": "",            # 学校认证客户端路径
            "lan_profile": "",           # 已保存的 802.1X 配置名
            "portal": {
                "mode": "auto",          # auto=自动填表 / template=按模板提交 / script=跑命令
                "url": "",               # 门户地址（留空自动从跳转里取）
                "method": "post",        # post / get
                "body": "",              # 提交内容模板，支持 {username}/{password}
                "headers": {},           # 自定义请求头
                "probe_url": "",         # 判断是否已放行的地址（留空用内置的）
                "script": "",            # mode=script 时执行的命令
                "username": "",          # 门户账号（留空用拨号账号）
                "password_enc": "",      # 门户密码（留空用拨号密码）
            },
        },
    },
    "guard": {
        "kill_proxies": True,            # 连上校园网就关掉下面的进程
        # 「自动连校园网」总开关：关掉后只监视不拨号（想自己手动控制时用）
        "auto_dial": True,
        # 拨号前是否也先关掉代理/VPN（会抢路由、拦 DNS，导致拨号慢甚至拨不上）
        "kill_before_dial": True,
        "kill_processes": [
            "flclash.exe", "flclashcore.exe", "flclashhelperservice.exe",
            "eix_client.exe", "mihomo.exe",
            "flyingbird.exe", "flyingbirdcore.exe", "flyingbirdhelperservice.exe",
            "wandacloud.exe", "wandacloudcore.exe", "wandacloudhelperservice.exe",
            "魔戒.exe", "mojie-service.exe", "mojie-windows-amd64.exe",
        ],
        "wifi_policy": "off",            # off / manual=全部改手动连接 / disable=禁用网卡
        # 「断开校园网」之后，如果一直没在使用代理（没在翻墙），就自动把校园网连回来。
        # 典型场景：为了翻墙而断开校园网 → 翻完墙 / 根本没翻 → 自动恢复校园网。
        # 默认关闭：手动断开就保持断开，直到点「立即连接」。打开后断开就真的不用管。
        "reconnect_when_no_flip": False,
        "reconnect_after": 180,          # 断开多久、且确认没在用代理之后才恢复（秒）
        # 「跟着 VPN 走」：以"有没有在用代理"为准自动切网络，保证随时都有网 ——
        #   VPN 在跑 → 断开校园网（校园网里翻墙会认证失败）+ 连上下面的无线（手机热点）
        #   VPN 一停 → 立刻把校园网连回来
        # 这是最省心的一种用法：你只管开/关 VPN，网络自己跟着换。
        "follow_vpn": False,
        "vpn_hotspot_ssid": "",          # 翻墙时用哪个无线（填手机热点名；留空=不主动连）
        "vpn_switch_delay": 10,          # VPN 关掉后多少秒切回校园网（越小越"立刻"）
        # **安全闸门**：断开校园网之前必须先确认"有别的能上网的路"（手机热点等）。
        # 没有就什么都不做 —— 否则一断校园网就彻底没网了。
        "require_other_network": True,
    },
    "flip": {
        "enabled": False,                # 翻墙模式：打开指定程序就开 VPN
        # 什么时候允许翻墙：
        #   off_campus = 只在没连校园网时（默认，避免代理干扰校园网认证）
        #   always     = 任何时候都翻墙，连着校园网也开（认证完成后再开代理）
        "when": "off_campus",
        "order": ["eix", "mojie"],       # 勾选允许使用的客户端（只有勾选的 id 写在这里）
        "apps": ["telegram.exe", "codex.exe", "chrome.exe"],
        "region_hints": ["美国", "united states", "america", "🇺🇸", "los angeles", "san jose",
                         "seattle", "dallas", "new york", "chicago", "miami", "usla",
                         "us-", "us_", "-us"],
        # 浏览器靠"窗口标题里出现这些关键词"判断（避免一开浏览器就翻墙）
        "title_hints": ["chatgpt", "openai", "claude", "gemini", "perplexity", "sora",
                        "google", "youtube", "twitter", "x.com", "facebook", "instagram",
                        "wikipedia", "reddit", "discord", "github", "notion", "medium",
                        "stackoverflow", "stack overflow", "huggingface", "copilot",
                        "bing.com", "duckduckgo"],
        "auto_close": False,             # 触发程序全部退出后是否自动关掉客户端
        # 浏览器一打开就翻墙（不靠窗口标题判断）。
        # 最可靠：不受标题语言、报错页、新标签页影响；代价是一开浏览器就开代理。
        "browser_always": False,
    },
    "clients": DEFAULT_CLIENTS,
    "ui": {"minimize_to_tray": True},
}


def expand(path: str) -> str:
    """展开 %APPDATA% 这类环境变量。"""
    return os.path.expandvars(path) if path else ""


# --------------------------------------------------------------------------
# 用户配置
# --------------------------------------------------------------------------
def _deep_merge(defaults, user):
    """把用户配置合并到默认值上：嵌套字典逐层合并，新增字段不会把用户设置整段顶掉。"""
    out = dict(defaults)
    if not isinstance(user, dict):
        return out
    for key, value in user.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    cfg = json_load(CONFIG_FILE, None)
    if not isinstance(cfg, dict):
        cfg = {}
    return _deep_merge(DEFAULT_CONFIG, cfg)


def save_config(cfg):
    json_dump(CONFIG_FILE, cfg)
    return True


def get_password(cfg) -> str:
    return dpapi_decrypt((cfg.get("campus") or {}).get("password_enc", ""))


def set_account(cfg, account: str, password: str):
    cfg.setdefault("campus", {})
    cfg["campus"]["account"] = account
    cfg["campus"]["password_enc"] = dpapi_encrypt(password) if password else ""
    save_config(cfg)


# --------------------------------------------------------------------------
# 实时规则（界面改，守护立即生效，不需要提权）
# --------------------------------------------------------------------------
def rules_path(cfg=None) -> str:
    base = HOME_DIR
    if cfg and cfg.get("user_rules"):
        base = os.path.dirname(cfg["user_rules"])
    return os.path.join(base, "rules.json")


def load_rules(cfg=None):
    d = {"kill_proxies": True, "processes": list(DEFAULT_CONFIG["guard"]["kill_processes"]),
         "wifi_policy": "off", "reconnect_when_no_flip": False, "follow_vpn": False,
         "auto_dial": True, "kill_before_dial": True,
         "vpn_hotspot_ssid": "", "require_other_network": True,
         "flip": dict(DEFAULT_CONFIG["flip"])}
    data = json_load(rules_path(cfg) if cfg else RULES_FILE, None)
    if isinstance(data, dict):
        if "enabled" in data:                 # 兼容旧字段名
            d["kill_proxies"] = bool(data["enabled"])
        if "kill_proxies" in data:
            d["kill_proxies"] = bool(data["kill_proxies"])
        if "reconnect_when_no_flip" in data:
            d["reconnect_when_no_flip"] = bool(data["reconnect_when_no_flip"])
        if "follow_vpn" in data:
            d["follow_vpn"] = bool(data["follow_vpn"])
        if "auto_dial" in data:
            d["auto_dial"] = bool(data["auto_dial"])
        if "kill_before_dial" in data:
            d["kill_before_dial"] = bool(data["kill_before_dial"])
        if "vpn_hotspot_ssid" in data:
            d["vpn_hotspot_ssid"] = str(data["vpn_hotspot_ssid"] or "")
        if "require_other_network" in data:
            d["require_other_network"] = bool(data["require_other_network"])
        if data.get("processes"):
            d["processes"] = [str(x).lower() for x in data["processes"]]
        if data.get("wifi_policy") in ("off", "manual", "disable"):
            d["wifi_policy"] = data["wifi_policy"]
        if isinstance(data.get("flip"), dict):
            d["flip"].update(data["flip"])
        elif isinstance(data.get("flip_mode"), dict):   # 兼容旧字段名
            d["flip"].update(data["flip_mode"])
    return d


def save_rules(rules):
    data = json_load(RULES_FILE, None) or {}
    data["kill_proxies"] = bool(rules.get("kill_proxies", True))
    data["processes"] = [str(x).lower() for x in rules.get("processes", [])]
    data["wifi_policy"] = rules.get("wifi_policy", "off")
    data["reconnect_when_no_flip"] = bool(rules.get("reconnect_when_no_flip", False))
    data["auto_dial"] = bool(rules.get("auto_dial", True))
    data["kill_before_dial"] = bool(rules.get("kill_before_dial", True))
    data["follow_vpn"] = bool(rules.get("follow_vpn", False))
    data["vpn_hotspot_ssid"] = str(rules.get("vpn_hotspot_ssid") or "")
    data["require_other_network"] = bool(rules.get("require_other_network", True))
    data["flip"] = dict(rules.get("flip", {}))
    json_dump(RULES_FILE, data)
    return True


# --------------------------------------------------------------------------
# 系统级配置（--install-boot 写入，SYSTEM 读取）
# --------------------------------------------------------------------------
def load_boot_config():
    data = json_load(BOOT_CONFIG, None)
    return data if isinstance(data, dict) else None


def save_boot_config(data):
    json_dump(BOOT_CONFIG, data)
    return True


def campus_portal(cfg):
    """取门户认证配置（有线无线共用的那一份）。

    兼容早期版本：那时门户设置存在 campus.wired.portal 里，
    如果新的 campus.portal 是空的、而旧位置有内容，就沿用旧位置。
    """
    campus = cfg.get("campus") or {}
    new = dict(campus.get("portal") or {})
    old = dict((campus.get("wired") or {}).get("portal") or {})
    if not any(new.get(k) for k in ("url", "script", "username", "password_enc", "body")):
        merged = dict(new)
        merged.update({k: v for k, v in old.items() if v})
        return merged
    return new


def portal_credentials(cfg):
    """门户认证用的账号密码；没单独配就沿用校园网账号。"""
    campus = cfg.get("campus") or {}
    portal = campus_portal(cfg)
    user = portal.get("username") or campus.get("account") or ""
    enc = portal.get("password_enc") or campus.get("password_enc") or ""
    return user, (dpapi_decrypt(enc) if enc else "")


def build_boot_config(cfg) -> dict:
    """把用户配置转成系统级守护要用的那份（含机器范围加密的密码）。"""
    campus = cfg.get("campus") or {}
    guard = cfg.get("guard") or {}
    wired = dict(campus.get("wired") or {})
    portal = dict(campus_portal(cfg))
    p_user, p_pwd = portal_credentials(cfg)
    portal["username"] = p_user
    portal.pop("password_enc", None)                  # 系统级配置里不带用户范围密文
    portal["password_machine"] = dpapi_encrypt(p_pwd, machine=True) if p_pwd else ""
    wired["portal"] = portal
    return {
        "mode": campus.get("mode", "wired"),
        "connection": campus.get("connection", ""),
        "wifi_ssid": campus.get("wifi_ssid", ""),
        "account": campus.get("account", ""),
        "password_machine": dpapi_encrypt(get_password(cfg), machine=True),
        "wired": wired,
        "portal": portal,
        "interval": int(campus.get("interval") or 10),
        "probes": campus.get("probes") or [],
        "kill_proxies": bool(guard.get("kill_proxies", True)),
        "processes": list(guard.get("kill_processes") or []),
        "wifi_policy": guard.get("wifi_policy", "off"),
        "user_rules": RULES_FILE,
        "pause_file": PAUSE_FILE,
        "phonebook": os.path.join(os.environ.get("APPDATA", ""),
                                  r"Microsoft\Network\Connections\Pbk\rasphone.pbk"),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "installed_by": os.environ.get("USERNAME", ""),
    }


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def boot_log(text):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    try:
        os.makedirs(BOOT_DIR, exist_ok=True)
        if os.path.isfile(BOOT_LOG) and os.path.getsize(BOOT_LOG) > 1024 * 1024:
            os.replace(BOOT_LOG, BOOT_LOG + ".old")
        with open(BOOT_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    return line


def boot_heartbeat(online, ip, reason, campus=""):
    try:
        os.makedirs(BOOT_DIR, exist_ok=True)
        with open(BOOT_HEARTBEAT, "w", encoding="utf-8") as fh:
            fh.write("%.0f\t%s\t%s\t%s\t%s\n" % (
                time.time(), "在线" if online else "离线", ip or "", reason or "", campus or ""))
    except Exception:
        pass


def heartbeat_state():
    """返回 (守护是否在跑, 心跳描述)。"""
    try:
        with open(BOOT_HEARTBEAT, "r", encoding="utf-8-sig") as fh:
            parts = fh.read().strip().split("\t")
        if time.time() - float(parts[0]) <= HEARTBEAT_FRESH:
            return True, " ".join(p for p in parts[1:] if p)
    except Exception:
        pass
    return False, ""


def pause_active(path=None, cfg=None):
    p = path or (cfg or {}).get("pause_file") or PAUSE_FILE
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            return float(fh.read().strip()) > time.time()
    except Exception:
        return False


def clear_pause(cfg=None):
    """取消"先别拨"冷却期（用户点「立即连接」时用）。"""
    p = (cfg or {}).get("pause_file") or PAUSE_FILE
    try:
        os.remove(p)
    except OSError:
        pass


# --------------------------------------------------------------------------
# 断开请求
#
# 为什么需要它：校园网拨号是**系统级守护以 SYSTEM 身份**建立的，
# 界面程序（普通用户权限）执行 rasdial /disconnect **断不开它**
# —— 表现就是"点了断开，链路还在"。
# 所以界面只能"请求"守护去断：守护有权限，而且它每 15 秒巡检一次。
# --------------------------------------------------------------------------
DISCONNECT_FILE = "disconnect.request"


def disconnect_path(boot_cfg=None):
    base = HOME_DIR
    if boot_cfg and boot_cfg.get("user_rules"):
        base = os.path.dirname(boot_cfg["user_rules"])
    return os.path.join(base, DISCONNECT_FILE)


def request_disconnect(note=""):
    """界面调用：请求系统级守护把校园网断开。"""
    path = disconnect_path(None)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%.0f\t%s" % (time.time(), note or ""))
        return True
    except Exception:
        return False


def disconnect_requested(boot_cfg=None):
    try:
        with open(disconnect_path(boot_cfg), "r", encoding="utf-8-sig") as fh:
            return bool(fh.read().strip())
    except Exception:
        return False


def clear_disconnect(cfg=None):
    p = disconnect_path(cfg)
    try:
        os.remove(p)
    except OSError:
        pass


def disabled_adapter_path(boot_cfg=None):
    base = HOME_DIR
    if boot_cfg and boot_cfg.get("user_rules"):
        base = os.path.dirname(boot_cfg["user_rules"])
    return os.path.join(base, "disabled_adapter.txt")


def set_disabled_adapter(name, boot_cfg=None):
    """记下"为了断开校园网而禁用了哪块网卡"。

    必须记住具体名字：网卡一旦被禁用，"自动挑选有线网卡"就可能挑到另一块
    （比如已禁用的"本地连接"），恢复时就会启用错的那块。
    """
    try:
        with open(disabled_adapter_path(boot_cfg), "w", encoding="utf-8") as fh:
            fh.write(str(name or ""))
        return True
    except Exception:
        return False


def get_disabled_adapter(boot_cfg=None):
    try:
        with open(disabled_adapter_path(boot_cfg), "r", encoding="utf-8-sig") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def clear_disabled_adapter(boot_cfg=None):
    try:
        os.remove(disabled_adapter_path(boot_cfg))
    except OSError:
        pass


# --------------------------------------------------------------------------
# 手动网络选择
#
# 用户手动挑"用哪个网络"时记在这里。它的优先级**高于**自动逻辑：
#   choice = wifi / none  → 不要自动把校园网连回来（用户是故意切走的）
#   choice = campus       → 明确要校园网
#   没有这个文件          → 自动模式（断开→翻完墙→自动连回来 就是这种情况）
# --------------------------------------------------------------------------
NETWORK_CHOICE_FILE = "network.choice"


def network_choice_path(boot_cfg=None):
    base = HOME_DIR
    if boot_cfg and boot_cfg.get("user_rules"):
        base = os.path.dirname(boot_cfg["user_rules"])
    return os.path.join(base, NETWORK_CHOICE_FILE)


def set_network_choice(choice, note=""):
    """choice: campus / wifi / none。传空字符串等于取消手动选择。"""
    path = network_choice_path(None)
    if not choice:
        return clear_network_choice()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%s\t%.0f\t%s" % (choice, time.time(), note or ""))
        return True
    except Exception:
        return False


def get_network_choice(boot_cfg=None):
    try:
        with open(network_choice_path(boot_cfg), "r", encoding="utf-8-sig") as fh:
            parts = fh.read().strip().split("\t")
        return parts[0] if parts and parts[0] else ""
    except Exception:
        return ""


def clear_network_choice(boot_cfg=None):
    try:
        os.remove(network_choice_path(boot_cfg))
    except OSError:
        pass
    return True


# --------------------------------------------------------------------------
# 连接请求
#
# 界面点「立即连接」时写这个文件，让**系统级守护**去拨号，而不是界面自己拨：
#   1. 守护才有权限恢复被禁用的网卡（界面没有）；
#   2. 两边同时拨号会互相杀进程 —— 实测会出现
#      `错误 1：正在连接到 宽带连接...`（正在拨号的 rasdial 被另一边清掉了）。
# --------------------------------------------------------------------------
CONNECT_FILE = "connect.request"


def connect_path(boot_cfg=None):
    base = HOME_DIR
    if boot_cfg and boot_cfg.get("user_rules"):
        base = os.path.dirname(boot_cfg["user_rules"])
    return os.path.join(base, CONNECT_FILE)


def request_connect(note=""):
    path = connect_path(None)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%.0f\t%s" % (time.time(), note or ""))
        return True
    except Exception:
        return False


def connect_requested(boot_cfg=None):
    try:
        with open(connect_path(boot_cfg), "r", encoding="utf-8-sig") as fh:
            return bool(fh.read().strip())
    except Exception:
        return False


def clear_connect(cfg=None):
    try:
        os.remove(connect_path(cfg))
    except OSError:
        pass


# --------------------------------------------------------------------------
# 「翻墙模式正在用代理」标记
#
# 翻墙模式打开代理后会写这个文件；系统级守护读到它（且足够新）就**不关代理**，
# 否则"连着校园网就关代理"的规则会把刚开起来的 E-IX 在 20 秒内杀掉 ——
# 这正是"打开 Google 翻不了墙"的根因。
# 用时间戳而不是"文件存在与否"，所以进程被强杀时也不会留下永久豁免。
# --------------------------------------------------------------------------
FLIP_ACTIVE_FILE = "flip.active"
FLIP_ACTIVE_FRESH = 180          # 秒：多久没刷新就算翻墙模式已经不活跃了


def flip_active_path(boot_cfg=None):
    base = HOME_DIR
    if boot_cfg and boot_cfg.get("user_rules"):
        base = os.path.dirname(boot_cfg["user_rules"])
    return os.path.join(base, FLIP_ACTIVE_FILE)


def set_flip_active(client="", note=""):
    """写/刷新标记。翻墙模式在代理运行期间应定期调用（例如每 30 秒一次）。"""
    path = flip_active_path(None)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("%.0f\t%s\t%s" % (time.time(), client or "", note or ""))
        return True
    except Exception:
        return False


def clear_flip_active():
    try:
        os.remove(flip_active_path(None))
        return True
    except Exception:
        return False


def flip_active(boot_cfg=None, max_age=None):
    """返回 (是否活跃, 客户端, 说明, 距今秒数)。"""
    max_age = FLIP_ACTIVE_FRESH if max_age is None else max_age
    try:
        with open(flip_active_path(boot_cfg), "r", encoding="utf-8-sig") as fh:
            parts = fh.read().strip().split("\t")
        age = time.time() - float(parts[0])
        return (age <= max_age), (parts[1] if len(parts) > 1 else ""), \
            (parts[2] if len(parts) > 2 else ""), age
    except Exception:
        return False, "", "", -1.0


def set_pause(minutes=10):
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(PAUSE_FILE, "w", encoding="utf-8") as fh:
            fh.write("%.0f" % (time.time() + minutes * 60))
    except Exception:
        pass
