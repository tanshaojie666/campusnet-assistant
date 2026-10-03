#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""静态自查：找出"用了但没定义/没导入"的名字。

为什么需要它：`python -m py_compile` 只检查语法，抓不到
`name 'xxx' is not defined` 这类运行时才爆的错。本项目就踩过一次
（守护循环里用了 wired_bind_ip 但忘了 import，结果每轮都抛异常、
自动拨号和关代理静默失效）。这个脚本用 AST 做一次轻量检查，
不需要任何第三方依赖。

用法：
    python scripts/check_names.py          # 检查 campusnet/ 与 tests/
    python scripts/check_names.py <目录>

局限（有意为之，避免误报）：
  · 只做"模块级可见名字"的粗粒度判断，不分析作用域细节；
  · 对 getattr/globals()/exec 之类动态取名字的地方一律放行；
  · 因此它**只报几乎可以确定的错误**，不追求覆盖全部未定义名字。
"""
from __future__ import annotations

import ast
import builtins
import os
import sys

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__",
                                 "__spec__", "__loader__", "__builtins__", "self", "cls"}


def module_bound_names(tree, path):
    """收集模块里"能被引用到"的名字：导入、赋值、def/class、for 目标、with as 等。"""
    names = set()

    class Collect(ast.NodeVisitor):
        def visit_Import(self, node):
            for a in node.names:
                names.add((a.asname or a.name.split(".")[0]))

        def visit_ImportFrom(self, node):
            for a in node.names:
                if a.name != "*":
                    names.add(a.asname or a.name)

        def visit_FunctionDef(self, node):
            names.add(node.name)
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                names.add(arg.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                names.add(arg.arg)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            names.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                names.add(node.name)
            self.generic_visit(node)

        def visit_Global(self, node):
            names.update(node.names)

        def visit_Nonlocal(self, node):
            names.update(node.names)

        def visit_comprehension(self, node):
            self.generic_visit(node)

    Collect().visit(tree)
    return names


def check_file(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src, path)
    except SyntaxError as exc:
        return ["%s:%s 语法错误: %s" % (path, exc.lineno, exc.msg)]

    bound = module_bound_names(tree, path) | BUILTINS

    # 动态取名字的地方一律放行，避免误报
    if "getattr(" in src or "globals()" in src or "exec(" in src or "eval(" in src:
        dynamic = True
    else:
        dynamic = False

    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in bound:
                continue
            problems.append("%s:%d 疑似未定义的名字: %s" % (path, node.lineno, node.id))
    return problems


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
