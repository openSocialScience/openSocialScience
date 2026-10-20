"""`oss results import`：结果已经在别处跑完了，直接接进来写论文。

最常见的真实用户就是这种：实验做完了、Stata 也跑完了，就差写；原始数据带不出来
（单位的、签了协议的、在保密机器上、或者他就是不想再跑一遍）。**他不缺想法、
不缺数据、不缺结果，缺的正是这套东西最强的那一段。**而现在系统只会说
「把原始数据给我，我要自己跑一遍」，他给不了就卡死。

三条纪律，别的都不问：

1. **原文必须存。**用户粘进来的那张回归表原样落盘。中间隔着一道「模型把表转成
   结构化数据」的手工活——**小数点看错一位，整篇论文的数字全错，而且不报错**。
   存了原文，任何一个数都能翻回去对。
2. **软件名照用户原话。**他说「Stata」就是 Stata，**不许补成「Stata 18」**；
   没说就空着，论文里一个字都不写。补全就是编造，跟拿缺省效应量冒充文献值同类。
3. **不问别的。**为什么数据带不出来不是我们该管的事，跟论文成不成立也没关系。

**外部标记只落在文件里，不进论文。**论文里出现关于本系统的任何字都是过程泄漏，
`no-process-language` 那道门会拦。留标记只为两件事：那几道查「有没有真跑过」的
规则能明说「不适用」而不是判绿；将来对比运行记录时分得清哪份是真跑过的。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from oss.api.errors import BadInput
from oss.api.result import result
from oss.api.rundir import require_run
from oss.core.contracts import ARTIFACTS, AnalysisResults, ExternalImport, TraceEvent

#: 原文存成这个名字。固定下来，规则与审计都按它找。
RAW_NAME = "results_raw.txt"


def import_results(run: str, *, payload: str, raw: str, software: str = "") -> dict:
    """把外部跑出来的结果接进运行目录。

    `payload` 是照 `oss schema results` 填好的结构化结果（通常由模型从用户粘贴的
    回归表转出来）；`raw` 是**用户粘的那张表原文**，原样存档。
    """
    rundir = require_run(run)

    if not str(raw).strip():
        raise BadInput(
            "没给原文（--raw）。**用户粘的那张回归表必须原样存一份**——"
            "中间隔着一道把表转成数据的手工活，小数点看错一位整篇论文就全错且不报错",
            code=2)
    raw_path = Path(raw)
    if not raw_path.exists():
        raise BadInput(f"原文文件不在：{raw_path}", code=2)
    raw_text = raw_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise BadInput("原文是空的——没有可以回溯的东西，等于没存", code=2)

    p = Path(payload)
    if not p.exists():
        raise BadInput(f"结果文件不在：{p}", code=2)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BadInput(f"结果文件不是 JSON：{exc}", code=2) from exc
    if not isinstance(data, dict) or "main" not in (data.get("estimates") or {}):
        raise BadInput(
            "结果里没有名为 `main` 的估计。**论文里的占位符全锚在它上面**，"
            "缺了后面一路串——照 `oss schema results --example` 填",
            code=2)
    data.pop("imported", None)          # 标记由这里盖，不许调用方自己写

    try:
        res = AnalysisResults.model_validate(data)
    except Exception as exc:  # noqa: BLE001 —— pydantic 的报错原样转给用户
        raise BadInput(f"结果格式不对：{str(exc)[:400]}", code=2) from exc

    stored = rundir.file(RAW_NAME)
    stored.write_text(raw_text, encoding="utf-8")
    res.imported = ExternalImport(
        raw_file=RAW_NAME,
        software=software.strip(),       # **原话，不补全**
        imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    path = rundir.file(ARTIFACTS["results"])
    res.save(path)

    # **留痕的 step 不是 results。**那几条「功效要在分析之前算」「跑完要开方」
    # 查的正是 results 这一步——写成 results 等于伪装成跑过，把它们骗过去。
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="results_import", command="oss results import",
        outcome="ok", artifacts=[path.name, RAW_NAME],
        note=(f"外部结果导入：n={res.n}，{len(res.estimates)} 个估计"
              + (f"，用户说用 {res.imported.software} 处理" if res.imported.software else "")),
        args={"software": res.imported.software, "raw_file": RAW_NAME}))

    return result(
        f"{ARTIFACTS['results']}  ←  外部导入（n={res.n}）",
        {"path": str(path), "raw_path": str(stored), "external": True,
         "software": res.imported.software, "n": res.n,
         "estimates": sorted(res.estimates)},
        problems=([] if res.imported.software else
                  ["没给 --software：用户没说用什么处理的话就这样，论文里也不写；"
                   "他说了就照原话填，**不要替他补版本号**"]))
