# -*- coding: utf-8 -*-
"""pytest 全局配置：把项目根目录加入 sys.path，保证 from data... / from utils... 可导入"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
