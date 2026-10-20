"""`oss merge` / `oss reshape`：把表摆成能分析的形状。**只做机械变换，不做判断。**

两条都是写的命令，所以走 `open_run`：它们的产物落在运行目录里，
而运行目录常常正是被这一步第一次建出来的。
"""

from __future__ import annotations

from pathlib import Path

from oss.api.errors import BadInput, Refused
from oss.api.result import result
from oss.api.rundir import open_run
from oss.core import merge as merge_mod
from oss.core import reshape as reshape_mod
from oss.core import stack as stack_mod
from oss.core.contracts import TraceEvent
from oss.core.loaders import MissingDependency, UnsupportedFormat, load_any


def reshape(data: str, *, run: str, out: str = "long.csv", unit: str = "",
            wave_name: str = "wave") -> dict:
    """宽表面板转长表：每人每轮一行。

    画像认得出宽表面板，却只会提示「请重塑成长表」然后没有下文——
    真跑一次三轮面板时，模型只好**自己手写重塑脚本**。那是模型在干算的活，
    而**没有任何检查会去核它写对没有**：轮次拼错了回归照跑，结果照出。

    只做摞行。量表反向、取对数、转哑变量一概不做——那些是研究者的决定。
    """
    rundir = open_run(run)
    try:
        table = load_any(data)
    except (FileNotFoundError, UnsupportedFormat, MissingDependency) as exc:
        raise BadInput(str(exc), code=1) from exc

    frame = table.frame
    try:
        long, notes = reshape_mod.wide_to_long(frame, unit=unit or None, wave_name=wave_name)
    except reshape_mod.NotWidePanel as exc:
        raise Refused(str(exc), code=1) from exc

    target = rundir.file(out)
    long.to_csv(target, index=False)
    note = f"{len(frame)} 行宽表 → {len(long)} 行长表"
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="reshape", command=f"oss reshape {data}",
        outcome="ok", artifacts=[target.name], note=note,
        args={"data": str(data), "unit": unit, "wave_name": wave_name}))
    return result(note, {
        "run": str(rundir.path), "out": out, "path": str(target),
        "rows_in": len(frame), "rows_out": len(long), "notes": list(notes),
        "next_command": f"oss profile {target} --run {run}",
    })


def stack(spec: str, *, run: str, out: str = "stacked.csv") -> dict:
    """多轮调查摞成一张表：每轮一段，列名按声明对齐。

    `merge` 是横着拼，这是**竖着摞**。11 轮 CGSS 每轮列名都不一样，
    而这一段之前只能落在模型手写的 `prep.py` 里——**这条链上唯一没人核的计算**，
    偏偏最危险的东西全在这儿：编码翻转（`1=是` 变成 `4=是`）、
    某一轮整个模块没问、缺失码逐轮不同。**三样错了都不报错。**

    只做机械的那一半：按声明摞行，把摞不齐的地方全部喊出来。
    哪一列对应哪个概念、编码怎么对齐，写进映射文件——**可审计的声明**，
    不是埋在脚本里的一行代码。
    """
    rundir = open_run(run)
    try:
        sources, wave_col = stack_mod.load_spec(spec)
        frame, rep = stack_mod.stack(sources, wave_col)
    except stack_mod.StackRejected as exc:
        raise Refused("摞不齐，逐条见 problems", code=1, problems=exc.problems) from exc

    target = rundir.file(out)
    frame.to_csv(target, index=False)
    note = f"{len(rep.rows_in)} 轮 {sum(rep.rows_in.values())} 行 → {rep.rows_out} 行"
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="stack", command=f"oss stack {spec}",
        outcome="ok", artifacts=[target.name], note=note,
        args={"spec": str(spec), "out": out}))
    return result(note, {
        "run": str(rundir.path), "out": out, "path": str(target),
        "rows_in": rep.rows_in, "rows_out": rep.rows_out,
        "per_variable": rep.per_variable, "notes": rep.notes,
        "warnings": rep.warnings,
        "missing_codes": stack_mod.missing_code_hint(sources),
        "next_command": f"oss profile {target} --run {run}",
    })


def merge(left: str, right: str, *, run: str, on: str = "", left_on: str = "",
          right_on: str = "", how: str = "inner", allow_loss: float = -1.0,
          map_file: str = "", out: str = "merged.csv") -> dict:
    """拼两份数据，并**把丢掉的东西摊开**。

    真数据上量过：两份「国家-年」数据按国家名直接拼，丢 25.3%、30 个国家整个消失，
    **没有任何报错**——一边写 `Korea (the Republic of)`，一边写 `South Korea`。
    而丢掉的那批里民主国家比例低 10 个百分点，**那份数据研究的正是民主化**。

    对不上的键只列出来、只给待确认的候选，**绝不自动套用**。
    """
    rundir = open_run(run)
    keys_l = [c.strip() for c in (left_on or on).split(",") if c.strip()]
    keys_r = [c.strip() for c in (right_on or on).split(",") if c.strip()]
    if not keys_l or not keys_r:
        raise BadInput("要给键：--on country,year（或 --left-on/--right-on）", code=1)

    try:
        lf, rf = load_any(left).frame, load_any(right).frame
    except (FileNotFoundError, UnsupportedFormat, MissingDependency) as exc:
        raise BadInput(str(exc), code=1) from exc

    cw: dict[str, str] | None = None
    if map_file:
        mp = load_any(map_file).frame
        if mp.shape[1] < 2:
            raise BadInput("对照表要有两列：左表写法,右表写法", code=1)
        cw = dict(zip(mp.iloc[:, 0].astype(str), mp.iloc[:, 1].astype(str)))

    try:
        out_df, notes = merge_mod.merge_tables(
            lf, rf, left_on=keys_l, right_on=keys_r, how=how,
            allow_loss=None if allow_loss < 0 else allow_loss, crosswalk=cw,
            left_name=Path(left).stem, right_name=Path(right).stem)
    except merge_mod.MergeRefused as exc:
        raise Refused(str(exc), code=1) from exc

    target = rundir.file(out)
    out_df.to_csv(target, index=False)
    note = (f"{len(lf)} × {len(rf)} → {len(out_df)} 行"
            + (f"；对照表 {len(cw)} 条" if cw else "")
            + (f"；显式接受损失 {allow_loss:.0%}" if allow_loss >= 0 else ""))
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="merge", command=f"oss merge {left} {right}",
        outcome="ok", artifacts=[target.name], note=note,
        # **对照表和接受的损失率必须进留痕。**它们是这一步全部的判断——
        # 「用了哪张对照表」「接受了多大损失」，正是事后要审的两件事。
        # 真跑一次时它接受了 48.6% 的损失、用了 31 条对照，
        # 而留痕里只有 left/right/on/how，**两样判断一样都没记下**。
        args={"left": left, "right": right, "on": keys_l, "how": how,
              "map": map_file or None, "map_entries": len(cw) if cw else 0,
              "allow_loss": None if allow_loss < 0 else allow_loss,
              "rows_in": len(lf), "rows_out": len(out_df),
              "loss_rate": round(1 - len(out_df) / max(len(lf), 1), 4)}))
    return result(note, {
        "run": str(rundir.path), "out": out, "path": str(target),
        "rows_in": len(lf), "rows_right": len(rf), "rows_out": len(out_df),
        "loss_rate": round(1 - len(out_df) / max(len(lf), 1), 4),
        "map_entries": len(cw) if cw else 0, "notes": list(notes),
        "next_command": f"oss profile {target} --run {run}",
    })
