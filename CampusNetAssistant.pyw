#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""双击这个文件即可运行（不会弹黑窗口）。

等价于：python -m campusnet
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from campusnet.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
