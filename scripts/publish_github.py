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
import urllib.parse
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
    ap.add_argument("--no-files", action="store_true",
                    help="只处理 Release（跳过推送仓库文件），修附件时用")
    ap.add_argument("--release", metavar="TAG",
                    help="更新完文件后创建 GitHub Release（例如 v1.2.0）")
    ap.add_argument("--asset", action="append", default=[],
                    help="附加到 Release 的本地文件，可重复；可用 路径::远端文件名 指定名字")
    ap.add_argument("--notes-file", default="",
                    help="Release 说明文件（markdown）；留空则自动从 CHANGELOG.md 取对应版本那段")
    ap.add_argument("--prerelease", action="store_true", help="标记为预发布")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = collect_files(root)
    if args.no_files:
        print("跳过仓库文件（--no-files），只处理 Release。")
    else:
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

    branch = repo.get("default_branch") or "main"
    ok, fail = 0, []
    if not args.no_files:
        ok, fail = commit_all(op, token, owner, args.repo, branch, files, args.message)
    if not args.no_files:
        print("\n完成：成功 %d 个，失败 %d 个" % (ok, len(fail)))
        for rel, s, info in fail:
            print("   × %s -> %s %s" % (rel, s, str(info)[:120]))
        print("\n仓库地址：", repo.get("html_url"))

    if args.release:
        publish_release(op, token, owner, args)
    return 0 if not fail else 2


def commit_all(op, token, owner, repo, branch, files, message):
    """把全部文件放进**一个提交**里推上去（Git Data API）。

    以前是用 Contents API 一个文件一个提交：30 个文件 = 30 个提交 = 30 次 CI，
    仓库历史也很碎。现在改成 blobs → tree → commit → 更新 ref，只有 1 个提交。
    """
    def _log(m):
        print("   " + m)

    s, ref = api(op, token, "GET",
                 "/repos/%s/%s/git/ref/heads/%s" % (owner, repo, branch))
    if s != 200:
        _log("拿不到分支 %s 的指针：%s" % (branch, str(ref)[:120]))
        return 0, [(branch, s, ref)]
    head = ref["object"]["sha"]
    s, commit = api(op, token, "GET", "/repos/%s/%s/git/commits/%s" % (owner, repo, head))
    base_tree = (commit or {}).get("tree", {}).get("sha")
    if not base_tree:
        _log("拿不到基线 tree")
        return 0, [(branch, s, commit)]

    def build(exclude_workflows=False):
        items, skipped = [], []
        for rel, full in files:
            if exclude_workflows and rel.replace("\\", "/").startswith(".github/workflows/"):
                skipped.append(rel)
                continue
            with open(full, "rb") as fh:
                content = base64.b64encode(fh.read()).decode("ascii")
            s2, blob = api(op, token, "POST", "/repos/%s/%s/git/blobs" % (owner, repo),
                           {"content": content, "encoding": "base64"})
            if s2 not in (200, 201):
                skipped.append(rel)
                continue
            items.append({"path": rel.replace("\\", "/"), "mode": "100644",
                          "type": "blob", "sha": blob["sha"]})
        return items, skipped

    def push(items):
        s2, tree = api(op, token, "POST", "/repos/%s/%s/git/trees" % (owner, repo),
                       {"base_tree": base_tree, "tree": items})
        if s2 not in (200, 201):
            return s2, tree, None, None
        s3, newc = api(op, token, "POST", "/repos/%s/%s/git/commits" % (owner, repo),
                       {"message": message, "tree": tree["sha"], "parents": [head]})
        if s3 not in (200, 201):
            return s3, newc, None, None
        s4, res = api(op, token, "PATCH",
                      "/repos/%s/%s/git/refs/heads/%s" % (owner, repo, branch),
                      {"sha": newc["sha"], "force": False})
        return s4, res, newc, tree

    items, skipped = build()
    _log("已生成 %d 个文件对象，准备提交 …" % len(items))
    status, info, newc, tree = push(items)
    if status not in (200, 201) and any(
            rel.replace("\\", "/").startswith(".github/workflows/") for rel, _f in files):
        # 多半是 token 没有 workflow 权限（GitHub 对 workflow 目录单独校验）。
        # 去掉这些文件重试 —— 它们通常已经在仓库里了，不需要每次重传。
        _log("整批提交失败（%s），去掉 .github/workflows/ 后重试 …" % status)
        items2, skipped2 = build(exclude_workflows=True)
        status, info, newc, tree = push(items2)
        skipped += skipped2
        items = items2
    if status not in (200, 201):
        _log("提交失败（%s）：%s" % (status, str(info)[:200]))
        return 0, [(branch, status, info)]

    if skipped:
        _log("跳过的文件：%s（多半是权限或不存在）" % "、".join(skipped[:5]))
    _log("√ 已提交 %d 个文件（单个提交 %s）" % (len(items), (newc or {}).get("sha", "")[:8]))
    return len(items), []


def extract_notes(root: str, tag: str) -> str:
    """从 CHANGELOG.md 里取对应版本的段落，当 Release 说明。"""
    ver = tag.lstrip("vV")
    try:
        with open(os.path.join(root, "CHANGELOG.md"), "r", encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return ""
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = ver in line
            if inside:
                continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def asset_name(path: str, override: str = "") -> str:
    """给 GitHub 用的附件名：只保留 ASCII。

    GitHub 的 uploads API 对非 ASCII 名字处理不靠谱（中文名会变成 "-.zip"），
    所以统一转成 ASCII 短横线形式；本地文件名不受影响。
    """
    raw = override or os.path.basename(path)
    stem, ext = os.path.splitext(raw)
    clean = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "-_.")) else "-"
                    for ch in stem)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-.") or "asset"
    return clean + (ext if ext else "")


def delete_asset(op, token, owner, repo_name, asset_id):
    return api(op, token, "DELETE", "/repos/%s/%s/releases/assets/%d"
               % (owner, repo_name, asset_id))


def upload_asset(op, token, owner, repo_name, release_id, path, name):
    """把本地文件作为 Release 附件上传。"""
    url = ("https://uploads.github.com/repos/%s/%s/releases/%d/assets?name=%s"
           % (owner, repo_name, release_id, urllib.parse.quote(name)))
    with open(path, "rb") as fh:
        data = fh.read()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("User-Agent", "CampusNetAssistant-publisher")
    try:
        with op.open(req, timeout=1800) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return exc.code, body


def publish_release(op, token, owner, args):
    """创建（或复用已有）tag + Release，并上传附件。

    可重复执行：Release 已存在就复用，同名附件先删再传。
    """
    tag, repo_name = args.release, args.repo
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    notes = ""
    if args.notes_file and os.path.isfile(args.notes_file):
        with open(args.notes_file, "r", encoding="utf-8-sig") as fh:
            notes = fh.read().strip()
    if not notes:
        notes = extract_notes(root, tag)
    if not notes:
        notes = "见 [CHANGELOG.md](CHANGELOG.md)"

    status, rel = api(op, token, "GET",
                      "/repos/%s/%s/releases/tags/%s" % (owner, repo_name, tag))
    if status == 200:
        print("\nRelease %s 已存在，复用：%s" % (tag, rel.get("html_url")))
    else:
        print("\n创建 Release %s …" % tag)
        status, rel = api(op, token, "POST", "/repos/%s/%s/releases" % (owner, repo_name), {
            "tag_name": tag, "name": tag, "body": notes,
            "draft": False, "prerelease": bool(args.prerelease),
        })
        if status not in (200, 201):
            print("× 创建 Release 失败（%s）：%s" % (status, str(rel)[:200]))
            return
        print("√ Release 已创建：", rel.get("html_url"))

    # 附件：支持 "本地路径::远端文件名"，中文名会自动转 ASCII
    for spec in args.asset or []:
        path, _, override = spec.partition("::")
        if not os.path.isfile(path):
            print("   ! 附件不存在，跳过：%s" % path)
            continue
        name = asset_name(path, override)
        for old in rel.get("assets") or []:
            if old.get("name") == name:
                delete_asset(op, token, owner, repo_name, old["id"])
                print("   已删除同名旧附件：%s" % name)
        print("   上传附件 %s → %s（%.1f MB）…" % (os.path.basename(path), name,
                                             os.path.getsize(path) / 1048576.0))
        s, info = upload_asset(op, token, owner, repo_name, rel["id"], path, name)
        if s in (200, 201):
            print("   ↑ 成功：%s" % info.get("browser_download_url"))
        else:
            print("   × 上传失败（%s）：%s" % (s, str(info)[:200]))
    print("\nRelease 页面：%s" % rel.get("html_url"))


if __name__ == "__main__":
    sys.exit(main())
