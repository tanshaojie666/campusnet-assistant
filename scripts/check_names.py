#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""静态自查：找出"用了，但在当前作用域里没定义/没导入"的名字。

为什么需要它：`py_compile` 只检查语法，抓不到 `name 'xxx' is not defined`。
本项目踩过两次，两次都是**静默失效**：
  1. 守护循环里用了 `wired_bind_ip` 却忘了 import → 每轮抛异常，自动拨号静默失效；
  2. `gui.py` 的 `_flip_watch` 里用了 `pause_active`，而它只在**兄弟函数**
     `_watchdog` 内部 import 过 → 翻墙模式每 5 秒崩一次，整个功能失效。

旧版检查器只做"模块级粗查"（名字在文件里出现过就算数），
兄弟函数的局部导入正好骗过它 —— 所以第 2 个 bug 溜到了线上。
现在做**真正的作用域分析**：只认"自己这一层 + 外层"绑定的名字，
兄弟作用域里的局部变量/局部导入**不算**。

用法：
    python scripts/check_names.py            # 检查 campusnet/ tests/ scripts/
    python scripts/check_names.py <目录或文件>

局限（有意为之，避免误报）：
  · 不做类型推断，只做名字解析；
  · 文件里以**字符串字面量**出现过的名字一律放行（可能是 getattr/globals 注入）；
  · 出现 `import *` 的文件整体跳过（无法知道导入了什么）。
"""
from __future__ import annotations

import ast
import builtins
import os
import re
import sys

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__class__", "__debug__", "self", "cls", "super",
}
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _params(fn):
    a = fn.args
    out = set()
    for arg in (list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs)):
        out.add(arg.arg)
    if a.vararg:
        out.add(a.vararg.arg)
    if a.kwarg:
        out.add(a.kwarg.arg)
    return out


def _bound(node):
    """收集"这一层作用域自己绑定"的名字（不下钻嵌套作用域、不下钻推导式）。"""
    names = set()

    def walk(b):
        for ch in ast.iter_child_nodes(b):
            if isinstance(ch, NESTED):
                names.add(getattr(ch, "name", None) or "<lambda>")
                continue                      # 它的局部名字属于它自己
            if isinstance(ch, COMPS):
                continue
            if isinstance(ch, ast.Import):
                for a in ch.names:
                    names.add(a.asname or a.name.split(".")[0])
                continue
            if isinstance(ch, ast.ImportFrom):
                for a in ch.names:
                    names.add("*" if a.name == "*" else (a.asname or a.name))
                continue
            if isinstance(ch, ast.Name) and isinstance(ch.ctx, (ast.Store, ast.Del)):
                names.add(ch.id)
            elif isinstance(ch, ast.ExceptHandler) and ch.name:
                names.add(ch.name)
            elif isinstance(ch, ast.MatchAs) and ch.name:
                names.add(ch.name)
            elif isinstance(ch, ast.MatchStar) and ch.name:
                names.add(ch.name)
            elif isinstance(ch, ast.MatchMapping) and ch.rest:
                names.add(ch.rest)
            elif isinstance(ch, ast.arg):
                names.add(ch.arg)
            walk(ch)

    walk(node)
    return names


class Checker:
    def __init__(self, path, src):
        self.path = path
        self.out = []
        # 文件里作为字符串出现过的名字放松检查（getattr/globals/setattr 注入）
        self.relax = set(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""", src))
        self.skipped = "import *" in src

    # ---------------------------------------------------------------- 主流程
    def run(self, tree):
        if self.skipped:
            return []
        module_names = _bound(tree)
        for stmt in tree.body:
            self.scan(stmt, [module_names | BUILTINS])
        return self.out

    # ---------------------------------------------------------------- 作用域
    def scan(self, node, chain):
        """在 chain（由外到内的可见名字集合）里扫这个节点的 Load 名字。"""
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self.check(node, chain)
            return
        if isinstance(node, (ast.Lambda,)):
            for stmt in (node.body,):
                self.scan(stmt, chain + [_params(node)])
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.handle_func(node, chain)
            return
        if isinstance(node, ast.ClassDef):
            for e in list(node.bases) + list(node.decorator_list) + \
                    [k.value for k in node.keywords]:
                self.scan(e, chain)
            self.scan_body(node.body, chain + [_bound(node)])
            return
        if isinstance(node, COMPS):
            inner = set()
            for gen in node.generators:
                for ch in ast.walk(gen.target):
                    if isinstance(ch, ast.Name):
                        inner.add(ch.id)
            new_chain = chain + [inner]
            for i, gen in enumerate(node.generators):
                # 第一个 for 后面的可迭代对象在**外层**求值
                self.scan(gen.iter, chain if i == 0 else new_chain)
                for cond in gen.ifs:
                    self.scan(cond, new_chain)
            if isinstance(node, ast.DictComp):
                self.scan(node.key, new_chain)
                self.scan(node.value, new_chain)
            else:
                self.scan(node.elt, new_chain)
            return
        if isinstance(node, ast.arguments):
            # 默认值属于**外层**作用域；参数名属于内层（由 handle_func 提供）
            for e in list(node.defaults) + [x for x in node.kw_defaults if x]:
                self.scan(e, chain)
            for arg in [node.vararg, node.kwarg] + list(node.args) + list(node.kwonlyargs) + \
                    list(getattr(node, "posonlyargs", [])):
                if arg is not None and arg.annotation is not None:
                    self.scan(arg.annotation, chain)
            return
        for ch in ast.iter_child_nodes(node):
            self.scan(ch, chain)

    def handle_func(self, fn, chain):
        for e in fn.decorator_list:
            self.scan(e, chain)
        for e in list(fn.args.defaults) + [x for x in fn.args.kw_defaults if x]:
            self.scan(e, chain)
        for arg in [fn.args.vararg, fn.args.kwarg] + list(fn.args.args) + \
                list(fn.args.kwonlyargs) + list(getattr(fn.args, "posonlyargs", [])):
            if arg is not None and arg.annotation is not None:
                self.scan(arg.annotation, chain)
        ret = getattr(fn, "returns", None)
        if ret is not None:
            self.scan(ret, chain)
        self.scan_body(fn.body, chain + [_params(fn) | _bound(fn)])

    def scan_body(self, body, chain):
        for stmt in body:
            self.scan(stmt, chain)

    def check(self, node, chain):
        name = node.id
        if name in self.relax:
            return
        for scope in chain:
            if name in scope:
                return
        if name in BUILTINS:
            return
        self.out.append("%s:%d 疑似未定义的名字: %s"
                        % (self.path, node.lineno, name))


def check_file(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src, path)
    except SyntaxError as exc:
        return ["%s:%s 语法错误: %s" % (path, exc.lineno, exc.msg)]
    return Checker(path, src).run(tree)


def main(argv):
    roots = argv[1:] or ["campusnet", "tests", "scripts"]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems = []
    checked = 0
    for root in roots:
        root = root if os.path.isabs(root) else os.path.join(here, root)
        if os.path.isfile(root):
            files = [root]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                files += [os.path.join(dirpath, f) for f in filenames
                          if f.endswith((".py", ".pyw"))]
        for f in sorted(files):
            checked += 1
            problems += check_file(f)

    print("检查了 %d 个文件" % checked)
    if problems:
        print("\n发现 %d 处可疑的未定义名字：" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("没有发现未定义的名字 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
