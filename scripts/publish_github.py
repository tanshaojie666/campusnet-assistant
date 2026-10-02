#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本项目发布到 GitHub。

为什么需要它：这台机器可能没装 git，或者 GitHub 被墙直连不上。
本脚本用 GitHub REST API（可以走本地代理）创建仓库并逐个上传文件。

用法：
    set GITHUB_TOKEN=ghp_xxxxxxxx
    python scripts/publish_github.py --repo campusnet-assistant --proxy http://127.0.0.1:7893

参数：
    --repo      仓库名（默认 campusnet-assistant）
    --user      GitHub 用户名（默认用 token 查 /user）
    --proxy     本地代理地址；留空则直连。可先用你的代理客户端把节点切到可用地区
    --private   建私有仓库
    --message   提交信息
    --dry-run   只列出要上传哪些文件，不真的上传

Token 需要的最小权限：repo（经典 token）或 Contents+Administration 读写（细粒度 token）。
上传完可以立刻在 GitHub 设置里删掉这个 token。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build", "_t"}
SKIP_FILES = {"config.json", "rules.json", ".env"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".bak", ".tmp"}


def opener_with(proxy: str):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def api(opener, token, method, path, payload=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "CampusNetAssistant-publisher")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return exc.code, body


def collect_files(root: str):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append((rel, full))
    out.sort()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="把校园网助手发布到 GitHub")
    ap.add_argument("--repo", default="campusnet-assistant")
    ap.add_argument("--user", default="")
    ap.add_argument("--proxy", default="http://127.0.0.1:7893",
                    help="本地代理地址；填 none 表示直连（例如已经用 Watt Toolkit 加速过 GitHub）")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--message", default="Initial commit: 校园网助手 CampusNetAssistant")
    ap.add_argument("--description", default="校园网自动接入（有线/无线可选）+ 翻墙模式："
                                             "打开指定程序时自动开代理并切到可用节点。纯 Python 标准库。")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = collect_files(root)
    print("准备上传 %d 个文件（来自 %s）" % (len(files), root))
    for rel, _full in files[:200]:
        print("   ", rel)
    if len(files) > 200:
        print("    … 还有 %d 个" % (len(files) - 200))
    if args.dry_run:
        print("\n--dry-run：没有真的上传。")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("\n× 没有找到 token。请先设置环境变量：")
        print("    set GITHUB_TOKEN=ghp_xxxxxxxxxx")
        return 1

    proxy = (args.proxy or "").strip()
    if proxy.lower() in ("none", "off", "direct", "-", "no"):
        proxy = ""                      # 直连
    op = opener_with(proxy)
    print("\n使用代理：", proxy or "(直连)")

    def _user(opener):
        return api(opener, token, "GET", "/user")

    status, me = _user(op)
    # 代理不通时自动改直连（例如已经用 Watt Toolkit 之类加速过 GitHub）
    if status != 200 and proxy:
        print("代理方式失败（%s），改用直连重试 …" % status)
        op = opener_with("")
        status, me = _user(op)
        if status == 200:
            print("直连成功。")
    if status != 200:
        print("× 读取 GitHub 用户失败（%s）：%s" % (status, me))
        print("  如果是网络错误：先用代理加速（Watt Toolkit / 本地代理）再试，")
        print("  或者用 --proxy none 强制直连。")
        return 1
    owner = args.user or me.get("login")
    print("GitHub 用户：", owner)

    status, repo = api(op, token, "GET", "/repos/%s/%s" % (owner, args.repo))
    if status == 404:
        print("仓库不存在，正在创建 …")
        status, repo = api(op, token, "POST", "/user/repos", {
            "name": args.repo, "description": args.description,
            "private": bool(args.private), "auto_init": False,
            "has_issues": True, "has_wiki": False,
        })
        if status not in (200, 201):
            print("× 创建仓库失败（%s）：%s" % (status, repo))
            return 1
        print("√ 仓库已创建：", repo.get("html_url"))
    else:
        print("仓库已存在，将更新其中的文件：", repo.get("html_url"))

    ok, fail = 0, []
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        path = "/repos/%s/%s/contents/%s" % (owner, args.repo, rel)
        sha = None
        s, info = api(op, token, "GET", path)
        if s == 200 and isinstance(info, dict):
            sha = info.get("sha")
        payload = {"message": args.message, "content": content}
        if sha:
            payload["sha"] = sha
        s, info = api(op, token, "PUT", path, payload)
        if s in (200, 201):
            ok += 1
            print("   ↑ %s" % rel)
        else:
            fail.append((rel, s, info))
            print("   × %s (%s)" % (rel, s))

    print("\n完成：成功 %d 个，失败 %d 个" % (ok, len(fail)))
    for rel, s, info in fail:
        print("   × %s -> %s %s" % (rel, s, str(info)[:120]))
    print("\n仓库地址：", repo.get("html_url"))
    return 0 if not fail else 2


if __name__ == "__main__":
    sys.exit(main())
