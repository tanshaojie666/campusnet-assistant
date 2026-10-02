# -*- coding: utf-8 -*-
"""图形界面（tkinter，无第三方依赖）。

三个分页：
  状态   —— 现在连没连上、生效了什么策略、运行日志、按钮
  校园网 —— **接入方式可选**：有线(PPPoE) / 无线(指定SSID) / 两者；账号密码；关闭代理名单；无线策略
  翻墙   —— **客户端可选、顺序可选、触发程序可选**；目标地区关键词
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import config as C
from .clients import client_by_id, scan_clients
from .config import (APP_TITLE, HOME_DIR, LOG_FILE, heartbeat_state,
                     load_config, load_rules, save_config, save_rules,
                     set_account, set_pause)
from .guard import boot_task_registered
from .installer import install_boot, uninstall_boot
from .net import (build_prober, campus_link_state, pppoe_connections, ppp_state,
                  ras_dial, ras_hangup, wifi_profiles)
from .clients import ensure_client_ready
from .rules import flip_triggered
from .util import TrayIcon, list_processes, pids_of

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
        self.log("程序已启动。接入方式：%s" % MODE_LABEL.get(self.snap["mode"], self.snap["mode"]))
        if self.tray_ok:
            self.log("已驻留右下角托盘：点 × 只是把窗口收起来，程序继续在后台跑。")
        self.root.after(150, self._pump)
        self.root.after(400, self._refresh_boot_state)
        self.root.after(600, self._refresh_status)
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
        ttk.Button(bar, text="打开日志", command=self.open_log).pack(side="left")
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
        for val, text in (("wired", "有线：PPPoE 拨号（常见于宿舍网口）"),
                          ("wireless", "无线：自动连接指定 Wi-Fi"),
                          ("both", "两者都要（有线优先，失败再连无线）")):
            ttk.Radiobutton(box, text=text, value=val, variable=self.mode_var,
                            command=self._on_mode).pack(anchor="w", pady=2)

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
        ttk.Checkbutton(f, text="启用翻墙模式：**没连校园网**时，打开指定程序才自动开代理并切到可用节点",
                        variable=self.flip_var).pack(anchor="w")

        cl = ttk.LabelFrame(f, text=" 翻墙客户端（可选、可调顺序：从上到下依次尝试） ", padding=12)
        cl.pack(fill="both", expand=True, pady=(10, 0))
        self.client_list = tk.Listbox(cl, font=("Consolas", 10), height=6)
        self.client_list.pack(fill="both", expand=True)
        cb = ttk.Frame(cl)
        cb.pack(fill="x", pady=(6, 0))
        ttk.Button(cb, text="↑ 上移", command=lambda: self._move(self.client_list, -1)).pack(side="left")
        ttk.Button(cb, text="↓ 下移", command=lambda: self._move(self.client_list, 1)).pack(side="left", padx=6)
        ttk.Button(cb, text="扫描本机已装客户端", command=self._scan_clients).pack(side="left")
        ttk.Label(cl, foreground="#5f6368", font=("Microsoft YaHei UI", 9),
                  text="提示：多数 mihomo/Clash 客户端可以直接用「自带内核 + 自己的配置」启动，"
                       "不需要开界面、不需要管理员权限。").pack(anchor="w", pady=(6, 0))
        self._fill_list(self.client_list, self.cfg["flip"].get("order") or [])

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
        ttk.Label(rg, text="浏览器标题关键词：", font=self.font).pack(anchor="w", pady=(8, 2))
        ttk.Entry(rg, textvariable=self.title_var, font=self.font).pack(fill="x")

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
            up, desc = campus_link_state(campus.get("mode"), campus.get("connection"),
                                         campus.get("wifi_ssid"), {})
            self.link_desc = desc
            prober = build_prober(campus, campus.get("connection"))
            _ppp_up, ppp_ip = ppp_state(campus.get("connection") or "")
            ok, ip, why = prober.check(bind_ip=ppp_ip or None)
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

    def check_now(self):
        self._refresh_status()

    # ======================================================= 动作
    def collect_config(self):
        cfg = self.cfg
        cfg["campus"]["mode"] = self.mode_var.get()
        cfg["campus"]["connection"] = self.conn_var.get().strip()
        cfg["campus"]["wifi_ssid"] = self.ssid_var.get().strip()
        cfg["campus"]["account"] = self.user_var.get().strip()
        from .util import dpapi_encrypt
        cfg["campus"]["password_enc"] = dpapi_encrypt(self.pwd_var.get()) if self.pwd_var.get() else ""
        cfg["guard"]["kill_proxies"] = bool(self.kill_var.get())
        cfg["guard"]["kill_processes"] = self._list_items(self.kill_list)
        cfg["guard"]["wifi_policy"] = self.wifi_policy_var.get()
        cfg["flip"]["enabled"] = bool(self.flip_var.get())
        cfg["flip"]["order"] = self._list_items(self.client_list)
        cfg["flip"]["apps"] = self._list_items(self.app_list)
        cfg["flip"]["region_hints"] = [x.strip() for x in self.region_var.get().replace("、", ",").split(",") if x.strip()]
        cfg["flip"]["title_hints"] = [x.strip() for x in self.title_var.get().replace("、", ",").split(",") if x.strip()]
        save_config(cfg)
        rules = load_rules(None)
        rules["kill_proxies"] = cfg["guard"]["kill_proxies"]
        rules["processes"] = cfg["guard"]["kill_processes"]
        rules["wifi_policy"] = cfg["guard"]["wifi_policy"]
        rules["flip"] = cfg["flip"]
        save_rules(rules)
        if cfg["campus"]["account"] and self.pwd_var.get():
            set_account(cfg, cfg["campus"]["account"], self.pwd_var.get())
        self._sync_snap()
        return cfg

    def save(self):
        try:
            self.collect_config()
            self.log("设置已保存（系统级守护会在 15 秒内读到新规则）", "ok")
        except Exception as exc:  # noqa: BLE001
            self.log("保存失败：%s" % exc, "err")

    def connect(self, force=False):
        if self.busy:
            return
        self.busy = True
        self.queue.put(("buttons", False))
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self):
        try:
            self.save()
            s = self.snap
            if s["mode"] in ("wired", "both") and s["connection"]:
                self.log("正在拨号（%s）…" % s["connection"])
                ras_hangup(s["connection"])
                code, text = ras_dial(s["connection"], s["account"], s["password"])
                if code != 0:
                    from .net import friendly_error
                    self.log("拨号失败 → " + friendly_error(code, text), "err")
                else:
                    self.log("拨号成功。", "ok")
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
        s = self.snap
        set_pause(10)
        if s["mode"] in ("wired", "both") and s["connection"]:
            ras_hangup(s["connection"])
        self.log("已断开，并通知系统级守护暂停 10 分钟（免得立刻又连上）。")
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
                    if s.get("mode") in ("wired", "both") and s.get("connection"):
                        prober = build_prober(self.cfg["campus"], s["connection"])
                        _up, ip = ppp_state(s["connection"])
                        ok, _lip, _why = prober.check(bind_ip=ip or None)
                        if not ok:
                            from .config import pause_active
                            if not pause_active():
                                self.log("检测到掉线，正在自动重拨…", "warn")
                                ras_hangup(s["connection"])
                                code, _t = ras_dial(s["connection"], s.get("account"), s.get("password"))
                                if code == 0:
                                    self.log("自动重连成功。", "ok")
            except Exception as exc:  # noqa: BLE001
                self.log("守护线程出错（已忽略）：%s" % exc, "err")
            self._sleep(15)

    def _flip_watch(self):
        """翻墙模式：没连校园网 + 打开指定程序 → 开客户端 + 切目标地区节点。"""
        cache, last_try, told = {}, 0.0, 0.0
        while not self.stop_event.is_set():
            try:
                rules = load_rules(None)
                if (rules.get("flip") or {}).get("enabled"):
                    hit, what = flip_triggered(rules)
                    if hit:
                        s = self.snap
                        up, desc = campus_link_state(s.get("mode"), s.get("connection"),
                                                     s.get("ssid"), cache)
                        if up:
                            if time.time() - told > 600:
                                told = time.time()
                                self.log("检测到 %s，但现在连着校园网（%s）—— 按规则不开代理。"
                                         % (what, desc), "warn")
                        else:
                            procs = list_processes() or {}
                            running = any(pids_of(procs, n)
                                          for c in (self.cfg.get("clients") or [])
                                          for n in (c.get("cores") or c.get("kill") or []))
                            if not running and time.time() - last_try > 120:
                                last_try = time.time()
                                self.log("检测到 %s，且没连校园网 → 打开代理并挑可用节点…"
                                         % what, "warn")
                                ok, name, node, key = ensure_client_ready(
                                    self.cfg, rules["flip"].get("order") or [], self.log)
                                if ok:
                                    self.log("翻墙已就绪：%s → %s" % (name, node), "ok")
                                    order = rules["flip"].get("order") or []
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
            self.tray.remove()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def run_gui(minimized=False, cfg=None):
    cfg = cfg or load_config()
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    app = App(root, cfg)
    if minimized:
        try:
            root.withdraw() if app.tray_ok else root.iconify()
        except Exception:
            pass
    root.mainloop()
    return 0
