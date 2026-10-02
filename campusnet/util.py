# -*- coding: utf-8 -*-
"""系统层工具：进程、DPAPI 加密、管理员、netstat、注册表、托盘图标。

只依赖 Python 标准库。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------
# 执行命令
# --------------------------------------------------------------------------
def decode_bytes(data: bytes) -> str:
    for enc in ("gbk", "utf-8"):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return data.decode("latin-1", "replace")


def run_cmd(args, timeout=60):
    """静默执行命令，返回 (退出码, 输出文本)。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, startupinfo=si,
                           creationflags=CREATE_NO_WINDOW, timeout=timeout)
        return p.returncode, decode_bytes(p.stdout).strip()
    except subprocess.TimeoutExpired:
        return -999, "命令超时"
    except FileNotFoundError:
        return -998, "找不到命令：" + str(args[0])
    except Exception as exc:  # noqa: BLE001
        return -997, "执行出错：%s" % exc


def read_tail(path, lines=20):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except Exception as exc:  # noqa: BLE001
        return "（读不到日志：%s）" % exc


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def create_no_window_flag():
    """启动子进程时用，避免闪黑框。"""
    return CREATE_NO_WINDOW


def named_mutex(name):
    """创建命名互斥体；已被占用返回 None。"""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)
        handle = k.CreateMutexW(None, False, name)
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            return None
        return handle or True
    except Exception:
        return True


# --------------------------------------------------------------------------
# 密码加密（Windows DPAPI）
# --------------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _make_blob(data: bytes):
    buf = ctypes.create_string_buffer(data if data else b"\0", max(len(data), 1))
    return buf, DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _crypt32():
    lib = ctypes.WinDLL("crypt32", use_last_error=True)
    lib.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    lib.CryptProtectData.restype = wintypes.BOOL
    lib.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    lib.CryptUnprotectData.restype = wintypes.BOOL
    return lib


def dpapi_encrypt(text: str, machine: bool = False, entropy: bytes = b"CampusNetAssistant.v1") -> str:
    """machine=True 用机器范围加密：同机任何账户（含 SYSTEM）都能解开。

    系统级守护以 SYSTEM 身份运行，而 SYSTEM 解不开用户范围的密文，
    所以系统级那份密码必须用机器范围存。
    """
    if not text:
        return ""
    lib, k = _crypt32(), ctypes.WinDLL("kernel32", use_last_error=True)
    k.LocalFree.argtypes = [wintypes.HLOCAL]
    keep1, blob_in = _make_blob(text.encode("utf-8"))
    keep2, blob_ent = _make_blob(entropy)
    blob_out = DATA_BLOB()
    flags = 0x1 | (0x4 if machine else 0)       # 0x1 不弹界面；0x4 机器范围
    if not lib.CryptProtectData(ctypes.byref(blob_in), "CampusNetAssistant",
                                ctypes.byref(blob_ent), None, None, flags,
                                ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "加密失败")
    try:
        return base64.b64encode(ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
    finally:
        k.LocalFree(ctypes.cast(blob_out.pbData, wintypes.HLOCAL))
        del keep1, keep2


def dpapi_decrypt(token: str, machine: bool = False, entropy: bytes = b"CampusNetAssistant.v1") -> str:
    if not token:
        return ""
    lib, k = _crypt32(), ctypes.WinDLL("kernel32", use_last_error=True)
    k.LocalFree.argtypes = [wintypes.HLOCAL]
    try:
        raw = base64.b64decode(token)
    except Exception:
        return ""
    keep1, blob_in = _make_blob(raw)
    keep2, blob_ent = _make_blob(entropy)
    blob_out = DATA_BLOB()
    desc = wintypes.LPWSTR()
    flags = 0x1 | (0x4 if machine else 0)
    if not lib.CryptUnprotectData(ctypes.byref(blob_in), ctypes.byref(desc),
                                  ctypes.byref(blob_ent), None, None, flags,
                                  ctypes.byref(blob_out)):
        return ""
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8", "replace")
    finally:
        k.LocalFree(ctypes.cast(blob_out.pbData, wintypes.HLOCAL))
        if desc:
            k.LocalFree(ctypes.cast(desc, wintypes.HLOCAL))
        del keep1, keep2


# --------------------------------------------------------------------------
# 进程
# --------------------------------------------------------------------------
class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260),
    ]


def list_processes():
    """返回 {进程名(小写): [pid,...]}；用 Toolhelp 快照，不依赖 WMI。"""
    TH32CS_SNAPPROCESS = 0x00000002
    result = {}
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        try:
            e = _PROCESSENTRY32()
            e.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            ok = k.Process32First(snap, ctypes.byref(e))
            while ok:
                name = e.szExeFile.decode("gbk", "replace").strip().lower()
                if name:
                    result.setdefault(name, []).append(int(e.th32ProcessID))
                ok = k.Process32Next(snap, ctypes.byref(e))
        finally:
            k.CloseHandle(ctypes.c_void_p(snap))
        return result
    except Exception:
        return None


def pids_of(procs, image):
    return list((procs or {}).get(str(image).lower(), []))


def terminate_pid(pid):
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x0001, False, int(pid))      # PROCESS_TERMINATE
        if not h:
            return False
        try:
            return bool(k.TerminateProcess(ctypes.c_void_p(h), 1))
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    except Exception:
        return False


def listening_ports(pids):
    """返回这些 PID 在 127.0.0.1 上监听的端口集合。"""
    pids = set(int(p) for p in pids)
    if not pids:
        return set()
    code, out = run_cmd(["netstat", "-ano"], timeout=30)
    ports = set()
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        m = re.search(r"127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line, re.I)
        if m and int(m.group(2)) in pids:
            ports.add(int(m.group(1)))
    return ports


def window_titles():
    """枚举可见窗口标题（用于识别浏览器里正在看 ChatGPT 这类情况）。"""
    user32 = ctypes.windll.user32
    titles = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value:
                        titles.append(buf.value)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(enum_proc(_cb), 0)
    except Exception:
        pass
    return titles


# --------------------------------------------------------------------------
# 系统代理（HKCU）
# --------------------------------------------------------------------------
def set_system_proxy(enable: bool, server: str = ""):
    """把系统代理指向本地端口（翻墙时必须，否则应用流量不走代理）。"""
    import winreg
    sub = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            if enable and server:
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.*;<local>")
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            else:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        fn = ctypes.windll.user32.SendMessageTimeoutW
        fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                       wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
        fn.restype = wintypes.LPARAM
        fn(wintypes.HWND(0xFFFF), 0x001A, 0, sub, 0x0002, 3000, None)
        return True
    except Exception:
        return False


def clear_all_user_proxies():
    """清掉所有已加载用户配置里的系统代理（客户端被杀后残留会让浏览器上不了网）。"""
    import winreg
    sub = r"\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    changed = []
    try:
        root = winreg.OpenKey(winreg.HKEY_USERS, "")
    except Exception:
        return changed
    i = 0
    while True:
        try:
            sid = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if not sid.startswith("S-1-5-21"):
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, sid + sub, 0,
                                winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                need = False
                try:
                    if winreg.QueryValueEx(k, "ProxyEnable")[0]:
                        need = True
                except OSError:
                    pass
                for name in ("AutoConfigURL", "AutoConfigScript"):
                    try:
                        winreg.QueryValueEx(k, name)
                        need = True
                    except OSError:
                        pass
                if need:
                    winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                    for name in ("AutoConfigURL", "AutoConfigScript"):
                        try:
                            winreg.DeleteValue(k, name)
                        except OSError:
                            pass
                    changed.append(sid)
        except OSError:
            pass
    try:
        winreg.CloseKey(root)
    except Exception:
        pass
    if changed:
        try:
            fn = ctypes.windll.user32.SendMessageTimeoutW
            fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                           wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
            fn.restype = wintypes.LPARAM
            fn(wintypes.HWND(0xFFFF), 0x001A, 0, sub.strip("\\"), 0x0002, 3000, None)
        except Exception:
            pass
    return changed


# --------------------------------------------------------------------------
# 右下角托盘图标（纯 ctypes）
# --------------------------------------------------------------------------
WM_TRAY = 0x8000 + 1
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0203, 0x0205
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
MF_STRING, MF_SEPARATOR = 0x0, 0x800
IDM_SHOW, IDM_CHECK, IDM_QUIT = 1001, 1002, 1003

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
    ]


class TrayIcon:
    """托盘图标：双击=显示主界面，右键=菜单（显示 / 立即检查 / 退出）。

    add() 返回 False 时表示系统不允许创建（例如受限环境），调用方应退化为
    “隐藏窗口 + 靠快捷方式唤回”。
    """

    def __init__(self, tooltip, schedule, on_show, on_check, on_quit):
        self.tooltip = tooltip
        self.schedule = schedule
        self.on_show = on_show
        self.on_check = on_check
        self.on_quit = on_quit
        self.hwnd = None
        self.hicon = None
        self.added = False
        self._proc_ref = None
        self._class = "CampusNetAssistantTrayWnd"
        self._uid = 1

    def _fire(self, cb):
        try:
            self.schedule(cb)
        except Exception:
            pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONDBLCLK:
                    self._fire(self.on_show)
                elif lparam == WM_RBUTTONUP:
                    self._menu(hwnd)
        except Exception:
            pass
        try:
            fn = ctypes.windll.user32.DefWindowProcW
            fn.restype = ctypes.c_ssize_t
            fn.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            return fn(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def _menu(self, hwnd):
        u = ctypes.windll.user32
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.TrackPopupMenu.restype = ctypes.c_int
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, MF_STRING, IDM_SHOW, "显示主界面")
        u.AppendMenuW(menu, MF_STRING, IDM_CHECK, "立即检查")
        u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出程序")
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        u.PostMessageW(hwnd, 0x0000, 0, 0)
        u.DestroyMenu(menu)
        if cmd == IDM_SHOW:
            self._fire(self.on_show)
        elif cmd == IDM_CHECK:
            self._fire(self.on_check)
        elif cmd == IDM_QUIT:
            self._fire(self.on_quit)

    def _data(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self._uid
        nid.uCallbackMessage = WM_TRAY
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip[:127]
        return nid

    def add(self):
        if self.added:
            return True
        try:
            u = ctypes.windll.user32
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            if not self.hwnd:
                self._proc_ref = WNDPROC(self._wnd_proc)
                k.GetModuleHandleW.restype = wintypes.HINSTANCE
                hinst = k.GetModuleHandleW(None)
                wc = WNDCLASSW()
                wc.lpfnWndProc = self._proc_ref
                wc.hInstance = hinst
                wc.lpszClassName = self._class
                u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
                u.RegisterClassW(ctypes.byref(wc))
                u.CreateWindowExW.restype = wintypes.HWND
                u.CreateWindowExW.argtypes = [
                    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
                self.hwnd = u.CreateWindowExW(0, self._class, self._class, 0, 0, 0, 0, 0,
                                              None, None, hinst, None)
            if not self.hwnd:
                return False
            shell32 = ctypes.windll.shell32
            shell32.ExtractIconW.restype = wintypes.HICON
            try:
                self.hicon = shell32.ExtractIconW(None, sys.executable, 0)
            except Exception:
                self.hicon = None
            if not self.hicon:
                u.LoadIconW.restype = wintypes.HICON
                self.hicon = u.LoadIconW(None, ctypes.c_void_p(32512))
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            self.added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data())))
            return self.added
        except Exception:
            return False

    def remove(self):
        if not self.added:
            return
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
        except Exception:
            pass
        self.added = False


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def sleep_interruptible(seconds, stop_check=None):
    end = time.time() + max(0, int(seconds))
    while time.time() < end:
        if stop_check and stop_check():
            return
        time.sleep(0.5)


def json_dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def json_load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return default
