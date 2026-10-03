# -*- coding: utf-8 -*-
"""图形界面（tkinter，无第三方依赖）。

三个分页：
  状态   —— 现在连没连上、生效了什么策略、运行日志、按钮
  校园网 —— **接入方式可选**：有线(PPPoE) / 无线(指定SSID) / 两者；账号密码；关闭代理名单；无线策略
  翻墙   —— **客户端可选、顺序可选、触发程序可选**；目标地区关键词
"""
from __future__ import annotations

import os
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import config as C
from .clients import client_by_id, client_installed, scan_clients
from .config import (APP_TITLE, CONFIG_FILE, HOME_DIR, LOG_FILE, clear_disconnect,
                     clear_flip_active, clear_pause, heartbeat_state, load_config,
                     load_rules, request_disconnect, save_config, save_rules,
                     set_account, set_flip_active, set_pause)
from .guard import boot_task_registered
from .installer import install_boot, uninstall_boot
from .net import (build_prober, campus_link_state, pppoe_connections, ppp_state,
                  ras_dial, ras_hangup, wifi_connect, wifi_profiles)
from .clients import ensure_client_ready
from .rules import flip_triggered
from .util import TrayIcon, create_no_window_flag, dpapi_decrypt, dpapi_encrypt, \
    list_processes, pids_of

MODE_LABEL = {"wired": "有线拨号", "wireless": "无线", "both": "有线+无线"}


class App:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.closing = False
        self.busy = False
        self.online = False
        self.local_ip = ""
        self.reason = ""
        self.link_desc = ""
        self.boot_alive = False
        self.snap = {}
        self.log_lines = []
        self.code_mtime = self._code_mtime()      # 用于"程序更新后自动重启"

        root.title("%s · 校园网助手" % APP_TITLE)
        root.minsize(720, 620)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        self.font = ("Microsoft YaHei UI", 10)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.tab_status = ttk.Frame(nb, padding=10)
        self.tab_campus = ttk.Frame(nb, padding=10)
        self.tab_flip = ttk.Frame(nb, padding=10)
        nb.add(self.tab_status, text="  状态  ")
        nb.add(self.tab_campus, text="  校园网  ")
        nb.add(self.tab_flip, text="  翻墙模式  ")

        self._build_status(self.tab_status)
        self._build_campus(self.tab_campus)
        self._build_flip(self.tab_flip)

        self.tray = TrayIcon("校园网助手（双击打开）", lambda fn: self.root.after(0, fn),
                             self.show_window, self.check_now, self.quit_app)
        self.tray_ok = self.tray.add()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._sync_snap()
        self._refresh_wired_summary()
        self.log("程序已启动（v%s）。接入方式：%s"
                 % (__import__("campusnet").__version__,
                    MODE_LABEL.get(self.snap["mode"], self.snap["mode"])))
        if self.tray_ok:
            self.log("已驻留右下角托盘：点 × 只是把窗口收起来，程序继续在后台跑。")
        self.root.after(150, self._pump)
        self.root.after(400, self._refresh_boot_state)
        self.root.after(600, self._refresh_status)
        self.root.after(15000, self._check_self_update)
        threading.Thread(target=self._watchdog, daemon=True).start()
        threading.Thread(target=self._flip_watch, daemon=True).start()

    # ======================================================= 状态页
    def _build_status(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.dot = ttk.Label(top, text="●", font=("Microsoft YaHei UI", 22), foreground="#9aa0a6")
        self.dot.pack(side="left")
        box = ttk.Frame(top)
        box.pack(side="left", padx=(8, 0))
        self.status_label = ttk.Label(box, text="正在检查…", font=("Microsoft YaHei UI", 13, "bold"))
        self.status_label.pack(anchor="w")
        self.detail_label = ttk.Label(box, text="", font=self.font, foreground="#5f6368")
        self.detail_label.pack(anchor="w")
        self.reason_label = ttk.Label(top, text="", font=("Microsoft YaHei UI", 9), foreground="#9aa0a6")
        self.reason_label.pack(side="right", anchor="e")

        bar = ttk.Frame(f, padding=(0, 12, 0, 6))
        bar.pack(fill="x")
        self.btn_connect = ttk.Button(bar, text="立即连接", command=lambda: self.connect(force=True))
        self.btn_connect.pack(side="left")
        ttk.Button(bar, text="断开", command=self.disconnect).pack(side="left", padx=6)
        ttk.Button(bar, text="立即检查", command=self.check_now).pack(side="left")
        ttk.Button(bar, text="保存设置", command=self.save).pack(side="left", padx=6)
        ttk.Button(bar, text="导出设置…", command=self.export_config).pack(side="left")
        ttk.Button(bar, text="导入设置…", command=self.import_config).pack(side="left", padx=6)
        ttk.Button(bar, text="打开日志", command=self.open_log).pack(side="left")
        ttk.Button(bar, text="检查更新", command=self.check_update).pack(side="left", padx=6)
        self.autostart_var = tk.BooleanVar(value=self._startup_entry_exists())
        ttk.Checkbutton(bar, text="登录后自动打开", variable=self.autostart_var,
                        command=self.toggle_autostart).pack(side="left")
        ttk.Button(bar, text="退出程序", command=self.quit_app).pack(side="right")

        ttk.Label(f, text="运行日志", font=self.font).pack(anchor="w")
        self.log_text = tk.Text(f, height=16, font=("Consolas", 9), wrap="word",
                                background="#fbfbfb", relief="flat")
        sb = ttk.Scrollbar(f, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.hint = ttk.Label(f, foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                              text="点右上角 × 会把窗口隐藏到托盘（任务栏里不出现），程序继续在后台运行。")
        self.hint.pack(anchor="w", pady=(6, 0))

    # ======================================================= 校园网页
    def _build_campus(self, f):
        box = ttk.LabelFrame(f, text=" 接入方式（可选） ", padding=12)
        box.pack(fill="x")
        self.mode_var = tk.StringVar(value=self.cfg["campus"].get("mode", "wired"))
        for val, text in (("wired", "有线（宿舍网口 / 楼道网线）"),
                          ("wireless", "无线：自动连接指定 Wi-Fi"),
                          ("both", "两者都要（有线优先，失败再连无线）")):
            ttk.Radiobutton(box, text=text, value=val, variable=self.mode_var,
                            command=self._on_mode).pack(anchor="w", pady=2)

        # ---- 有线认证方式（可多选；PPPoE 之外还有 DHCP / 静态IP / 门户 / 客户端 / 802.1X）----
        wired_box = ttk.LabelFrame(f, text=" 有线怎么认证（可多选，按此顺序执行） ", padding=12)
        wired_box.pack(fill="x", pady=(10, 0))
        wcfg = (self.cfg["campus"].get("wired") or {})
        saved_auth = [str(a).lower() for a in (wcfg.get("auth") or [])]
        if not saved_auth:
            saved_auth = ["pppoe"] if self.cfg["campus"].get("connection") else ["dhcp"]
        self.wired_vars = {}
        WIRED_OPTS = [
            ("pppoe", "PPPoE 拨号（宿舍网口最常见，需要账号密码）"),
            ("dhcp", "自动获取 IP（插上网线就有 IP，不需要拨号）"),
            ("static", "静态 IP（学校分配了固定 IP / 网关 / DNS）"),
            ("portal", "Web 门户认证（打开登录页输入账号，深信服/锐捷/Dr.COM 这类）"),
            ("client", "学校专用认证客户端（必须跑学校发的软件）"),
            ("lan", "有线 802.1X 认证（Windows 自带，需要先建好配置）"),
            ("restart", "先重启一次网卡再认证（相当于拔插网线，网口卡住时有用）"),
        ]
        for key, text in WIRED_OPTS:
            var = tk.BooleanVar(value=key in saved_auth)
            self.wired_vars[key] = var
            ttk.Checkbutton(wired_box, text=text, variable=var).pack(anchor="w")
        wb = ttk.Frame(wired_box)
        wb.pack(fill="x", pady=(8, 0))
        ttk.Button(wb, text="有线详细设置…", command=self.wired_dialog).pack(side="left")
        ttk.Button(wb, text="网页（门户）认证设置…", command=self.portal_dialog).pack(side="left", padx=6)
        self.wired_summary = ttk.Label(wb, text="", font=("Microsoft YaHei UI", 9),
                                       foreground="#5f6368")
        self.wired_summary.pack(side="left", padx=10)

        grid = ttk.Frame(f, padding=(0, 10, 0, 0))
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="拨号连接：", font=self.font).grid(row=0, column=0, sticky="w", pady=4)
        self.conn_var = tk.StringVar(value=self.cfg["campus"].get("connection", ""))
        conns = pppoe_connections() or [self.cfg["campus"].get("connection", "")]
        ttk.Combobox(grid, textvariable=self.conn_var, values=conns, font=self.font).grid(
            row=0, column=1, sticky="ew", pady=4)
        ttk.Button(grid, text="重新探测", width=10, command=self._rescan_conn).grid(row=0, column=2, padx=6)

        ttk.Label(grid, text="无线 SSID：", font=self.font).grid(row=1, column=0, sticky="w", pady=4)
        self.ssid_var = tk.StringVar(value=self.cfg["campus"].get("wifi_ssid", ""))
        ttk.Combobox(grid, textvariable=self.ssid_var, values=wifi_profiles(), font=self.font).grid(
            row=1, column=1, sticky="ew", pady=4)

        ttk.Label(grid, text="账号：", font=self.font).grid(row=2, column=0, sticky="w", pady=4)
        self.user_var = tk.StringVar(value=self.cfg["campus"].get("account", ""))
        ttk.Entry(grid, textvariable=self.user_var, font=self.font).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(grid, text="密码：", font=self.font).grid(row=3, column=0, sticky="w", pady=4)
        from .util import dpapi_decrypt
        self.pwd_var = tk.StringVar(value=dpapi_decrypt(self.cfg["campus"].get("password_enc", "")))
        self.pwd_entry = ttk.Entry(grid, textvariable=self.pwd_var, font=self.font, show="●")
        self.pwd_entry.grid(row=3, column=1, sticky="ew", pady=4)
        self.show_pwd = tk.BooleanVar(value=False)
        ttk.Checkbutton(grid, text="显示", variable=self.show_pwd, command=self._toggle_pwd).grid(
            row=3, column=2, padx=6)
        ttk.Label(grid, text="密码用 Windows DPAPI 加密后存在本机，不存明文、不上传。",
                  foreground="#5f6368", font=("Microsoft YaHei UI", 9)).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # ---- 手动切换网络 ----
        sw = ttk.LabelFrame(f, text=" 手动切换网络 ", padding=12)
        sw.pack(fill="x", pady=(10, 0))
        self.net_mode_var = tk.StringVar(value="campus")
        ttk.Radiobutton(sw, text="校园网（有线拨号）—— 取消暂停，守护会重新启用网线并拨号",
                        value="campus", variable=self.net_mode_var).pack(anchor="w")
        sw_row = ttk.Frame(sw)
        sw_row.pack(fill="x")
        ttk.Radiobutton(sw_row, text="无线：", value="wifi",
                        variable=self.net_mode_var).pack(side="left")
        self.net_ssid_var = tk.StringVar(value=self.cfg["campus"].get("wifi_ssid") or "")
        ttk.Combobox(sw_row, textvariable=self.net_ssid_var, font=self.font, width=26,
                     values=wifi_profiles()).pack(side="left", padx=6)
        ttk.Radiobutton(sw, text="全部断开（只用别的网络，比如手机热点）", value="none",
                        variable=self.net_mode_var).pack(anchor="w")
        ttk.Button(sw, text="切换到这个网络", command=self.switch_network).pack(
            anchor="w", pady=(8, 0))
        ttk.Label(sw, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="手动选过之后，自动逻辑会让路 —— 不会再自己把校园网拨回来，"
                       "直到你切回「校园网」。").pack(anchor="w", pady=(4, 0))

        kill = ttk.LabelFrame(f, text=" 连上校园网后自动关闭的代理/VPN ", padding=12)
        kill.pack(fill="both", expand=True, pady=(12, 0))
        self.kill_var = tk.BooleanVar(value=bool(self.cfg["guard"].get("kill_proxies", True)))
        ttk.Checkbutton(kill, text="启用（触发时机：拨号前先关一次；连上后再复查一次）",
                        variable=self.kill_var).pack(anchor="w")
        self.kill_list = tk.Listbox(kill, font=("Consolas", 9), height=6)
        self.kill_list.pack(fill="both", expand=True, pady=6)
        kb = ttk.Frame(kill)
        kb.pack(fill="x")
        ttk.Button(kb, text="添加程序…", command=self._kill_add_file).pack(side="left")
        ttk.Button(kb, text="手动输入…", command=self._kill_add_manual).pack(side="left", padx=6)
        ttk.Button(kb, text="删除选中", command=lambda: self._list_del(self.kill_list)).pack(side="left")
        ttk.Button(kb, text="用内置名单", command=self._kill_reset).pack(side="left", padx=6)
        self._fill_list(self.kill_list, self.cfg["guard"].get("kill_processes") or [])

        self.reconnect_var = tk.BooleanVar(
            value=bool(self.cfg["guard"].get("reconnect_when_no_flip")))
        ttk.Checkbutton(kill, variable=self.reconnect_var,
                        text="断开后如果一直没在用代理（没在翻墙），就自动把校园网连回来").pack(
            anchor="w", pady=(6, 0))

        # ---- 跟着 VPN 走：一个开关解决"翻墙时用热点、关掉 VPN 立刻回校园网" ----
        self.follow_vpn_var = tk.BooleanVar(value=bool(self.cfg["guard"].get("follow_vpn")))
        ttk.Checkbutton(kill, variable=self.follow_vpn_var,
                        text="跟着 VPN 走：翻墙时断开校园网并连下面这个无线，"
                             "VPN 一关就立刻切回校园网").pack(anchor="w", pady=(8, 0))
        frow = ttk.Frame(kill)
        frow.pack(fill="x", pady=(2, 0))
        ttk.Label(frow, text="翻墙时连接：").pack(side="left")
        self.vpn_ssid_var = tk.StringVar(
            value=self.cfg["guard"].get("vpn_hotspot_ssid") or "")
        ttk.Combobox(frow, textvariable=self.vpn_ssid_var, font=self.font, width=26,
                     values=wifi_profiles()).pack(side="left", padx=6)
        ttk.Label(frow, text="（手机热点名；留空=不主动连）",
                  foreground="#5f6368").pack(side="left")

        ttk.Label(kill, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="勾上之后：为翻墙而点「断开」→ 翻完墙 / 没在翻墙 → 自动恢复校园网，"
                       "你就不用管了。\n判定「没在用代理」看的是翻墙标记 + 上面的关闭名单里的进程。"
                  ).pack(anchor="w", pady=(2, 0))

        wp = ttk.LabelFrame(f, text=" 无线策略 ", padding=12)
        wp.pack(fill="x", pady=(12, 0))
        self.wifi_policy_var = tk.StringVar(value=self.cfg["guard"].get("wifi_policy", "off"))
        for val, text in (("off", "不干预"), ("manual", "把所有无线改成“手动连接”（不会自动连别的网）"),
                          ("disable", "直接禁用无线网卡")):
            ttk.Radiobutton(wp, text=text, value=val, variable=self.wifi_policy_var).pack(anchor="w")

        sysbox = ttk.LabelFrame(f, text=" 系统级守护（开机 / 锁屏 / 未登录也生效） ", padding=12)
        sysbox.pack(fill="x", pady=(12, 0))
        self.boot_label = ttk.Label(sysbox, text="检查中…", font=self.font, foreground="#5f6368")
        self.boot_label.pack(anchor="w")
        bb = ttk.Frame(sysbox)
        bb.pack(fill="x", pady=(6, 0))
        self.boot_btn = ttk.Button(bb, text="安装/修复", command=self._install_boot)
        self.boot_btn.pack(side="left")
        ttk.Button(bb, text="卸载", command=self._uninstall_boot).pack(side="left", padx=6)

    # ======================================================= 翻墙页
    def _build_flip(self, f):
        self.flip_var = tk.BooleanVar(value=bool(self.cfg["flip"].get("enabled")))
        ttk.Checkbutton(f, text="启用翻墙模式：打开指定程序时，自动开代理并切到可用的目标地区节点",
                        variable=self.flip_var).pack(anchor="w")

        self.flip_when_var = tk.StringVar(value=self.cfg["flip"].get("when") or "off_campus")
        when_row = ttk.Frame(f)
        when_row.pack(fill="x", pady=(6, 0))
        ttk.Label(when_row, text="什么时候翻墙：", font=self.font).pack(side="left")
        ttk.Radiobutton(when_row, text="只在没连校园网时", value="off_campus",
                        variable=self.flip_when_var).pack(side="left", padx=(4, 12))
        ttk.Radiobutton(when_row, text="任何时候（连着校园网也翻墙）", value="always",
                        variable=self.flip_when_var).pack(side="left")
        ttk.Label(f, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="选「只在没连校园网时」= 连着校园网时绝不翻墙（推荐）；"
                       "连着手机热点 / 别的有线无线网时才翻墙。").pack(anchor="w", pady=(2, 0))

        self.browser_always_var = tk.BooleanVar(
            value=bool(self.cfg["flip"].get("browser_always")))
        ttk.Checkbutton(f, text="浏览器一打开就翻墙（不靠窗口标题判断，最可靠 —— "
                                "标题是中文/报错页/新标签页时也能触发）",
                        variable=self.browser_always_var).pack(anchor="w", pady=(4, 0))

        cl = ttk.LabelFrame(f, text=" 翻墙客户端（勾选允许使用的；列表顺序 = 尝试顺序） ", padding=12)
        cl.pack(fill="both", expand=True, pady=(10, 0))
        self.client_list = tk.Listbox(cl, font=("Consolas", 10), height=7, selectmode="extended",
                                      activestyle="none")
        self.client_list.pack(fill="both", expand=True)
        self.client_list.bind("<Double-Button-1>", lambda _e: self.toggle_client())
        self.client_list.bind("<space>", lambda _e: self.toggle_client())
        cb = ttk.Frame(cl)
        cb.pack(fill="x", pady=(6, 0))
        ttk.Button(cb, text="勾选 / 取消勾选", command=self.toggle_client).pack(side="left")
        ttk.Button(cb, text="↑ 上移", command=lambda: self._move_client(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(cb, text="↓ 下移", command=lambda: self._move_client(1)).pack(side="left", padx=4)
        ttk.Button(cb, text="只留勾选的", command=self.keep_checked_clients).pack(side="left", padx=(8, 0))
        ttk.Button(cb, text="扫描本机已装客户端", command=self._scan_clients).pack(side="left", padx=4)
        ttk.Label(cl, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="☑ = 允许使用；只勾 E-IX 就只尝试 E-IX。双击某一行也能切换。\n"
                       "提示：多数 mihomo/Clash 客户端可以直接用「自带内核 + 自己的配置」启动，"
                       "不需要开界面、不需要管理员权限。").pack(anchor="w", pady=(6, 0))
        self._client_rows = []
        self.refresh_client_list()

        ap = ttk.LabelFrame(f, text=" 触发程序（打开这些程序时才会开代理） ", padding=12)
        ap.pack(fill="both", expand=True, pady=(10, 0))
        self.app_list = tk.Listbox(ap, font=("Consolas", 10), height=6)
        self.app_list.pack(fill="both", expand=True)
        ab = ttk.Frame(ap)
        ab.pack(fill="x", pady=(6, 0))
        ttk.Button(ab, text="添加程序…", command=self._flip_add_file).pack(side="left")
        ttk.Button(ab, text="手动输入…", command=self._flip_add_manual).pack(side="left", padx=6)
        ttk.Button(ab, text="删除选中", command=lambda: self._list_del(self.app_list)).pack(side="left")
        ttk.Label(ap, foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="浏览器（chrome/msedge）按“窗口标题里出现关键词”判断，避免一开浏览器就翻墙。").pack(
            anchor="w", pady=(6, 0))
        self._fill_list(self.app_list, self.cfg["flip"].get("apps") or [])

        rg = ttk.LabelFrame(f, text=" 目标地区关键词（节点名里包含这些词才被视为目标地区） ", padding=12)
        rg.pack(fill="x", pady=(10, 0))
        self.region_var = tk.StringVar(value="、".join(self.cfg["flip"].get("region_hints") or []))
        ttk.Entry(rg, textvariable=self.region_var, font=self.font).pack(fill="x")
        self.title_var = tk.StringVar(value="、".join(self.cfg["flip"].get("title_hints") or []))
        ttk.Label(rg, text="浏览器标题关键词（标题里出现这些词，才认为是需要翻墙的网页）：",
                  font=self.font).pack(anchor="w", pady=(8, 2))
        ttk.Entry(rg, textvariable=self.title_var, font=self.font).pack(fill="x")
        ttk.Button(rg, text="恢复默认关键词（含 google / youtube / twitter 等）",
                   command=self.restore_default_hints).pack(anchor="w", pady=(6, 0))
        ttk.Label(rg, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="提示：从老版本升级上来时，这里保留的是你原来的关键词，不会自动增加。"
                       "想让「打开 Google 就翻墙」，点上面的按钮，或手动把 google 加进去。"
                  ).pack(anchor="w", pady=(2, 0))

    # ======================================================= 小工具
    def _fill_list(self, widget, items):
        widget.delete(0, "end")
        for it in items:
            widget.insert("end", it)

    def _list_items(self, widget):
        return [str(widget.get(i)).strip() for i in range(widget.size()) if str(widget.get(i)).strip()]

    def _list_del(self, widget):
        for i in reversed(widget.curselection()):
            widget.delete(i)

    def _move(self, widget, delta):
        sel = widget.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if j < 0 or j >= widget.size():
            return
        text = widget.get(i)
        widget.delete(i)
        widget.insert(j, text)
        widget.selection_set(j)

    def _add_to(self, widget, name):
        name = (name or "").strip().lower()
        if not name:
            return
        if name not in [str(x).lower() for x in widget.get(0, "end")]:
            widget.insert("end", name)

    def _kill_add_file(self):
        p = filedialog.askopenfilename(title="选择要关闭的程序", filetypes=[("程序", "*.exe")])
        if p:
            self._add_to(self.kill_list, os.path.basename(p))

    def _kill_add_manual(self):
        n = simpledialog.askstring("手动输入", "进程名（例如 FlClash.exe）：")
        if n:
            self._add_to(self.kill_list, n if n.lower().endswith(".exe") else n + ".exe")

    def _kill_reset(self):
        self._fill_list(self.kill_list, C.DEFAULT_CONFIG["guard"]["kill_processes"])

    def _flip_add_file(self):
        p = filedialog.askopenfilename(title="选择需要翻墙的程序", filetypes=[("程序", "*.exe")])
        if p:
            self._add_to(self.app_list, os.path.basename(p))

    def _flip_add_manual(self):
        n = simpledialog.askstring("手动输入", "进程名（例如 chatgpt.exe）：")
        if n:
            self._add_to(self.app_list, n if n.lower().endswith(".exe") else n + ".exe")

    # ------------------------------------------------------- 翻墙客户端清单
    def refresh_client_list(self):
        """把「所有已定义客户端 + 当前勾选状态」画进列表。

        列表顺序 = 尝试顺序；勾选状态 = 是否允许使用（存进 flip.order 的只有勾选的）。
        """
        order = list((self.cfg.get("flip") or {}).get("order") or [])
        all_ids = [c.get("id") for c in (self.cfg.get("clients") or []) if c.get("id")]
        # 先按已保存的顺序排，剩下的按配置里的定义顺序补上
        shown = [i for i in order if i in all_ids] + [i for i in all_ids if i not in order]
        self._client_rows = [(i, i in order) for i in shown]
        self.client_list.delete(0, "end")
        for cid, on in self._client_rows:
            c = client_by_id(self.cfg, cid) or {}
            installed = "已装" if client_installed(c) else "未装"
            self.client_list.insert("end", "%s %-12s %-12s %s"
                                    % ("☑" if on else "☐", cid, c.get("name") or "", installed))

    def toggle_client(self):
        for idx in self.client_list.curselection():
            cid, on = self._client_rows[idx]
            self._client_rows[idx] = (cid, not on)
        self.client_list.delete(0, "end")
        for cid, on in self._client_rows:
            c = client_by_id(self.cfg, cid) or {}
            installed = "已装" if client_installed(c) else "未装"
            self.client_list.insert("end", "%s %-12s %-12s %s"
                                    % ("☑" if on else "☐", cid, c.get("name") or "", installed))

    def _move_client(self, delta):
        sel = list(self.client_list.curselection())
        if not sel:
            return
        if delta < 0:
            for idx in sel:
                if idx > 0:
                    row = self._client_rows.pop(idx)
                    self._client_rows.insert(idx - 1, row)
        else:
            for idx in reversed(sel):
                if idx < len(self._client_rows) - 1:
                    row = self._client_rows.pop(idx)
                    self._client_rows.insert(idx + 1, row)
        checked = [c for c, on in self._client_rows if on]
        self.refresh_client_list()
        # 尽量保持原来选中的行
        for idx, (cid, _on) in enumerate(self._client_rows):
            if cid in checked:
                self.client_list.selection_set(idx)

    def keep_checked_clients(self):
        """把勾选的挪到最前面（没勾的留在后面，方便再勾回来）。"""
        checked = [r for r in self._client_rows if r[1]]
        unchecked = [r for r in self._client_rows if not r[1]]
        self._client_rows = checked + unchecked
        self.refresh_client_list()

    def checked_client_ids(self):
        return [cid for cid, on in self._client_rows if on]

    def restore_default_hints(self):
        """把目标地区关键词和浏览器标题关键词恢复成内置默认值。"""
        d = C.DEFAULT_CONFIG["flip"]
        self.region_var.set("、".join(d["region_hints"]))
        self.title_var.set("、".join(d["title_hints"]))
        self.log("已恢复默认关键词：目标地区 %d 个、浏览器标题 %d 个（含 google/youtube/twitter 等），"
                 "记得点「保存设置」。" % (len(d["region_hints"]), len(d["title_hints"])), "ok")

    def _scan_clients(self):
        lines = []
        for c, ok, detail in scan_clients(self.cfg):
            lines.append("%-12s %s  %s" % (c.get("name"), "已安装" if ok else "未找到", detail))
        messagebox.showinfo("本机翻墙客户端", "\n".join(lines) or "（没有可用客户端）")

    def _rescan_conn(self):
        conns = pppoe_connections()
        self.log("探测到 PPPoE 连接：%s" % ("、".join(conns) if conns else "没有"))
        if conns and not self.conn_var.get():
            self.conn_var.set(conns[0])

    @staticmethod
    def _wired_value_of(value):
        """单行文本 → 去掉首尾空白（用于对话框里的小输入框）。"""
        return str(value).strip()

    def _refresh_wired_summary(self):
        w = (self.cfg["campus"].get("wired") or {})
        bits = ["网卡: " + (w.get("adapter") or "(自动选择)")]
        p = w.get("portal") or {}
        if p.get("mode"):
            bits.append("门户: " + str(p["mode"]))
        if p.get("url"):
            bits.append(p["url"][:32])
        st = w.get("static") or {}
        if st.get("address"):
            bits.append("静态IP: " + str(st["address"]))
        if w.get("client_exe"):
            bits.append("客户端: " + os.path.basename(str(w["client_exe"])))
        if w.get("lan_profile"):
            bits.append("802.1X: " + str(w["lan_profile"]))
        self.wired_summary.configure(text="　|　".join(bits))

    def wired_dialog(self):
        """有线接入的详细设置：网卡 / 静态 IP / 门户认证 / 学校客户端 / 802.1X。"""
        from tkinter import filedialog
        from .net import PORTAL_PRESETS, apply_portal_preset, wired_adapters

        w = self.cfg["campus"].setdefault("wired", {})
        p = w.setdefault("portal", {})
        st = w.setdefault("static", {})
        win = tk.Toplevel(self.root)
        win.title("有线接入详细设置")
        win.geometry("660x600")
        win.transient(self.root)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=8)

        # ---------- 网卡 / IP ----------
        t1 = ttk.Frame(nb, padding=14)
        nb.add(t1, text="  网卡 / IP  ")
        ttk.Label(t1, text="有线网卡（留空自动选择“已启用且已连接”的那块）：",
                  font=self.font).grid(row=0, column=0, sticky="w", pady=(0, 4))
        adapter_var = tk.StringVar(value=w.get("adapter") or "")
        ad_box = ttk.Combobox(t1, textvariable=adapter_var, font=self.font, width=44,
                              values=[a[0] for a in wired_adapters(include_virtual=True)])
        ad_box.grid(row=1, column=0, sticky="ew", columnspan=2)
        ttk.Label(t1, text="（列表里带 vEthernet/Virtual 的是虚拟网卡，一般不用选）",
                  foreground="#5f6368", font=("Microsoft YaHei UI", 9)).grid(
            row=2, column=0, sticky="w", pady=(2, 12))

        ttk.Label(t1, text="静态 IP 设置（选了“静态 IP”方式才需要填）：",
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=3, column=0, sticky="w",
                                                                pady=(6, 6))
        rows = [("IP 地址", "address", st.get("address", "")),
                ("子网掩码", "mask", st.get("mask", "255.255.255.0")),
                ("默认网关", "gateway", st.get("gateway", "")),
                ("DNS（多个用逗号隔开）", "dns", "、".join(st.get("dns") or []))]
        st_vars = {}
        for i, (label, key, val) in enumerate(rows):
            ttk.Label(t1, text=label + "：", font=self.font).grid(row=4 + i, column=0,
                                                                 sticky="w", pady=4)
            v = tk.StringVar(value=val)
            st_vars[key] = v
            ttk.Entry(t1, textvariable=v, font=self.font, width=34).grid(
                row=4 + i, column=1, sticky="ew", pady=4)
        t1.columnconfigure(1, weight=1)
        ttk.Label(t1, text="示意图：学校给的 IP 要填全（地址+掩码+网关），否则会断网。",
                  foreground="#5f6368", font=("Microsoft YaHei UI", 9)).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # ---------- 门户认证 ----------
        t2 = ttk.Frame(nb, padding=14)
        nb.add(t2, text="  Web 门户认证  ")
        ttk.Label(t2, text="拿到 IP 后需要打开登录页输账号的情况（深信服 / 锐捷 / Dr.COM 等）",
                  foreground="#5f6368", font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        # 厂商预设：一键把常见字段名填好
        from .net import PORTAL_PRESETS
        preset_row = ttk.Frame(t2)
        preset_row.pack(fill="x", pady=(8, 0))
        ttk.Label(preset_row, text="厂商预设：", font=self.font).pack(side="left")
        preset_var = tk.StringVar(value="")
        preset_labels = [PORTAL_PRESETS[k]["label"] for k in PORTAL_PRESETS]
        preset_box = ttk.Combobox(preset_row, textvariable=preset_var, values=preset_labels,
                                  font=self.font, width=22, state="readonly")
        preset_box.pack(side="left", padx=(4, 8))
        preset_note = ttk.Label(t2, text="", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                                wraplength=560, justify="left")
        preset_note.pack(anchor="w", pady=(4, 0))

        p_mode = tk.StringVar(value=p.get("mode") or "auto")
        ttk.Label(t2, text="方式：", font=self.font).pack(anchor="w", pady=(8, 2))
        for val, text in (("auto", "自动（打开登录页 → 找表单 → 填账号密码 → 提交）"),
                          ("template", "按模板提交（知道门户接口时最准，兼容各种厂商）"),
                          ("script", "执行命令（学校给了脚本或命令行工具时最省事）")):
            ttk.Radiobutton(t2, text=text, value=val, variable=p_mode).pack(anchor="w")

        pf = ttk.Frame(t2)
        pf.pack(fill="x", pady=(10, 0))
        pf.columnconfigure(1, weight=1)
        p_vars = {}
        prows = [("门户地址", "url", p.get("url", ""), 48),
                 ("请求方式(post/get)", "method", p.get("method", "post"), 48),
                 ("提交内容模板", "body", p.get("body", "username={username}&password={password}"), 48),
                 ("执行命令", "script", p.get("script", ""), 48),
                 ("门户账号", "username", p.get("username", ""), 48),
                 ("门户密码", "password", dpapi_decrypt(p.get("password_enc", "")), 48),
                 ("判定地址", "probe_url", p.get("probe_url", ""), 48)]
        for i, (label, key, val, width) in enumerate(prows):
            ttk.Label(pf, text=label + "：", font=self.font).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=val)
            p_vars[key] = v
            show = "●" if key == "password" else ""
            ttk.Entry(pf, textvariable=v, font=self.font, width=width, show=show).grid(
                row=i, column=1, sticky="ew", pady=3)
        ttk.Label(t2, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="模板里可用占位符：{username} {password}。\n"
                       "各校字段名可能不同，最稳的办法是：先手动登录一次门户 → F12 → "
                       "Network → 找到那条登录请求 → 照抄地址和内容。").pack(
            anchor="w", pady=(10, 0))

        def apply_preset(_event=None):
            want = None
            for key, item in PORTAL_PRESETS.items():
                if item["label"] == preset_var.get():
                    want = key
                    break
            if not want:
                return
            merged, note = apply_portal_preset({}, want)
            if merged.get("mode"):
                p_mode.set(merged["mode"])
            for key, widget_key in (("url", "url"), ("method", "method"), ("body", "body")):
                if merged.get(key) is not None:
                    p_vars[widget_key].set(str(merged.get(key) or ""))
            preset_note.configure(text=note)

        preset_box.bind("<<ComboboxSelected>>", apply_preset)

        def test_portal():
            """按当前填的内容真的试一次门户认证，把过程打到主窗口日志里。"""
            cfg_try = {
                "mode": p_mode.get(),
                "url": p_vars["url"].get().strip(),
                "method": p_vars["method"].get().strip() or "post",
                "body": p_vars["body"].get().strip(),
                "script": p_vars["script"].get().strip(),
                "probe_url": p_vars["probe_url"].get().strip(),
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            }
            user = p_vars["username"].get().strip() or self.snap.get("account", "")
            pwd = p_vars["password"].get() or self.snap.get("password", "")
            self.log("— 开始测试门户认证（方式：%s）—" % cfg_try["mode"], "warn")

            def run():
                from .net import portal_login, portal_probe
                online, page = portal_probe(cfg_try.get("probe_url") or None)
                if online:
                    self.log("现在就已经能上网（没被门户拦住）——不需要认证。", "ok")
                    return
                self.log("检测到门户登录页：%s" % (page or "(没拿到)"))
                ok, msg = portal_login(cfg_try, user, pwd, lambda m: self.log(m))
                self.log("门户测试结果：%s（%s）" % ("成功" if ok else "未成功", msg),
                         "ok" if ok else "err")

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(t2, text="测试门户认证", command=test_portal).pack(anchor="w", pady=(10, 0))

        # ---------- 客户端 / 802.1X ----------
        t3 = ttk.Frame(nb, padding=14)
        nb.add(t3, text="  客户端 / 802.1X  ")
        ttk.Label(t3, text="学校专用认证客户端（选了“学校客户端”方式才需要）：",
                  font=self.font).pack(anchor="w")
        cl = ttk.Frame(t3)
        cl.pack(fill="x", pady=(4, 14))
        client_var = tk.StringVar(value=w.get("client_exe") or "")
        ttk.Entry(cl, textvariable=client_var, font=self.font, width=52).pack(side="left", fill="x",
                                                                             expand=True)

        def pick_client():
            f = filedialog.askopenfilename(title="选择学校认证客户端", filetypes=[("程序", "*.exe")],
                                           parent=win)
            if f:
                client_var.set(f)

        ttk.Button(cl, text="浏览…", command=pick_client).pack(side="left", padx=6)

        ttk.Label(t3, text="有线 802.1X 配置名（先用 Windows 自己建好这个配置，再填名字）：",
                  font=self.font).pack(anchor="w")
        lan_var = tk.StringVar(value=w.get("lan_profile") or "")
        ttk.Entry(t3, textvariable=lan_var, font=self.font, width=52).pack(anchor="w", pady=(4, 6))
        ttk.Label(t3, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="802.1X 怎么建：控制面板 → 网络和共享中心 → 更改适配器设置 →\n"
                       "右键有线网卡 → 属性 → 身份验证 → 勾选“启用 IEEE 802.1X 身份验证” →\n"
                       "选择你的认证方式并填账号密码，保存后回到这里把配置名填上。").pack(anchor="w")

        # ---------- 保存 ----------
        def save_all():
            w["adapter"] = adapter_var.get().strip()
            w["static"] = {
                "address": st_vars["address"].get().strip(),
                "mask": st_vars["mask"].get().strip() or "255.255.255.0",
                "gateway": st_vars["gateway"].get().strip(),
                "dns": [x.strip() for x in st_vars["dns"].get().replace("，", ",").replace("、", ",").split(",")
                        if x.strip()],
            }
            w["client_exe"] = client_var.get().strip()
            w["lan_profile"] = lan_var.get().strip()
            p.update({
                "mode": p_mode.get(),
                "url": p_vars["url"].get().strip(),
                "method": (p_vars["method"].get().strip() or "post").lower(),
                "body": p_vars["body"].get().strip(),
                "script": p_vars["script"].get().strip(),
                "username": p_vars["username"].get().strip(),
                "password_enc": dpapi_encrypt(p_vars["password"].get()) if p_vars["password"].get() else "",
                "probe_url": p_vars["probe_url"].get().strip(),
                "headers": p.get("headers") or {"Content-Type":
                                                "application/x-www-form-urlencoded"},
            })
            self.cfg["campus"]["wired"] = w
            self.save()
            self._refresh_wired_summary()
            self.log("有线详细设置已保存。", "ok")
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar, text="保存", command=save_all).pack(side="right")
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=6)

    def _on_mode(self):
        self.log("接入方式改为：%s（保存后生效）" % MODE_LABEL.get(self.mode_var.get(), ""))

    def _toggle_pwd(self):
        self.pwd_entry.configure(show="" if self.show_pwd.get() else "●")

    # ======================================================= 状态刷新
    def log(self, text, level="info"):
        stamp = time.strftime("%H:%M:%S")
        prefix = {"info": "", "ok": "✔ ", "warn": "! ", "err": "✘ "}.get(level, "")
        line = "[%s] %s%s" % (stamp, prefix, text)
        self.queue.put(("log", line))
        try:
            os.makedirs(HOME_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _pump(self):
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "status":
                    text, color, level = payload
                    self.status_label.configure(text=text)
                    self.dot.configure(foreground=color)
                    if level == "online":
                        self.detail_label.configure(text="本机 IP：%s　%s" % (self.local_ip or "—",
                                                                        self.link_desc or ""))
                    elif level == "busy":
                        self.detail_label.configure(text="请稍候…")
                    else:
                        self.detail_label.configure(text=self.link_desc or "未连接")
                elif kind == "reason":
                    self.reason_label.configure(text=payload)
                elif kind == "buttons":
                    state = "normal" if payload else "disabled"
                    self.btn_connect.configure(state=state)
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _set_status(self, text, level):
        color = {"online": "#1a7f37", "offline": "#c5221f",
                 "busy": "#e37400", "unknown": "#9aa0a6"}.get(level, "#9aa0a6")
        self.queue.put(("status", (text, color, level)))

    def _sync_snap(self):
        self.snap = {
            "mode": self.mode_var.get(),
            "connection": self.conn_var.get().strip(),
            "ssid": self.ssid_var.get().strip(),
            "account": self.user_var.get().strip(),
            "password": self.pwd_var.get(),
        }

    def _refresh_status(self):
        def work():
            campus = dict(self.cfg["campus"])
            campus["mode"] = self.snap.get("mode") or campus.get("mode")
            campus["connection"] = self.snap.get("connection") or campus.get("connection")
            campus["wifi_ssid"] = self.snap.get("ssid") or campus.get("wifi_ssid")
            wired_cfg = campus.get("wired") or {}
            up, desc = campus_link_state(campus.get("mode"), campus.get("connection"),
                                         campus.get("wifi_ssid"), {}, wired_cfg)
            self.link_desc = desc
            prober = build_prober(campus, campus.get("connection"))
            from .net import wired_auth_list, wired_bind_ip
            auths = wired_auth_list(wired_cfg, campus.get("connection") or "")
            _ppp_up, ppp_ip = ppp_state(campus.get("connection") or "") \
                if "pppoe" in auths else (False, "")
            bind = ppp_ip or (wired_bind_ip(wired_cfg) if campus.get("mode") == "wired" else "")
            ok, ip, why = prober.check(bind_ip=bind or None)
            self.online, self.local_ip, self.reason = ok, ip, why
            if ok:
                self._set_status("已联网", "online")
                self.queue.put(("reason", why))
            else:
                self._set_status("未联网", "offline")
                self.queue.put(("reason", desc or "连不上校园网"))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(15000, self._refresh_status)

    def _refresh_boot_state(self):
        if self.closing:
            return
        try:
            self.boot_alive, desc = heartbeat_state()
            if self.boot_alive:
                self.boot_label.configure(text="● 系统级守护运行中（%s）" % (desc or "正常"),
                                          foreground="#1a7f37")
                self.boot_btn.configure(text="重装/修复")
            elif boot_task_registered():
                self.boot_label.configure(text="已安装，但守护没在跑 → 点“安装/修复”",
                                          foreground="#e37400")
            else:
                self.boot_label.configure(text="未安装（开机/锁屏时不会自动工作）",
                                          foreground="#9aa0a6")
        except Exception:
            pass
        self.root.after(15000, self._refresh_boot_state)

    def portal_dialog(self):
        """网页（门户）认证 —— 有线无线共用的一份设置。"""
        from .net import PORTAL_PRESETS, apply_portal_preset
        p = self.cfg["campus"].setdefault("portal", {})
        win = tk.Toplevel(self.root)
        win.title("网页（门户）认证设置")
        win.geometry("640x560")
        win.transient(self.root)

        ttk.Label(win, justify="left", font=("Microsoft YaHei UI", 9), foreground="#5f6368",
                  text="适用：连上网后打开网页会跳登录页的校园网（有线或无线都可能这样）。\n"
                       "有线和无线共用这一份设置。").pack(anchor="w", padx=14, pady=(12, 6))

        row = ttk.Frame(win)
        row.pack(fill="x", padx=14)
        ttk.Label(row, text="厂商预设：", font=self.font).pack(side="left")
        preset_var = tk.StringVar(value="")
        preset_box = ttk.Combobox(row, textvariable=preset_var, font=self.font, width=24,
                                  state="readonly",
                                  values=[PORTAL_PRESETS[k]["label"] for k in PORTAL_PRESETS])
        preset_box.pack(side="left", padx=(4, 8))
        note = ttk.Label(win, text="", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                         wraplength=580, justify="left")
        note.pack(anchor="w", padx=14, pady=(6, 0))

        mode_var = tk.StringVar(value=p.get("mode") or "auto")
        ttk.Label(win, text="方式：", font=self.font).pack(anchor="w", padx=14, pady=(8, 2))
        for val, text in (("auto", "自动（打开登录页 → 找表单 → 填账号密码 → 提交）"),
                          ("template", "按模板提交（知道门户接口时最准，兼容各种厂商）"),
                          ("script", "执行命令（学校给了脚本或命令行工具时最省事）")):
            ttk.Radiobutton(win, text=text, value=val, variable=mode_var).pack(anchor="w", padx=14)

        pf = ttk.Frame(win, padding=(14, 10, 14, 0))
        pf.pack(fill="x")
        pf.columnconfigure(1, weight=1)
        p_vars = {}
        rows = [("门户地址", "url", p.get("url", "")),
                ("请求方式(post/get)", "method", p.get("method", "post")),
                ("提交内容模板", "body", p.get("body", "username={username}&password={password}")),
                ("执行命令", "script", p.get("script", "")),
                ("门户账号", "username", p.get("username", "")),
                ("门户密码", "password", dpapi_decrypt(p.get("password_enc", ""))),
                ("判定地址", "probe_url", p.get("probe_url", ""))]
        for i, (label, key, val) in enumerate(rows):
            ttk.Label(pf, text=label + "：", font=self.font).grid(row=i, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=val)
            p_vars[key] = v
            ttk.Entry(pf, textvariable=v, font=self.font, show=("●" if key == "password" else "")
                      ).grid(row=i, column=1, sticky="ew", pady=3)

        ttk.Label(win, justify="left", foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="占位符：{username} {password}。不知道自己学校的接口就点下面的\n"
                       "「测试网页认证」：它会真实试一次，过程和结果写进主窗口的日志。").pack(
            anchor="w", padx=14, pady=(10, 0))

        def collect():
            return {
                "mode": mode_var.get(),
                "url": p_vars["url"].get().strip(),
                "method": (p_vars["method"].get().strip() or "post").lower(),
                "body": p_vars["body"].get().strip(),
                "script": p_vars["script"].get().strip(),
                "username": p_vars["username"].get().strip(),
                "password_enc": dpapi_encrypt(p_vars["password"].get()) if p_vars["password"].get() else "",
                "probe_url": p_vars["probe_url"].get().strip(),
                "preset": p.get("preset", ""),
                "headers": p.get("headers") or {"Content-Type":
                                                "application/x-www-form-urlencoded"},
            }

        def apply_preset(_event=None):
            want = next((k for k, item in PORTAL_PRESETS.items()
                         if item["label"] == preset_var.get()), None)
            if not want:
                return
            merged, tip = apply_portal_preset({}, want)
            mode_var.set(merged.get("mode") or "auto")
            for key in ("url", "method", "body"):
                if merged.get(key) is not None:
                    p_vars[key].set(str(merged.get(key) or ""))
            note.configure(text=tip)

        preset_box.bind("<<ComboboxSelected>>", apply_preset)

        def test_portal():
            cfg_try = collect()
            cfg_try["headers"] = {"Content-Type": "application/x-www-form-urlencoded"}
            user = p_vars["username"].get().strip() or self.snap.get("account", "")
            pwd = p_vars["password"].get() or self.snap.get("password", "")
            self.log("— 测试网页（门户）认证，方式：%s —" % cfg_try["mode"], "warn")

            def run():
                from .net import portal_login, portal_probe
                online, page = portal_probe(cfg_try.get("probe_url") or None)
                if online:
                    self.log("现在就能上网（没被门户拦住）—— 不需要认证，或者已经认证过了。", "ok")
                    return
                self.log("检测到门户登录页：%s" % (page or "(没拿到)"))
                ok, msg = portal_login(cfg_try, user, pwd, lambda m: self.log(m))
                self.log("网页认证测试：%s（%s）" % ("成功" if ok else "未成功", msg),
                         "ok" if ok else "err")

            threading.Thread(target=run, daemon=True).start()

        ttk.Button(win, text="测试网页认证", command=test_portal).pack(anchor="w", padx=14, pady=(10, 0))

        def save_close():
            self.cfg["campus"]["portal"] = collect()
            self.save()
            self.log("网页（门户）认证设置已保存（有线和无线都会用这份）。", "ok")
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=12)
        ttk.Button(bar, text="保存", command=save_close).pack(side="right")
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=6)

    def check_update(self):
        """查询 GitHub 最新 Release，和本机版本比较。"""
        def work():
            import urllib.request
            from . import __version__
            url = ("https://api.github.com/repos/tanshaojie666/campusnet-assistant"
                   "/releases/latest")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "CampusNetAssistant"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=15) as resp:
                    data = json.load(resp)
                latest = (data.get("tag_name") or "").lstrip("vV")
                if latest and latest != __version__:
                    self.log("发现新版本：%s（当前 %s）→ %s"
                             % (latest, __version__, data.get("html_url")), "warn")
                else:
                    self.log("已是最新版本（%s）。" % __version__, "ok")
            except Exception as exc:  # noqa: BLE001
                self.log("检查更新失败（多半是访问不了 GitHub）：%s" % exc, "warn")
                self.log("可以手动看：https://github.com/tanshaojie666/campusnet-assistant/releases", "warn")

        self.log("正在检查更新…")
        threading.Thread(target=work, daemon=True).start()

    def switch_network(self):
        """手动切换网络：校园网 / 某个无线 / 全部断开。

        复用了已经验证过的机制，不新造轮子：
          · 切校园网 = 取消暂停与断开状态 → 守护会重新启用网线并拨号
          · 切无线   = 暂停自动拨号 + 请求守护断开校园网（必要时禁网卡）+ 连该 SSID
          · 全部断开 = 暂停自动拨号 + 请求守护断开校园网
        并记下"手动选择"，让「没翻墙就自动连回校园网」那个自动逻辑让路。
        """
        from .config import clear_disabled_adapter, clear_network_choice, \
            clear_pause, request_disconnect, set_network_choice
        choice = self.net_mode_var.get()
        try:
            if choice == "campus":
                clear_network_choice()
                clear_pause()
                clear_disconnect()
                clear_disabled_adapter()
                self.log("已切换到校园网：取消暂停与断开状态，守护会在 15 秒内重新启用网线并拨号。",
                         "ok")
            elif choice == "wifi":
                ssid = self.net_ssid_var.get().strip()
                if not ssid:
                    self.log("请先选择或填写一个无线网络名（SSID）。", "warn")
                    return
                set_network_choice("wifi", ssid)
                set_pause(24 * 60)
                request_disconnect("手动切换到无线：%s" % ssid)
                ok, _out = wifi_connect(ssid)
                self.log("已切换到无线「%s」：连接%s；校园网已请求断开（约 15 秒生效），"
                         "自动拨号已暂停。" % (ssid, "已发起" if ok else "发起失败"),
                         "ok" if ok else "warn")
                self.cfg["campus"]["wifi_ssid"] = ssid
                self.ssid_var.set(ssid)
                self.save()
            else:
                set_network_choice("none")
                set_pause(24 * 60)
                request_disconnect("手动全部断开")
                self.log("已请求断开校园网并暂停自动拨号（约 15 秒生效）；"
                         "无线/热点那些不归本程序管，不受影响。", "warn")
            self._refresh_status()
        except Exception as exc:  # noqa: BLE001
            self.log("切换网络失败：%s" % exc, "err")

    def check_now(self):
        self._refresh_status()

    # ------------------------------------------------------- 自动更新（换新版本）
    @staticmethod
    def _code_mtime():
        """程序包（campusnet/*.py）里最新的修改时间。"""
        latest = 0.0
        base = os.path.dirname(os.path.abspath(__file__))
        try:
            for name in os.listdir(base):
                if name.endswith((".py", ".pyw")):
                    try:
                        latest = max(latest, os.path.getmtime(os.path.join(base, name)))
                    except OSError:
                        pass
        except OSError:
            pass
        return latest

    def restart_self(self):
        """用新代码把自己重新启动（界面会闪一下，托盘图标会回来）。

        为什么要这个：程序更新后，正在跑的旧进程仍然执行内存里的旧代码 ——
        以前必须让用户手动关掉再打开。现在检测到文件变化就自动换新版本，
        避免"修好了但没生效"。
        """
        try:
            self.stop_event.set()
            clear_flip_active()
            self.tray.remove()
        except Exception:
            pass
        # 先把自己持有的单实例锁放掉，否则新实例会以为"已经有程序在跑"而不启动
        try:
            if getattr(self, "gui_mutex", None):
                import ctypes
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.gui_mutex))
                self.gui_mutex = None
        except Exception:
            pass
        exe = sys.executable or "python.exe"
        if exe.lower().endswith("python.exe"):
            cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
            if os.path.isfile(cand):
                exe = cand
        entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "CampusNetAssistant.pyw")
        args = [exe, entry, "--minimized"]
        ok = False
        try:
            subprocess.Popen(args, close_fds=True,
                             creationflags=create_no_window_flag())
            ok = True
        except Exception as exc:  # noqa: BLE001
            try:
                self.log("自动重启失败（%s），请手动关掉再打开一次。" % exc, "err")
            except Exception:
                pass
        if ok:
            try:
                self.log("程序文件已更新，已自动重启为新版本（窗口收到托盘里）。", "ok")
            except Exception:
                pass
        self.closing = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def _check_self_update(self):
        """每 15 秒看一眼程序文件有没有被更新过。"""
        if self.closing:
            return
        try:
            if self._code_mtime() > self.code_mtime:
                self.log("检测到程序文件已更新，正在自动重启以加载新版本…", "warn")
                self.root.after(600, self.restart_self)
                return
        except Exception:
            pass
        self.root.after(15000, self._check_self_update)

    # ------------------------------------------------------- 登录自启
    @staticmethod
    def _startup_path():
        return os.path.join(os.environ.get("APPDATA", ""),
                            r"Microsoft\Windows\Start Menu\Programs\Startup\校园网助手.cmd")

    def _startup_entry_exists(self):
        return os.path.isfile(self._startup_path())

    def toggle_autostart(self):
        """在“启动”文件夹里放/删一个快捷启动脚本（登录后后台打开，无窗口）。"""
        path = self._startup_path()
        try:
            if self.autostart_var.get():
                import sys
                exe = sys.executable or "python.exe"
                if exe.lower().endswith("python.exe"):
                    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
                    if os.path.isfile(cand):
                        exe = cand
                entry = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "CampusNetAssistant.pyw")
                # 内容保持纯 ASCII：.cmd 里的中文可能被 cmd.exe 按其它代码页误解
                body = ('@echo off\r\n'
                        'rem CampusNetAssistant - start minimized after sign-in\r\n'
                        'start "" "%s" "%s" --minimized\r\n' % (exe, entry))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="ascii", errors="replace", newline="") as fh:
                    fh.write(body)
                self.log("已开启：登录后会后台打开校园网助手（窗口收在托盘里）。", "ok")
            else:
                if os.path.isfile(path):
                    os.remove(path)
                self.log("已关闭登录自启。", "ok")
        except Exception as exc:  # noqa: BLE001
            self.log("设置登录自启失败：%s" % exc, "err")
            try:
                self.autostart_var.set(not self.autostart_var.get())
            except Exception:
                pass

    # ======================================================= 动作
    def collect_config(self):
        cfg = self.cfg
        cfg["campus"]["mode"] = self.mode_var.get()
        cfg["campus"]["connection"] = self.conn_var.get().strip()
        cfg["campus"]["wifi_ssid"] = self.ssid_var.get().strip()
        cfg["campus"]["account"] = self.user_var.get().strip()
        from .util import dpapi_encrypt
        cfg["campus"]["password_enc"] = dpapi_encrypt(self.pwd_var.get()) if self.pwd_var.get() else ""
        # 有线认证方式（多选）
        wired = cfg["campus"].setdefault("wired", {})
        wired["auth"] = [k for k, v in self.wired_vars.items() if v.get()]
        cfg["guard"]["kill_proxies"] = bool(self.kill_var.get())
        cfg["guard"]["kill_processes"] = self._list_items(self.kill_list)
        cfg["guard"]["wifi_policy"] = self.wifi_policy_var.get()
        cfg["guard"]["reconnect_when_no_flip"] = bool(self.reconnect_var.get())
        cfg["guard"]["follow_vpn"] = bool(self.follow_vpn_var.get())
        cfg["guard"]["vpn_hotspot_ssid"] = self.vpn_ssid_var.get().strip()
        cfg["flip"]["enabled"] = bool(self.flip_var.get())
        cfg["flip"]["when"] = self.flip_when_var.get() or "off_campus"
        cfg["flip"]["browser_always"] = bool(self.browser_always_var.get())
        cfg["flip"]["order"] = self.checked_client_ids() or []      # 只有勾选的客户端会被使用
        cfg["flip"]["apps"] = self._list_items(self.app_list)
        cfg["flip"]["region_hints"] = [x.strip() for x in self.region_var.get().replace("、", ",").split(",") if x.strip()]
        cfg["flip"]["title_hints"] = [x.strip() for x in self.title_var.get().replace("、", ",").split(",") if x.strip()]
        save_config(cfg)
        rules = load_rules(None)
        rules["kill_proxies"] = cfg["guard"]["kill_proxies"]
        rules["processes"] = cfg["guard"]["kill_processes"]
        rules["wifi_policy"] = cfg["guard"]["wifi_policy"]
        rules["reconnect_when_no_flip"] = cfg["guard"]["reconnect_when_no_flip"]
        rules["follow_vpn"] = cfg["guard"]["follow_vpn"]
        rules["vpn_hotspot_ssid"] = cfg["guard"]["vpn_hotspot_ssid"]
        rules["flip"] = cfg["flip"]
        save_rules(rules)
        if cfg["campus"]["account"] and self.pwd_var.get():
            set_account(cfg, cfg["campus"]["account"], self.pwd_var.get())
        self._sync_snap()
        return cfg

    def export_config(self):
        """把设置导出成一个 JSON 文件（密码是密文，换到别的电脑/账户要重新填）。"""
        from tkinter import filedialog
        try:
            self.save()
            path = filedialog.asksaveasfilename(
                title="导出设置", defaultextension=".json",
                initialfile="校园网助手-设置.json", filetypes=[("JSON 文件", "*.json")])
            if not path:
                return
            shutil.copy2(CONFIG_FILE, path)
            self.log("设置已导出：%s" % path, "ok")
            self.log("提示：里面的密码是用本机账户加密的，换台电脑要重新填一次密码。", "warn")
        except Exception as exc:  # noqa: BLE001
            self.log("导出失败：%s" % exc, "err")

    def import_config(self):
        from tkinter import filedialog, messagebox
        try:
            path = filedialog.askopenfilename(title="导入设置",
                                              filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
            if not path:
                return
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("不是有效的设置文件")
            if not messagebox.askokcancel(APP_TITLE,
                                          "导入会覆盖当前设置（含接入方式、认证方式、名单、翻墙设置）。\n"
                                          "确定继续吗？"):
                return
            with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            self.log("设置已导入。建议关掉程序再重新打开，让各页面都刷新。", "ok")
            messagebox.showinfo(APP_TITLE, "导入完成。请关闭程序后重新打开以生效。")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "导入失败：%s" % exc)

    def save(self):
        try:
            self.collect_config()
            self.log("设置已保存（系统级守护会在 15 秒内读到新规则）", "ok")
        except Exception as exc:  # noqa: BLE001
            self.log("保存失败：%s" % exc, "err")

    def connect(self, force=False):
        """立即连接：取消"先别拨"暂停、断开请求和手动切换选择，然后开始拨号。"""
        clear_pause()
        clear_disconnect()
        try:
            from .config import clear_network_choice
            clear_network_choice()          # 点了"立即连接"就是明确要校园网
        except Exception:
            pass
        if self.busy:
            return
        self.busy = True
        self.queue.put(("buttons", False))
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self):
        try:
            self.save()
            s = self.snap
            wired_cfg = self.cfg["campus"].get("wired") or {}
            auths = [k for k, v in self.wired_vars.items() if v.get()]
            if s["mode"] in ("wired", "both"):
                if "pppoe" in auths and s["connection"]:
                    # 系统级守护在跑就交给它拨：它权限足、能恢复被禁用的网卡、
                    # 还能清卡死状态；而且**两边同时拨号会互相杀进程**
                    # （实测会出现"错误 1：正在连接到 宽带连接..."）。
                    alive, _desc = heartbeat_state()
                    if alive:
                        from .config import request_connect
                        request_connect("界面点了立即连接")
                        self.log("已请系统级守护去拨号（约 15 秒内生效）——"
                                 "它权限更足，万一网卡被禁用也能恢复。", "ok")
                    else:
                        self.log("正在拨号（%s）…" % s["connection"])
                        ras_hangup(s["connection"])
                        code, text = ras_dial(s["connection"], s["account"], s["password"])
                        if code != 0:
                            from .net import disabled_wired_adapters, friendly_error
                            if code == 756 and disabled_wired_adapters():
                                self.log("有线网卡当前是禁用状态，界面拔不了号 —— "
                                         "双击「③ 安装开机自动连」装好守护后它才能自动恢复。",
                                         "warn")
                            else:
                                self.log("拨号失败 → " + friendly_error(code, text), "err")
                        else:
                            self.log("拨号成功。", "ok")
                elif any(a in auths for a in ("dhcp", "static", "portal", "client", "lan", "restart")):
                    from .net import wired_authenticate
                    self.log("按配置接入有线（%s）…" % "+".join(auths))
                    wired_authenticate(wired_cfg, s.get("connection", ""),
                                       s.get("account", ""), s.get("password", ""), self.log)
                else:
                    self.log("没有勾选任何有线认证方式，请在上面勾一个。", "warn")
            if s["mode"] in ("wireless", "both") and s["ssid"]:
                from .net import wifi_connect
                self.log("正在连接无线 %s …" % s["ssid"])
                ok, _ = wifi_connect(s["ssid"])
                self.log("无线连接%s。" % ("已发起" if ok else "失败"), "ok" if ok else "err")
            time.sleep(4)
            self._refresh_status()
        finally:
            self.busy = False
            self.queue.put(("buttons", True))

    def disconnect(self):
        """断开校园网。

        注意：拨号是**系统级守护以 SYSTEM 身份**建立的，界面程序没有权限断开它
        （直接 rasdial /disconnect 会静默失败，用户看到的就是"点了没反应/断不开"）。
        所以这里做两件事：
          1. 写「暂停自动拨号」标志（守护看到就不会马上又拨回来）
          2. 写「断开请求」文件，让守护去真正挂断（它有权限，最多 15 秒生效）
        """
        s = self.snap
        set_pause(24 * 60)                       # 暂停到用户主动点「立即连接」为止
        try:
            ras_hangup(s["connection"]) if s.get("connection") else None
        except Exception:
            pass
        request_disconnect("用户在界面点了断开")
        self.log("已请求断开校园网：系统级守护会在 15 秒内执行（它有权限，界面没有）。"
                 "自动拨号已暂停，直到你点「立即连接」。", "warn")
        self._refresh_status()

    def open_log(self):
        try:
            os.makedirs(HOME_DIR, exist_ok=True)
            if not os.path.isfile(LOG_FILE):
                open(LOG_FILE, "a", encoding="utf-8").close()
            os.startfile(LOG_FILE)          # noqa: S606
        except Exception as exc:  # noqa: BLE001
            self.log("打开日志失败：%s" % exc, "err")

    def _install_boot(self):
        self.save()
        self._run_elevated("--install-boot")

    def _uninstall_boot(self):
        if messagebox.askokcancel(APP_TITLE, "确定卸载系统级守护吗？卸载后开机/锁屏时不再自动连校园网。"):
            self._run_elevated("--uninstall-boot")

    def _run_elevated(self, arg):
        import ctypes
        import sys
        import ctypes.wintypes as wt
        exe = sys.executable or "python.exe"
        if exe.lower().endswith("pythonw.exe"):
            cand = os.path.join(os.path.dirname(exe), "python.exe")
            if os.path.isfile(cand):
                exe = cand
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "CampusNetAssistant.pyw")
        params = '/k ""%s" "%s" %s"' % (exe, script, arg) if os.path.isfile(script) \
            else '/k ""%s" -m campusnet %s"' % (exe, arg)
        try:
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
                                              wt.LPCWSTR, ctypes.c_int]
            rc = shell32.ShellExecuteW(None, "runas", "cmd.exe", params,
                                       os.path.dirname(script), 1)
        except Exception as exc:  # noqa: BLE001
            self.log("没能打开管理员窗口：%s" % exc, "err")
            return
        if not rc or rc <= 32:
            self.log("没有获得管理员权限（可能点了“否”）。", "warn")
            return
        self.log("已弹出管理员窗口，请在里面完成安装；完成后这里会自动更新状态。", "ok")
        self.root.after(9000, self._refresh_boot_state)

    # ======================================================= 后台线程
    def _watchdog(self):
        """界面在跑时的兜底：掉线自动重连（系统级守护在跑时交给它）。"""
        while not self.stop_event.is_set():
            try:
                if not self.boot_alive:
                    s = self.snap
                    wired_cfg = self.cfg["campus"].get("wired") or {}
                    from .net import wired_auth_list, wired_authenticate, wired_bind_ip
                    if s.get("mode") in ("wired", "both"):
                        auths = wired_auth_list(wired_cfg, s.get("connection") or "")
                        if "pppoe" in auths and s.get("connection"):
                            prober = build_prober(self.cfg["campus"], s["connection"])
                            _up, ip = ppp_state(s["connection"])
                            ok, _lip, _why = prober.check(bind_ip=ip or None)
                            if not ok:
                                from .config import pause_active
                                if not pause_active():
                                    self.log("检测到掉线，正在自动重拨…", "warn")
                                    ras_hangup(s["connection"])
                                    code, _t = ras_dial(s["connection"], s.get("account"),
                                                        s.get("password"))
                                    if code == 0:
                                        self.log("自动重连成功。", "ok")
                        elif any(a in auths for a in ("dhcp", "static", "portal", "client",
                                                      "lan", "restart")):
                            prober = build_prober(self.cfg["campus"], "")
                            ok, _lip, _why = prober.check(bind_ip=wired_bind_ip(wired_cfg) or None)
                            if not ok:
                                from .config import pause_active
                                if not pause_active():
                                    self.log("检测到掉线，按配置重新接入有线…", "warn")
                                    wired_authenticate(wired_cfg, s.get("connection") or "",
                                                       s.get("account", ""), s.get("password", ""),
                                                       self.log)
            except Exception as exc:  # noqa: BLE001
                self.log("守护线程出错（已忽略）：%s" % exc, "err")
            self._sleep(15)

    def _flip_watch(self):
        """翻墙模式：打开指定程序 → 开客户端 + 切目标地区节点。

        是否允许"连着校园网时也翻墙"由 flip.when 决定：
          off_campus（默认）= 只有没连校园网时才开
          always           = 连着校园网也开（校园网认证完成后再开代理）

        开着代理期间会持续刷新 flip.active 标记，让系统级守护别把它杀掉。
        """
        cache, last_try, told = {}, 0.0, 0.0
        own_key = ""            # 本程序亲自开起来的客户端；只要它还活着就保持标记
        wait_until = 0.0        # 「跟着 VPN 走」时：等校园网断开再开代理的截止时间
        while not self.stop_event.is_set():
            try:
                rules = load_rules(None)
                flip = rules.get("flip") or {}
                policy = str(flip.get("when") or "off_campus").lower()

                # 维护"翻墙模式在用代理"标记：活着就刷新，死了就撤掉
                if own_key:
                    c = client_by_id(self.cfg, own_key) or {}
                    procs = list_processes() or {}
                    alive = any(pids_of(procs, n)
                                for n in (c.get("cores") or c.get("kill") or []))
                    if alive:
                        set_flip_active(own_key, "翻墙模式自动开启的代理")
                    else:
                        clear_flip_active()
                        own_key = ""

                if flip.get("enabled"):
                    hit, what = flip_triggered(rules)
                    if hit:
                        s = self.snap
                        up, desc = campus_link_state(s.get("mode"), s.get("connection"),
                                                     s.get("ssid"), cache,
                                                     self.cfg["campus"].get("wired") or {})
                        allowed = (not up) or (policy == "always")
                        follow = bool(rules.get("follow_vpn"))
                        if not allowed and follow:
                            # 「跟着 VPN 走」：翻墙本来就要离开校园网 ——
                            # 主动请求守护断开校园网，断开后再开代理。
                            # 不能在校园网还连着的时候开代理：守护的策略是
                            # "连着校园网就关代理"，刚开的代理会被立刻杀掉。
                            if up and not wait_until:
                                request_disconnect("翻墙模式启动 → 断开校园网")
                                wait_until = time.time() + 90
                                self.log("检测到 %s：按「跟着 VPN 走」的规则，"
                                         "先请守护断开校园网（约 15 秒），断开后再开代理。"
                                         % what, "warn")
                            elif up and time.time() > wait_until:
                                wait_until = 0.0
                                if time.time() - told > 300:
                                    told = time.time()
                                    self.log("校园网还没断开，暂不开代理（避免和守护互相打）。"
                                             "可以点「断开」，或稍后再试。", "warn")
                            if not up:
                                wait_until = 0.0
                                allowed = True
                        elif not up:
                            wait_until = 0.0
                        if not allowed and not follow:
                            if time.time() - told > 600:
                                told = time.time()
                                self.log("检测到 %s，但现在连着校园网（%s）—— 当前规则是"
                                         "「只在没连校园网时翻墙」，所以不开。"
                                         "想让连着校园网也能翻墙，到「翻墙模式」页把"
                                         "「什么时候翻墙」改成「任何时候」。"
                                         "（或者到「校园网」页勾上「跟着 VPN 走」，"
                                         "它会自动断开校园网再翻墙）" % (what, desc), "warn")
                        if allowed:
                            procs = list_processes() or {}
                            running = any(pids_of(procs, n)
                                          for c in (self.cfg.get("clients") or [])
                                          for n in (c.get("cores") or c.get("kill") or []))
                            if not running and time.time() - last_try > 120:
                                last_try = time.time()
                                self.log("检测到 %s，%s → 打开代理并挑可用节点…"
                                         % (what, "没连校园网" if not up else "按规则允许（连着校园网）"),
                                         "warn")
                                ok, name, node, key = ensure_client_ready(
                                    self.cfg, flip.get("order") or [], self.log)
                                if ok:
                                    self.log("翻墙已就绪：%s → %s" % (name, node), "ok")
                                    own_key = key
                                    set_flip_active(key, "翻墙模式自动开启的代理")
                                    order = flip.get("order") or []
                                    rules["flip"]["order"] = [key] + [x for x in order if x != key]
                                    save_rules(rules)
                                else:
                                    self.log("没能开出可用代理：可以手动打开客户端连一次再试。", "err")
            except Exception as exc:  # noqa: BLE001
                self.log("翻墙模式出错（已忽略）：%s" % exc, "err")
            self._sleep(5)

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            time.sleep(0.2)

    # ======================================================= 关闭/托盘
    def show_window(self):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def on_close(self):
        try:
            self.root.withdraw()
        except Exception:
            pass
        self.log("已隐藏到托盘，程序继续在后台运行（双击托盘图标可重新打开）。")

    def quit_app(self):
        if not messagebox.askokcancel(APP_TITLE,
                                      "确定退出校园网助手吗？\n\n"
                                      "· 已安装的系统级守护不受影响，照常工作；\n"
                                      "· 只是关掉这个界面程序。\n\n"
                                      "如果只是不想看到窗口，点右上角 × 就会隐藏到托盘。"):
            return
        self.closing = True
        self.stop_event.set()
        try:
            clear_flip_active()          # 撤掉"翻墙模式在用代理"的豁免，免得守护一直不关代理
        except Exception:
            pass
        try:
            self.tray.remove()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def run_gui(minimized=False, cfg=None):
    cfg = cfg or load_config()

    # 只允许一个界面实例：重复点桌面快捷方式会开出第二个，
    # 两个检测器同时干活反而互相干扰。已有实例时提示一下并退出。
    # 注意要给"自更新重启"留出交接时间：老实例是先放锁再拉新实例，
    # 所以这里等几秒再判定。
    import ctypes
    from .util import named_mutex
    handle = None
    for _ in range(8):
        handle = named_mutex("Global\\CampusNetAssistant.Gui")
        if handle is not None:
            break
        time.sleep(1)
    if handle is None:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "校园网助手已经在运行了。\n\n"
                      "请看屏幕右下角托盘里的图标，双击它就能打开窗口。",
                "校园网助手", 0x40)
        except Exception:
            pass
        return 0

    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    app = App(root, cfg)
    app.gui_mutex = handle
    if minimized:
        try:
            root.withdraw() if app.tray_ok else root.iconify()
        except Exception:
            pass
    root.mainloop()
    return 0
