"""`oss eval`：说明书的测试。对着运行目录核执行者有没有照说明书做。

工具有五百多个测试，说明书一个都没有——听不听话只能事后翻对话记录。
这里核的是留在目录里的产物：命令先后、估计量、处方状态、论文里有没有表、几位审稿人。

两把尺子，**分开量**：

    run()        产物合不合说明书  → eval_report.json
    behavior()   这次是怎么跑的    → eval_behavior.json

往同一份报告里塞另一把尺子的条目，会让「must 级全过」的意思悄悄改掉。
"""

from __future__ import annotations

import json
import os

from oss.api.result import result
from oss.api.rundir import require_run
from oss.core import behavior as behavior_mod
from oss.core import skillcheck as skillcheck_mod


def list_rules(*, behavior: bool = False) -> dict:
    """只列规则，不评。**不碰运行目录**，所以没有 run 参数。"""
    if behavior:
        rules = [{"id": rule_id, "skill": "behavior", "priority": priority, "what": what}
                 for _key, rule_id, priority, what in behavior_mod.RULES]
    else:
        rules = [{"id": r.id, "skill": r.skill, "priority": r.priority, "what": r.what}
                 for r in skillcheck_mod.RULES]
    return result(f"{len(rules)} 条规则", {"rules": rules})


def run(run: str) -> dict:  # noqa: A002 - 参数名就叫 run，跟命令行一致
    """评一次产物。退出码：0 must 级全过；7 有 must 级没过。

    只读，所以走 `require_run`：目录不存在就抛 `Missing`，**不建**——
    对着一个刚由核账自己造出来的空目录报分，报出来的是核账自己的影子。
    """
    rundir = require_run(run)
    rep = skillcheck_mod.evaluate(rundir.path)
    out = rundir.file("eval_report.json")
    payload = rep.to_dict()
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = payload["must_failed"]
    code = 7 if failed else 0
    summary = (f"{len(failed)} 条 must 级规则没过：{'、'.join(failed)}" if failed
               else "must 级规则全过")
    # 报告已经写出来了：这不是「失败」，是**成功地报告了一个坏消息**，所以不抛异常
    return result(summary, {
        "run": str(rundir.path),
        "items": payload["items"],
        "must_failed": failed,
        "summary_counts": payload["summary"],
        "report_path": str(out),
    }, problems=failed or None, exit_code=code)


def behavior(run: str) -> dict:  # noqa: A002
    """换一把尺子：不查产物合不合说明书，查**这次是怎么跑的**。

    指标全部从 trace.jsonl + run_state.json + loop_state.json 算（提案 §九）：
    审稿轮数、换题、问用户、探索、被拒、oss run 次数、总耗时。

    退出码：0 must 级全过；7 有 must 级没过；另有两条 1——

    * 运行目录不存在（抛 `Missing`：不建目录、不写报告，那样的「全过」是核账自己造出来的）；
    * 全部指标都是 n/a（一条留痕都没有 = 没交卷，不是合格）。这一条**不抛异常**：
      报告已经落盘，它是证据；退出码 1 由 `data.exit_code` 带出去，
      散文那一侧照旧先打表再打这一行。**这里不写死条数**：指标表会长
      （自带想法那两条是第八、九条），写死一个数迟早跟表对不上。
    """
    rundir = require_run(run)
    rep, ms = behavior_mod.evaluate(rundir.path)
    # 判词跟着落盘：读 JSON 的人不必自己再比一遍数，也不会比出跟表上不一样的结论
    payload = {**rep.to_dict(), "metrics": {
        k: {**m.__dict__, "status": m.status, "shown": m.shown, "bar": m.bar}
        for k, m in ms.items()}}
    out = rundir.file("eval_behavior.json")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)      # 先写临时文件再改名：核账被打断也不留半截报告
    counts = payload["summary"]
    failed = payload["must_failed"]
    # 全部 n/a：跟「刚建好、还没动手」分开——那种目录有 trace.jsonl，
    # 计数器是真的 0，四条 ✓ 其余 ·，照旧退 0
    undecidable = not failed and not counts["pass"] + counts["fail"]
    code = 7 if failed else 1 if undecidable else 0
    summary = (f"{len(failed)} 条 must 级行为指标没过：{'、'.join(failed)}" if failed
               else "判不了：全部指标都是 n/a——这个目录里没有可核的留痕，不是「全过」" if undecidable
               else "must 级行为指标全过")
    return result(summary, {
        "run": str(rundir.path),
        "table": behavior_mod.table(ms),
        "metrics": payload["metrics"],
        "items": payload["items"],
        "must_failed": failed,
        "summary_counts": counts,
        "undecidable": undecidable,
        "report_path": str(out),
    }, problems=failed or None, exit_code=code)
