# -*- coding: utf-8 -*-
"""utils/common.py 安全防护与通用工具单测"""
import os
import tempfile
from unittest.mock import patch

from utils.common import (
    _PROJECT_ROOT,
    calc_profit_rate,
    is_safe_public_url,
    is_safe_write_path,
    safe_float_convert,
    safe_json_parse,
)


def test_url_rejects_non_http_schemes():
    assert is_safe_public_url('ftp://example.com/file') is False
    assert is_safe_public_url('file:///etc/passwd') is False
    assert is_safe_public_url('') is False
    assert is_safe_public_url('not a url') is False


def test_url_rejects_localhost_and_private_ips():
    assert is_safe_public_url('http://localhost:8501') is False
    assert is_safe_public_url('http://127.0.0.1:8501') is False
    assert is_safe_public_url('http://[::1]/') is False
    assert is_safe_public_url('http://192.168.1.1/') is False
    assert is_safe_public_url('http://10.0.0.1/') is False
    assert is_safe_public_url('http://172.16.0.1/') is False
    assert is_safe_public_url('http://169.254.169.254/latest/meta-data') is False  # 云元数据端点


def test_url_accepts_public_ip_literal():
    assert is_safe_public_url('https://93.184.216.34/') is True


def test_url_resolves_domain_and_blocks_private():
    with patch('utils.common.socket.getaddrinfo',
               return_value=[(2, 1, 6, '', ('192.168.1.1', 0))]):
        assert is_safe_public_url('https://internal.example.com/api') is False
    with patch('utils.common.socket.getaddrinfo',
               return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):
        assert is_safe_public_url('https://public.example.com/api') is True


def test_write_path_confined_to_project():
    assert is_safe_write_path('my_funds.json') is True                   # 项目根内
    assert is_safe_write_path(os.path.join(_PROJECT_ROOT, 'a.json')) is True
    assert is_safe_write_path(os.path.join(tempfile.gettempdir(), 'x.json')) is False
    assert is_safe_write_path('../escape.json') is False                 # 上级目录穿越
    assert is_safe_write_path('a/../../escape.json') is False


def test_safe_json_parse_and_float():
    assert safe_json_parse('{"a": 1}') == {'a': 1}
    assert safe_json_parse('not json') is None
    assert safe_json_parse('') is None
    assert safe_float_convert('3.14') == 3.14
    assert safe_float_convert('abc', default=-1) == -1
    assert safe_float_convert(None, default=0) == 0


def test_calc_profit_rate():
    assert calc_profit_rate(10, 100) == 10.0
    assert calc_profit_rate(10, 0) == 0.0
    assert calc_profit_rate(10, None) == 0.0
