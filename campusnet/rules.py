# -*- coding: utf-8 -*-
"""规则执行：连上校园网就关代理/VPN；翻墙模式的触发判断。"""
from __future__ import annotations

import time

from .util import (clear_all_user_proxies, list_processes, pids_of,
                   terminate_pid, window_titles, run_cmd)


def running_targets(processes):
    procs = list_processes() or {}
    hit = []
    for img in processes or []:
        if pids_of(procs, img):
            hit.append(img)
    return hit


def kill_processes(processes, log=None, graceful=True):
    """结束这些进程：先礼貌关闭（很多客户端会借机恢复系统代理），再强制结束。"""
    def _log(m):
        if log:
            log(m)

    hits = running_targets(processes)
    if not hits:
        return False, ""
    if graceful:
        for img in hits:
            run_cmd(["taskkill", "/IM", img, "/T"], timeout=25)     # 不发 /F
        time.sleep(3)

    procs = list_processes() or {}
    forced = []
    for img in hits:
        left = pids_of(procs, img)
        if not left:
            continue
        if run_cmd(["taskkill", "/IM", img, "/F", "/T"], timeout=25)[0] != 0:
            for pid in left:
                terminate_pid(pid)
        forced.append(img)

    changed = clear_all_user_proxies()
    detail = "已关闭：" + "、".join(hits)
    if forced:
        detail += "（强制结束：" + "、".join(forced) + "）"
    if changed:
        detail += "，并清理了残留的系统代理设置"
    return True, detail


def flip_triggered(rules, conn=None):
    """“需要翻墙的程序”是不是正在用？

    进程名直接匹配；浏览器（chrome/msedge）按窗口标题关键词判断。
    返回 (是否触发, 是什么触发的)。
    """
    flip = rules.get("flip") or {}
    apps = [str(a).lower() for a in (flip.get("apps") or [])]
    browsers = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe")
    if not apps:
        return False, ""
    procs = list_processes() or {}
    for a in apps:
        if a in browsers:
            continue
        if pids_of(procs, a):
            return True, a
    if any(a in browsers for a in apps):
        hints = [str(h).lower() for h in (flip.get("title_hints") or [])]
        for t in window_titles():
            low = t.lower()
            if any(h in low for h in hints):
                return True, "浏览器：%s" % t[:30]
    return False, ""


def proxy_clients_running(cfg):
    """flip 顺序里任一客户端的内核/进程是否在跑。"""
    procs = list_processes() or {}
    for c in (cfg.get("clients") or []):
        for name in (c.get("cores") or c.get("kill") or []):
            if pids_of(procs, name):
                return True
    return False
