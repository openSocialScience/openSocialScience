"""`oss anchor switch`：换题。**模型不能授权自己换题。**"""

from __future__ import annotations

from oss.api.errors import Refused
from oss.api.result import result
from oss.api.rundir import require_run
from oss.core import loop as loop_mod
from oss.core import state as state_mod
from oss.core.contracts import TraceEvent
from oss.gates import anchor as anchor_gate
from oss.host.base import RunDir


def _trace_refused(rundir: RunDir, *, to: str, why: str, authorized_by: str,
                   reason: str) -> None:
    """被拒也留痕：每一条拒绝路径在抛之前都往 trace.jsonl 写一行。

    原先拒绝就是「打一行 stderr 然后 Exit(2)」，盘上什么都不留。于是
    **模型自己授权自己换题、被这道门拦下来**这件事事后完全看不见——
    而行为指标 topic_switch 数的正是留痕里的原始条数（授权的、被拒的都算一次，
    见 core/behavior.py §换题按原始条数算）。一行都没有，核账的人读到的是
    「这次跑压根没想过换题」：最该报警的那次尝试被判成了满分。

    写的是 outcome="refused"，跟轮次门的拒绝留痕同一个口径；
    `args.reason` 是短理由键，用来分辨被拦在哪一道上。
    **留痕是证据，不是闸门**：目录只读之类写不进去也照样往下抛，
    不能让记账的动作反过来改变命令的结果。
    """
    try:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="topic_switch",
            command=f"oss anchor switch --to {to}",
            args={"to": to, "why": why, "authorized_by": authorized_by, "reason": reason},
            outcome="refused", note=why))
    except OSError:
        pass


def switch(run: str, *, to: str, why: str, authorized_by: str = "") -> dict:
    """换到另一个 idea。没有 `authorized_by == "user"` 就拒绝，退出码 2。

    走 `require_run`：换题是在**已有的**运行目录上换，打错路径不该被建成一个
    新目录再往里写锚点——那样换出来的题没有任何来历。
    """
    rundir = require_run(run)
    if authorized_by != "user":
        _trace_refused(rundir, to=to, why=why, authorized_by=authorized_by,
                       reason="unauthorized")
        raise Refused("换题必须有用户授权：--authorized-by user。模型自己不能授权自己", code=2)
    old = state_mod.load(rundir.path).anchor
    # 状态文件坏过 = 锚点丢了，此时「没冻结」不是「还没开始」。锚点门就是让人来这里重建的，
    # 再拦一次就成了死路：只剩「手删证据」或「重跑 oss run 静默冻上当前题目」两条更坏的路。
    corrupt = (rundir.path / state_mod.CORRUPT).exists()
    if not old.get("frozen_at") and not corrupt:
        # 没冻过就 switch = 用换题的口子凭空冻一个锚点。冻结只发生在跑出第一份结果时，
        # 否则「先 switch 到自己想做的题、再去跑」就是一条绕过冻结的路
        _trace_refused(rundir, to=to, why=why, authorized_by=authorized_by,
                       reason="not_frozen")
        raise Refused("还没冻结锚点（先跑 oss run），没有题可换", code=2)
    cur = state_mod.current_anchor(rundir.path)
    # 留痕要记**从哪换来**：只记 to，事后就还原不出这次跑总共漂了多远
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="topic_switch", command=f"oss anchor switch --to {to}",
        args={"from": old.get("idea_id"), "to": to, "from_direction": old.get("direction"),
              "to_direction": cur["direction"], "authorized_by": authorized_by, "why": why,
              "rebuild_after_corrupt": corrupt}, note=why))
    # plan_sha 留空：新题的 plan.json 还没写，拿旧计划的指纹当新锚点的指纹是假的。
    # source 按**换题这一刻盘上的 ideas.json** 重算：用户把自带想法换成另一条自带想法，
    # 它仍是 user；换成模型出的候选，它就该变回 model。抹掉它等于让两条指标从此不判。
    # research_type 只在盘上的计划已经指向新题时才取（先写计划、再换题）。先换题、后写计划时，
    # 盘上还是旧题的计划，它的类型不算数——留空，等新题第一次跑出结果时 refill_plan_sha 补上。
    # 不留空的话，旧题的类型会冒充新题的：研究类型门按它拦新计划，识别审计按它误判
    state_mod.reset_anchor(rundir.path, direction=cur["direction"], idea_id=to, plan_sha="",
                           source=cur["source"],
                           research_type=cur["research_type"] if cur["idea_id"] == to else "")

    evidence = ""
    if corrupt:
        # 证据不删，改名归档：run_state.corrupt.json 这个名字本身是**警报**，
        # 锚点门看见它就响。授权重建是唯一能把它摘下来的动作——摘的时候带上时间戳，
        # 坏掉的那一版还在盘上，事后照样能查
        ev = rundir.path / state_mod.CORRUPT
        stamped = ev.with_name(f"run_state.corrupt.{state_mod.now_stamp().replace(':', '-')}.json")
        ev.replace(stamped)
        evidence = stamped.name
    changed = None
    if not corrupt and anchor_gate.norm_direction(cur["direction"]) != anchor_gate.norm_direction(
            old.get("direction", "")):
        # 换 idea 常常连方向一起换掉，而方向才是「还是不是同一个问题」——说出来。
        # 重建那条路上 old 是空锚点，「从哪变到哪」无从谈起，索性不说
        changed = {"from": old.get("direction") or "—", "to": cur["direction"]}
    return result(f"锚点 {old.get('idea_id') or '—'} → {to}（用户授权）：{why}", {
        "run": str(rundir.path), "from": old.get("idea_id") or "", "to": to, "why": why,
        "authorized_by": authorized_by,
        "rebuild_after_corrupt": corrupt, "corrupt_evidence": evidence,
        "direction_changed": changed,
        "plan_sha": loop_mod.plan_sha(rundir.path / "plan.json"),
    })
