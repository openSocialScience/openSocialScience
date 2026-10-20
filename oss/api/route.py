"""`oss route`：诊断 → 处置。**一次一处置。**

退出码：0 没有待处置的 must 级处方；6 有。
"""

from __future__ import annotations

from oss.api.errors import BadInput
from oss.api.result import result
from oss.api.rundir import require_run
from oss.core import route as route_mod
from oss.core.contracts import ARTIFACTS, TraceEvent
from oss.host.base import RunDir


def _trace(rundir: RunDir, items: list[dict], pend: list[str], *, action: str) -> None:
    """**复查也要留一条痕，包括「一条处方都没有」那次。**

    处置（apply / dismiss / disclose）本来就由 `core.route` 记；
    **只有「光看一眼」这条路一条痕都不留**，而那正是空复查走的路。

    而 `oss eval` 的 `route-after-run`（must 级）恰恰是去留痕里找 `oss route`。
    于是这条规则测的不是有没有复查，**是模型有没有手工补一行记录**——
    以前几次跑能过，是因为模型碰巧补了。

    11 轮 CGSS 那次真跑坐实了：执行者跑了三次 route、每次都是空的，
    还专门用 `oss trace save` 写了段说明，规则照样判它不合格。
    **一条永远靠手工补才能过的 must 规则，测的是抄写员不是研究者。**

    空复查尤其要留痕：「复查过、没有处方」与「压根没复查」，
    在 `route.json` 上长得一模一样（都是 `items: []`），只有留痕分得开。
    """
    if action:
        return                          # 处置那条路 core.route 已经记过，别记两遍
    if not items:
        note = "复查：没有处方——诊断里没有触发任何处置（或还没跑 oss run）"
    else:
        done = sum(1 for it in items if it["status"] != "pending")
        note = f"复查：{len(items)} 条处方，已处置 {done}，must 级待处置 {len(pend)}"
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="route", command="oss route", outcome="ok",
        artifacts=[ARTIFACTS["route"]], note=note,
        args={"n_items": len(items), "pending_must": list(pend)}))


def _view(rundir: RunDir, *, action: str = "", item_id: str = "", why: str = "",
          applied_note: str = "") -> dict:
    """处置之后重新 build 一次，看的永远是盘上最新的那一份。

    `action` 那几栏是给门面用的：散文那一侧要打「✓ 驳回 x：理由」，
    而**哪一步真的做了**只有这里知道——不许让门面凭参数猜。
    """
    rt = route_mod.build(rundir.path)
    items = [{"id": it.id, "priority": it.priority, "status": it.status,
              "trigger": it.trigger, "evidence": it.evidence, "advice": it.advice,
              "why": it.why} for it in rt.items]
    pend = [it.id for it in rt.pending_must()]
    data = {"run": str(rundir.path), "items": items, "pending_must": pend,
            "action": action, "action_id": item_id, "action_why": why,
            "applied_note": applied_note,
            "route_path": str(rundir.file(ARTIFACTS["route"]))}
    _trace(rundir, items, pend, action=action)
    if not items:
        return result("没有处方：诊断里没有触发任何处置（或还没跑 oss run）", data)
    code = 6 if pend else 0
    summary = (f"{len(pend)} 条 must 级处方待处置——paper check / export 会拦" if pend
               else "没有待处置的 must 级处方")
    # route.json 已经写出来了：这不是「失败」，是成功地报告了一个坏消息，所以不抛异常
    return result(summary, data, problems=pend or None, exit_code=code)


def status(run: str) -> dict:
    """只看，不动。"""
    return _view(require_run(run))


def apply(run: str, item_id: str) -> dict:
    """把这条处方的补丁打进 plan.json。"""
    rundir = require_run(run)
    try:
        note = route_mod.apply(rundir.path, route_mod.build(rundir.path), item_id)
    except (KeyError, ValueError) as exc:
        raise BadInput(str(exc), code=1) from exc
    return _view(rundir, action="apply", item_id=item_id, applied_note=note)


def dismiss(run: str, item_id: str, *, why: str) -> dict:
    """驳回。**必须给理由**——理由进 route.json，事后要被 `oss eval` 核。"""
    rundir = require_run(run)
    try:
        route_mod.dismiss(rundir.path, route_mod.build(rundir.path), item_id, why)
    except (KeyError, ValueError) as exc:
        raise BadInput(str(exc), code=1) from exc
    return _view(rundir, action="dismiss", item_id=item_id, why=why)


def disclose(run: str, item_id: str, *, why: str) -> dict:
    """标记为已写进局限。"""
    rundir = require_run(run)
    try:
        route_mod.disclose(rundir.path, route_mod.build(rundir.path), item_id, why)
    except (KeyError, ValueError) as exc:
        raise BadInput(str(exc), code=1) from exc
    return _view(rundir, action="disclose", item_id=item_id, why=why)


#: `dispatch` 的三个参数跟上面三个函数同名（命令行的旗标名就叫这个），
#: 函数体里那些名字会被参数遮住，所以先在这里各留一个别名
_APPLY, _DISMISS, _DISCLOSE = apply, dismiss, disclose


def dispatch(run: str, *, apply: str = "", dismiss: str = "",
             disclose: str = "", why: str = "") -> dict:
    """三个处置开关 → 一条 api。**给了不止一个就拒绝，不替调用方挑一个做。**

    分派放在 api 层而不是各自的门面里：CLI 与 MCP 的 `oss_route` 收的是同三个开关，
    判据只该有一份。从前是 `if apply / elif dismiss / elif disclose`——同时给两个时
    **静默只做第一个还退 0**，调用方以为两件事都办了。「一次一处置」是这条命令的
    规矩，那就让它出声（退出码 2，跟别的参数错一样）。
    """
    given = [(flag, item) for flag, item in
             (("--apply", apply), ("--dismiss", dismiss), ("--disclose", disclose)) if item]
    if len(given) > 1:
        raise BadInput("一次只做一种处置：--apply / --dismiss / --disclose 择一",
                       problems=[f"{flag} {item}" for flag, item in given])
    if not given:
        return status(run)
    flag, item_id = given[0]
    if flag == "--apply":
        return _APPLY(run, item_id)
    return (_DISMISS if flag == "--dismiss" else _DISCLOSE)(run, item_id, why=why)
