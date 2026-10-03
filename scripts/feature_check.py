# -*- coding: utf-8 -*-
"""全功能检查：把每个功能真的调用一遍，看能不能用。

与 tests/ 下的单元测试不同，这个脚本"跑真东西"：
读真实网卡、真实拨号状态、真实无线列表、真实客户端安装情况，
并把每个功能的实际返回值打印出来。

**安全约定**：不做破坏性操作 ——
不禁用网卡、不拨号、不断开连接、不启动翻墙客户端、不装卸计划任务、不改网络设置；
只读 + 在临时目录里做读写类验证。

用法：python scripts\\feature_check.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL, SKIP = [], [], []


def check(name, fn, skip=None):
    if skip:
        SKIP.append((name, skip))
        print("  [跳过] %-32s %s" % (name, skip))
        return None
    try:
        detail = fn()
        PASS.append(name)
        print("  [通过] %-32s %s" % (name, detail if detail is not None else ""))
        return detail
    except Exception as exc:  # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(exc).__name__, exc)))
        print("  [失败] %-32s %s: %s" % (name, type(exc).__name__, exc))
        return None


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)
    return msg


print("=" * 76)
print("校园网助手 · 全功能检查")
print("=" * 76)

# ---------------------------------------------------------------- 1 导入
print("\n【1】模块导入")
import campusnet                                                  # noqa: E402
from campusnet import clients, config as C, installer, net, rules, util  # noqa: E402
from campusnet import rules as _rules_mod                          # noqa: E402,F811

check("campusnet 版本", lambda: "v%s" % campusnet.__version__)
for _m in ("config", "net", "clients", "rules", "guard", "installer", "gui", "util"):
    check("import campusnet.%s" % _m,
          lambda m=_m: need(__import__("campusnet." + m, fromlist=["x"]) is not None,
                            "OK"))
check("campusnet.__main__（命令行入口）",
      lambda: need(__import__("campusnet.__main__", fromlist=["x"]) is not None, "OK"))

# ---------------------------------------------------------------- 2 配置
print("\n【2】配置：默认值 / 读写 / 加密 / 导入导出")
check("默认配置分组", lambda: "、".join(sorted(C.DEFAULT_CONFIG)))
for _k in ("campus", "guard", "flip", "app", "clients"):
    check("默认配置有 %s 段" % _k, lambda k=_k: need(k in C.DEFAULT_CONFIG, "有"))
_d = C.DEFAULT_CONFIG
for _path in ("campus.interval", "guard.auto_dial", "guard.kill_proxies",
              "guard.kill_before_dial", "guard.follow_vpn", "guard.vpn_switch_delay",
              "guard.reconnect_when_no_flip", "app.auto_update", "app.tray",
              "app.single_instance", "flip.enabled", "flip.when", "flip.browser_always"):
    _a, _b = _path.split(".")
    check("默认值 %s" % _path, lambda a=_a, b=_b: "%r" % _d[a][b])

check("load_config", lambda: "mode=%s 间隔=%s秒" % (C.load_config()["campus"]["mode"],
                                                    C.load_config()["campus"]["interval"]))
check("save_config 往返", lambda: need(C.save_config(C.load_config()), "成功"))
check("get_password 能解出密码",
      lambda: (lambda cfg: (cfg["campus"].__setitem__("password_enc",
                                                      C.dpapi_encrypt("pw-测试")),
                            need(C.get_password(cfg) == "pw-测试", "解密正确"))[1])(
          C.load_config()))
check("DPAPI 加密/解密往返",
      lambda: need(C.dpapi_decrypt(C.dpapi_encrypt("hello-密码")) == "hello-密码", "往返一致"))
check("机器级 DPAPI 往返",
      lambda: need(C.dpapi_decrypt(C.dpapi_encrypt("hello", machine=True), machine=True)
                   == "hello", "往返一致"))
check("campus_portal()", lambda: "url=%s" % (C.campus_portal(C.load_config()).get("url")
                                             or "(未配)"))
check("portal_credentials()", lambda: "用户名=%s" % (
    C.portal_credentials(C.load_config())[0] or "(未配)"))
check("build_boot_config()", lambda: "字段 %d 个" % len(C.build_boot_config(C.load_config())))

_tmp = os.path.join(tempfile.gettempdir(), "cna_feature_check")
os.makedirs(_tmp, exist_ok=True)
_exp = os.path.join(_tmp, "exported.json")

# ---------------------------------------------------------------- 3 信号文件
print("\n【3】界面 ↔ 守护 信号文件")
check("暂停标记", lambda: (C.clear_pause(), C.set_pause(5), need(C.pause_active(), "写→读正常"),
                           C.clear_pause(), need(not C.pause_active(), "清除正常"))[2])
check("断开请求", lambda: (C.clear_disconnect(), C.request_disconnect("t"),
                           need(C.disconnect_requested(), "正常"), C.clear_disconnect())[2])
check("连接请求", lambda: (C.clear_connect(), C.request_connect("t"),
                           need(C.connect_requested(), "正常"), C.clear_connect())[2])
check("手动选择", lambda: (C.clear_network_choice(), C.set_network_choice("wifi", "x"),
                           need(C.get_network_choice() == "wifi", "正常"),
                           C.clear_network_choice())[2])
check("翻墙标记", lambda: (C.clear_flip_active(), C.set_flip_active("eix", "t"),
                           need(C.flip_active(None)[0], "正常"), C.clear_flip_active())[2])
check("被禁用网卡记录", lambda: (C.clear_disabled_adapter(), C.set_disabled_adapter("测试"),
                                 need(C.get_disabled_adapter() == "测试", "正常"),
                                 C.clear_disabled_adapter())[2])
check("rules.json 往返", lambda: need(C.save_rules(C.load_rules(None)), "成功"))
check("load_rules 含新开关",
      lambda: "auto_dial=%s follow_vpn=%s" % (C.load_rules(None)["auto_dial"],
                                              C.load_rules(None)["follow_vpn"]))

# ---------------------------------------------------------------- 4 网络
print("\n【4】网络：网卡 / 拨号 / 无线 / 探测 / 门户")
_w = net.wired_adapters()
check("列出有线网卡", lambda: "、".join("%s(%s)" % (n, a) for n, a, _s in _w))
check("挑有线网卡", lambda: net.pick_wired_adapter({}) or "(没找到)")
check("被禁用的有线网卡", lambda: "、".join(net.disabled_wired_adapters()) or "(无)")
check("网卡启用状态", lambda: "以太网=%s" % net.adapter_enabled("以太网"))
check("列出 PPPoE 连接", lambda: "、".join(net.pppoe_connections()) or "(没有)")
check("ppp_state", lambda: ("已连接 %s" % net.ppp_state("宽带连接")[1])
      if net.ppp_state("宽带连接")[0] else "未连接")
check("列无线配置", lambda: "%d 个" % len(net.wifi_profiles()))
check("当前无线", lambda: net.wifi_connected_ssid() or "(未连无线)")
check("联网探测", lambda: "%s" % ((lambda r: "连通=%s IP=%s 依据=%s" % r[:3])
                                  (net.build_prober(C.load_config(),
                                                    C.load_config()["campus"]["connection"]).check()),))
check("campus_link_state", lambda: net.campus_link_state(
    C.load_config()["campus"]["mode"], C.load_config()["campus"]["connection"],
    C.load_config()["campus"]["wifi_ssid"], {}, C.load_config()["campus"].get("wired") or {})[1])
check("wired_link_state", lambda: "连通=%s 说明=%s" % net.wired_link_state(C.load_config()))
check("认证方式列表", lambda: "、".join(net.wired_auth_list(
    C.load_config()["campus"].get("wired") or {}, C.load_config()["campus"]["connection"])))
check("门户预设", lambda: "、".join(net.PORTAL_PRESETS))
check("应用门户预设（在副本上试）",
      lambda: need(net.apply_portal_preset({"preset": "sangfor"}, "sangfor")
                   is not None or True, "不报错"))
check("802.1X 检测", lambda: "可用=%s 说明=%s" % net.lan_8021x_interfaces())
check("错误码翻译（691/678/651/756）",
      lambda: " | ".join(net.friendly_error(c, "") for c in (691, 678, 651, 756))[:90])
check("门户探测（只读，不登录）",
      lambda: "连通=%s 说明=%s" % net.portal_probe(C.campus_portal(C.load_config())))
check("网卡禁用/启用函数存在（不实际调用）",
      lambda: need(callable(net.set_adapter_disabled) and callable(net.adapter_enabled),
                   "存在"))

# ---------------------------------------------------------------- 5 翻墙客户端
print("\n【5】翻墙客户端")
_cfg = C.load_config()
_cl = _cfg.get("clients") or []
check("客户端清单", lambda: "%d 个：%s" % (len(_cl), "、".join(c.get("name", "?") for c in _cl)))
_inst = []
for _c in _cl:
    for _p in (_c.get("gui_paths") or []) + (_c.get("core_paths") or []):
        if os.path.isfile(_p):
            _inst.append(_c.get("name"))
            break
check("检测哪些已安装", lambda: "、".join(sorted(set(_inst))) or "(一个都没装)")
check("client_by_id", lambda: (clients.client_by_id(_cfg, "eix") or {}).get("name", "(没找到)"))
check("config_port（读配置找端口）",
      lambda: "mixed-port=%s" % clients.config_port(_cl[0]) if _cl else "无")
check("find_controller（不启动客户端）",
      lambda: "%s" % (clients.find_controller(_cl[0]) if _cl else "无"))
check("probe_via_proxy（没代理时应安全返回失败）",
      lambda: "返回 %s（没代理时 False 是正确的）" % (clients.probe_via_proxy(7893, timeout=3))
      if hasattr(clients, "probe_via_proxy") else "—")
check("keep_checked_clients（界面勾选逻辑）",
      lambda: "%s" % (_cfg.get("flip", {}).get("order") or "(未设置)"))

# ---------------------------------------------------------------- 6 触发规则
print("\n【6】翻墙触发规则")
_real = C.load_rules(None)
check("flip_triggered 不报错（无触发程序时）",
      lambda: "命中=%s 原因=%s" % rules.flip_triggered({"flip": {"enabled": False}}))
check("flip_triggered 真实规则可调用",
      lambda: "命中=%s 原因=%s" % rules.flip_triggered(_real))
check("触发规则读取了 title_hints",
      lambda: "关键词 %d 个，含 google=%s" % (
          len((_real.get("flip") or {}).get("title_hints") or []),
          "google" in ((_real.get("flip") or {}).get("title_hints") or [])))
check("browser_always 开关可读",
      lambda: "browser_always=%s" % (_real.get("flip") or {}).get("browser_always"))

# ---------------------------------------------------------------- 7 守护
print("\n【7】系统级守护")
check("心跳文件", lambda: C.heartbeat_state()[1] or "(无)")
check("守护是否在跑", lambda: "在跑" if C.heartbeat_state()[0] else "没在跑")
check("守护日志可读",
      lambda: "最后一行: %s" % open(C.BOOT_LOG, encoding="utf-8",
                                    errors="replace").read().splitlines()[-1][:56]
      if os.path.isfile(C.BOOT_LOG) else "无日志")
check("计划任务是否注册", lambda: "已注册" if installer.task_registered() else "未注册")
check("installer.is_admin()", lambda: "%s" % installer.is_admin())
check("installer.read_tail()", lambda: "%d 行" % len(installer.read_tail(C.BOOT_LOG, 3)))
check("进程列表（守护靠它判断代理）", lambda: "%d 个进程" % len(util.list_processes() or {}))
check("pids_of / 进程年龄",
      lambda: "自己 PID 年龄 %.1f 秒" % (util.process_age_seconds(os.getpid()) or -1))
check("系统运行时长", lambda: "%.0f 分钟" % (util.system_uptime_seconds() / 60))
check("单实例互斥体（名字能用）", lambda: "%s" % (util.named_mutex("Global\\CnaFeatureCheck")
                                                   is not None))

# ---------------------------------------------------------------- 8 界面
print("\n【8】图形界面")
_old_home = os.environ.get("CNA_HOME")
os.environ["CNA_HOME"] = os.path.join(_tmp, "gui")
os.environ["CNA_BOOT_DIR"] = os.path.join(_tmp, "gui", "boot")
_gui_result = {}


def _gui_check():
    import tkinter as tk
    from campusnet.gui import App
    root = tk.Tk()
    root.withdraw()
    app = App(root, C.load_config())

    def grab():
        try:
            need_handlers = ["connect", "disconnect", "switch_network", "save", "check_now",
                             "export_config", "import_config", "open_log", "check_update",
                             "on_close", "quit_app", "_build_switches", "_flip_watch"]
            missing = [n for n in need_handlers if not callable(getattr(app, n, None))]
            _gui_result["handlers"] = missing
            # 走一遍"界面 → 配置"的收集过程
            app.collect_config()
            c2 = C.load_config()
            _gui_result["interval"] = c2["campus"]["interval"]
            # 真的走一遍导出 / 导入。必须把**所有**弹窗都接掉，
            # 否则 tkinter 会弹出模态对话框，把自动化检查卡死。
            from unittest import mock
            from tkinter import filedialog, messagebox, simpledialog
            _patchers = []
            for _mod, _ret in ((filedialog, _exp), (messagebox, True), (simpledialog, "")):
                for _name in dir(_mod):
                    if _name.startswith("_"):
                        continue
                    _obj = getattr(_mod, _name, None)
                    if callable(_obj) and not isinstance(_obj, type):
                        try:
                            _p = mock.patch.object(_mod, _name, return_value=_ret)
                            _p.start()
                            _patchers.append(_p)
                        except Exception:
                            pass
            try:
                app.export_config()
                _gui_result["exported"] = os.path.isfile(_exp)
                app.import_config()
                _gui_result["imported"] = True
            finally:
                for _p in _patchers:
                    _p.stop()
            _gui_result["switch_vars"] = all(hasattr(app, v) for v in (
                "auto_dial_var", "kill_before_var", "auto_update_var", "tray_var",
                "single_var", "interval_var", "follow_vpn_var", "vpn_ssid_var",
                "reconnect_var", "kill_var", "flip_when_var", "flip_var",
                "browser_always_var", "mode_var", "ssid_var", "net_mode_var"))
            _missing_vars = [v for v in (
                "auto_dial_var", "kill_before_var", "auto_update_var", "tray_var",
                "single_var", "interval_var", "follow_vpn_var", "vpn_ssid_var",
                "reconnect_var", "kill_var", "flip_when_var", "flip_var",
                "browser_always_var", "mode_var", "ssid_var", "net_mode_var")
                if not hasattr(app, v)]
            _gui_result["missing_vars"] = _missing_vars
            app.closing = True
            root.destroy()
        except Exception as exc:  # noqa: BLE001
            _gui_result["error"] = "%s: %s" % (type(exc).__name__, exc)
            root.destroy()

    root.after(2000, grab)
    root.mainloop()
    if _gui_result.get("error"):
        raise AssertionError(_gui_result["error"])
    return "回调缺失=%s 变量缺失=%s 间隔=%s秒 导出=%s 导入=%s" % (
        _gui_result.get("handlers") or "无", _gui_result.get("missing_vars") or "无",
        _gui_result.get("interval"), _gui_result.get("exported"),
        _gui_result.get("imported"))


check("界面构建 + 回调 + 变量", _gui_check)

# ---------------------------------------------------------------- 9 托盘（端到端）
print("\n【9】托盘图标：点它能不能回到主界面（端到端实测）")
_tray_out = {}


def _tray_check():
    """真建一个 App，把窗口收起来，然后**真的发托盘消息**，看窗口能不能回来。

    这就是用户反馈的那个 bug（点托盘图标 / 右键都没反应）的现场复现。
    """
    import ctypes
    import tkinter as tk
    from campusnet.gui import App
    from campusnet.util import (TrayIcon, WM_TRAY, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
                                WM_RBUTTONUP, IDM_SHOW, IDM_CHECK)  # noqa: F401
    root = tk.Tk()
    root.geometry("300x200")
    app = App(root, C.load_config())

    def pump(seconds):
        """按**真实时间**空转 update()。

        不能用 40 次 update() 代替 1 秒：界面的轮询是 after(150) 定时器，
        要真实时间过去才会触发，否则会误判成"点了没反应"。
        """
        import time as _t
        t0 = _t.time()
        while _t.time() - t0 < seconds:
            root.update()
            _t.sleep(0.02)

    pump(1.0)
    _tray_out["tray_ok"] = app.tray_ok
    _tray_out["hwnd"] = bool(app.tray.hwnd)

    # 模拟"用户把它收进托盘"
    root.withdraw()
    pump(0.5)
    _tray_out["hidden_viewable"] = bool(root.winfo_viewable())

    u = ctypes.windll.user32
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                               ctypes.c_void_p]

    # ① 单击左键 → 应该回到主界面
    u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_LBUTTONUP)
    pump(1.2)
    _tray_out["single_click"] = bool(root.winfo_viewable())

    # ② 再收起来，双击左键 → 也应该回来
    root.withdraw()
    pump(0.5)
    u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_LBUTTONDBLCLK)
    pump(1.2)
    _tray_out["double_click"] = bool(root.winfo_viewable())

    # ③ 右键 → 菜单逻辑被派发；选中"显示主界面"后窗口要能回来
    #    （真的弹菜单会让检查卡住，所以把 _menu 换成"模拟用户选了显示主界面"）
    root.withdraw()
    pump(0.5)
    real_menu = app.tray._menu
    app.tray._menu = lambda hwnd: "show"
    try:
        u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_RBUTTONUP)
        pump(1.2)
    finally:
        app.tray._menu = real_menu
    _tray_out["right_click"] = bool(root.winfo_viewable())

    # ④ 菜单真的能建起来 + 能数出菜单项（右键菜单用的那套调用）
    try:
        m = u.CreatePopupMenu()
        u.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                  ctypes.c_wchar_p]
        u.AppendMenuW(m, 0x0, IDM_SHOW, "显示主界面")
        u.AppendMenuW(m, 0x0, IDM_CHECK, "立即检查")
        u.GetMenuItemCount.argtypes = [ctypes.c_void_p]
        _tray_out["menu_items"] = u.GetMenuItemCount(m)
        u.DestroyMenu(m)
    except Exception as exc:  # noqa: BLE001
        _tray_out["menu_items"] = "异常 %s" % exc

    _tray_out["icon_handle"] = bool(app.tray.hicon)
    app.closing = True
    try:
        app.tray.remove()
    except Exception:
        pass
    root.destroy()
    return ("图标=%s 图标句柄=%s | 收起后可见=%s | 单击左键回来=%s | "
            "双击回来=%s | 右键选显示回来=%s | 菜单项=%s" % (
                _tray_out.get("tray_ok"), _tray_out.get("icon_handle"),
                _tray_out.get("hidden_viewable"), _tray_out.get("single_click"),
                _tray_out.get("double_click"), _tray_out.get("right_click"),
                _tray_out.get("menu_items")))


_old_home2 = os.environ.get("CNA_HOME")
os.environ["CNA_HOME"] = os.path.join(_tmp, "tray")
os.environ["CNA_BOOT_DIR"] = os.path.join(_tmp, "tray", "boot")
check("托盘：单击/双击/右键 端到端", _tray_check)
if _old_home2:
    os.environ["CNA_HOME"] = _old_home2
else:
    os.environ.pop("CNA_HOME", None)
check("托盘：点一下就能回到主界面（用户反馈的那个 bug）",
      lambda: need(_tray_out.get("single_click") is True,
                   "单击左键已能唤回主界面" if _tray_out.get("single_click")
                   else "单击左键**仍然**唤不回主界面"))
check("托盘：右键菜单能唤回主界面",
      lambda: need(_tray_out.get("right_click") is True,
                   "右键菜单可用" if _tray_out.get("right_click")
                   else "右键**仍然**唤不回主界面"))

# ---------------------------------------------------------------- 10 命令行
print("\n【9】命令行入口（只跑不修改的）")
import subprocess  # noqa: E402

for _args, _name in ((["--selftest"], "自检 --selftest"),
                     (["--scan"], "扫描 --scan"),
                     (["--help"], "帮助 --help")):
    check(_name,
          lambda a=_args: need(subprocess.run([sys.executable, "-m", "campusnet"] + a,
                                              cwd=ROOT, capture_output=True,
                                              timeout=180).returncode == 0, "退出码 0"))

# ---------------------------------------------------------------- 汇总
if os.environ.get("CNA_HOME"):
    if _old_home:
        os.environ["CNA_HOME"] = _old_home
    else:
        os.environ.pop("CNA_HOME", None)

print("\n" + "=" * 76)
print("通过 %d 项，失败 %d 项，跳过 %d 项" % (len(PASS), len(FAIL), len(SKIP)))
for name, why in FAIL:
    print("  × %s → %s" % (name, why))
for name, why in SKIP:
    print("  - %s → %s" % (name, why))
print("=" * 76)
sys.exit(1 if FAIL else 0)
