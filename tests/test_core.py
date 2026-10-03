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


if __name__ == "__main__":
    unittest.main(verbosity=2)
