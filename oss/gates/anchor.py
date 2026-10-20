"""锚点门：论文必须回答冻结时的那个问题。

事故（2026-08-28）：审计 FAIL 之后模型换了 4 次题，ideas.json 的 direction 被改写成
「（原方向 edu_001 换题）」。说明书写着「换设计是为了回答同一个问题」，但没有门。
换题只有一条路：`oss anchor switch --authorized-by user`，留痕里必须有用户授权。

门要堵的是**绕路**，不只是明着改：删掉 plan.json、把状态文件写坏，
在「比对当前与冻结」这种朴素写法下都会变成「没发现问题」——静默放行比没有门更糟。
"""

from __future__ import annotations

from pathlib import Path

from oss.core import state as state_mod
from oss.core.findings import Finding
#: 判据在 core 里（`oss.core.state`），门和事后核账（`core/behavior.py`）用的是同一个函数。
#: 名字仍从这里导出：`oss anchor switch` 与测试都写 `anchor_gate.norm_direction`。
from oss.core.state import norm_direction


def check(rundir: Path) -> list[str]:
    """当前 plan.json / ideas.json 与冻结锚点比对。没冻结（还没跑过分析）不拦。"""
    rundir = Path(rundir)
    anchor = state_mod.load(rundir).anchor
    # 损坏标记排在「冻没冻」**前面**：损坏标记只有授权重建才清，重跑 oss run 重新冻上不算——
    # 那正是「静默冻上当前题目」。先看 frozen_at 的话，一次 oss run 就能把警报按掉。
    if (rundir / state_mod.CORRUPT).exists():
        return [Finding("锚点检测：run_state.json 曾损坏（见 run_state.corrupt.json），锚点已丢失——"
                "确认原始问题后 oss anchor switch --to <idea_id> --why 状态损坏重建 "
                "--authorized-by user", "gate.anchor")]
    # 半冻结的锚点是**工具箱写不出的形状**：_anchor() 六格一起写、frozen_at 必是时间戳，
    # 没冻过的运行 anchor 就是空字典。有方向/题号却没有冻结时间，只能是有人手写了
    # run_state.json——说明书里模型只写 ideas.json / plan.json / paper.md，状态文件归工具箱。
    # 这一条排在「冻没冻」前面，跟损坏标记同理：否则手写一个 frozen_at 为空的锚点，
    # 就正好从下面那句 return [] 溜过去，而它绕开的恰恰是换题要用户授权那道门。
    # 实例：验收跑 r20260907_1938 的 run_state.json 就是这个形状，
    # 而 ideas.json 的 direction 被写成了一串列名，两处的「研究方向」对不上。
    if not anchor.get("frozen_at") and (anchor.get("direction") or anchor.get("idea_id")):
        return [Finding("锚点检测：run_state.json 的锚点是半冻结的（有方向或题号，却没有冻结时间）——"
                "工具箱写不出这个形状，只能是被手工改过。**状态文件由工具箱维护**，"
                "模型只写 ideas.json / plan.json / paper.md。"
                "确认原始问题后：oss anchor switch --to <idea_id> --why 状态被手改重建 "
                "--authorized-by user", "gate.anchor")]
    if not anchor.get("frozen_at"):
        return []
    cur = state_mod.current_anchor(rundir)
    problems: list[str] = []
    # current_anchor 读不到文件时给空串，而空串在下面是「不比」——所以先查文件在不在
    if not (rundir / "plan.json").exists():
        problems.append(Finding("锚点检测：锚点已冻结却找不到 plan.json——删掉计划不等于没换题", "gate.anchor"))
    elif cur["idea_id"] and cur["idea_id"] != anchor.get("idea_id"):
        problems.append(
            Finding(f"锚点检测：plan.json 的 idea_id「{cur['idea_id']}」≠ 冻结的「{anchor.get('idea_id')}」——"
            f"换题只能 oss anchor switch --to <idea_id> --why ... --authorized-by user", "gate.anchor"))
    if anchor.get("direction") and not (rundir / "ideas.json").exists():
        problems.append(Finding("锚点检测：锚点已冻结却找不到 ideas.json——删掉方向不等于没换题", "gate.anchor"))
    elif cur["direction"] and anchor.get("direction") and norm_direction(cur["direction"]) != norm_direction(anchor["direction"]):
        problems.append(
            Finding(f"锚点检测：ideas.json 的方向已改为「{cur['direction'][:40]}」，冻结的是「{anchor['direction'][:40]}」——"
            f"同上，模型不能自己改方向", "gate.anchor"))
    return problems
