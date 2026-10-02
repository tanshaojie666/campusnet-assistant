# -*- coding: utf-8 -*-
"""命令行入口：python -m campusnet [选项]"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="campusnet",
        description="校园网助手：自动接入校园网（有线/无线可选）+ 代理/VPN 策略管理")
    p.add_argument("--selftest", action="store_true", help="环境自检，排查问题用")
    p.add_argument("--scan", action="store_true", help="扫描本机：PPPoE 连接 / 无线配置 / 已装代理客户端")
    p.add_argument("--boot", action="store_true", help="系统级守护（由计划任务以 SYSTEM 调用）")
    p.add_argument("--install-boot", action="store_true", help="安装开机/锁屏守护（需管理员）")
    p.add_argument("--uninstall-boot", action="store_true", help="卸载系统级守护（需管理员）")
    p.add_argument("--purge-boot", action="store_true", help="卸载并删除系统级配置（需管理员）")
    p.add_argument("--set-account", nargs=2, metavar=("账号", "密码"), help="保存有线拨号账号密码")
    p.add_argument("--minimized", action="store_true", help="启动后隐藏到托盘（开机自启用）")
    args = p.parse_args(argv)

    from .config import load_config, set_account
    cfg = load_config()

    if args.boot:
        from .guard import boot_mode
        return boot_mode()
    if args.set_account:
        set_account(cfg, args.set_account[0], args.set_account[1])
        print("已保存账号（密码用 Windows DPAPI 加密）。")
        return 0
    if args.selftest:
        from .guard import selftest
        return selftest(cfg)
    if args.scan:
        from .guard import scan
        return scan(cfg)
    if args.install_boot or args.uninstall_boot or args.purge_boot:
        from .installer import install_boot, uninstall_boot
        if args.install_boot:
            return install_boot(cfg)
        return uninstall_boot(purge=args.purge_boot)

    from .gui import run_gui
    return run_gui(args.minimized, cfg)


if __name__ == "__main__":
    sys.exit(main())
