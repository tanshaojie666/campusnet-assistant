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
                     boot_heartbeat, boot_log, heartbeat_state, load_boot_config,
                     load_rules, pause_active)
from .clients import scan_clients
from .net import (apply_wifi_policy, campus_link_state, friendly_error, pppoe_connections,
                  ppp_state, ppp_state_cached, ras_dial, ras_hangup,
                  wifi_connected_ssid, wifi_profiles)
from .net import build_prober
from .rules import kill_processes
from .util import named_mutex, sleep_interruptible

MODE_LABEL = {"wired": "有线拨号", "wireless": "无线", "both": "有线+无线"}


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
                 "接入方式按“有线”处理；重新运行 --install-boot 可写入新格式。")

    prober = build_prober(cfg, connection)
    link_cache = {}
    fails, backoff = 0, interval
    last_wifi = 0.0
    mtime = _script_mtime()

    boot_log("===== 系统级守护启动：接入方式=%s 拨号连接=%s 无线=%s =====" %
             (MODE_LABEL.get(mode, mode), connection or "(未配置)", wifi_ssid or "(未配置)"))
    boot_heartbeat(False, "", "启动中")
    if mode in ("wired", "both") and not password:
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

            link_up, link_desc = campus_link_state(mode, connection, wifi_ssid, link_cache)

            # 校园网一连上，就关掉代理/VPN（链路级判断，代理软件没法伪造）
            if kill_on and link_up:
                acted, detail = kill_processes(processes, boot_log)
                if acted:
                    boot_log("校园网已连上，" + detail)
                    link_cache["t"] = 0

            ppp_up, ppp_ip = (False, "")
            if mode in ("wired", "both") and connection:
                ppp_up, ppp_ip = ppp_state_cached(connection, link_cache)

            ok, ip, why = prober.check(bind_ip=ppp_ip or None)
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

            if mode in ("wired", "both") and connection:
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
            elif mode == "wireless":
                boot_log("无线未连上（%s），%d 秒后重试" % (link_desc or "原因未知", backoff))
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
def selftest(cfg) -> int:
    from .config import heartbeat_state, rules_path
    from .net import pppoe_connections, ppp_state, wifi_connected_ssid, wifi_profiles
    from .util import is_admin

    line = "-" * 60
    campus = cfg.get("campus") or {}
    print(line)
    print("1) 校园网接入")
    print("   接入方式：", MODE_LABEL.get(campus.get("mode"), campus.get("mode")))
    print("   找到的 PPPoE 连接：", pppoe_connections() or "（没找到）")
    print("   当前使用：", campus.get("connection") or "（未配置）")
    up, ip = ppp_state(campus.get("connection") or "")
    print("   PPPoE 状态：", ("已连接 %s" % ip) if up else "未连接")
    print("   无线 SSID 配置：", campus.get("wifi_ssid") or "（未配置）")
    print("   当前无线：", wifi_connected_ssid() or "未连接")
    print("   已保存无线配置：", len(wifi_profiles()), "个")
    print(line)
    print("2) 密码")
    from .config import get_password
    print("   有线账号：", campus.get("account") or "（未配置）")
    print("   密码能否解开：", "能" if get_password(cfg) else "不能/未配置")
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
    print("   计划任务：", BOOT_TASK, "→", "已注册" if boot_task_registered() else "未注册")
    print("   守护进程：", ("运行中（%s）" % desc) if alive else "未运行")
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
