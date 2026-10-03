# -*- coding: utf-8 -*-
"""系统级守护：以 SYSTEM 身份在后台常驻，开机/锁屏/未登录时也能工作。

职责：
  1. 按配置的接入方式（有线 PPPoE / 无线 / 两者）保证校园网连着；掉线自动重拨
  2. 校园网一连上，就关掉配置里的代理/VPN 进程（含播号前先关一遍）
  3. 执行无线策略（全部改手动连接 / 禁用网卡）
  4. 写心跳和日志，供界面显示
"""
from __future__ import annotations

import os
import time

from .config import (BOOT_CONFIG, BOOT_LOG, BOOT_STOP, BOOT_TASK, CONFIG_FILE,
                     boot_heartbeat, boot_log, flip_active, heartbeat_state,
                     load_boot_config, load_rules, pause_active)
from .clients import scan_clients
from .net import (adapter_ip, apply_wifi_policy, campus_link_state, friendly_error,
                  pppoe_connections, ppp_state, ppp_state_cached, ras_dial, ras_hangup,
                  wifi_connected_ssid, wifi_profiles, wired_adapters, wired_auth_list,
                  wired_authenticate, wired_bind_ip)
from .net import build_prober
from .rules import kill_processes
from .util import named_mutex, sleep_interruptible

MODE_LABEL = {"wired": "有线", "wireless": "无线", "both": "有线+无线"}

# 校园网刚连上后的多少秒内，仍然关掉代理/VPN（这段时间代理可能干扰认证、抢路由）。
# 超过这段时间就**不再反复关** —— 否则"翻墙模式"刚开起来的代理会立刻被自己杀掉，
# 表现就是"打开 Google 也翻不了墙"。
KILL_WINDOW = 90


def _script_mtime():
    try:
        return os.path.getmtime(os.path.abspath(__file__))
    except OSError:
        return 0


def boot_mode():
    cfg = load_boot_config()
    if not cfg:
        boot_log("无法启动：读不到系统级配置 %s，请先运行 --install-boot" % BOOT_CONFIG)
        return 1

    guard = named_mutex("Global\\CampusNetAssistant.Boot")
    if guard is None:
        boot_log("已有系统级守护在运行，本实例退出。")
        return 0

    mode = cfg.get("mode") or "wired"
    connection = cfg.get("connection") or ""
    wifi_ssid = cfg.get("wifi_ssid") or ""
    phonebook = cfg.get("phonebook") or None
    pause_file = cfg.get("pause_file") or None
    account = cfg.get("account") or cfg.get("username") or ""   # 兼容旧版字段名
    password = cfg.get("password_machine") or ""
    from .util import dpapi_decrypt
    password = dpapi_decrypt(password, machine=True)
    interval = max(5, int(cfg.get("interval") or 15))
    if not cfg.get("mode"):
        boot_log("提示：系统级配置仍是旧版格式（只有 connection/username），"
                 "接入方式按“有线拨号”处理；重新运行 --install-boot 可写入新格式。")

    # 有线接入配置（PPPoE 之外的 DHCP / 静态 IP / 门户认证 / 学校客户端 / 802.1X）
    wired_cfg = dict(cfg.get("wired") or {})
    # 门户认证：有线无线共用（旧版配置存在 wired.portal 里，这里做兼容）
    portal_cfg = dict(cfg.get("portal") or wired_cfg.get("portal") or {})
    portal_user = portal_cfg.get("username") or account
    portal_pwd = dpapi_decrypt(portal_cfg.get("password_machine") or "", machine=True) \
        if portal_cfg.get("password_machine") else password
    if portal_pwd:
        portal_cfg["_password"] = portal_pwd        # 只放内存，不写回文件
    portal_on = bool(portal_cfg.get("url") or portal_cfg.get("script")
                     or portal_cfg.get("mode") == "auto")
    wires = wired_auth_list(wired_cfg, connection)
    if not wired_cfg.get("auth"):
        wired_cfg["auth"] = wires

    prober = build_prober(cfg, connection)
    link_cache = {}
    fails, backoff = 0, interval
    last_wifi = 0.0
    link_started = 0.0            # 校园网链路是什么时候连上的（用于 KILL_WINDOW 判断）
    flip_logged = False           # 「翻墙模式在用代理」这条日志只记一次，避免刷屏
    mtime = _script_mtime()

    boot_log("===== 系统级守护启动：接入方式=%s 拨号连接=%s 有线方式=%s 无线=%s =====" %
             (MODE_LABEL.get(mode, mode), connection or "(未配置)",
              "+".join(wires), wifi_ssid or "(未配置)"))
    boot_heartbeat(False, "", "启动中")
    if mode in ("wired", "both") and not password and ("pppoe" in wires):
        boot_log("警告：读不到拨号密码（机器范围加密失败），只能用系统记住的凭据。")

    # 刚开机时网络栈可能还没就绪，先缓一下
    sleep_interruptible(15, lambda: os.path.isfile(BOOT_STOP))

    while True:
        if os.path.isfile(BOOT_STOP):
            try:
                os.remove(BOOT_STOP)
            except OSError:
                pass
            boot_log("收到停止标志，系统级守护退出。")
            return 0
        if _script_mtime() > mtime:
            boot_log("程序文件已更新，守护退出，稍后由计划任务用新版本重启。")
            return 0

        try:
            rules = load_rules(cfg)                     # 每轮重读：界面改开关立即生效
            kill_on = bool(rules.get("kill_proxies", cfg.get("kill_proxies", True)))
            processes = rules.get("processes") or cfg.get("processes") or []
            wifi_policy = rules.get("wifi_policy") or cfg.get("wifi_policy") or "off"

            if wifi_policy != "off" and time.time() - last_wifi > 600:
                _ok, _total, msg = apply_wifi_policy(wifi_policy)
                if msg:
                    boot_log(msg)
                last_wifi = time.time()

            link_up, link_desc = campus_link_state(mode, connection, wifi_ssid, link_cache,
                                                   wired_cfg)

            # 校园网一连上就关掉代理/VPN（链路级判断，代理软件没法伪造）。
            #
            # 这里要和「翻墙模式」的策略联动，否则两边会打架：
            #   when = off_campus（默认，推荐）
            #       翻墙模式不会在校园网上开代理 → 所以只要链路在，就**一直**关代理/VPN，
            #       这样"连校园网就一定不翻墙"是硬保证。
            #   when = always（用户明确要连着校园网也翻墙）
            #       那就只在新连上的 KILL_WINDOW 内关（此时认证需要代理让路），
            #       链路稳定后不再反复关，并且在翻墙模式正用代理时让路，
            #       免得把刚开起来的代理在 20 秒内杀掉。
            flip_policy = str((rules.get("flip") or {}).get("when") or "off_campus").lower()
            if link_up:
                if not link_started:
                    link_started = time.time()
            else:
                link_started = 0.0
                flip_logged = False

            flip_on, flip_client, _flip_note, _flip_age = flip_active(cfg)
            fresh_link = bool(link_up and link_started
                              and (time.time() - link_started) <= KILL_WINDOW)

            if flip_policy == "always":
                # 用户明确要"连着校园网也翻墙"：给翻墙模式让路，只在刚连上时关
                if kill_on and link_up and flip_on:
                    if not flip_logged:
                        boot_log("翻墙模式正在使用代理（%s），本次不关闭 —— "
                                 "避免把它刚开起来的代理杀掉。" % (flip_client or "未知"))
                        flip_logged = True
                elif kill_on and fresh_link:
                    acted, detail = kill_processes(processes, boot_log)
                    if acted:
                        boot_log("校园网刚连上，" + detail)
                        link_cache["t"] = 0
                        boot_heartbeat(False, "", "刚连上校园网，已关闭代理/VPN",
                                       campus=link_desc)
            else:
                # 默认策略：只要连着校园网就一直关代理/VPN ——
                # 这是"连校园网绝不翻墙"的硬保证（该策略下翻墙模式也不会在校园网上开代理）
                if kill_on and link_up:
                    acted, detail = kill_processes(processes, boot_log)
                    if acted:
                        boot_log("校园网已连上（策略：校园网内不翻墙），" + detail)
                        link_cache["t"] = 0
                        boot_heartbeat(False, "", "校园网已连上，已关闭代理/VPN",
                                       campus=link_desc)
            if not link_up:
                flip_logged = False

            ppp_up, ppp_ip = (False, "")
            if mode in ("wired", "both") and connection and "pppoe" in wires:
                ppp_up, ppp_ip = ppp_state_cached(connection, link_cache)
            bind_ip = ppp_ip or (wired_bind_ip(wired_cfg) if mode == "wired" else "")

            ok, ip, why = prober.check(bind_ip=bind_ip or None)
            if os.environ.get("CNA_TEST_OFFLINE"):
                ok = False                              # 仅自测用：假装掉线
            boot_heartbeat(ok, ip, why, campus=link_desc)

            if ok:
                if fails:
                    boot_log("网络已恢复（IP %s，依据 %s）" % (ip or "?", why or "?"))
                fails, backoff = 0, interval
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue

            if pause_active(pause_file, cfg):
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue

            fails += 1
            if fails < 2:
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue

            # ---- 接入 ----
            if mode == "wireless" or (mode == "both" and not ppp_up and wifi_ssid):
                from .net import wifi_connect, wifi_connected_ssid
                if wifi_ssid and wifi_connected_ssid().lower() != wifi_ssid.lower():
                    boot_log("正在连接无线 %s …" % wifi_ssid)
                    wifi_connect(wifi_ssid)
                    time.sleep(8)

            use_pppoe = ("pppoe" in wires) and bool(connection)
            if mode in ("wired", "both") and use_pppoe:
                boot_log("检测到掉线，开始拨号…")
                if kill_on:
                    acted, detail = kill_processes(processes)
                    if acted:
                        boot_log("拨号前先关闭了代理/VPN：" + detail)
                        sleep_interruptible(3)
                ras_hangup(connection, phonebook)
                code, text = ras_dial(connection, account, password, phonebook)
                if code == 0:
                    time.sleep(5)
                    link_cache["t"] = 0
                    ppp_up2, ppp_ip2 = ppp_state_cached(connection, link_cache)
                    ok2, ip2, why2 = prober.check(bind_ip=ppp_ip2 or None)
                    boot_heartbeat(ok2, ip2, why2)
                    if ok2:
                        boot_log("拨号成功，已联网（IP %s）" % (ip2 or "?"))
                        fails, backoff = 0, interval
                    else:
                        boot_log("拨号返回成功，但还探测不到网络。")
                else:
                    boot_log("拨号失败 → %s；%d 秒后重试" % (friendly_error(code, text), backoff))
                    sleep_interruptible(backoff, lambda: os.path.isfile(BOOT_STOP))
                    backoff = min(backoff * 2, 300)
                    continue
            elif mode in ("wired", "both") and any(
                    a in wires for a in ("dhcp", "static", "portal", "client", "lan", "restart")):
                # DHCP / 静态 IP / 门户认证 / 学校客户端 / 802.1X
                boot_log("检测到掉线，按配置接入有线（%s）…" % "+".join(wires))
                if kill_on:
                    acted, detail = kill_processes(processes)
                    if acted:
                        boot_log("接入前先关闭了代理/VPN：" + detail)
                        sleep_interruptible(3)
                pc = dict(portal_cfg)
                if pc.pop("_password", None):
                    pass
                wired_authenticate(wired_cfg, connection, portal_user,
                                   portal_cfg.get("_password") or password, boot_log)
                time.sleep(5)
                link_cache["t"] = 0
                ok2, ip2, why2 = prober.check(bind_ip=wired_bind_ip(wired_cfg) or None)
                boot_heartbeat(ok2, ip2, why2)
                if ok2:
                    boot_log("有线接入成功，已联网（IP %s）" % (ip2 or "?"))
                    fails, backoff = 0, interval
                else:
                    boot_log("有线接入动作已执行，但还没联网；%d 秒后重试" % backoff)
                    sleep_interruptible(backoff, lambda: os.path.isfile(BOOT_STOP))
                    backoff = min(backoff * 2, 300)
                    continue
            elif mode in ("wireless", "both"):
                # 无线：连上了却不通 → 很可能是被门户拦住（校园 WiFi 常见）
                from .net import portal_login, wifi_connected_ssid
                got = wifi_connected_ssid()
                if wifi_ssid and got and got.lower() == wifi_ssid.lower():
                    if portal_on:
                        boot_log("无线已连上但上不了网，尝试网页（门户）认证 …")
                        ok3, msg3 = portal_login(portal_cfg, portal_user,
                                                 portal_cfg.get("_password") or password, boot_log)
                        boot_log("  " + str(msg3))
                        time.sleep(3)
                        link_cache["t"] = 0
                        ok4, ip4, why4 = prober.check()
                        boot_heartbeat(ok4, ip4, why4)
                        if ok4:
                            boot_log("门户认证后已联网（IP %s）" % (ip4 or "?"))
                            fails, backoff = 0, interval
                            continue
                        boot_log("门户认证后仍不通；%d 秒后重试" % backoff)
                    else:
                        boot_log("无线已连上但探测不到网络；%d 秒后重试" % backoff)
                else:
                    boot_log("无线未连上（%s），%d 秒后重试"
                             % (link_desc or ("当前：%s" % got if got else "原因未知"), backoff))
                sleep_interruptible(backoff, lambda: os.path.isfile(BOOT_STOP))
                backoff = min(backoff * 2, 300)
                continue

            fails = 0
        except Exception as exc:  # noqa: BLE001
            boot_log("守护循环出错（已忽略，继续运行）：%s" % exc)
        sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))


# --------------------------------------------------------------------------
# 自检 / 扫描
# --------------------------------------------------------------------------
def task_status(name=None):
    """查询计划任务，返回 (是否注册, 说明)。

    区分三态：已注册 / 确实没有 / 当前权限查不了 —— 免得"权限不足"被误报成"未安装"。
    """
    from .util import run_cmd
    name = name or BOOT_TASK
    code, out = run_cmd(["schtasks", "/query", "/tn", name], timeout=30)
    if code == 0:
        return True, "已注册"
    low = (out or "").lower()
    if any(k in low for k in ("access is denied", "拒绝访问", "cannot find the path",
                              "系统找不到指定的路径", "权限")):
        return None, "查不到（当前权限不足，请用管理员身份运行）"
    return False, "未注册"


def selftest(cfg) -> int:
    from .config import heartbeat_state, rules_path
    from .net import (pick_wired_adapter, pppoe_connections, ppp_state, wifi_connected_ssid,
                      wifi_profiles, wired_adapters, wired_auth_list, wired_link_state)
    from .util import is_admin

    line = "-" * 60
    campus = cfg.get("campus") or {}
    wired_cfg = campus.get("wired") or {}
    auths = wired_auth_list(wired_cfg, campus.get("connection") or "")
    AUTH_LABEL = {"pppoe": "PPPoE 拨号", "dhcp": "自动获取 IP", "static": "静态 IP",
                  "portal": "Web 门户认证", "client": "学校客户端", "lan": "802.1X",
                  "restart": "先重启网卡"}
    print(line)
    print("1) 校园网接入")
    print("   接入方式：", MODE_LABEL.get(campus.get("mode"), campus.get("mode")))
    print("   有线认证：", " → ".join(AUTH_LABEL.get(a, a) for a in auths))
    print("   有线网卡：", wired_cfg.get("adapter") or
          ("（自动：%s）" % (pick_wired_adapter(wired_cfg) or "没找到")))
    print("   可用有线网卡：", [a[0] for a in wired_adapters()] or "（没找到）")
    up0, desc0 = wired_link_state(wired_cfg, campus.get("connection") or "")
    print("   有线链路：", "已连上 - " + desc0 if up0 else desc0)
    if "pppoe" in auths:
        print("   找到的 PPPoE 连接：", pppoe_connections() or "（没找到）")
        print("   当前使用：", campus.get("connection") or "（未配置）")
    if "static" in auths:
        st = wired_cfg.get("static") or {}
        print("   静态 IP：", st.get("address") or "（未填）", "网关",
              st.get("gateway") or "-", "DNS", st.get("dns") or "-")
    if "portal" in auths:
        p = wired_cfg.get("portal") or {}
        print("   门户认证：方式=%s 地址=%s 账号=%s" % (
            p.get("mode") or "auto", p.get("url") or "(自动获取)",
            p.get("username") or campus.get("account") or "(未填)"))
    if "client" in auths:
        print("   学校客户端：", wired_cfg.get("client_exe") or "（未配置）")
    if "lan" in auths:
        print("   802.1X 配置名：", wired_cfg.get("lan_profile") or "（未配置）")
    print("   无线 SSID 配置：", campus.get("wifi_ssid") or "（未配置）")
    print("   当前无线：", wifi_connected_ssid() or "未连接")
    print("   已保存无线配置：", len(wifi_profiles()), "个")
    print(line)
    print("2) 密码")
    from .config import get_password, portal_credentials
    pu, pp = portal_credentials(cfg)
    print("   账号：", campus.get("account") or "（未配置）")
    print("   拨号/认证密码能否解开：", "能" if get_password(cfg) else "不能/未配置")
    if "portal" in auths:
        print("   门户账号：", pu or "（未配置）", "| 门户密码：",
              "能" if pp else ("沿用上面的密码" if get_password(cfg) else "未配置"))
    print(line)
    print("3) 翻墙客户端")
    for c, ok, detail in scan_clients(cfg):
        print("   %-12s %s  %s" % (c.get("name"), "已安装" if ok else "未找到", detail))
    print(line)
    print("4) 代理/VPN 关闭名单")
    print("   ", "、".join((cfg.get("guard") or {}).get("kill_processes") or []))
    print(line)
    print("5) 系统级守护")
    alive, desc = heartbeat_state()
    reg, reg_msg = task_status(BOOT_TASK)
    legacy_reg, _ = task_status("CampusNet-AutoDial-Boot")
    print("   计划任务：", BOOT_TASK, "→", reg_msg)
    print("   守护进程：", ("运行中（%s）" % desc) if alive else "未运行")
    if reg is None and alive:
        print("   （上面说“查不到”只是权限问题；守护其实在跑，说明任务确实存在）")
    if legacy_reg and reg is not True:
        print("   注意：检测到你还在用旧任务名 CampusNet-AutoDial-Boot 启动。")
        print("         它靠一个转发壳跑新代码 —— 只要旧文件夹还在就没问题，")
        print("         但建议跑一次 --install-boot 注册成正式任务名，之后旧文件夹可删。")
    print("   配置：", BOOT_CONFIG, "→", "存在" if os.path.isfile(BOOT_CONFIG) else "不存在")
    print("   日志：", BOOT_LOG)
    print(line)
    print("6) 翻墙模式")
    flip = cfg.get("flip") or {}
    print("   开关：", "已开启" if flip.get("enabled") else "已关闭")
    print("   客户端顺序：", " → ".join(flip.get("order") or []))
    print("   触发程序：", "、".join(flip.get("apps") or []))
    print("   目标地区关键词：", "、".join((flip.get("region_hints") or [])[:6]), "…")
    print(line)
    print("7) 环境")
    print("   管理员权限：", is_admin())
    print("   配置文件：", CONFIG_FILE)
    print("   实时规则：", rules_path(None))
    print(line)
    return 0


def scan(cfg) -> int:
    print("本机扫描结果：")
    print("\n[PPPoE 拨号连接]")
    for n in pppoe_connections() or ["（没找到）"]:
        print("   ", n)
    print("\n[无线配置]")
    for n in wifi_profiles() or ["（没有）"]:
        print("   ", n)
    print("\n[翻墙客户端]")
    for c, ok, detail in scan_clients(cfg):
        print("   %-12s %s  %s" % (c.get("name"), "已安装" if ok else "未找到", detail))
    return 0


def boot_task_registered():
    from .util import run_cmd
    code, _ = run_cmd(["schtasks", "/query", "/tn", BOOT_TASK], timeout=30)
    return code == 0
