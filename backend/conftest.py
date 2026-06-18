"""让 backend/ 进入 sys.path，使测试可 import 顶层 ``agents`` 包。

``anp`` 已 editable 安装可直接 import；``agents`` 是 backend 下的独立示例包
（不随 anp 安装，见 pyproject 的 packages.find），故在此补一条路径。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
