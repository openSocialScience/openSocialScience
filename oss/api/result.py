"""api 的返回形状。**只有一处定义。**

提案 §七：`{ "ok": bool, "summary": "一句人话", "data": {...}, "decision": {...可选} }`，
再加一个 `problems`（被拒时的逐条理由）。双通道的想法借自 research-plugins 的
`{ content: 文本, details: 结构 }`——`summary` 是给人和模型读的那一句，
`data` 是给程序读的那一份。**两边出自同一次计算**，不做两套。

两条规矩，写死在这里：

1. **`ok` 就是「退出码为零」。**不另立一套「成功但是不 ok」的说法——
   说明书按退出码分支（0 可交付 / 3 还要复审 / 4 轮次耗尽 / 5 没审过 /
   6 处方没处置 / 7 must 级没过），api 再发明一个平行的真假值只会多一个要对齐的东西。
   所以 `oss loop status` 报「还要复审」时 `ok` 是 false —— 它确实还不 ok。
2. **可选字段为空就不出现。**写成 `null` 的话下游要分「没有」和「有但是空」两种情况；
   不写就只有一种。
"""

from __future__ import annotations

from typing import Any

#: 返回字典允许出现的键，多一个都不行（`tests/test_stage3_api.py` 硬查）
KEYS = ("ok", "summary", "data", "decision", "problems")


def result(summary: str, data: dict[str, Any] | None = None, *,
           decision: dict | None = None, problems: list[str] | None = None,
           exit_code: int = 0) -> dict:
    """拼一个返回。`exit_code` 一定进 `data`——MCP 那一侧没有退出码可退。"""
    out: dict[str, Any] = {
        "ok": exit_code == 0,
        "summary": summary,
        "data": dict(data or {}) | {"exit_code": exit_code},
    }
    if decision:
        out["decision"] = decision
    if problems:
        out["problems"] = list(problems)
    return out
