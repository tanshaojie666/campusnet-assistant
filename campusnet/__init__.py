# -*- coding: utf-8 -*-
"""CampusNetAssistant —— 校园网助手。

Windows 校园网自动接入（有线 / 无线可选）+ 代理/VPN 策略管理。

有线支持多种认证方式：PPPoE 拨号、自动获取 IP(DHCP)、静态 IP、
Web 门户认证、学校专用客户端、有线 802.1X。
只用 Python 标准库，不依赖任何第三方包。
"""
__version__ = "1.6.0"
__all__ = ["config", "net", "clients", "rules", "guard", "installer", "gui", "util"]
