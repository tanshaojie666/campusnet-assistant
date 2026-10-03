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
                     boot_heartbeat, boot_log, clear_connect, clear_disabled_adapter,
                     clear_disconnect, clear_network_choice, clear_pause,
                     connect_requested, disabled_adapter_path, disconnect_requested,
                     flip_active, get_disabled_adapter, get_network_choice,
                     heartbeat_state, load_boot_config, load_rules, pause_active,
                     set_disabled_adapter, set_pause)
from .clients import scan_clients
from .net import (adapter_ip, apply_wifi_policy, campus_link_state, clear_stale_dials,
                  ensure_wifi_connected, friendly_error, pppoe_connections, ppp_state,
                  ppp_state_cached, ras_dial, ras_hangup, reset_ras, set_adapter_disabled,
                  wifi_connected_ssid, wifi_profiles, wifi_visible_ssids, wired_adapters,
                  wired_auth_list, wired_authenticate, wired_bind_ip)
from .net import build_prober
from .rules import kill_processes
from .util import list_processes, named_mutex, pids_of, sleep_interruptible

MODE_LABEL = {"wired": "有线", "wireless": "无线", "both": "有线+无线"}

# 校园网刚连上后的多少秒内，仍然关掉代理/VPN（这段时间代理可能干扰认证、抢路由）。
# 超过这段时间就**不再反复关** —— 否则"翻墙模式"刚开起来的代理会立刻被自己杀掉，
# 表现就是"打开 Google 也翻不了墙"。
KILL_WINDOW = 90

# 两次拨号之间至少隔这么久（秒）。防止"拨号返回成功但探测不到网络"时
# 每 15 秒猛拨一次 —— 既刷日志，也可能撞上学校的并发限制。
MIN_DIAL_GAP = 60


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
    interval = max(5, int(cfg.get("interval") or 10))
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
    # 「断开后没在翻墙就自动连回来」用到的开关
    guard_cfg = cfg.get("guard") or {}
    reconnect_no_flip = bool(guard_cfg.get("reconnect_when_no_flip"))
    try:
        reconnect_after = max(60, int(guard_cfg.get("reconnect_after") or 180))
    except (TypeError, ValueError):
        reconnect_after = 180
    wait_proxy_logged = False
    procs_snapshot = {}
    err756 = 0                      # 连续 756 的次数（用来判断要不要重启 RasMan）
    last_dial_ts = 0.0              # 上次拨号的时间（两次之间至少隔 MIN_DIAL_GAP 秒）
    last_missing_log = 0.0          # "用着别的网但校园网没连上"这条日志的节流
    # 「跟着 VPN 走」
    follow_vpn = bool(guard_cfg.get("follow_vpn"))
    vpn_hotspot_ssid = str(guard_cfg.get("vpn_hotspot_ssid") or "")
    try:
        vpn_switch_delay = max(5, int(guard_cfg.get("vpn_switch_delay") or 10))
    except (TypeError, ValueError):
        vpn_switch_delay = 10
    vpn_last_on = 0.0               # 最近一次"检测到在用代理"的时间
    last_hotspot_pre = 0.0          # 上次"预连接手机热点"的尝试时间（节流用）
    vpn_restored = False            # 这一轮翻墙结束后是否已经把校园网切回来了
    auto_dial = bool(guard_cfg.get("auto_dial", True))
    kill_before_dial = bool(guard_cfg.get("kill_before_dial", True))
    last_nodial_log = 0.0           # "自动连校园网已关闭"这条日志的节流
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

    # 区分"刚开机"和"计划任务每 5 分钟的重启"：
    # 开机启动时要清掉上一次留下的「断开」状态 —— 用户的要求是
    # "开机 / 锁屏界面照样连校园网，只有我主动点断开才保持断开"。
    try:
        from .util import system_uptime_seconds
        if system_uptime_seconds() < 600:
            if pause_active(pause_file, cfg):
                clear_pause(cfg)
                boot_log("本次是开机启动 → 已清除上次留下的「断开」状态，照常连校园网。")
            clear_disconnect(cfg)
    except Exception as exc:  # noqa: BLE001
        boot_log("开机状态判断出错（已忽略）：%s" % exc)

    # 注意：开机配置可能是旧格式（没有 guard 段），所以这里从 rules.json 再确认一次，
    # 免得"明明开了却不打日志"，排查时让人怀疑功能没生效。
    try:
        _r0 = load_rules(cfg)
        _fv = bool(_r0.get("follow_vpn", follow_vpn))
        _ssid0 = str(_r0.get("vpn_hotspot_ssid") or vpn_hotspot_ssid or "")
    except Exception:
        _fv, _ssid0 = follow_vpn, vpn_hotspot_ssid
    if _fv:
        boot_log("「跟着 VPN 走」已开启：翻墙时连「%s」，VPN 关闭后 %.0f 秒切回校园网。"
                 % (_ssid0 or "(未指定无线，只断开校园网)", vpn_switch_delay))

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
            # 「断开后没在翻墙就自动连回来」也支持界面开关实时生效
            guard_rules = cfg.get("guard") or {}
            reconnect_no_flip = bool(rules.get("reconnect_when_no_flip",
                                               guard_rules.get("reconnect_when_no_flip")))
            follow_vpn = bool(rules.get("follow_vpn", guard_rules.get("follow_vpn")))
            auto_dial = bool(rules.get("auto_dial", guard_rules.get("auto_dial", True)))
            kill_before_dial = bool(rules.get("kill_before_dial",
                                              guard_rules.get("kill_before_dial", True)))
            vpn_hotspot_ssid = str(rules.get("vpn_hotspot_ssid")
                                   or guard_rules.get("vpn_hotspot_ssid") or "")
            procs_snapshot = list_processes() or {}

            if wifi_policy != "off" and time.time() - last_wifi > 600:
                # ★ 关键：把「翻墙时用的手机热点」和「校园网无线」排除在"改手动连接"之外，
                # 否则我们一边要程序自动连热点、一边又把它设成手动 → Windows 永远不会自己连，
                # 用户只能手动去 Wi-Fi 列表里点（实测踩到过这个自相矛盾的坑）。
                _ok, _total, msg = apply_wifi_policy(wifi_policy,
                                                     keep_auto=[vpn_hotspot_ssid, wifi_ssid])
                if msg:
                    boot_log(msg)
                last_wifi = time.time()

            # ★ 热点预连接：手机热点一出现在范围内，就自己把电脑连上去。
            # 这样等打开浏览器时"替代网络"已经就绪 → 翻墙立刻生效，全程不用动手。
            if (follow_vpn and vpn_hotspot_ssid
                    and bool(rules.get("hotspot_preconnect", True))
                    and get_network_choice(cfg) != "none"
                    and not pause_active(pause_file, cfg)
                    and time.time() - last_hotspot_pre > 90):
                last_hotspot_pre = time.time()
                _want = vpn_hotspot_ssid.strip()
                _cur = (wifi_connected_ssid() or "").strip()
                if _cur.lower() != _want.lower() and _want in wifi_visible_ssids():
                    boot_log("热点预连接：扫到「%s」，正在自己连上去（不用手动切）…" % _want)
                    _ok_hs, _why_hs = ensure_wifi_connected(_want, timeout=30, log=boot_log)
                    link_cache["t"] = 0
                    boot_log("热点预连接：「%s」%s（%s）"
                             % (_want, "已连上" if _ok_hs else "没连上", _why_hs))

            # 处理界面发来的「断开」请求 —— **必须放在循环最前面**。
            # 为什么：校园网拨号是守护以 SYSTEM 身份建立的，界面（普通用户权限）
            # 执行 rasdial /disconnect 断不开它，用户看到的就是"点了断开没反应"。
            # 也正因为是"正在联网"的时候要点断开，所以这段绝不能放在
            # "在线就 continue" 之后（那正是第一次写错的地方）。
            if disconnect_requested(cfg):
                clear_disconnect(cfg)
                if connection:
                    code, out = ras_hangup(connection, phonebook)
                    boot_log("收到「断开」请求 → rasdial 断开 %s（返回码 %s）"
                             % (connection, code))
                time.sleep(4)
                still, ip = ppp_state(connection) if connection else (False, "")
                if still:
                    # rasdial 对这种"不属于枚举会话的 PPPoE"无效，只能断它的网卡
                    adapter = wired_cfg.get("adapter") or ""
                    if not adapter:
                        from .net import pick_wired_adapter
                        adapter = pick_wired_adapter(wired_cfg)
                    if adapter:
                        # 先记下"要禁用哪块"，再动手禁用 ——
                        # 顺序反过来的话，记录写失败就会留下"一块被禁用的网卡 + 没有记录"
                        # 的状态，那是最难查的组合（守护会靠扫描兜底，但仍先记更稳）。
                        set_disabled_adapter(adapter, cfg)
                        recorded = get_disabled_adapter(cfg)
                        disabled_ok = set_adapter_disabled(adapter, True)
                    else:
                        recorded, disabled_ok = "", False
                    if adapter and disabled_ok:
                        if not recorded:
                            boot_log("警告：没能记录被禁用的网卡（%s），"
                                     "不过守护会靠扫描恢复它。" % disabled_adapter_path(cfg))
                        boot_log("rasdial 没断掉（链路 %s 仍在）→ 已禁用有线网卡「%s」，"
                                 "校园网必定断开；手机热点/无线不受影响。"
                                 "点「立即连接」会自动重新启用它。" % (ip or "?", adapter))
                    else:
                        boot_log("断开失败：rasdial 没生效，也没找到可禁用的有线网卡。")
                else:
                    boot_log("校园网已断开（%s 已释放）。" % (ip or "IP"))
                link_cache["t"] = 0
                link_started = 0.0
                boot_heartbeat(False, "", "已按请求断开校园网")

            # 处理界面发来的「连接」请求：交给守护来做，而不是界面自己拨。
            # 原因有二：守护才有权限恢复被禁用的网卡；两边同时拨号会互相杀进程
            # （实测会出现 `错误 1：正在连接到 宽带连接...`）。
            if connect_requested(cfg):
                clear_connect(cfg)
                clear_pause(cfg)
                clear_network_choice(cfg)
                from .net import enable_disabled_wired_adapters
                fixed = enable_disabled_wired_adapters(boot_log)
                boot_log("收到「连接」请求 → %s立即拨号。"
                         % ("已恢复网卡，" if fixed else ""))
                link_cache["t"] = 0
                link_started = 0.0
                last_dial_ts = 0.0          # 允许本轮立刻拨
                time.sleep(2 if fixed else 0)

            # 「跟着 VPN 走」：以"有没有在用代理"为准自动切网络，保证随时都有网 ——
            #   VPN 在跑 → 断开校园网（校园网里翻墙会认证失败）+ 连上指定的无线（手机热点）
            #   VPN 一停 → 立刻把校园网连回来
            # 手动选择过网络（无线/全断）时不插手，用户的明确选择优先。
            if follow_vpn and get_network_choice(cfg) not in ("wifi", "none"):
                flip_on2, _fc2, _fn2, _fage2 = flip_active(cfg)
                proxying = flip_on2 or any(pids_of(procs_snapshot, n) for n in processes)
                if proxying:
                    vpn_last_on = time.time()
                    vpn_restored = False
                    if not pause_active(pause_file, cfg):
                        set_pause(24 * 60)
                        boot_log("检测到正在使用代理（翻墙中）→ 暂停校园网自动拨号。")
                    # 断开校园网（复用已验证的两步断开：先 rasdial，不行就禁网卡）
                    up2, ip2 = ppp_state_cached(connection, link_cache) if connection \
                        else (False, "")
                    if up2 and connection:
                        ras_hangup(connection, phonebook)
                        time.sleep(3)
                        if ppp_state(connection)[0]:
                            from .net import pick_wired_adapter
                            ad = get_disabled_adapter(cfg) or pick_wired_adapter(wired_cfg)
                            if ad:
                                set_disabled_adapter(ad, cfg)
                                if set_adapter_disabled(ad, True):
                                    boot_log("翻墙中 → 已断开校园网（禁用网卡「%s」）。" % ad)
                        else:
                            boot_log("翻墙中 → 已断开校园网（%s 已释放）。" % (ip2 or "IP"))
                        link_cache["t"] = 0
                    # 保证有网：连上指定的无线（手机热点）—— 自己连，并盯到真的拿到 IP
                    if vpn_hotspot_ssid:
                        _ok_hs, _why_hs = ensure_wifi_connected(vpn_hotspot_ssid, timeout=45,
                                                                log=boot_log)
                        if _ok_hs:
                            boot_log("翻墙中 → %s" % _why_hs)
                        else:
                            boot_log("翻墙中 → 自动连「%s」没成功：%s"
                                     % (vpn_hotspot_ssid, _why_hs))
                        link_cache["t"] = 0
                elif (vpn_last_on and not vpn_restored
                      and (time.time() - vpn_last_on) >= vpn_switch_delay):
                    vpn_restored = True
                    clear_pause(cfg)
                    from .net import enable_disabled_wired_adapters
                    fixed = enable_disabled_wired_adapters(boot_log)
                    boot_log("代理已关闭 → %s切回校园网。"
                             % ("已恢复网卡，" if fixed else ""))
                    link_cache["t"] = 0
                    last_dial_ts = 0.0

            # 「立即连接」后把上次断开时禁用的网卡恢复回来。
            # 这里**不依赖"禁用了哪块"的记录**：记录一旦丢失，网卡就会一直禁用，
            # 拨号一直报 756，校园网再也连不回来（实测踩到过）。
            # 所以直接扫一遍：只要有被禁用的物理有线网卡，就启用它们。
            if (not pause_active(pause_file, cfg)) and mode in ("wired", "both"):
                from .net import adapter_enabled, enable_disabled_wired_adapters, \
                    pick_wired_adapter
                ad = get_disabled_adapter(cfg) or wired_cfg.get("adapter") \
                    or pick_wired_adapter(wired_cfg)
                if ad and not adapter_enabled(ad):
                    if set_adapter_disabled(ad, False):
                        boot_log("检测到有线网卡「%s」被禁用（上次断开留下的）→ 已重新启用。"
                                 % ad)
                        clear_disabled_adapter(cfg)
                        time.sleep(6)
                        link_cache["t"] = 0
                else:
                    extra = enable_disabled_wired_adapters(boot_log)
                    if extra:
                        clear_disabled_adapter(cfg)
                        time.sleep(6)
                        link_cache["t"] = 0

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

            # 可选：断开校园网之后，如果一直没在使用代理（没在翻墙），就自动连回来。
            # 场景很实用：为了翻墙而断开校园网 → 翻完了 / 压根没翻 → 自动恢复校园网。
            # 判定"没在用代理"用两个信号：flip.active 标记 + 关代理名单里的进程，
            # 所以不依赖界面程序是否在运行。
            if reconnect_no_flip and pause_active(pause_file, cfg) \
                    and get_network_choice(cfg) not in ("wifi", "none"):
                _flip_on2, _fc2, _fn2, _fage2 = flip_active(cfg)
                proxying = _flip_on2 or any(pids_of(procs_snapshot, n) for n in processes)
                if proxying:
                    if not wait_proxy_logged:
                        boot_log("已按请求断开校园网；检测到正在使用代理 → 暂不连回来。")
                        wait_proxy_logged = True
                else:
                    try:
                        waited = time.time() - os.path.getmtime(pause_file)
                    except OSError:
                        waited = reconnect_after
                    if waited >= reconnect_after:
                        clear_pause(cfg)
                        boot_log("已经断开 %.1f 分钟且没有在使用代理 → 自动把校园网连回来。"
                                 % (waited / 60.0))
                        wait_proxy_logged = False
                        link_cache["t"] = 0
            else:
                wait_proxy_logged = False

            ppp_up, ppp_ip = (False, "")
            if mode in ("wired", "both") and connection and "pppoe" in wires:
                ppp_up, ppp_ip = ppp_state_cached(connection, link_cache)
            bind_ip = ppp_ip or (wired_bind_ip(wired_cfg) if mode == "wired" else "")

            ok, ip, why = prober.check(bind_ip=bind_ip or None)
            if os.environ.get("CNA_TEST_OFFLINE"):
                ok = False                              # 仅自测用：假装掉线

            # 关键一步：开了「没在翻墙就自动连回校园网」之后，
            # **"校园网没连上"本身就是拨号信号** —— 哪怕手机热点让整台机器
            # 整体是"在线"的。否则守护会以为"网络已恢复"而永远不去拨校园网，
            # 表现就是"连着热点时校园网一直不上"。
            campus_missing = bool(
                reconnect_no_flip and connection and "pppoe" in wires
                and not ppp_up and not pause_active(pause_file, cfg))
            if campus_missing and ok:
                # 这条日志要节流：热点上会一直成立，否则每轮都刷一行
                if time.time() - last_missing_log > 300:
                    last_missing_log = time.time()
                    boot_log("检测到正在使用别的网络（%s），但校园网没连上 → 仍按设置去拨校园网。"
                             % (why or "其他网络"))
                ok = False

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
            # 拨号时机：如果目标就是校园网（PPPoE 没连上、且不是"用户故意切走"的状态），
            # **第一次失败就拨**，不再等第二次 —— 否则白白多等一个巡检间隔（默认 15 秒）。
            want_campus = bool(connection and "pppoe" in wires and not ppp_up
                               and get_network_choice(cfg) not in ("wifi", "none"))
            if fails < (1 if want_campus else 2):
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue

            # 两次拨号之间至少隔 MIN_DIAL_GAP 秒：
            # 否则"拨号返回成功但探测不到网络"这种情况会让它每 10 秒猛拨一次，
            # 既刷日志也可能撞上学校的并发限制。
            #
            # **例外**：链路本身是断的（PPPoE 没连上）时不等 —— 这正是
            # "没连 VPN 时保证不断网"最需要快速恢复的场景，等 60 秒就太久了。
            if ppp_up and (time.time() - last_dial_ts < MIN_DIAL_GAP):
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue
            last_dial_ts = time.time()

            # ---- 接入 ----
            # 用户手动切走时（wifi / none），**不要**再自作主张连校园网 ——
            # 否则"全部断开"之后一没网就会被他自动拨回来。
            manual_off = get_network_choice(cfg) in ("wifi", "none")
            # 「自动连校园网」总开关：关掉后只监视、不拨号（想完全手动时用）
            if not auto_dial:
                if time.time() - last_nodial_log > 600:
                    last_nodial_log = time.time()
                    boot_log("检测到掉线，但「自动连校园网」开关是关的 → 不拨号。")
                sleep_interruptible(interval, lambda: os.path.isfile(BOOT_STOP))
                continue

            if not manual_off and (mode == "wireless"
                                   or (mode == "both" and not ppp_up and wifi_ssid)):
                from .net import wifi_connect, wifi_connected_ssid
                if wifi_ssid and wifi_connected_ssid().lower() != wifi_ssid.lower():
                    boot_log("正在连接无线 %s …" % wifi_ssid)
                    wifi_connect(wifi_ssid)
                    time.sleep(8)

            use_pppoe = ("pppoe" in wires) and bool(connection) and not manual_off
            if mode in ("wired", "both") and use_pppoe:
                boot_log("检测到校园网没连上，准备拨号…")
                # 先干掉代理/VPN：它们会抢路由、拦 DNS，导致拨号慢甚至拨不上。
                # 杀完只等 2 秒（原来是 3 秒）就继续，尽量快。
                if kill_on and kill_before_dial:
                    t_kill = time.time()
                    acted, detail = kill_processes(processes)
                    if acted:
                        boot_log("已先关闭代理/VPN（用时 %.1f 秒）：%s"
                                 % (time.time() - t_kill, detail))
                        sleep_interruptible(2, lambda: os.path.isfile(BOOT_STOP))
                ras_hangup(connection, phonebook)
                # 清掉卡住的 rasdial，否则本次拨号会立刻报 756 而失败
                stale = clear_stale_dials()
                if stale:
                    boot_log("清理了 %d 个卡住的 rasdial 进程（否则会报 756）。" % stale)
                    sleep_interruptible(2, lambda: os.path.isfile(BOOT_STOP))
                code, text = ras_dial(connection, account, password, phonebook)
                if code == 0:
                    # 不再死等固定 5 秒：PPP 一拿到地址就立刻继续（通常 1~2 秒）
                    link_cache["t"] = 0
                    ppp_up2, ppp_ip2 = (False, "")
                    t_dial = time.time()
                    for _ in range(10):
                        time.sleep(1)
                        link_cache["t"] = 0
                        ppp_up2, ppp_ip2 = ppp_state_cached(connection, link_cache)
                        if ppp_up2:
                            break
                    ok2, ip2, why2 = prober.check(bind_ip=ppp_ip2 or None)
                    boot_heartbeat(ok2, ip2, why2)
                    if ok2:
                        boot_log("拨号成功，已联网（IP %s，用时 %.1f 秒）"
                                 % (ip2 or "?", time.time() - t_dial))
                        fails, backoff = 0, interval
                        err756 = 0                      # 成功了就把 756 计数清零
                    else:
                        boot_log("拨号返回成功，但还探测不到网络。")
                else:
                    # 756（已经在拨号中）通常是暂时的：别按指数退避等下去，尽快重试。
                    # 如果连续好几次都是 756，说明拨号状态卡在 RasMan 服务里了
                    # （实测 rasdial 进程数为 0 也会一直报），此时重启该服务来清。
                    if code == 756:
                        backoff = 20
                        err756 += 1
                        if err756 >= 3:
                            boot_log("连续 %d 次 756（拨号状态卡在 RasMan）→ 重启 RasMan 服务清状态。"
                                     % err756)
                            reset_ras()
                            err756 = 0
                            link_cache["t"] = 0
                            continue
                    else:
                        err756 = 0
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
    # 翻墙前的"替代网络"判断（热点没准备好时这里会明说，避免"翻墙莫名其妙不生效"）
    gc = cfg.get("guard") or {}
    if gc.get("follow_vpn") or gc.get("require_other_network", True):
        from .net import other_network_available
        hs = (gc.get("vpn_hotspot_ssid") or "").strip()
        ok_alt, why_alt = other_network_available(cfg, hs)
        print("   替代网络（翻墙前必须先有）：", ("有 - " if ok_alt else "没有 - ") + why_alt)
        if hs:
            from .net import hotspot_diagnosis, wifi_profile_mode
            saved = hs in wifi_profiles()
            print("     手机热点：", hs, "｜已保存过该热点：", "是" if saved else "否（要先连一次）")
            print("     热点诊断：", hotspot_diagnosis(hs))
            if wifi_profile_mode(hs) == "manual":
                print("     ⚠ 它现在是「手动连接」→ Windows 永远不会自己连它。")
                print("        打开程序界面、在「校园网」页保存一次设置就会自动改回「自动连接」，")
                print("        或者勾上「热点一开就自动连」让它以后一直自动化。")
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
