"""运行目录、落盘、留痕：api 与 cli 共用同一份实现。

`_emit` 原先长在 `oss/cli/main.py` 里，做三件事：落盘、留痕、回显。
搬到这里之后只做前两件——**回显归门面**：CLI 打散文，MCP 打 JSON，
落的盘是同一份。第三件事留在调用方，是为了让「盘上写了什么」
不取决于是谁在调。
"""

from __future__ import annotations

import json
from pathlib import Path

from oss.api.errors import Missing, Refused
from oss.core.contracts import ARTIFACTS, AnalysisResults, IdeaSet, TraceEvent
from oss.host.base import RunDir


def open_run(run: str | Path) -> RunDir:
    """`--run runs/xxx` → RunDir。**目录不存在就建**——写的命令要的就是这个。

    只读的命令别用它，用 `require_run`。
    """
    p = Path(run)
    return RunDir(root=p.parent, run_id=p.name)


def require_run(run: str | Path) -> RunDir:
    """同上，但**目录不存在就拒绝，绝不顺手建一个**。

    打错路径是最常见的一次调用错误，而 `RunDir` 会把目录建出来——于是
    `oss loop status --run runs/currnet` 在一个刚由这条命令自己造出来的空目录上
    报「还没有任何审稿」，看起来像一次干净的新运行。核账那一侧（`eval --behavior`）
    早就先查目录再建（阶段 5），读的命令在这里跟上：**凭空造出来的通过是最坏的一种。**
    """
    p = Path(run)
    if not p.is_dir():
        raise Missing("run", f"运行目录不存在：{run}", code=1)
    return RunDir(root=p.parent, run_id=p.name)


def emit(rundir: RunDir, step: str, command: str, artifact: str, payload, *,
         note: str = "", args: dict | None = None) -> Path:
    """落盘 + 留痕，返回落盘路径。**不回显。**

    `note` 必须由调用方给：它原先写死成「FAKE：阶段 0 假实现」，
    于是工具侧全部换真之后留痕仍对每一次运行说「这是假的」——
    留痕分不出真跑和 smoke，价值归零。
    """
    path = rundir.file(ARTIFACTS[artifact])
    payload.save(path)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step=step, command=command, outcome="ok",
        artifacts=[ARTIFACTS[artifact]], args=args or {}, note=note))
    return path


def first_idea_id(rundir: RunDir) -> str:
    """缺省的 idea：ideas.json 的第一个。"""
    path = rundir.file(ARTIFACTS["ideas"])
    if not path.exists():
        raise Missing("ideas", f"缺 {ARTIFACTS['ideas']}——idea 是 skill 写的，先让它产出")
    ideas = IdeaSet.load(path).ideas
    if not ideas:
        raise Missing("ideas", "ideas.json 里一个 idea 都没有")
    return ideas[0].idea_id


def official_results(rundir: RunDir) -> AnalysisResults:
    """读 results.json，**并挡住探索结果冒充的那一份**。

    图跟正文一样是把数字发出去的出口：`oss figure coef` 画的是 results.json，
    拷一份探索结果过去就能画出一张「正式」的图。稿子那一侧由 `manuscript.check`
    拦，图这一侧只能在这里拦。
    """
    res = AnalysisResults.load(rundir.file(ARTIFACTS["results"]))
    if res.plan_id.startswith("explore:"):
        raise Refused("results.json 是探索结果冒充的（plan_id 以 explore: 开头）——探索不进论文",
                      code=1)
    return res


def read_json_file(p: Path) -> dict | None:
    """读不出来就是 None。**读一个坏文件不该让整条命令挂掉。**"""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
