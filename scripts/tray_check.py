# -*- coding: utf-8 -*-
"""托盘图标端到端检查：点它到底能不能回到主界面。

为什么单独一个脚本：托盘检查必须在**干净的进程**里跑。
同一个进程里先建过别的 Tk 根再建第二个，会互相干扰，结果不准；
真实程序整个生命周期只有一个 Tk 根，所以用独立进程最接近真实情况。

会做四件事（都是真的，不是模拟）：
  1. 建界面 → 收进托盘
  2. 往托盘窗口 PostMessage「单击左键」→ 看窗口能不能回来
  3. 「双击左键」→ 看窗口能不能回来
  4. 「右键」→ 看菜单逻辑能不能被派发，并模拟选中「显示主界面」→ 看窗口能不能回来

用法：python scripts\\tray_check.py   （退出码 0 = 全部正常）
"""
import ctypes
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

tmp = os.path.join(tempfile.gettempdir(), "cna_tray_check")
os.makedirs(tmp, exist_ok=True)
os.environ["CNA_HOME"] = tmp
os.environ["CNA_BOOT_DIR"] = os.path.join(tmp, "boot")

import tkinter as tk                                              # noqa: E402

from campusnet import config as C                                 # noqa: E402
from campusnet.gui import App                                     # noqa: E402
from campusnet.util import (WM_TRAY, WM_LBUTTONUP, WM_LBUTTONDBLCLK,  # noqa: E402
                            WM_RBUTTONUP, taskbar_created_message)


def main():
    root = tk.Tk()
    root.geometry("220x160")
    app = App(root, C.load_config())

    def pump(seconds):
        """按真实时间空转。

        界面轮询是 after(150) 定时器，必须真的过时间才会触发，
        否则会被误判成"点了没反应"。
        """
        t0 = time.time()
        while time.time() - t0 < seconds:
            root.update()
            time.sleep(0.02)

    res = {}
    pump(1.0)
    res["tray_ok"] = app.tray_ok
    res["icon"] = bool(app.tray.hicon)

    root.withdraw()
    pump(0.5)
    res["hidden"] = bool(root.winfo_viewable())

    u = ctypes.windll.user32
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                               ctypes.c_void_p]

    u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_LBUTTONUP)
    pump(1.2)
    res["single"] = bool(root.winfo_viewable())

    root.withdraw()
    pump(0.5)
    u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_LBUTTONDBLCLK)
    pump(1.2)
    res["double"] = bool(root.winfo_viewable())

    root.withdraw()
    pump(0.5)
    real_menu = app.tray._menu
    app.tray._menu = lambda hwnd: "show"          # 模拟用户在菜单里选了「显示主界面」
    try:
        u.PostMessageW(app.tray.hwnd, WM_TRAY, 1, WM_RBUTTONUP)
        pump(1.2)
    finally:
        app.tray._menu = real_menu
    res["right"] = bool(root.winfo_viewable())

    # 5) 假装资源管理器重启：它会把托盘图标全丢掉，只广播一条 TaskbarCreated。
    #    收到就必须自己重新登记，否则图标永久消失、窗口又收着 = 再也点不到。
    #    这里数 add() 被调了几次（受限环境里 NIM_ADD 本来就失败，也能验证）。
    calls = {"n": 0}
    real_add = app.tray.add

    def counting_add():
        calls["n"] += 1
        return real_add()

    app.tray.add = counting_add
    u.PostMessageW(app.tray.hwnd, taskbar_created_message(), 0, 0)
    pump(0.8)
    res["readd_after_explorer"] = calls["n"] > 0

    # 6) 体检自愈：系统连着两次说"没这个图标"时，必须尝试重新登记。
    before = calls["n"]
    app.tray.added = True                               # 假装当前图标是登记着的
    app.tray._shell = lambda op, data=None: False       # 假装系统那边没图标了
    app.tray.verify()
    res["no_flicker"] = calls["n"] == before            # 第一次失败不该动图标
    app.tray.verify()
    res["self_heal"] = calls["n"] > before

    app.closing = True
    try:
        app.tray.remove()
    except Exception:
        pass
    root.destroy()
    return res


if __name__ == "__main__":
    try:
        r = main()
    except Exception as exc:  # noqa: BLE001
        print("托盘检查出错：%s: %s" % (type(exc).__name__, exc))
        sys.exit(2)
    print("  托盘创建成功=%s 图标句柄=%s" % (r.get("tray_ok"), r.get("icon")))
    print("  收进托盘后可见=%s（应为 False）" % r.get("hidden"))
    print("  【单击左键】后主界面可见=%s" % r.get("single"))
    print("  【双击左键】后主界面可见=%s" % r.get("double"))
    print("  【右键菜单选显示】后主界面可见=%s" % r.get("right"))
    print("  【模拟资源管理器重启】图标自动重新登记=%s" % r.get("readd_after_explorer"))
    print("  【图标真丢了】连丢两次才动手（不闪）=%s / 已自动放回=%s"
          % (r.get("no_flicker"), r.get("self_heal")))
    print("  进程正常退出（没有崩溃）✓")
    ok = (r.get("tray_ok") and r.get("single") and r.get("double") and r.get("right")
          and r.get("readd_after_explorer") and r.get("no_flicker") and r.get("self_heal"))
    print("\n结论：%s" % ("托盘工作正常 ✓" if ok else "托盘仍有问题 ✗"))
    sys.exit(0 if ok else 1)
