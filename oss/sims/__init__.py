"""插头注册表：模拟器名 → pack 实现。查不到就点名，不静默。"""
from __future__ import annotations

import importlib

KNOWN = ("oasis", "agentsociety")


def get_adapter(name: str):
    """按名字取插头模块。再添新插头时只改 KNOWN（agentsociety 已照此办理）。

    抛 ValueError 不抛 KeyError——KeyError 的 str() 自带引号，
    api 层把它透传成散文行会多出一对引号（散文是钉死的）。
    """
    if name not in KNOWN:
        raise ValueError(f"不认识的模拟器「{name}」，现有插头：{'、'.join(KNOWN)}")
    return importlib.import_module(f"oss.sims.{name}.adapter")
