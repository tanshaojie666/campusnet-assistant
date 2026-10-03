# -*- coding: utf-8 -*-
"""核心逻辑单元测试 —— 不联网、不改动系统设置。

运行：
    python -m unittest discover -s tests -v
"""
import atexit
import json
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _make_tmp():
    """挑一个能写的临时目录当配置目录。

    注意：这里刻意不用 tempfile.mkdtemp —— 它在 Windows 上会建出带限制性
    ACL 的目录，后续写入会被拒绝。os.makedirs 建的目录才正常。
    """
    import tempfile
    candidates = [os.path.join(tempfile.gettempdir(), "_cna-test-config"),
                  os.path.join(ROOT, "_test_tmp")]
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, "probe.txt")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return path
        except OSError:
            continue
    raise RuntimeError("找不到可写的临时目录")


# 先指定临时配置目录，避免测试碰到真实配置
_TMP = _make_tmp()
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
os.environ["CNA_HOME"] = _TMP
os.environ["CNA_BOOT_DIR"] = os.path.join(_TMP, "boot")

from campusnet import config as C                                    # noqa: E402
from campusnet.clients import is_target_region                       # noqa: E402
from campusnet.config import DEFAULT_CONFIG, _deep_merge             # noqa: E402
from campusnet.net import (PORTAL_PRESETS, apply_portal_preset,      # noqa: E402
                           fill_template, parse_login_form,
                           pppoe_connections, wired_auth_list)


class TestTemplate(unittest.TestCase):
    """门户模板占位符替换。"""

    def test_basic(self):
        self.assertEqual(fill_template("u={username}&p={password}", "a", "b"), "u=a&p=b")

    def test_alias_placeholders(self):
        self.assertEqual(fill_template("{user}|{pwd}|{account}|{pass}", "a", "b"), "a|b|a|b")

    def test_chinese_placeholders(self):
        self.assertEqual(fill_template("{学号}/{密码}", "2620", "x"), "2620/x")

    def test_empty_input(self):
        self.assertEqual(fill_template("", "a", "b"), "")
        self.assertEqual(fill_template("no-placeholder", "a", "b"), "no-placeholder")


class TestLoginForm(unittest.TestCase):
    """自动填表用的登录页解析。"""

    HTML = ('<html><body><form action="/do_login" method="post">'
            '<input name="username" value="pre">'
            '<input type="hidden" name="token" value="abc">'
            '<input type="password" name="pwd" value="">'
            '</form></body></html>')

    def test_parse(self):
        action, method, fields = parse_login_form(self.HTML)
        self.assertEqual(action, "/do_login")
        self.assertEqual(method, "post")
        self.assertEqual(fields["username"], "pre")
        self.assertEqual(fields["token"], "abc")
        self.assertIn("pwd", fields)

    def test_no_form(self):
        self.assertEqual(parse_login_form("<html>nothing</html>"), ("", "", {}))

    def test_form_without_password_is_skipped(self):
        html = '<form action="/x"><input name="a" value="1"></form>'
        self.assertEqual(parse_login_form(html), ("", "", {}))

    def test_default_method_is_post(self):
        html = '<form action="/x"><input type="password" name="p"></form>'
        self.assertEqual(parse_login_form(html)[1], "post")


class TestWiredAuthList(unittest.TestCase):
    """有线认证方式的推断与优先级。"""

    def test_infer_from_connection(self):
        self.assertEqual(wired_auth_list({}, "宽带连接"), ["pppoe"])
        self.assertEqual(wired_auth_list({}, ""), ["dhcp"])

    def test_explicit_list_wins(self):
        self.assertEqual(wired_auth_list({"auth": ["dhcp", "portal"]}, "宽带连接"),
                         ["dhcp", "portal"])

    def test_case_insensitive(self):
        self.assertEqual(wired_auth_list({"auth": ["DHCP"]}, ""), ["dhcp"])


class TestDeepMerge(unittest.TestCase):
    """配置合并：新增默认字段不能顶掉用户已有设置。"""

    def test_nested_defaults_survive(self):
        default = {"a": {"x": 1, "y": {"z": 2}}, "b": 3}
        user = {"a": {"y": {"w": 9}}}
        merged = _deep_merge(default, user)
        self.assertEqual(merged["a"]["x"], 1)
        self.assertEqual(merged["a"]["y"]["z"], 2)
        self.assertEqual(merged["a"]["y"]["w"], 9)
        self.assertEqual(merged["b"], 3)

    def test_user_overrides_scalar(self):
        self.assertEqual(_deep_merge({"k": 1}, {"k": 2})["k"], 2)

    def test_non_dict_user_ignored(self):
        self.assertEqual(_deep_merge({"k": 1}, None)["k"], 1)


class TestRegionMatch(unittest.TestCase):
    """翻墙模式的节点地区筛选（用配置里的默认关键词）。"""

    HINTS = DEFAULT_CONFIG["flip"]["region_hints"]

    def test_us_nodes(self):
        self.assertTrue(is_target_region("🇺🇸 United States 03", self.HINTS))
        self.assertTrue(is_target_region("美国USLA2-A", self.HINTS))
        self.assertTrue(is_target_region("US-Los Angeles", self.HINTS))
        self.assertTrue(is_target_region("Los Angeles 01", self.HINTS))

    def test_other_regions(self):
        self.assertFalse(is_target_region("🇭🇰 Hong Kong 01", self.HINTS))
        self.assertFalse(is_target_region("🇯🇵 Japan 02", self.HINTS))
        self.assertFalse(is_target_region("🇸🇬 Singapore 01", self.HINTS))

    def test_empty_hints(self):
        self.assertFalse(is_target_region("🇺🇸 United States 01", []))


class TestPortalPresets(unittest.TestCase):
    """门户厂商预设。"""

    def test_presets_have_required_keys(self):
        for key, item in PORTAL_PRESETS.items():
            self.assertIn("label", item, key)
            self.assertIn("cfg", item, key)
            self.assertIn("mode", item["cfg"], key)

    def test_apply_keeps_credentials(self):
        merged, note = apply_portal_preset({"username": "u1", "password_enc": "xx"}, "sangfor")
        self.assertEqual(merged["mode"], "template")
        self.assertEqual(merged["username"], "u1")
        self.assertEqual(merged["password_enc"], "xx")
        self.assertIn("ac_portal", merged["url"])
        self.assertTrue(note)

    def test_unknown_preset(self):
        merged, note = apply_portal_preset({"mode": "auto"}, "no-such-vendor")
        self.assertEqual(merged["mode"], "auto")
        self.assertTrue(note)


class TestPppoeParsing(unittest.TestCase):
    """拨号电话簿解析（找出 PPPoE 连接名）。"""

    def setUp(self):
        self.pbk = os.path.join(_TMP, "rasphone.pbk")
        with open(self.pbk, "w", encoding="utf-8") as fh:
            fh.write("[我的宽带]\nEncoding=1\nDEVICE=PPPoE\nPHONE=\n\n"
                     "[公司VPN]\nDEVICE=vpn\n\n[校园网]\nDEVICE=PPPoE\n")

    def test_find_pppoe_only(self):
        self.assertEqual(pppoe_connections(self.pbk), ["我的宽带", "校园网"])

    def test_missing_file(self):
        self.assertEqual(pppoe_connections(os.path.join(_TMP, "nope.pbk")), [])


class TestConfigRoundTrip(unittest.TestCase):
    """配置读写 + 默认值完整性。"""

    def test_roundtrip(self):
        cfg = C.load_config()
        cfg["campus"]["mode"] = "wireless"
        cfg["campus"]["wifi_ssid"] = "MyCampus"
        cfg["flip"]["apps"] = ["a.exe", "b.exe"]
        C.save_config(cfg)

        again = C.load_config()
        self.assertEqual(again["campus"]["mode"], "wireless")
        self.assertEqual(again["campus"]["wifi_ssid"], "MyCampus")
        self.assertEqual(again["flip"]["apps"], ["a.exe", "b.exe"])

    def test_defaults_present(self):
        cfg = C.load_config()
        for key in ("mode", "connection", "wifi_ssid", "account", "wired"):
            self.assertIn(key, cfg["campus"])
        for key in ("auth", "adapter", "static", "client_exe", "lan_profile", "portal"):
            self.assertIn(key, cfg["campus"]["wired"])
        self.assertIn("clients", cfg)
        self.assertTrue(cfg["clients"])

    def test_wired_defaults_survive_partial_user_config(self):
        """用户只写了 auth，其它有线字段仍要拿到默认值。"""
        C.save_config({"campus": {"wired": {"auth": ["dhcp"]}}})
        cfg = C.load_config()
        self.assertEqual(cfg["campus"]["wired"]["auth"], ["dhcp"])
        self.assertIn("portal", cfg["campus"]["wired"])
        self.assertIn("mask", cfg["campus"]["wired"]["static"])


class TestRulesCompat(unittest.TestCase):
    """实时规则：兼容旧版字段名（enabled / flip_mode）。"""

    def test_legacy_keys(self):
        with open(C.RULES_FILE, "w", encoding="utf-8") as fh:
            json.dump({"enabled": False, "processes": ["a.exe"],
                       "wifi_policy": "manual",
                       "flip_mode": {"enabled": True, "apps": ["t.exe"]}}, fh)
        rules = C.load_rules(None)
        self.assertFalse(rules["kill_proxies"])
        self.assertEqual(rules["processes"], ["a.exe"])
        self.assertEqual(rules["wifi_policy"], "manual")
        self.assertTrue(rules["flip"]["enabled"])
        self.assertEqual(rules["flip"]["apps"], ["t.exe"])

    def test_save_and_reload(self):
        C.save_rules({"kill_proxies": False, "processes": ["x.exe"],
                      "wifi_policy": "off", "flip": {"enabled": False, "apps": []}})
        rules = C.load_rules(None)
        self.assertFalse(rules["kill_proxies"])
        self.assertEqual(rules["processes"], ["x.exe"])

    def test_processes_lowercased(self):
        C.save_rules({"kill_proxies": True, "processes": ["FlClash.EXE"],
                      "wifi_policy": "off", "flip": {}})
        self.assertEqual(C.load_rules(None)["processes"], ["flclash.exe"])


class TestPortalShared(unittest.TestCase):
    """门户认证配置：有线和无线共用一份，且兼容旧位置 campus.wired.portal。"""

    def tearDown(self):
        C.save_config({})

    def test_new_location(self):
        C.save_config({"campus": {"portal": {"mode": "template", "url": "http://new/login"}}})
        cfg = C.load_config()
        self.assertEqual(C.campus_portal(cfg)["url"], "http://new/login")

    def test_legacy_fallback(self):
        C.save_config({"campus": {"wired": {"portal": {"mode": "auto", "url": "http://old/login"}}}})
        cfg = C.load_config()
        self.assertEqual(C.campus_portal(cfg)["url"], "http://old/login")
        self.assertEqual(C.campus_portal(cfg)["mode"], "auto")

    def test_new_location_wins_when_set(self):
        C.save_config({"campus": {"portal": {"url": "http://new/login"},
                                  "wired": {"portal": {"url": "http://old/login"}}}})
        cfg = C.load_config()
        self.assertEqual(C.campus_portal(cfg)["url"], "http://new/login")

    def test_credentials_fall_back_to_campus_account(self):
        cfg = C.load_config()
        C.set_account(cfg, "2620", "pw")
        user, pwd = C.portal_credentials(cfg)
        self.assertEqual(user, "2620")
        self.assertEqual(pwd, "pw")

    def test_portal_credentials_override(self):
        cfg = C.load_config()
        C.set_account(cfg, "2620", "pw")
        cfg["campus"]["portal"] = {"username": "other"}
        cfg["campus"]["portal"]["password_enc"] = cfg["campus"]["password_enc"]
        user, pwd = C.portal_credentials(cfg)
        self.assertEqual(user, "other")
        self.assertEqual(pwd, "pw")


class TestRuntimeSignals(unittest.TestCase):
    """守护与界面之间的"信号文件"：暂停 / 断开请求 / 被禁用网卡 / 翻墙标记。

    这些是 1.5~1.6 版为了修「断不开""守护误杀代理"而加的机制，
    都是纯文件读写，最容易悄悄坏掉，所以重点测。
    """

    def tearDown(self):
        C.clear_pause()
        C.clear_disconnect()
        C.clear_disabled_adapter()
        C.clear_flip_active()
        C.clear_network_choice()
        C.clear_connect()

    def test_connect_request_lifecycle(self):
        """界面点「立即连接」→ 写连接请求 → 守护执行后清掉。"""
        self.assertFalse(C.connect_requested())
        self.assertTrue(C.request_connect("测试"))
        self.assertTrue(C.connect_requested())
        C.clear_connect()
        self.assertFalse(C.connect_requested())

    def test_process_age_is_sane(self):
        """进程年龄检测（用来区分"正在拨号"和"卡住的拨号"）。"""
        import os
        from campusnet.util import process_age_seconds
        age = process_age_seconds(os.getpid())
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)
        self.assertLess(age, 86400)

    def test_process_age_of_missing_pid(self):
        from campusnet.util import process_age_seconds
        self.assertIsNone(process_age_seconds(0x7FFFFFFF))

    def test_follow_vpn_defaults_off(self):
        """「跟着 VPN 走」默认关闭：不想被自动切网络的人应该不受影响。"""
        guard = DEFAULT_CONFIG["guard"]
        self.assertFalse(guard["follow_vpn"])
        self.assertEqual(guard["vpn_hotspot_ssid"], "")
        self.assertLessEqual(guard["vpn_switch_delay"], 60)   # 关掉 VPN 后要"立刻"切回

    def test_follow_vpn_roundtrip(self):
        """这两个值必须能通过 rules.json 传给系统级守护（界面改了立刻生效）。"""
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "follow_vpn": True, "vpn_hotspot_ssid": "iPhone", "flip": {}})
        r = C.load_rules(None)
        self.assertTrue(r["follow_vpn"])
        self.assertEqual(r["vpn_hotspot_ssid"], "iPhone")
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "follow_vpn": False, "vpn_hotspot_ssid": "", "flip": {}})
        self.assertFalse(C.load_rules(None)["follow_vpn"])

    def test_feature_switches_default_on(self):
        """所有自动行为默认开着，但都必须能关（「功能开关」区）。"""
        app = DEFAULT_CONFIG["app"]
        self.assertTrue(app["auto_update"])
        self.assertTrue(app["tray"])
        self.assertTrue(app["single_instance"])
        self.assertTrue(DEFAULT_CONFIG["guard"]["auto_dial"])
        self.assertTrue(DEFAULT_CONFIG["guard"]["kill_before_dial"])
        # 没连 VPN 时要够灵敏：默认巡检间隔不超过 10 秒
        self.assertLessEqual(DEFAULT_CONFIG["campus"]["interval"], 10)

    def test_feature_switches_roundtrip(self):
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "auto_dial": False, "kill_before_dial": False, "flip": {}})
        r = C.load_rules(None)
        self.assertFalse(r["auto_dial"])
        self.assertFalse(r["kill_before_dial"])
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "auto_dial": True, "kill_before_dial": True, "flip": {}})
        self.assertTrue(C.load_rules(None)["auto_dial"])

    def test_task_registered_has_fallback(self):
        """不能只看 schtasks 的退出码：普通权限下它会被拒绝 → 界面误报"守护没安装"。

        实测踩到过：守护明明在跑（心跳正常），界面却说没安装。
        """
        import inspect
        from campusnet import installer
        src = inspect.getsource(installer.task_registered)
        self.assertIn("schtasks", src)
        self.assertIn("BOOT_CONFIG", src)
        self.assertIn("heartbeat", src)
        self.assertIsInstance(installer.task_registered(), bool)

    def test_wifi_connected_ssid_ignores_hosted_state(self):
        """netsh 最后一行「承载网络状态 : 不可用」曾把"已连接"覆盖成"不可用" → 读成空。

        后果很严重：热点明明连着，程序却认为没有可用网络 → 翻墙永远不生效。
        """
        from campusnet import net
        fake = ("系统上有 1 个接口:\n\n    名称                   : WLAN\n"
                "    状态                   : 已连接\n"
                "    SSID                   : iPhone\n"
                "    BSSID                  : e2:15:6c:26:0f:68\n"
                "    配置文件               : iPhone \n\n"
                "    承载网络状态  : 不可用\n")
        orig = net.run_cmd
        net.run_cmd = lambda *a, **k: (0, fake)
        try:
            self.assertEqual(net.wifi_connected_ssid(), "iPhone")
        finally:
            net.run_cmd = orig

    def test_adapter_ip_and_gateway_exact_match(self):
        """adapter_ip 必须精确匹配网卡名：以前 "WLAN" 会串到 "vEthernet (WLAN)"。

        串号的后果：拿虚拟网卡的地址去探测 → 永远"上不了网" → 热点白连。
        """
        from campusnet import net
        fake = ("以太网适配器 以太网:\n\n   媒体状态  . . . : 媒体已断开连接\n\n"
                "无线局域网适配器 WLAN:\n\n   IPv4 地址 . . . : 172.20.10.4\n"
                "   默认网关. . . . : 172.20.10.1\n\n"
                "以太网适配器 vEthernet (WLAN):\n\n   IPv4 地址 . . . : 172.23.64.1\n")
        orig = net.run_cmd
        net.run_cmd = lambda *a, **k: (0, fake)
        net._IPCONFIG_CACHE["blocks"] = {}
        try:
            self.assertEqual(net.adapter_ip("WLAN"), "172.20.10.4")
            self.assertEqual(net.adapter_ip("vEthernet (WLAN)"), "172.23.64.1")
            self.assertEqual(net.adapter_gateway("WLAN"), "172.20.10.1")
        finally:
            net.run_cmd = orig
            net._IPCONFIG_CACHE["blocks"] = {}

    def test_other_network_candidates_include_wifi(self):
        """手机热点连着时，无线网卡必须算进"别的网络"候选（以前会整张漏掉）。"""
        from campusnet import net
        fns = (net.adapter_ip, net.wifi_connected_ssid, net.wifi_interfaces, net.wired_adapters)
        net.wifi_interfaces = lambda: ["WLAN"]
        net.wifi_connected_ssid = lambda: "iPhone"
        net.adapter_ip = lambda n: "172.20.10.4" if n == "WLAN" else "172.25.240.1"
        net.wired_adapters = lambda **k: []
        try:
            cands = net._other_network_candidates(
                {"campus": {"mode": "wired", "wifi_ssid": "iPhone"}})
            self.assertIn("172.20.10.4", [c[0] for c in cands])
        finally:
            (net.adapter_ip, net.wifi_connected_ssid,
             net.wifi_interfaces, net.wired_adapters) = fns

    def test_require_other_network_default_on(self):
        """安全闸门默认必须是开的：没有替代网络就绝不能断校园网。

        否则"打开浏览器 → 断校园网 → 热点又没连 → 彻底断网"，
        这正是用户实际踩到的坑。
        """
        self.assertTrue(DEFAULT_CONFIG["guard"]["require_other_network"])

    def test_other_network_available_shape(self):
        """这个函数是"能不能断校园网"的唯一依据，必须存在、且老实返回 (bool, 说明)。"""
        import inspect
        from campusnet import net
        self.assertTrue(callable(net.other_network_available))
        ok, why = net.other_network_available(DEFAULT_CONFIG)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)
        self.assertTrue(why)
        # 探测必须"真的发一个包出去"，不能只看网卡有没有 IP
        src = inspect.getsource(net.other_network_available)
        self.assertIn("tcp_probe", src)
        self.assertIn("bind_ip", src)

    def test_tray_wnd_proc_never_calls_tk(self):
        """托盘窗口过程里**绝不能**调用 Tk。

        实测（Python 3.14）：在 ctypes 窗口过程里调用 root.after() 会直接触发
        `Fatal Python error: PyEval_RestoreThread ... thread state is NULL`，
        进程当场死掉；用 pythonw 启动时还看不到任何提示 ——
        用户看到的就是"点托盘图标没反应"（就是它）。
        正确做法：窗口过程只登记动作，由界面线程轮询 poll() 执行。
        """
        from campusnet.util import (TrayIcon, WM_TRAY, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
                                    WM_RBUTTONUP)
        touched = []
        tray = TrayIcon("测试",
                        lambda fn: touched.append("schedule"),   # 绝不该被调用
                        lambda: touched.append("show"),
                        lambda: touched.append("check"),
                        lambda: touched.append("quit"))

        tray._wnd_proc(0, WM_TRAY, 1, WM_LBUTTONUP)          # 单击左键
        self.assertEqual(tray.poll(), ["show"])
        tray._wnd_proc(0, WM_TRAY, 1, WM_LBUTTONDBLCLK)      # 双击左键
        self.assertEqual(tray.poll(), ["show"])

        # 右键走菜单：把菜单替换掉，验证命令是被"登记"而不是直接执行
        tray._menu = lambda hwnd: "quit"
        tray._wnd_proc(0, WM_TRAY, 1, WM_RBUTTONUP)
        self.assertEqual(tray.poll(), ["quit"])

        # 关键断言：全程没有触碰任何 Tk 回调
        self.assertEqual(touched, [])
        self.assertEqual(tray.poll(), [])                    # 取过一次就空了

    def test_network_choice_lifecycle(self):
        """手动切换网络的选择：切走要记住（自动逻辑让路），切回要清掉。"""
        self.assertEqual(C.get_network_choice(), "")
        C.set_network_choice("wifi", "iPhone")
        self.assertEqual(C.get_network_choice(), "wifi")
        C.set_network_choice("none")
        self.assertEqual(C.get_network_choice(), "none")
        C.clear_network_choice()
        self.assertEqual(C.get_network_choice(), "")

    def test_kill_before_dial_default_on(self):
        """拨号前先关代理/VPN 默认开启（会抢路由、拦 DNS，导致拨号慢甚至拨不上）。"""
        self.assertTrue(DEFAULT_CONFIG["guard"]["kill_before_dial"])

    def test_dial_gap_is_reasonable(self):
        """两次拨号的最小间隔：太短会猛拨刷屏，太长会让人等太久。"""
        from campusnet.guard import MIN_DIAL_GAP
        self.assertGreaterEqual(MIN_DIAL_GAP, 30)
        self.assertLessEqual(MIN_DIAL_GAP, 300)

    def test_clear_stale_dials_is_safe(self):
        """清理卡住的 rasdial：没有卡住进程时也必须安全返回 0。"""
        from campusnet.net import clear_stale_dials
        self.assertGreaterEqual(clear_stale_dials(), 0)

    def test_pause_lifecycle(self):
        self.assertFalse(C.pause_active())
        C.set_pause(5)
        self.assertTrue(C.pause_active())
        C.clear_pause()
        self.assertFalse(C.pause_active())

    def test_expired_pause_is_inactive(self):
        C.set_pause(-1)                      # 已经过期
        self.assertFalse(C.pause_active())

    def test_disconnect_request(self):
        self.assertFalse(C.disconnect_requested())
        self.assertTrue(C.request_disconnect("test"))
        self.assertTrue(C.disconnect_requested())
        C.clear_disconnect()
        self.assertFalse(C.disconnect_requested())

    def test_disabled_adapter_is_remembered(self):
        """必须记住具体是哪块网卡：否则恢复时会启用错的那一块。"""
        self.assertEqual(C.get_disabled_adapter(), "")
        C.set_disabled_adapter("以太网")
        self.assertEqual(C.get_disabled_adapter(), "以太网")
        C.clear_disabled_adapter()
        self.assertEqual(C.get_disabled_adapter(), "")

    def test_flip_active_lifecycle(self):
        self.assertFalse(C.flip_active(None)[0])
        C.set_flip_active("eix", "测试")
        on, client, _note, age = C.flip_active(None)
        self.assertTrue(on)
        self.assertEqual(client, "eix")
        self.assertLess(age, 5)
        C.clear_flip_active()
        self.assertFalse(C.flip_active(None)[0])

    def test_flip_active_expires(self):
        C.set_flip_active("eix")
        self.assertFalse(C.flip_active(None, max_age=-1)[0])

    def test_flip_defaults_are_safe(self):
        flip = DEFAULT_CONFIG["flip"]
        self.assertEqual(flip["when"], "off_campus")       # 默认：校园网内不翻墙
        self.assertFalse(flip["browser_always"])            # 默认：不靠"浏览器开着"触发
        self.assertIn("google", flip["title_hints"])        # 打开 Google 能触发
        self.assertIn("youtube", flip["title_hints"])

    def test_reconnect_defaults_off(self):
        """「断开后没在翻墙就自动连回来」是可选功能，默认关闭（手动断开应当保持）。"""
        guard = DEFAULT_CONFIG["guard"]
        self.assertFalse(guard["reconnect_when_no_flip"])
        self.assertGreaterEqual(guard["reconnect_after"], 60)

    def test_reconnect_flag_roundtrip(self):
        """这个开关要能通过 rules.json 传给系统级守护，并且界面改了立即生效。"""
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "reconnect_when_no_flip": True, "flip": {}})
        self.assertTrue(C.load_rules(None)["reconnect_when_no_flip"])
        C.save_rules({"kill_proxies": True, "processes": [], "wifi_policy": "off",
                      "reconnect_when_no_flip": False, "flip": {}})
        self.assertFalse(C.load_rules(None)["reconnect_when_no_flip"])


class TestClientSelection(unittest.TestCase):
    """翻墙客户端勾选：只勾谁就只用谁（flip.order 里只放勾选的 id）。"""

    def test_no_client_selected_is_handled(self):
        """一个都没勾时不能崩，也不能偷偷去试名单外的客户端。"""
        from campusnet.clients import ensure_client_ready
        cfg = C.load_config()
        logs = []
        ok, name, node, key = ensure_client_ready(cfg, [], logs.append)
        self.assertFalse(ok)
        self.assertEqual((name, node, key), ("", "", ""))
        self.assertTrue(any("没有勾选" in m for m in logs), logs)

    def test_unknown_ids_are_dropped(self):
        from campusnet.clients import ensure_client_ready
        cfg = C.load_config()
        logs = []
        ok, _n, _node, _k = ensure_client_ready(cfg, ["no-such-client"], logs.append)
        self.assertFalse(ok)
        self.assertTrue(any("没有勾选" in m for m in logs), logs)

    def test_order_is_what_gets_written(self):
        cfg = C.load_config()
        cfg["flip"]["order"] = ["eix"]
        self.assertEqual(cfg["flip"]["order"], ["eix"])
        self.assertIn("eix", [c["id"] for c in cfg["clients"]])


class TestBootConfig(unittest.TestCase):
    """系统级配置构建：密码必须是机器范围加密、且不含用户范围密文。"""

    def test_build(self):
        cfg = C.load_config()
        cfg["campus"]["account"] = "2620"
        C.set_account(cfg, "2620", "secret-pw")
        boot = C.build_boot_config(cfg)
        self.assertEqual(boot["account"], "2620")
        self.assertTrue(boot["password_machine"])
        from campusnet.util import dpapi_decrypt
        self.assertEqual(dpapi_decrypt(boot["password_machine"], machine=True), "secret-pw")
        self.assertIn("wired", boot)
        self.assertNotIn("password_enc", boot["wired"]["portal"])


class TestHotspotAutoConnect(unittest.TestCase):
    """手机热点"自己连"这一整套 —— 修的是"热点开着还得手动切"的真 bug。

    根因：我们自己的「无线策略=把所有无线改成手动连接」把手机热点也改成了手动，
    于是 Windows 永远不会自己连热点，用户只能手动去 Wi-Fi 列表点。
    """

    def _clear_caches(self):
        from campusnet import net
        net._WIFI_MODE_CACHE.clear()
        net._WIFI_VISIBLE["t"] = 0.0
        net._WIFI_VISIBLE["names"] = []

    def test_profile_mode_read_via_netsh(self):
        """连接模式必须从 netsh 读（普通权限）—— 以前读 ProgramData 永远读不到。"""
        from unittest import mock
        from campusnet import net
        self._clear_caches()
        out = ("配置文件 iPhone 的接口信息\n====\n"
               "    连接模式             : 手动连接\n")
        with mock.patch.object(net, "run_cmd", return_value=(0, out)):
            self._clear_caches()
            self.assertEqual(net.wifi_profile_mode("iPhone"), "manual")
        out2 = ("    Connection mode    : Connect automatically\n")
        with mock.patch.object(net, "run_cmd", return_value=(0, out2)):
            self._clear_caches()
            self.assertEqual(net.wifi_profile_mode("iPhone"), "auto")

    def test_policy_keeps_hotspot_auto(self):
        """「无线策略=手动」必须放过手机热点和校园网无线，否则功能自相矛盾。"""
        from unittest import mock
        from campusnet import net
        calls = []

        def fake(args, timeout=30, **_kw):
            calls.append(list(args))
            return 0, ""

        with mock.patch.object(net, "wifi_profiles",
                               return_value=["iPhone", "CMCC-NXM5", "xd-wlan"]), \
                mock.patch.object(net, "run_cmd", side_effect=fake):
            ok, total, msg = net.apply_wifi_policy("manual", keep_auto=["iPhone", "xd-wlan"])
        self.assertEqual((ok, total), (3, 3))
        auto = [c for c in calls if "connectionmode=auto" in c]
        manual = [c for c in calls if "connectionmode=manual" in c]
        self.assertEqual(len(auto), 2, calls)
        self.assertEqual(len(manual), 1, calls)
        self.assertTrue(any("name=iPhone" in c for c in auto), calls)
        self.assertIn("保持自动连接", msg)

    def test_ensure_connected_when_already_on(self):
        from unittest import mock
        from campusnet import net
        with mock.patch.object(net, "wifi_connected_ssid", return_value="iPhone"):
            ok, why = net.ensure_wifi_connected("iPhone", timeout=1)
        self.assertTrue(ok)
        self.assertIn("iPhone", why)

    def test_ensure_connected_missing_profile_gives_actionable_reason(self):
        from unittest import mock
        from campusnet import net
        with mock.patch.object(net, "wifi_connected_ssid", return_value=""), \
                mock.patch.object(net, "wifi_profiles", return_value=["CMCC-NXM5"]):
            ok, why = net.ensure_wifi_connected("iPhone", timeout=1)
        self.assertFalse(ok)
        self.assertIn("无线配置里没有", why)

    def test_visible_ssids_parsing(self):
        from unittest import mock
        from campusnet import net
        self._clear_caches()
        out = ("接口名称 : WLAN\n\nSSID 1 : iPhone\n    网络类型 : 基础结构\n"
               "SSID 2 : CMCC-NXM5\n")
        with mock.patch.object(net, "run_cmd", return_value=(0, out)):
            self._clear_caches()
            names = net.wifi_visible_ssids()
        self.assertEqual(names, ["iPhone", "CMCC-NXM5"])

    def test_preconnect_defaults_on(self):
        """热点预连接默认开着（用户要的就是"我自己不用动手"）。"""
        C.save_rules({"flip": {}})
        self.assertTrue(C.load_rules(None).get("hotspot_preconnect", True))
        self.assertTrue(DEFAULT_CONFIG["guard"]["hotspot_preconnect"])
        C.save_rules({"flip": {}, "hotspot_preconnect": False})
        self.assertFalse(C.load_rules(None)["hotspot_preconnect"])
        C.save_rules({"flip": {}, "hotspot_preconnect": True})

    # ------------------------------------------------------------ 回归：静态自查
    def test_check_names_is_scope_aware(self):
        """静态自查必须区分**作用域**：兄弟函数里的局部 import 不算数。

        真实事故：`gui.py` 的 `_flip_watch` 用了 `pause_active`，
        而它只在兄弟函数 `_watchdog` 内部 import 过 → 翻墙模式每 5 秒
        抛一次 NameError，整个功能静默失效。旧版检查器"名字在文件里出现过
        就算数"，正好放过了它。这里锁住"必须报出来"。
        """
        import ast
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "cna_check_names", os.path.join(root, "scripts", "check_names.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        bad = ("import os\n"
               "def a():\n"
               "    from x import y\n"
               "    return y()\n"
               "def b():\n"
               "    return y()\n")          # ← 这里 y 不存在，必须报
        problems = mod.Checker("t.py", bad).run(ast.parse(bad))
        self.assertTrue(problems, "兄弟作用域里的局部导入不该被当成可见名字")
        self.assertIn("y", problems[0])

        good = ("import os\n"
                "from x import y\n"
                "def a():\n"
                "    return y()\n")
        self.assertEqual(mod.Checker("t.py", good).run(ast.parse(good)), [])

    def test_flip_watch_survives_a_round(self):
        """翻墙模式的后台循环至少要能跑几秒不报错。

        上面那个 NameError 就是这么溜出去的：静态检查没抓到、单元测试也没跑到
        这条循环。这里真把它跑起来，并断言没有"出错"日志。
        （把触发规则和热点连接都换成假的，确保测试不碰真实网络。）
        """
        import threading
        import time
        from unittest import mock
        try:
            import tkinter as tk
        except Exception:
            self.skipTest("没有 tkinter")
        from campusnet import gui as G

        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            app = G.App(root, C.load_config())
            logs = []
            app.log = lambda msg, tag="info": logs.append("[%s] %s" % (tag, msg))
            with mock.patch.object(G, "flip_triggered", return_value=(False, "")), \
                    mock.patch.object(G.App, "_auto_connect_hotspot", return_value=False):
                t = threading.Thread(target=app._flip_watch, daemon=True)
                t.start()
                time.sleep(3.0)
                app.stop_event.set()
                t.join(timeout=5)
            bad = [x for x in logs if "出错" in x]
            self.assertEqual(bad, [], "翻墙 watcher 报错了：%s" % bad)
        finally:
            if app is not None:
                app.closing = True
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
