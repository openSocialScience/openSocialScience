"""`oss paper check / render / export`：校验、渲染、交付。

**markdown 不是成品。**社科期刊投稿收的是 Word；审稿人、导师、合作者打开的也是 Word。
链条的终点必须是一份能直接发给人看的文件。

导出的七道门与它们的退出码（**说明书按这些数分支，一个都不许并**）：
    7 锚点门与研究类型门（题都换了、类型改得不合规矩，审几轮都不相干——所以排在最前）
    8 正文的因果说法一族——非因果论文下了因果结论；或因果论文用了弱工具（有效 F 低于
      `EFFECTIVE_F_LOW`）却没在主结果的区间引 `{{diag:AR 区间}}`（后一条要改的是补引用，
      不是软化措辞——收到这个退出码先看 `problems` 里是哪一条，别顺手去改因果说法）
    4 正文没有主结果表
    5 没经过任何跨模型审稿
    3 最近一轮有 FAIL/ERROR 且轮次未耗尽
    6 must 级处方没处置
    1 风格包硬规矩

七道门之外，2026-09-16 补了**交付前的两道门**，排在最后、仍在查 pandoc 之前：
    9 审稿人看的那一份被改过（`paper_rendered.md` 不是 `oss paper render` 写下的那一份）
    10 交付文本复核（数字回账、因果措辞、过程话、局限的功效与共线）
补的是交付层的洞：正常路径下 `.docx` 是从 `paper.md` 重渲的第二份，最终交付的那份文字上
一道内容门都没跑过。两道门的判据与边界写在 `rendered_fingerprint`、`delivered_problems`。

`export` 里实际有 21 处 `raise`（交付前那两道门各一处），不止七道门。除上面七道门（锚点门与研究类型门都是 7，
这一族三处：题目或方向变了却没走 `oss anchor switch`、类型改得不合规矩、类型合法但
补了没重跑——锚点这一格还空着，见 `research_type.declared_unlocked`；正文的因果说法
一族判四处，都是 8——因果措辞门两处（正文 `paper.md` 一处，没有语料时兜底转换的
`paper_rendered.md` 另一处）；弱工具没引 AR 区间那道门也两处，同样是 `paper.md` 与
`paper_rendered.md` 各一处（`causal_checks.paper_problems` 查占位符，
`paper_problems_rendered` 查渲染稿里那段区间的实际值）；其余 `code=4/5/3/6/1`，
各一处）之外，还有 6 处没有单独编号、但都是真实的拒绝路径：
测量类论文报了信度却拿不出任何一类效度证据的产出（`manuscript.measurement_reliability_problems`，
判的是 `paper.md`——渲染之后占位符已经变成数字，「引没引信度那份产出」就查不出来了；
退出码并进 1，不新开一个数，因为说明书是按七道门那几个数分支的）、
测量类论文登记了跨组测量等价检验、那份产出却不在结果里
（`manuscript.measurement_equivalence_problems`，同样并进 1）、
导出前先要求 `paper_rendered.md` 已存在的前置检查（缺就是 `Missing`，退出码 1）、
`pandoc` 没装（`Unavailable`，退出码 1）、citeproc 渲染阶段 `manuscript.render`
抛出 `ManuscriptRejected`（退出码 1）、以及 `pandoc` 实际执行转换失败
（`Unavailable`，退出码 1）。说明书按退出码分支时，这五处都并进各自同退出码的
那一支，不需要单独的分支号，但读代码的人应该知道它们存在。

`export` 的 `data["log"]` 是**边走边打的那几行**（`[stream, text]` 二元组）：
「⚠ 因果审计判 FAIL」打在轮次门那一步、「⚠ 轮次耗尽」打在它后面、风格包的警告
和「参考文献样式 …」再往后——它们都在**后面某道门可能拒绝之前**就已经在屏幕上了。
攒进一个 `warnings` 一起打在最前会把这几行挪位置，所以走 log（跟 `figure` 同一套办法）：
门面先按序打 log，再打其余，散文一行都不动。

三条命令都走 `require_run`：它们都读一个**已经跑完分析**的目录，打错路径要拒绝，
不许顺手建一个空目录出来——凭空造出来的「缺 paper.md」把「路径错了」说成「这步没做」。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from oss.api import style as style_api
from oss.api.errors import BadInput, Missing, Refused, Unavailable
from oss.api.result import result
from oss.api.rundir import official_results, read_json_file, require_run
from oss.api.sim import SIM_STATE
from oss.core import analysis as analysis_mod
from oss.core import binding as binding_mod
from oss.core import causal_wording
from oss.core import datapacks as packs_mod
from oss.core import loop as loop_mod
from oss.core import manuscript
from oss.core import route as route_mod
from oss.core import state as state_mod
from oss.core import style as style_mod
from oss.core.contracts import (
    ARTIFACTS,
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DataProfile,
    LiteratureCorpus,
    PowerReport,
    TraceEvent,
)
from oss.core.findings import Finding
from oss.gates import anchor as anchor_gate
from oss.gates import research_type as rt_gate
from oss.host.base import RunDir


#: 审稿投影：`oss review` 也用它（`oss/cli/main.py` 里保留同名别名）
def results_digest(raw: dict) -> dict:
    """审稿用的结果投影：主规格全留，稳健性每项只留 main / n / error / 诊断。

    **只挑字段，不改任何数值，不做摘要。**
    """
    out = {k: v for k, v in raw.items() if k != "robustness"}
    rob = {}
    for label, run in (raw.get("robustness") or {}).items():
        if not isinstance(run, dict):
            continue
        est = run.get("estimates") or {}
        rob[label] = {
            "estimates": {"main": est["main"]} if "main" in est else {},
            "n": run.get("n"), "error": run.get("error"),
            "diagnostics": run.get("diagnostics"),
        }
    out["robustness"] = rob
    out["_note"] = "审稿投影：稳健性各项只保留 main/n/error/诊断，逐期系数见 results.json"
    return out


#: 留痕 args 里由模型自由填写的栏——**里面的数字不算出处**
_FREE_TEXT_ARGS = ("why", "to_direction", "from_direction", "note")


def trace_numbers(rundir: RunDir) -> set[str]:
    """留痕里**能当出处**的数字：除模型写的自由文本字段外的 `args`，别的一律不算。

    探索留痕里的数字**不算出处**——探索结果连 results.json 都进不去，
    更不该靠留痕绕回正文。`why` / `note` / 换题理由这些栏是模型自己写的自由文本，
    放行等于给它一条洗白的路：把任意数字写进理由，再当「产出里找得到」抄进论文。
    """
    nums: set[str] = set()
    f = rundir.file("trace.jsonl")
    if not f.exists():
        return nums
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("step") == "explore":
            continue
        args = row.get("args")
        if not isinstance(args, dict):
            continue
        for k, v in args.items():
            if k in _FREE_TEXT_ARGS:
                continue
            nums |= set(re.findall(r"\d+\.\d+", json.dumps(v, ensure_ascii=False)))
    return nums


def known_numbers(rundir: RunDir) -> set[str]:
    """本次运行产出文件里出现过的全部小数——正文照抄它们不算编造。

    **`sim_state.json` 也在其中**（2026-09-19 补）：配料单里的温度 `0.7`、模拟器版本
    `0.3.1` 是入库时记下的事实，不是手打的统计结果。不收它，两道门就互相打架——
    说明书要求方法节把配料单逐项报全（`sim.unreported` 现在还给这条装了牙齿），
    而裸小数禁令把同一个数拦下来：**规则在逼作者要么漏报版本、要么把 0.7 写成「较低温度」**。
    """
    nums: set[str] = set()
    names = ["results.json", "power.json", "profile.json", "binding.json", SIM_STATE]
    names += [p.name for p in rundir.path.glob("power_*.json")]
    for n in names:
        f = rundir.file(n)
        if f.exists():
            nums |= set(re.findall(r"\d+\.\d+", f.read_text(encoding="utf-8", errors="ignore")))
    return nums | trace_numbers(rundir)


def labels_of(rundir: RunDir) -> dict[str, str]:
    """运行目录里的列名 → 表和图里印的名字。

    **不进 results.json**：binding.json 与 plan.json 本来就在这条路上，
    渲染时现装就行——老的运行目录不用重跑分析也能拿到标签。
    组装规则见 `binding.display_labels`（plan.labels 压过 binding 的构念名）。
    """
    from oss.core.binding import display_labels

    bpath = rundir.file(ARTIFACTS["binding"])
    ppath = rundir.file(ARTIFACTS["plan"])
    gpath = rundir.file(ARTIFACTS["profile"])
    try:
        report = BindingReport.load(bpath) if bpath.exists() else None
    except Exception:  # noqa: BLE001 —— 老版本的 binding.json 读不出来不该挡渲染
        report = None
    try:
        plan_labels = AnalysisPlan.load(ppath).labels if ppath.exists() else {}
    except Exception:  # noqa: BLE001 —— 同上
        plan_labels = {}
    try:
        # 数据文件自带的变量标签：最低优先级的兜底，前两个都要模型填，这个不用
        profile_labels = ({v.name: v.label for v in DataProfile.load(gpath).variables}
                          if gpath.exists() else {})
    except Exception:  # noqa: BLE001 —— 同上
        profile_labels = {}
    return display_labels(report, plan_labels, profile_labels)


def rendered_numbers(paper: str, results: AnalysisResults, power=None, corpus=None,
                     figures: set[str] | None = None,
                     labels: dict[str, str] | None = None) -> set[str]:
    """占位符解析出来的那些数——渲染器自己写进正文的，也算「在产出里找得到」。

    **这一份非收不可**：结果文件里存的是全精度（`0.12345678`），渲染写进正文的是三位小数
    （`0.123`），按字面在 `known_numbers` 里找不到。不收它，一篇干净的稿子会被自己的数
    拦下几十条（四块评测底板上实测 43–69 处）。

    `labels` 要跟渲染时用的那一份一致（见 `labels_of`）：表里印的是人话标签，
    解析出来的那一格文字随标签变，传漏了白名单就收不全。
    """
    out: set[str] = set()
    en = manuscript._is_english(paper)
    for m in manuscript.PLACEHOLDER.finditer(paper):
        val, _ = manuscript._resolve_one(m.group(1), m.group(2), results, power, corpus,
                                         figures=figures, en=en, labels=labels)
        if val:
            out |= {d.lstrip("-") for d in manuscript.BARE_DECIMAL.findall(val)}
    return out


def rendered_fingerprint(rundir: RunDir) -> str:
    """最后一次 `oss paper render` 写下 `paper_rendered.md` 时那份文字的 sha256；没记过返回空串。

    交付前那道一致性门靠它分清两件事：**渲染稿被人手改了**（审稿人看的那一份被动过，
    而 `.docx` 是从 `paper.md` 重渲的，两者不是同一份东西），还是**源稿或产出在渲染之后
    变了**（渲染稿只是过期）。两者的处置不一样——前者要撤销改动或把改动写回源稿，
    后者重跑一次 render 就好——所以不能并成一条。
    """
    out = ""
    f = rundir.file("trace.jsonl")
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("command") != "oss paper render":
            continue
        if "paper_rendered.md" not in (row.get("artifacts") or []):
            continue                                  # `--out` 渲到别处的那几次不算
        sha = (row.get("args") or {}).get("sha256")
        if isinstance(sha, str):
            out = sha
    return out


def _cut(s: str, start: int, width: int = 60) -> str:
    return ("…" if start else "") + s[start:start + width] + ("…" if len(s) > start + width else "")


def _first_diffs(fresh: str, disk: str, limit: int = 3) -> list[tuple[int, str, str]]:
    """两份文字的前几处差异：（渲染稿里的行号，重渲那边的原文，渲染稿那边的原文）。

    两边都是长句时**从第一个不同的字起截**：从行首截一段会截出两段一模一样的话，
    读的人看不出差在哪（真跑 r20260907_edufert_zh 上踩过这个坑）。
    """
    a, b = fresh.split("\n"), disk.split("\n")
    out: list[tuple[int, str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        x, y = "\n".join(a[i1:i2]), "\n".join(b[j1:j2])
        k = 0
        while k < len(x) and k < len(y) and x[k] == y[k]:
            k += 1
        start = max(0, k - 12)
        out.append((j1 + 1, _cut(x, start), _cut(y, start)))
        if len(out) >= limit:
            break
    return out


def delivered_problems(delivered: str, *, known: set[str] | None, results, power,
                       wtype: str, downgraded: bool) -> list[Finding]:
    """**将要交付的那份文字**（喂给 pandoc 的那一份）上再跑一遍不依赖占位符的几条检查。

    **不能把 `manuscript.check` 整个搬过来**：渲染之后占位符已经变成数字，
    `paper.bare_decimal` 会把每个数都当成手打的——搬过去等于全篇误报。所以 post-render
    跑的是**另一组**：数字回账（白名单 = `known_numbers` 再加渲染器自己解析出来的那些数，
    见 `rendered_numbers`）、因果措辞、过程话、局限的功效与共线。

    **显著性说反不在这里重跑**：渲染之后 p 值已经是字面数字，要判得把占位符的解析映射
    一路传下来；源稿那一遍（`manuscript.check_claims`）已经覆盖这条，交付稿上再判一次
    不值当。这是定下来的边界，不要「顺手」补上。

    `known` 给 None 表示**这一次判不了数字回账**（源稿渲不出来，说不出哪些数是渲染器写的）：
    跳过这一条，其余三条照跑。
    """
    out: list[Finding] = []
    body = manuscript._strip_code_and_quotes(delivered)
    if known is not None:
        for m in manuscript.BARE_DECIMAL.finditer(body):
            if m.group(1).lstrip("-") in known:
                continue
            line = body[: m.start()].count("\n") + 1
            out.append(Finding(f"交付的那份文字第 {line} 行有数字 {m.group(1)}，本次运行的产出里找不到出处——"
                       f"正文的数只能由占位符渲染出来，交出去的这一份被改过", "export.delivered_check"))
    if wtype:
        out += [Finding(p, "export.delivered_check")
                for p in causal_wording.hard_problems(causal_wording.scan(delivered, wtype),
                                                      wtype, downgraded=downgraded)]
    out += [Finding(p, "export.delivered_check") for p in manuscript.process_language(delivered)]
    out += [Finding(p, "export.delivered_check") for p in manuscript.limits_power_problems(delivered, power)]
    if results is not None:
        out += [Finding(p, "export.delivered_check")
                for p in manuscript.limits_collinearity_problems(delivered, results)]
    return out


def csl_path(name: str) -> Path | None:
    """包里带的 CSL 样式（chicago-author-date / apa / american-sociological-association）。"""
    p = Path(__file__).resolve().parent.parent / "assets" / "csl" / f"{name}.csl"
    return p if p.exists() else None


def split_title(md: str) -> tuple[str, str]:
    """把第一行的 `# 标题` 拆出来做 Word 的标题样式；正文里不再重复一遍。"""
    lines = md.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            return "\n".join(lines[:i] + lines[i + 1:]), line[2:].strip()
        break
    return md, ""


@dataclass(frozen=True)
class _Layout:
    """一档排版。字段留 None 就是「这一档不管这条」，照 pandoc 模板原样。

    字号与缩进都用磅（小四 12、五号 10.5、小五 9；两字符缩进 = 字号 × 2）。
    `first_line` 为负是悬挂缩进，配合 `left` 用——参考文献那一档要的就是它。
    """

    size: float | None = None
    line: float | None = None
    first_line: float | None = None
    left: float | None = None
    right: float | None = None
    align: str | None = None
    cjk: str | None = None
    italic: bool | None = None
    color: str | None = None
    before: float | None = None
    after: float | None = None


#: 模板档位的版本。改了下面两张表或三线表就 +1——缓存里存着旧版本号的模板会被重建。
#: 不加这个，改完排版的人本地那份旧缓存会一直用下去，怎么改都看不出变化。
_LAYOUT_VERSION = "2"

#: 中文缺省模板的档位。依据是国内社科期刊通例：正文小四或五号、摘要关键词楷体、
#: 图表题居中、参考文献悬挂缩进、表用三线表。**各刊字号并不一致**
#: （《中国社会科学》正文小四，《社会建设》正文五号），所以这里只做「规范即可」的缺省，
#: 具体期刊的字号走风格包的 template.docx。
_ZH_LAYOUT: dict[str, _Layout] = {
    "Normal":          _Layout(size=12, line=1.5, first_line=24, cjk="宋体"),
    "Body Text":       _Layout(size=12, line=1.5, first_line=24, cjk="宋体"),
    "First Paragraph": _Layout(size=12, line=1.5, first_line=24, cjk="宋体"),
    "Block Text":      _Layout(size=10.5, line=1.25, first_line=0, left=24, right=24, cjk="楷体"),
    "Title":           _Layout(size=16, line=1.0, first_line=0, align="center", cjk="黑体",
                               color="000000", before=0, after=12),
    "Author":          _Layout(size=12, line=1.0, first_line=0, align="center", cjk="宋体",
                               color="000000"),
    # `##` 落到 Heading 2——中文稿的「一、二、……」是这一级
    "Heading 1":       _Layout(size=14, line=1.0, first_line=0, cjk="黑体", color="000000",
                               before=12, after=6),
    "Heading 2":       _Layout(size=14, line=1.0, first_line=0, cjk="黑体", color="000000",
                               before=12, after=6),
    "Heading 3":       _Layout(size=12, line=1.0, first_line=0, cjk="黑体", color="000000",
                               before=9, after=5),
    "Heading 4":       _Layout(size=12, line=1.0, first_line=0, cjk="楷体", color="000000",
                               before=6, after=3),
    "Abstract":        _Layout(size=10.5, line=1.25, first_line=21, left=24, right=24, cjk="楷体"),
    "Abstract Title":  _Layout(size=10.5, line=1.25, first_line=0, left=24, cjk="黑体",
                               color="000000"),
    "Keywords":        _Layout(size=10.5, line=1.25, first_line=0, left=24, right=24, cjk="楷体"),
    # 表格内文。pandoc 给每个单元格挂 Compact——不显式清缩进的话它从 Body Text 继承首行缩进两字符
    "Compact":         _Layout(size=10.5, line=1.0, first_line=0, cjk="宋体", before=2, after=2),
    "Caption":         _Layout(size=10.5, line=1.0, first_line=0, align="center", cjk="黑体",
                               italic=False, before=6, after=3),
    "Image Caption":   _Layout(size=10.5, line=1.0, first_line=0, align="center", cjk="黑体",
                               italic=False, before=6, after=3),
    "Table Caption":   _Layout(size=10.5, line=1.0, first_line=0, align="center", cjk="黑体",
                               italic=False, before=6, after=3),
    "Table Note":      _Layout(size=9, line=1.0, first_line=0, cjk="宋体", italic=False,
                               before=3, after=6),
    "Bibliography":    _Layout(size=10.5, line=1.0, first_line=-21, left=21, cjk="宋体", after=3),
    "Footnote Text":   _Layout(size=9, line=1.0, first_line=0, cjk="仿宋"),
}

#: 英文缺省模板的档位。正文 Times New Roman 12 号双倍行距是投稿稿的标准形态，不动；
#: 分档要管的是表、题注、参考文献和标题——这几处原先跟着正文一起被刷成了 12 号双倍行距。
#: 题注保留斜体（英文体例如此），中文那边不保留（汉字没有真正的斜体）。
_EN_LAYOUT: dict[str, _Layout] = {
    "Normal":          _Layout(size=12, line=2.0, first_line=24),
    "Body Text":       _Layout(size=12, line=2.0, first_line=24),
    "First Paragraph": _Layout(size=12, line=2.0, first_line=0),
    "Block Text":      _Layout(size=11, line=1.0, first_line=0, left=24, right=24),
    "Title":           _Layout(size=14, line=2.0, first_line=0, align="center", color="000000",
                               before=0, after=12),
    "Author":          _Layout(size=12, line=2.0, first_line=0, align="center", color="000000"),
    "Heading 1":       _Layout(size=12, line=2.0, first_line=0, color="000000", before=12, after=6),
    "Heading 2":       _Layout(size=12, line=2.0, first_line=0, color="000000", before=12, after=6),
    "Heading 3":       _Layout(size=12, line=2.0, first_line=0, color="000000", before=9, after=5),
    "Heading 4":       _Layout(size=12, line=2.0, first_line=0, color="000000", before=6, after=3),
    "Abstract":        _Layout(size=12, line=2.0, first_line=0, left=24, right=24),
    "Abstract Title":  _Layout(size=12, line=2.0, first_line=0, left=24, color="000000"),
    "Keywords":        _Layout(size=12, line=2.0, first_line=0, left=24, right=24),
    "Compact":         _Layout(size=10, line=1.0, first_line=0, before=2, after=2),
    "Caption":         _Layout(size=10, line=1.0, first_line=0, align="center", before=6, after=3),
    "Image Caption":   _Layout(size=10, line=1.0, first_line=0, align="center", before=6, after=3),
    "Table Caption":   _Layout(size=10, line=1.0, first_line=0, align="center", before=6, after=3),
    "Table Note":      _Layout(size=9, line=1.0, first_line=0, before=3, after=6),
    "Bibliography":    _Layout(size=12, line=2.0, first_line=-24, left=24),
    "Footnote Text":   _Layout(size=10, line=1.0, first_line=0),
}

#: 三线表的线宽，单位是八分之一磅：顶线与底线 1.5 磅，栏目线 0.75 磅。
_RULE_THICK, _RULE_THIN = 12, 6


def _pandoc_reference_docx(pandoc: str, cache: Path) -> bool:
    import subprocess as _sp

    raw = _sp.run([pandoc, "--print-default-data-file", "reference.docx"], capture_output=True).stdout
    if not raw:
        return False
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return True


def _pin_fonts(st, latin: str, cjk: str | None) -> None:
    """把字体钉死在样式上。

    pandoc 模板的各级标题带 `w:asciiTheme="majorHAnsi"` 这类**主题字体**属性，而 Word
    优先用主题——只写 `w:ascii` 改不动它。老模板给标题设了 Times New Roman 却没去掉
    主题属性，Word 里显示的其实一直是主题字体。
    """
    from docx.oxml.ns import qn

    fonts = st.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn("w:" + attr), None)
    for attr in ("ascii", "hAnsi", "cs"):
        fonts.set(qn("w:" + attr), latin)
    if cjk:
        fonts.set(qn("w:eastAsia"), cjk)


def _pin_color(st, value: str) -> None:
    """压成指定颜色。主题色属性同样要拿掉——pandoc 模板的各级标题是 Word 主题的强调蓝
    （`w:themeColor="accent1"`），成稿里肉眼可见。"""
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    st.font.color.rgb = RGBColor.from_string(value)
    color = st.element.get_or_add_rPr().find(qn("w:color"))
    if color is not None:
        for attr in ("themeColor", "themeShade", "themeTint"):
            color.attrib.pop(qn("w:" + attr), None)


def _apply_layout(doc, layout: dict[str, _Layout], latin: str) -> None:
    """按档位表改样式。模板里没有的档就新建——pandoc 的 `custom-style` 按**样式名**挂靠，
    名字对不上就等于没有样式。"""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    aligns = {"center": WD_ALIGN_PARAGRAPH.CENTER, "left": WD_ALIGN_PARAGRAPH.LEFT,
              "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
    named = {st.name: st for st in doc.styles}
    for name, spec in layout.items():
        st = named.get(name)
        if st is None:
            st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            st.base_style = named["Normal"]
        _pin_fonts(st, latin, spec.cjk)
        if spec.size is not None:
            st.font.size = Pt(spec.size)
        if spec.italic is not None:
            st.font.italic = spec.italic
        if spec.color:
            _pin_color(st, spec.color)
        pf = st.paragraph_format
        for value, attr in ((spec.line, "line_spacing"), (spec.first_line, "first_line_indent"),
                            (spec.left, "left_indent"), (spec.right, "right_indent"),
                            (spec.before, "space_before"), (spec.after, "space_after")):
            if value is not None:
                setattr(pf, attr, value if attr == "line_spacing" else Pt(value))
        if spec.align is not None:
            pf.alignment = aligns[spec.align]


def _three_line_table(doc) -> None:
    """把 `Table` 样式改成三线表：顶线、栏目线、底线，没有竖线，整表居中。

    pandoc 写出来的表在文档里只挂 `<w:tblStyle w:val="Table"/>`、不自带边框，
    所以改样式表就生效，**不需要任何后处理**。原样式只有一条栏目线，顶线底线都没有。
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn

    el = doc.styles["Table"].element
    old = el.find(qn("w:tblPr"))
    at = list(el).index(old) if old is not None else len(el)
    if old is not None:
        el.remove(old)
    # 子元素顺序按 CT_TblPrBase 的定义：jc → tblInd → tblBorders → tblCellMar，顺序错了 Word 会报修复
    el.insert(at, parse_xml(
        f'<w:tblPr {nsdecls("w")}>'
        f'<w:jc w:val="center"/>'
        f'<w:tblInd w:w="0" w:type="dxa"/>'
        f'<w:tblBorders>'
        f'<w:top w:val="single" w:sz="{_RULE_THICK}" w:space="0" w:color="000000"/>'
        f'<w:bottom w:val="single" w:sz="{_RULE_THICK}" w:space="0" w:color="000000"/>'
        f'</w:tblBorders>'
        f'<w:tblCellMar>'
        f'<w:top w:w="40" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
        f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
        f'</w:tblCellMar>'
        f'</w:tblPr>'))
    for sp in el.findall(qn("w:tblStylePr")):
        el.remove(sp)
    el.append(parse_xml(
        f'<w:tblStylePr {nsdecls("w")} w:type="firstRow">'
        f'<w:tcPr><w:tcBorders>'
        f'<w:bottom w:val="single" w:sz="{_RULE_THIN}" w:space="0" w:color="000000"/>'
        f'</w:tcBorders><w:vAlign w:val="bottom"/></w:tcPr>'
        f'</w:tblStylePr>'))


def _cached_version(cache: Path) -> str:
    """缓存模板里记的档位版本。读不出来就当旧的——这是缓存探测，失败一律重建。"""
    try:
        import docx

        return (docx.Document(str(cache)).core_properties.comments or "").strip()
    except Exception:
        return ""


#: 页码域的位置串 → （放页眉还是页脚，对齐方式）
_PGNUM_WHERE = {
    "header-left": ("header", "left"), "header-center": ("header", "center"),
    "header-right": ("header", "right"), "footer-left": ("footer", "left"),
    "footer-center": ("footer", "center"), "footer-right": ("footer", "right"),
}
#: 页码格式 → Word 的 `w:pgNumType/@w:fmt`
_PGNUM_FMT = {"arabic": "decimal", "roman-lower": "lowerRoman", "roman-upper": "upperRoman"}


def _sect_el(section, tag: str, *attrs):
    """在 `sectPr` 里取或建一个子元素，并设好属性。

    `sectPr` 的子元素**有顺序要求**，Word 对顺序不对的文件会报「内容有问题」。
    python-docx 的 `append` 直接加在末尾就够用：这几个标签（`cols`、`pgNumType`、
    `lnNumType`）在 schema 里都排在 `pgMar` 之后，而 pandoc 模板里 `pgMar` 已经在了。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    pr = section._sectPr
    el = pr.find(qn("w:" + tag))
    if el is None:
        el = OxmlElement("w:" + tag)
        pr.append(el)
    for k, v in zip(attrs[::2], attrs[1::2]):
        el.set(qn("w:" + k), str(v))
    return el


def _page_field(par, align: str) -> None:
    """往段落里放一个真的 PAGE 域。

    **不能写死数字。**写「1」出来就是每页都印 1；页码必须是域，Word 打开时才按页重算。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    par.text = ""
    par.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                     "right": WD_ALIGN_PARAGRAPH.RIGHT}[align]
    run = par.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind is None:
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = text
            run._r.append(instr)
        else:
            fld = OxmlElement("w:fldChar")
            fld.set(qn("w:fldCharType"), kind)
            run._r.append(fld)


#: 中文字号名 → 磅。国标里的固定对应，不是折算。
_CN_SIZE = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}


def _font_pt(size: str) -> float | None:
    """「五号」「5号」「10.5pt」都认，认不出返回 None。

    须知写的是字号名（「注文排5号仿宋体」），**照抄那个名字**比自己折成磅安全：
    折算这一步一旦写错，成品上看不出来，而读者会以为那就是该刊的要求。
    """
    s = (size or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:pt|磅)?", s)
    if m:
        return float(m.group(1))
    s = s.replace("号", "号")
    for name, pt in _CN_SIZE.items():
        if s in (name, name.replace("五号", "5号").replace("四号", "4号")):
            return pt
    # 「5号」这类阿拉伯数字写法
    m = re.fullmatch(r"(\d)\s*号", s)
    if m:
        cn = "一二三四五六七八九"[int(m.group(1)) - 1] + "号"
        return _CN_SIZE.get(cn)
    return None


def _apply_footnote_font(doc, page) -> None:
    """按风格包声明的字体字号改注文样式。

    **这条要求原先只躺在 `other_requirements` 里**——那是给写作的人看的散文，
    没有任何代码读它。于是《中国社会科学》须知白纸黑字写着「注文排5号仿宋体」，
    导出来的却是缺省的小五（9 磅），差一档，而成品上看不出来。
    """
    from docx.shared import Pt

    st = next((x for x in doc.styles if x.name == "Footnote Text"), None)
    if st is None:
        return
    if page.footnote_font:
        _pin_fonts(st, "Times New Roman", page.footnote_font)
    if (pt := _font_pt(page.footnote_size)) is not None:
        st.font.size = Pt(pt)


#: 纸张尺寸（毫米）。须知说「A4 纸格式」时按它设，不猜。
_PAPER = {"a4": (210, 297), "a5": (148, 210), "letter": (215.9, 279.4), "b5": (176, 250)}
#: 正文字体字号要改的那几档。pandoc 把正文段落挂在这三个样式上。
_BODY_STYLES = ("Normal", "Body Text", "First Paragraph")


def _apply_body_font(doc, page) -> None:
    """按风格包声明的正文字体字号改正文那几档。

    跟注文那一处同理：《中国社会科学》须知写着「研究论文类稿件宋体小4号」，
    而这条原先只抄在 `other_requirements` 里，没有代码读它。
    """
    from docx.shared import Pt

    if not (page.body_font or page.body_size):
        return
    pt = _font_pt(page.body_size)
    named = {x.name: x for x in doc.styles}
    for name in _BODY_STYLES:
        st = named.get(name)
        if st is None:
            continue
        if page.body_font:
            _pin_fonts(st, "Times New Roman", page.body_font)
        if pt is not None:
            st.font.size = Pt(pt)
            # 首行缩进两字符跟着字号走——写死的磅数在换了字号之后就不是两个字了
            st.paragraph_format.first_line_indent = Pt(pt * 2)


def _apply_paper_size(section, page) -> None:
    """纸张。须知说了才设——pandoc 模板缺省是 Letter，中文期刊一律 A4。"""
    from docx.shared import Mm

    wh = _PAPER.get((page.paper_size or "").strip().lower())
    if wh:
        section.page_width, section.page_height = Mm(wh[0]), Mm(wh[1])


def _apply_page_layout(doc, page, fields: dict[str, str]) -> None:
    """把 `PageLayout` 落成 docx 的页面设置。**须知没说的一格都不动。**

    `fields` 是 `{journal}`、`{title}`、`{year}` 这几个占位的取值——页眉写
    「《社会学研究》2026 年第 3 期」这类文字时用得上。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    sec = doc.sections[0]
    for val, attr in ((page.margin_top_cm, "top_margin"), (page.margin_bottom_cm, "bottom_margin"),
                      (page.margin_left_cm, "left_margin"), (page.margin_right_cm, "right_margin")):
        if val is not None:
            setattr(sec, attr, Cm(val))

    settings = doc.settings.element
    if page.two_sided:
        # 镜像页边距（内侧/外侧）与奇偶页不同页眉都是**文档级**开关，不在 sectPr 里。
        # 只设 even_page_header 不开 evenAndOddHeaders 的话，偶数页页眉根本不显示。
        for tag in ("mirrorMargins", "evenAndOddHeaders"):
            if settings.find(qn("w:" + tag)) is None:
                settings.append(OxmlElement("w:" + tag))

    fmt = lambda s: s.format(**fields) if s else ""      # noqa: E731
    slots = [("header", False, page.header_odd), ("footer", False, page.footer_odd)]
    if page.two_sided:
        slots += [("header", True, page.header_even), ("footer", True, page.footer_even)]
    for kind, even, text in slots:
        if not text:
            continue
        part = getattr(sec, f"even_page_{kind}" if even else kind)
        part.is_linked_to_previous = False
        part.paragraphs[0].text = fmt(text)

    if page.page_number:
        where, align = _PGNUM_WHERE[page.page_number]
        parts = [getattr(sec, where)]
        if page.two_sided:
            parts.append(getattr(sec, f"even_page_{where}"))
        for part in parts:
            part.is_linked_to_previous = False
            par = part.paragraphs[0]
            # 页眉已经有文字时另起一段放页码，别把文字冲掉
            _page_field(par.insert_paragraph_before() if par.text.strip() else par, align)
        attrs = ["fmt", _PGNUM_FMT[page.page_number_format]]
        if page.page_number_start is not None:
            attrs += ["start", page.page_number_start]
        _sect_el(sec, "pgNumType", *attrs)

    if page.columns > 1:
        attrs = ["num", page.columns, "space", int((page.column_gap_cm or 0.8) * 567)]
        _sect_el(sec, "cols", *attrs)
    if page.line_numbers:
        _sect_el(sec, "lnNumType", "countBy", 1, "restart", "continuous")
    _apply_paper_size(sec, page)
    _apply_body_font(doc, page)
    _apply_footnote_font(doc, page)
    if page.footnote_restart_each_page:
        # 脚注编号是**文档级**设置（`w:footnotePr`），不在 sectPr 里。
        # Word 缺省 continuous，要「每页单独编号」得显式写 eachPage。
        fpr = settings.find(qn("w:footnotePr"))
        if fpr is None:
            fpr = OxmlElement("w:footnotePr")
            settings.append(fpr)
        nr = fpr.find(qn("w:numRestart"))
        if nr is None:
            nr = OxmlElement("w:numRestart")
            fpr.append(nr)
        nr.set(qn("w:val"), "eachPage")


def _build_reference_doc(pandoc: str, cache: Path, layout: dict[str, _Layout],
                         latin: str, page=None, fields: dict[str, str] | None = None,
                         stamp: str = "") -> Path | None:
    """拿 pandoc 自带的模板套上档位表，缓存在 styles/_default/（不进仓库）。
    没装 python-docx 就退回 pandoc 自带的样子。

    `page` 给了就再套一层页面级版式（页边距、页眉页脚、页码、单双面、分栏）。
    `stamp` 进缓存标记：**不同的风格包必须各缓存一份**，否则第二本刊会拿到第一本的模板。
    """
    version = _LAYOUT_VERSION + (f"+{stamp}" if stamp else "")
    if cache.exists() and _cached_version(cache) == version:
        return cache
    try:
        import docx
    except ImportError:
        return None
    if not _pandoc_reference_docx(pandoc, cache):
        return None
    d = docx.Document(str(cache))
    _apply_layout(d, layout, latin)
    _three_line_table(d)
    if page is not None:
        _apply_page_layout(d, page, fields or {})
    d.core_properties.comments = version
    d.save(str(cache))
    return cache


def default_reference_doc(pandoc: str) -> Path | None:
    """英文稿的 Word 模板：Times New Roman 12 号、双倍行距的标准投稿稿，表是三线表。

    没有期刊模板时用它——「规范即可，不做特殊要求」。档位见 `_EN_LAYOUT`。
    """
    return _build_reference_doc(pandoc, Path("styles") / "_default" / "reference.docx",
                                _EN_LAYOUT, "Times New Roman")


def default_reference_doc_zh(pandoc: str) -> Path | None:
    """中文稿的 Word 模板：正文宋体小四、1.5 倍行距、首行缩进两字符，标题黑体，表是三线表。

    「规范即可」的中文对应物，档位见 `_ZH_LAYOUT`。拉丁字符保留 Times New Roman
    （中文排版的通例：汉字用中文字体，夹杂的英文和数字用西文字体）。
    """
    return _build_reference_doc(pandoc, Path("styles") / "_default" / "reference_zh.docx",
                                _ZH_LAYOUT, "Times New Roman")


def _patch_footnote_restart(target: Path) -> None:
    """成稿里补上「脚注每页单独编号」。

    **pandoc 不带参考模板的 `w:footnotePr` 过来。**实测：页边距、镜像页边距、
    奇偶页页眉、分栏、页码类型、行号、页眉页脚部件它全都保留，唯独 settings.xml 里
    这一格是它自己重写的，于是模板里声明好的 `eachPage` 在成稿里变回 Word 缺省的
    连续编号——**声明了却不生效**，而且肉眼极难发现（要翻到第二页去数脚注号）。
    所以转换完之后直接改 zip 里那一份。
    """
    import re as _re
    import shutil as _sh
    import zipfile as _zf

    with _zf.ZipFile(target) as z:
        items = {n: z.read(n) for n in z.namelist()}
    xml = items.get("word/settings.xml", b"").decode("utf-8")
    if not xml or "eachPage" in xml:
        return
    note = '<w:footnotePr><w:numRestart w:val="eachPage"/></w:footnotePr>'
    if "<w:footnotePr>" in xml:
        xml = _re.sub(r"<w:footnotePr>.*?</w:footnotePr>", note, xml, count=1, flags=_re.S)
    else:
        # `w:footnotePr` 在 schema 里排得很靠前，放在 w:settings 的第一个子元素之前最稳
        xml = _re.sub(r"(<w:settings[^>]*>)", r"\1" + note, xml, count=1)
    items["word/settings.xml"] = xml.encode("utf-8")
    tmp = target.with_suffix(".docx.tmp")
    with _zf.ZipFile(tmp, "w", _zf.ZIP_DEFLATED) as z:
        for name, data in items.items():
            z.writestr(name, data)
    _sh.move(str(tmp), str(target))


def style_reference_doc(pandoc: str, style: str, rules, zh: bool, paper_text: str) -> Path | None:
    """按风格包声明的页面版式建 Word 模板，缓存在该包目录下的 `reference.docx`。

    段落级的档位仍用缺省那套（中文 `_ZH_LAYOUT` / 英文 `_EN_LAYOUT`）——
    **须知一般不规定正文字号**，规定的是页边距、页眉页脚、页码、单双面这几件。
    要连字号一起改的刊，手工放一份 `template.docx` 覆盖。
    """
    import datetime as _dt
    import hashlib as _hl

    layout, latin = (_ZH_LAYOUT, "Times New Roman") if zh else (_EN_LAYOUT, "Times New Roman")
    title = next((ln.lstrip("# ").strip() for ln in paper_text.splitlines()
                  if ln.startswith("# ")), "")
    fields = {"journal": rules.journal or style, "title": title,
              "year": str(_dt.date.today().year)}
    # 缓存标记带上版式的指纹：改了 rules.json 里的版式，模板要重建
    stamp = _hl.sha1(rules.layout.model_dump_json().encode()).hexdigest()[:8]
    return _build_reference_doc(pandoc, style_api.pack_dir(style) / "reference.docx",
                                layout, latin, page=rules.layout, fields=fields, stamp=stamp)


def default_csl_name(paper_text: str) -> str:
    """没有风格包时的参考文献样式：两边都是**著者-出版年**（英文 Chicago、中文 GB/T 7714）。

    中文定成夹注是 2026-09-16 用户定的「甲」：《社会学研究》《社会》《社会学评论》《社会建设》
    这一路社会学期刊一律用夹注（作者，出版年：页码）加文末参考文献表，不用顺序编码。
    原先缺省给的是 `...-numeric`（正文排成 [1][2]），对社会学稿件是选错了体例。
    **顺序编码、页下脚注这些别的体例走风格包的 `reference_style`**，这一层没动。
    """
    return ("chicago-author-date" if manuscript._is_english(paper_text)
            else "china-national-standard-gb-t-7714-2015-author-date")


#: 编号体例（正文里是上标数字，不是作者-年份）的 CSL 名字里出现的记号。
#: 判的是**实际选中的样式**，不是稿子的语言——风格包的须知可以让中文稿走著者-出版年，
#: 也可以让英文稿走 Vancouver，按语言猜会两头都错。
_NUMERIC_CSL_HINTS = ("numeric", "vancouver", "-num", "ieee", "nature", "science")


def _is_numeric_csl(csl_name: str) -> bool:
    """这个样式的正文引用是纯编号吗。

    编号体例下 pandoc 的 `@key` 只排出数字，作者名整个丢掉——叙述式引用就成了
    「[1] 认为」，一个编号当句子主语。渲染要知道这件事，才能把作者名写进正文
    （`作者[-@key]`）。GB/T 7714 的两个变体正好一个 numeric 一个 author-date，
    名字里就带着记号。
    """
    n = (csl_name or "").lower()
    if "author-date" in n or "authordate" in n:
        return False
    return any(h in n for h in _NUMERIC_CSL_HINTS)


def fulltext_counts(rundir) -> tuple[int, int] | None:
    """精读集里读到全文的篇数 / 总篇数，来自 oss lit fetch 写的 reading/index.json。没有就是 None。"""
    idx = rundir.file("reading") / "index.json"
    if not idx.exists():
        return None
    try:
        items = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return sum(1 for i in items if i.get("level") == "fulltext"), len(items)


def figures_of(rundir) -> dict[str, str]:
    """运行目录里的图 → 语言标记。标记来自 `oss figure` 写的 fig_<name>.json；
    老图没有旁注就是空串——英文稿里引它会被拦，重画一次就有了。"""
    out: dict[str, str] = {}
    for f in rundir.path.glob("fig_*.png"):
        lang = ""
        meta = f.with_suffix(".json")
        if meta.exists():
            try:
                lang = str(json.loads(meta.read_text(encoding="utf-8")).get("lang", ""))
            except (json.JSONDecodeError, OSError):
                lang = ""
        out[f.stem[4:]] = lang
    return out


def plan_columns(plan: AnalysisPlan) -> set[str]:
    """分析计划用到的所有列名（含稳健性补丁和样本筛选里的）。"""
    cols = {plan.outcome, plan.treatment, plan.cluster_level, plan.treat_time,
            plan.unit, plan.time, *plan.controls, *plan.fixed_effects}
    texts = [plan.sample_filter]
    for spec in plan.interactions:
        cols |= set(analysis_mod.parts_of(spec))
    if analysis_mod.is_interaction(plan.treatment):
        cols |= set(analysis_mod.parts_of(plan.treatment))
    for rb in plan.robustness:
        cols |= {rb.outcome or "", rb.treatment or "", rb.cluster_level or "",
                 *(rb.controls or []), *(rb.fixed_effects or [])}
        for spec in rb.interactions or []:
            cols |= set(analysis_mod.parts_of(spec))
        texts.append(rb.sample_filter or "")
    for t in texts:
        cols |= set(re.findall(r"[A-Za-z_]\w*", t or ""))
    return {c for c in cols if c}


def required_sections(style: str) -> tuple[tuple[str, ...], ...] | None:
    """风格包给了就按它的骨架查节，没给按通用五节。"""
    if not style:
        return None
    pp = style_api.pack_dir(style) / "style_profile.json"
    profile = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else None
    return tuple((s["title"],) for s in style_mod.skeleton(profile))


# ─────────────────────────────────────────────── 三条命令


#: 配料单里**写得出确定值、因而核得动**的那几项：键 → 论文里该出现的是什么。
#: 值为 `True` 的按数字查（要独立成数），`False` 的按字符串查（不区分大小写）。
#:
#: **这张表就是「记模型快照」那条方法论要求的牙齿**（bisbee2024：合成数据的结果随模型版本
#: 与措辞漂移，不记版本就不可复现）。原先这里只查 `simulator` 与 `n_reps` 两项，
#: 而漏掉的恰恰是要紧的四项——模型名、模型版本、走哪个网关、什么温度。
_SIM_REPORTABLE: dict[str, tuple[bool, str]] = {
    "simulator": (False, "模拟器"),
    "simulator_version": (False, "模拟器版本"),
    "model": (False, "模型"),
    "model_provider": (False, "模型网关"),
    "temperature": (True, "温度"),
    "rounds": (True, "轮数"),
    "n_reps": (True, "每条件重复次数"),
}
#: 配料单里**没有牙齿**的那几项，说明书里要照实写明（见 social-paper/reference/simulation.md）。
#: 前四项是自由散文，查它就得读懂意思；`conditions` 的键是机器标识（`threshold_low`），
#: 论文里本来就该用人话描述条件，硬查字面会把正当写法误拦。
SIM_NO_TEETH = ("prompts", "population", "interview_plan", "contamination_note", "conditions")
#: 按字符串查的那几项，值短于这个长度就核不动——随便哪篇论文里都找得到一个 `m`。
#: 真实的标识都不短：oasis(5)、openai(6)、0.3.1(5)、gpt-4o-2024-08-06(17)。
_SIM_NAME_MIN = 3


def _sim_unreported(meta: dict, text: str) -> list[str]:
    """配料单里核得动的那几项，论文里没报的逐条列出来。**缺陷一次报全**（simbox 的同一条规矩）。

    数字一律**独立成数**地查：`rounds=10` 不许被正文里的 `100` 或 `10.5` 顶掉，
    `temperature=0` 不许被 `0.7` 顶掉（所以前后都排除数字**与小数点**）。
    `n_reps` 另有一条加严口径**照旧**：光出现那个数不算，要跟「重复 / rep」写在同一段——
    篇幅里随处可见的 3 不能替一句「每条件重复 3 次」。

    **配料单里干脆没有这一项的，也报**：`sim_state.json` 是运行目录里一个普通 JSON，
    把一项删掉就核不了；「核不了」不等于「过了」。
    """
    miss: list[str] = []
    paras = text.split("\n\n")
    for key, (numeric, label) in _SIM_REPORTABLE.items():
        val = meta.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            miss.append(f"配料单里没有{label}（{key}）这一项——核不了，不等于过了")
            continue
        shown = str(val).strip()
        if not numeric and len(shown) < _SIM_NAME_MIN:
            # 「这个名字在文中出现过没有」对一两个字符的值是空问：随便哪篇论文里都有 `m`。
            # **填一个字母就永远过**是零代价的绕行路，所以短到核不动就照实说核不动
            miss.append(f"配料单里的{label}是「{shown}」，只有 {len(shown)} 个字符——"
                        f"这么短的名字在任何一篇论文里都找得到，核不动。"
                        f"写完整的标识（模型写 gpt-4o-2024-08-06 这种带日期的版本号，不写「最新版」）")
            continue
        if numeric:
            hit = re.compile(rf"(?<![\d.]){re.escape(shown)}(?![\d.])")
            if key == "n_reps":
                ok = any(("重复" in p or re.search(r"\brep", p, re.I)) and hit.search(p)
                         for p in paras)
                if not ok:
                    miss.append(f"{label} {shown} 没跟「重复 / rep」写在同一段")
                continue
            if not hit.search(text):
                miss.append(f"{label} {shown} 没在文中出现")
        elif shown.lower() not in text.lower():
            miss.append(f"{label}「{shown}」没在文中出现")
    return miss


def check(run: str, *, style: str = "") -> dict:
    """校验稿件：节齐不齐、有没有裸数字、占位符解析得了吗。

    **正文里的统计结果必须是占位符。**手打的数字看起来跟真的一模一样——
    这是整套系统最危险的失败模式，只能在渲染前硬拦。

    警告（`data["warnings"]`）与拒绝（`problems`）分开：警告不挡路，但**都要打**，
    过不了校验时也一样——所以它跟着 `Refused` 一起走。
    """
    rundir = require_run(run)
    paper_path = rundir.file(ARTIFACTS["paper"])
    if not paper_path.exists():
        raise Missing("paper", f"缺 {ARTIFACTS['paper']}——论文是 skill 写的，先让它产出",
                      code=1)
    if not rundir.file(ARTIFACTS["results"]).exists():
        # 少了这一句，下面那行 load 会抛一个跟「你该先跑 oss run」毫无关系的堆栈
        raise Missing("results", f"缺 {ARTIFACTS['results']}——先跑 oss run", code=1)
    # 两道存在性守卫过了就落阶段：意思是「稿子在，进了第 7 环」。
    # 「校验过没过」是 stage_status 的事——接手的模型要能分清「还没写」和「写了没过」。
    state_mod.set_stage(rundir.path, "7-paper")
    res = AnalysisResults.load(rundir.file(ARTIFACTS["results"]))
    pw_path = rundir.file(ARTIFACTS["power"])
    pw = PowerReport.load(pw_path) if pw_path.exists() else None
    lit_path = rundir.file(ARTIFACTS["literature"])
    corpus = LiteratureCorpus.load(lit_path) if lit_path.exists() else None
    figures = figures_of(rundir)
    problems, warnings = manuscript.check(
        paper_path.read_text(encoding="utf-8"), res, pw, corpus, figures=figures,
        known_numbers=known_numbers(rundir), required_sections=required_sections(style),
        fulltext=fulltext_counts(rundir), labels=labels_of(rundir))
    plan_file = rundir.file(ARTIFACTS["plan"])
    plan_obj = AnalysisPlan.load(plan_file) if plan_file.exists() else None
    # 代理差距：绑定里 proxy / derived 的构念，三栏没填就警告——方法节那句「测的是什么、差在哪」
    # 靠这三栏写。评审第四条：「这不是 limitation，是理论和测量对不上」。
    # **第二条触发条件**（测量骨架设计稿 5.3）：测量类研究的主构念绑得再准也要填——
    # 「理论要什么 / 实际测什么 / 差距」正是这类研究的核心交代，而 `measurement.construct`
    # 是一句纯自由文本，三栏是程序唯一核得到的抓手。不重做机制，只多一条触发条件
    bpath = rundir.file(ARTIFACTS["binding"])
    if bpath.exists():
        try:
            targets = binding_mod.measurement_gap_targets(
                plan_obj.measurement if plan_obj is not None else None)
            for b in BindingReport.load(bpath).bindings:
                if b.gap and b.measures:
                    continue
                if b.status in ("proxy", "derived"):
                    warnings.append(
                        Finding(f"构念「{b.construct_name}」是{'代理' if b.status == 'proxy' else '构造'}变量（{b.column or b.derivation}），"
                        f"binding.json 里的 construct_def / measures / gap 三栏没填全——"
                        f"方法节要说清理论要的是什么、这一列测的是什么、差距会让估计偏向哪边", "paper.construct_gap"))
                elif targets & {b.construct_name, b.column or ""}:
                    warnings.append(
                        Finding(f"构念「{b.construct_name}」是这篇测量研究的主构念（{b.column or b.derivation}），"
                        f"binding.json 里的 construct_def / measures / gap 三栏没填全——"
                        f"**测量研究里这三栏绑得再准也要填**：「理论要什么 / 实际测什么 / 差距」"
                        f"正是这类研究的核心交代，而 plan.measurement.construct 是一句自由文本，"
                        f"程序核不了它写得对不对，三栏是它唯一的抓手", "paper.measurement_construct_gap"))
        except Exception:  # noqa: BLE001 —— 老版本 binding.json 读不出来不该挡校验
            pass
    # 诊断→处置的门：must 级处方没处置（功效 FAIL、分批用 TWFE、平行趋势不成立）不给写论文。
    # 评审：「它把自己所有的病症都准确地描述了一遍，然后没有治任何一个。」
    problems.extend(route_mod.blockers(route_mod.build(rundir.path)))
    # 锚点门：稿子回答的还是冻结时那个问题吗。**换题不是校验能放行的事**
    problems.extend(anchor_gate.check(rundir.path))

    # 研究类型门 + 因果措辞：非因果类型的论文不下因果结论（设计稿 4.2、4.4）
    rtype = rt_gate.plan_type(rundir.path)
    rt_problems, rt_down = rt_gate.check(rundir.path, rtype)
    problems.extend(rt_problems)
    if rt_down and not rt_gate.already_recorded(rundir.path):
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step=rt_gate.DOWNGRADE_STEP, command="oss paper check",
            args={"from": "causal", "to": "descriptive"}, note=rt_down))
    # 补了类型却没重跑：锚点这一格还留着空，`rt_problems` 那道门看不出来（它比的是
    # 已经冻结的锚点值，这一格空着算「不比」）——校验不能顺着这个空子把它当成定了型
    if rt_gate.declared_unlocked(state_mod.load(rundir.path).anchor, rtype):
        problems.append(
            Finding("plan.json 的研究类型是第一次 oss run 之后才写上的，还没锁定——"
            "先重跑一次 oss run（这一次会锁定，并记为「看过结果才补」，合规检查会一直判不过）", "gate.research_type_unlocked"))
    # 措辞判据只写一处（`wording_scope`）：因果或没声明不查，类型换回因果后盘上残留的
    # 旧 causal_flags.json 也不该留着——删掉，保持盘面跟当前类型一致
    wtype, wdown = rt_gate.wording_scope(rundir.path)
    if rt_gate.forced_downgrade(rundir.path):
        warnings.append(Finding("识别审计判 FAIL、替代设计已穷尽：按停止条件全文降级为关联——"
                        "正文按描述类查措辞（明确的因果说法照拦），plan.json 的 research_type "
                        "请一并改成 descriptive", "wording.forced_downgrade"))
    flags_path = rundir.file(causal_wording.FLAGS_FILE)
    if wtype:
        hits = causal_wording.scan(paper_path.read_text(encoding="utf-8"), wtype)
        problems.extend(causal_wording.hard_problems(hits, wtype, downgraded=wdown))
        causal_wording.write_flags(rundir.path, hits, wtype)
        if (soft := causal_wording.soft_warning(hits)) is not None:
            warnings.append(soft)
    elif flags_path.exists():
        flags_path.unlink()

    # 因果骨架（设计稿 4.1、4.3）：弱工具时主结果引 AR 区间是硬门；因果类 ols_fe 没引敏感性数字只警告
    from oss.core import causal_checks
    paper_text = paper_path.read_text(encoding="utf-8")
    problems.extend(causal_checks.paper_problems(res.diagnostics, paper_text))
    warnings.extend(causal_checks.paper_warnings(
        res.diagnostics, paper_text,
        causal_ols=bool(plan_obj and plan_obj.research_type == "causal" and plan_obj.estimator == "ols_fe")))

    # 测量骨架（设计稿 5.1 的 M3）：论文报了信度、产出里一类效度证据都没有——硬拦。
    # **不读论文怎么写的**：判的是产出在不在、正文引没引信度那份产出的占位符
    problems.extend(manuscript.measurement_reliability_problems(
        paper_text, res, plan_obj.measurement if plan_obj else None, figures))
    # M4 的论文期核实（设计稿 5.2）：登记了跨组测量等价检验，那份产出却不在结果里——
    # **登记一个空壳就能让计划那一侧的警告与 must 级处方整套不响**，代价为零
    problems.extend(manuscript.measurement_equivalence_problems(
        res, plan_obj.measurement if plan_obj else None, figures))

    # 派生列：prep.py 里构造、分析用到的，论文里必须提到（构造式写在方法节）。
    # 这是链上唯一没人核的计算——至少要能核「说没说」。
    prep = rundir.file("prep.py")
    if prep.exists() and plan_obj is not None:
        used = plan_columns(plan_obj)
        derived = manuscript.derived_columns(prep.read_text(encoding="utf-8"))
        missing = manuscript.unmentioned_derived(paper_path.read_text(encoding="utf-8"), used, derived)
        if missing:
            # 论文用文字交代构造式（「四个水平档」）而不写列名（net_low）是正常的，
            # 所以这只是一条提示，合成一行——验证跑的论文交代得很全，逐列报十条是噪声
            warnings.append(
                Finding(f"prep.py 构造了 {len(derived)} 个列，其中分析用到、论文里没按列名出现的有："
                f"{'、'.join(missing)}。方法节已用文字交代了构造式的不用管；没交代的要补", "paper.derived_unmentioned"))

    # 用了数据包的，论文数据节必须出现它的名字（引用格式在出处文件里）。
    # 用过哪些看留痕里各步的数据路径——原始数据、拼进来的、都算
    used: dict[str, str] = {}
    trace = rundir.file("trace.jsonl")
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                args = json.loads(line).get("args") or {}
            except json.JSONDecodeError:
                continue
            for key in ("data", "right", "left", "path"):
                if isinstance(args.get(key), str) and (pk := packs_mod.pack_of(args[key])):
                    used[pk.name] = pk.cite_name
    text_for_cite = paper_path.read_text(encoding="utf-8")
    for pname, cite_name in used.items():
        if cite_name.lower() not in text_for_cite.lower():
            warnings.append(Finding(f"用了数据包 {pname}，但论文里没出现「{cite_name}」——数据节要写明数据集并引用它"
                            f"（引用格式在 data/{pname}/{pname}.meta.json）", "paper.datapack_uncited"))

    # 仿真那几道门：**挂两处，不是一处**（2026-09-19）——盘上有入库档案（`oss sim import`
    # 写的），**或者**计划声明为仿真（`research_type` 或 `also`）。原先只挂档案存在：
    # 声明为仿真却没入库的论文，一道门都不响，正文就能拿模型里的数字去说现实。
    # 那种情形 `oss run` 那边报 `plan.simulation.box_missing`，这里则把**读得动文本**的
    # 两条照跑（结论限定、现实断言）；要读配料单的几条没档案就跑不了，如实跳过。
    #
    # 既有路径的输出一字节不变（散文冻结盯着这条）：有档案时走的还是原来那一条。
    # 宽严已决（V2 任务 7）：前两条 problems、第三条 warnings。
    sim_path = rundir.file(SIM_STATE)
    declared_sim = plan_obj is not None and (
        plan_obj.research_type == "simulation" or "simulation" in plan_obj.also)
    if sim_path.exists():
        # 盔甲：坏 JSON、顶层或配料单不是对象、关键项缺失——给一条人话，不崩，
        # 也不让「None」漏进散文（read_json_file 的规矩：读一个坏文件不该让整条命令挂掉）
        sim = read_json_file(sim_path)
        meta = sim.get("meta") if isinstance(sim, dict) else None
        simulator = str(meta.get("simulator") or "") if isinstance(meta, dict) else ""
        n_reps = meta.get("n_reps") if isinstance(meta, dict) else None
        if not simulator or n_reps is None:
            problems.append(Finding("sim_state.json 读不出来或缺了配料单关键项（simulator / n_reps）"
                            "——入库档案坏了，重新 oss sim import 一次", "sim.state_broken"))
        else:
            if (miss := _sim_unreported(meta, text_for_cite)):
                problems.append(Finding(
                    "仿真论文的方法节没按配料单报全：" + "；".join(miss)
                    + "——**结果随模型版本与措辞漂移**（Bisbee 2024），"
                    "不写清用的哪个模拟器、哪个模型、走哪个网关、什么温度，"
                    "这篇就是不可复现的。配料单里有的、写得出确定值的都要报",
                    "sim.unreported"))
            if sim.get("downgrade"):
                if "初步" not in text_for_cite and "探索性" not in text_for_cite:
                    problems.append(Finding("降级仿真（有条件重复不足）全文须至少出现一次"
                                    "「初步」或「探索性」——通篇措辞收紧，不只是局限节", "sim.downgrade_hedge"))
                if "单次模拟无法区分" not in text_for_cite:
                    problems.append(Finding("降级仿真的全文必须含降级固定句"
                                    "「单次模拟无法区分随机波动与真实规律」——查它的头"
                                    "「单次模拟无法区分」，逐字出现才算", "sim.downgrade_sentence"))
    if sim_path.exists() or declared_sim:
        # 读得动文本的两条：**没有入库档案也照跑**（声明为仿真就够了）。
        #
        # 结论要限定在模型内是那张五个词的违规清单的**对价**——词表查违规，换个说法就漏放
        # 且一声不响；这一条查「必须出现」，漏收一种写法只会误报。
        if (nolimit := causal_wording.missing_scope_limit(text_for_cite)):
            problems.append(nolimit)
        warnings.extend(causal_wording.sim_reality_warnings(text_for_cite))

    if problems:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="paper", command="oss paper check",
            outcome="refused", note="；".join(problems)))
        # 警告跟着异常走：门面在 ✗ 之前把它们打出来，位置跟从前一样
        raise Refused("稿件过不了校验：", code=1, problems=problems,
                      data={"run": str(rundir.path), "warnings": warnings})

    used = manuscript.placeholders_in(paper_path.read_text(encoding="utf-8"))
    # 校验**通过**也要留痕。原先只在失败时记，于是留痕上看不出这一步做过——
    # 而「这稿子过没过校验」正是事后最该能回答的问题之一。
    kinds: dict[str, int] = {}
    for u in used:
        kinds[u[2:].split(":", 1)[0]] = kinds.get(u[2:].split(":", 1)[0], 0) + 1
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="paper", command="oss paper check", outcome="ok",
        note=(f"校验通过：{len(manuscript.REQUIRED_SECTIONS)} 节齐全、正文零裸数字，"
              f"引用 {len(used)} 个占位符（"
              + "，".join(f"{k} {v}" for k, v in sorted(kinds.items())) + "）"),
        args={"placeholders": used},
    ))
    # 走到这里才是「校验过了」：阶段不动，状态从 in_progress 翻成 done。
    state_mod.set_stage(rundir.path, "7-paper", "done")
    return result(f"校验通过：{len(manuscript.REQUIRED_SECTIONS)} 节齐全，正文零裸数字", {
        "run": str(rundir.path), "paper": str(paper_path), "style": style,
        "n_sections": len(manuscript.REQUIRED_SECTIONS),
        "placeholders": used, "warnings": warnings,
    })


def render(run: str, *, out: str = "") -> dict:
    """把占位符换成真实数字。**校验不过就不给渲染。**"""
    rundir = require_run(run)
    paper_path = rundir.file(ARTIFACTS["paper"])
    if not paper_path.exists():
        raise Missing("paper", f"缺 {ARTIFACTS['paper']}", code=1)
    text = paper_path.read_text(encoding="utf-8")
    res = AnalysisResults.load(rundir.file(ARTIFACTS["results"]))
    pw_path = rundir.file(ARTIFACTS["power"])
    pw = PowerReport.load(pw_path) if pw_path.exists() else None

    lit_path = rundir.file(ARTIFACTS["literature"])
    corpus = LiteratureCorpus.load(lit_path) if lit_path.exists() else None
    figures = figures_of(rundir)
    labels = labels_of(rundir)
    problems, _ = manuscript.check(text, res, pw, corpus, figures=figures,
                                   known_numbers=known_numbers(rundir), labels=labels)
    if problems:
        # **把问题直接说出来。**原先只说「校验没过，去跑 check 看」，
        # 而 render 与 check 只要有一处传参不一致，两边结论就会不同——
        # 那时候人会陷在「check 明明过了」里查很久。
        raise Refused("校验没过，不给渲染：", code=1, problems=problems)

    try:
        rendered = manuscript.render(text, res, pw, corpus, figures=figures, labels=labels)
    except manuscript.ManuscriptRejected as exc:
        raise Refused(str(exc), code=1) from exc

    target = Path(out) if out else rundir.file("paper_rendered.md")
    target.write_text(rendered, encoding="utf-8")
    # 指纹记进留痕：导出前那道一致性门靠它分清「渲染稿被人手改了」与「源稿改完没重渲」
    # （见 `rendered_fingerprint`）。sha 里没有小数点，`trace_numbers` 不会把它当成出处。
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="paper", command="oss paper render",
        outcome="ok", artifacts=[target.name],
        note=f"渲染 {len(manuscript.placeholders_in(text))} 个占位符 → {target.name}",
        args={"plan_id": res.plan_id, "n": res.n,
              "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest()}))
    return result(f"已渲染 → {target}", {
        "run": str(rundir.path), "path": str(target), "name": target.name,
        "placeholders": manuscript.placeholders_in(text),
    })


def _export_target(rundir, out: str) -> Path:
    """`--out` 落到哪。**相对路径一律相对运行目录**，绝对路径照用。

    原先直接 `rundir.file(out)`。传仓库相对路径（`runs/demo_causal/paper.docx`）时
    会拼成 `runs/demo_causal/runs/demo_causal/paper.docx`，**而且照样打 ✓**——
    人按提示去看第一个路径，那里什么都没有。
    这是个**算得出来的事实**（相对路径里已经含了运行目录名），所以直接拦。
    """
    p = Path(out)
    if p.is_absolute():
        return p
    run_name = rundir.path.name
    if run_name in p.parts:
        raise BadInput(
            f"--out 是相对运行目录的，给了「{out}」会落到 {rundir.path / p}（运行目录套了两层）。"
            f"想写进运行目录就只给文件名（--out {p.name}），想写到别处给绝对路径")
    return rundir.file(out)


def export(run: str, *, out: str = "paper.docx", style: str = "") -> dict:
    """把渲染好的论文导出成 Word（.docx），图片嵌入正文。

    **markdown 不是成品。**社科期刊投稿收的是 Word；
    审稿人、导师、合作者打开的也是 Word。链条的终点必须是一份
    能直接发给人看的文件，而不是一个文本文件加一个图片文件夹。

    七道门的顺序与退出码见模块头。边走边打的那几行进 `data["log"]`——
    **包括被后面某道门拒绝时**：那时它们本来就已经打出来了。
    """
    rundir = require_run(run)
    log: list[list[str]] = []
    src = rundir.file("paper_rendered.md")
    if not src.exists():
        # `log` 此刻必然是空的，照样带上：七道门的返回形状**一律**有 `data["log"]`，
        # 调用方就不用为「第一道门」单开一条分支去问这个键在不在。
        raise Missing("paper_rendered", "缺 paper_rendered.md——先跑 oss paper render",
                      code=1, data={"log": log})

    # 锚点门排在轮次门**前面**：一篇回答错问题的稿子，审过几轮、审稿人怎么判都不相干，
    # 该在「审到哪一步了」之前就被拒——先问「审全没有」等于默认题目是对的。
    anchor_problems = anchor_gate.check(rundir.path)
    if anchor_problems:
        # 一次报全：idea 和方向一起改了却只报第一条，改完再跑又被拦一次
        raise Refused(Finding("不给导出：" + anchor_problems[0], "export.anchor_or_type"), code=7,
                      problems=[Finding(p, "export.anchor_or_type") for p in anchor_problems],
                      data={"log": log})

    # 研究类型门跟锚点门同一族（7）；非因果论文下了因果结论单独一个号（8）——
    # 都排在审稿门前面：稿子该说什么话，对着文件就判得出来，不必等审稿
    rtype = rt_gate.plan_type(rundir.path)
    rt_problems, rt_down = rt_gate.check(rundir.path, rtype)
    if rt_problems:
        raise Refused(Finding("不给导出：" + rt_problems[0], "export.anchor_or_type"), code=7,
                      problems=[Finding(p, "export.anchor_or_type") for p in rt_problems], data={"log": log})
    # 补了类型却没重跑：锚点这一格还留着空，上面那道门看不出来（比的是已经冻结的锚点
    # 值，这一格空着算「不比」）——不能让「改完 plan.json 不重跑」比走 oss run 更省事
    if rt_gate.declared_unlocked(state_mod.load(rundir.path).anchor, rtype):
        msg = ("plan.json 的研究类型是第一次 oss run 之后才写上的，还没锁定——"
              "先重跑一次 oss run（这一次会锁定，并记为「看过结果才补」，合规检查会一直判不过）")
        raise Refused(Finding("不给导出：" + msg, "export.anchor_or_type"), code=7,
                      problems=[Finding(msg, "export.anchor_or_type")], data={"log": log})
    # 措辞判据只写一处（`wording_scope`）；`wdown` 下面兜底路径那道门也要用，先留着
    wtype, wdown = rt_gate.wording_scope(rundir.path)
    paper_file = rundir.file(ARTIFACTS["paper"])
    if wtype and paper_file.exists():
        hard = causal_wording.hard_problems(
            causal_wording.scan(paper_file.read_text(encoding="utf-8"), wtype), wtype, downgraded=wdown)
        if hard:
            raise Refused(Finding("不给导出：" + hard[0], "export.causal_family"), code=8,
                          problems=[Finding(p, "export.causal_family") for p in hard], data={"log": log})

    # 探索戳的检查不能依赖有没有语料——没有 literature.json 的纯文本分支也不许放行冒充的结果。
    # 门长在这里而不是下面那个 citeproc 分支里：`.docx` 是最终发出去的那一份，
    # 它带不带探索的数字，跟这次导出走哪条渲染路径无关。
    res = official_results(rundir) if rundir.file(ARTIFACTS["results"]).exists() else None

    # 因果骨架硬门（设计稿 4.1）：有效 F 低于下界时正文必须引 AR 区间——退出码 8 现在是
    # 「正文的因果说法站不住脚」一族（跟研究类型/措辞门同一族），不新增退出码。这条判的是
    # 「引没引」不是「说没说」，收到拒绝理由要补 {{diag:AR 区间}}，不是去改因果措辞
    if res is not None and paper_file.exists():
        from oss.core import causal_checks
        gate_problems = causal_checks.paper_problems(res.diagnostics, paper_file.read_text(encoding="utf-8"))
        if gate_problems:
            raise Refused(Finding("不给导出：" + gate_problems[0], "export.causal_family"), code=8,
                          problems=[Finding(p, "export.causal_family") for p in gate_problems], data={"log": log})

    # 测量骨架硬门（设计稿 5.1 的 M3）：论文报了信度、产出里一类效度证据都没有。
    # **判的是 `paper.md` 而不是渲染稿**——渲染之后占位符已经变成数字，「引没引信度那份产出」
    # 就查不出来了。退出码并进 1（那一族「其余的拒绝路径」），不新开一个：
    # 说明书按七道门的那几个数分支，为一道新门加一个数会让每一份说明书都要改
    # 同一族还有 M4 的论文期核实（设计稿 5.2）：登记了跨组测量等价检验，那份产出却不在结果里
    # ——登记一个空壳，计划那一侧的警告与 must 级处方就整套不响。这一条不读稿子，只核产出在不在
    if res is not None:
        plan_path = rundir.file(ARTIFACTS["plan"])
        spec = AnalysisPlan.load(plan_path).measurement if plan_path.exists() else None
        figs = figures_of(rundir)
        if paper_file.exists():
            m_problems = manuscript.measurement_reliability_problems(
                paper_file.read_text(encoding="utf-8"), res, spec, figs)
            if m_problems:
                raise Refused(Finding("不给导出：" + m_problems[0], "paper.measurement_reliability_only"),
                              code=1, problems=m_problems, data={"log": log})
        e_problems = manuscript.measurement_equivalence_problems(res, spec, figs)
        if e_problems:
            raise Refused(Finding("不给导出：" + e_problems[0], "paper.measurement_equivalence_missing"),
                          code=1, problems=e_problems, data={"log": log})

    # 成品必须有回归表。评审：「所有系数用散文报出来——任何量化论文这都是硬伤，
    # 读者无法复核、无法引用、无法比较」。校验只警告，交付物这里拦。
    # **排在审稿门前面**：一篇连主结果表都没有的稿子，先送三位审稿人再告诉它缺表，
    # 白花三次跨模型调用——缺哪张表是对着文件一眼能看出来的，不必等审稿。
    # 描述论文（结果里有分组表）引了 {{table:groups}} 就算有主表，理由是 `manuscript.GROUPS_MAIN_TABLE` 那一句。
    # 跟 `manuscript.check` 的警告、合规规则 tables 同一口径、同一句话
    paper_src0 = rundir.file(ARTIFACTS["paper"])
    text0 = paper_src0.read_text(encoding="utf-8") if paper_src0.exists() else None
    grouped = res is not None and bool(res.groups)
    if text0 is not None and grouped and "{{table:main}}" not in text0 and "{{table:groups}}" not in text0:
        raise Refused(Finding("不给导出：正文没有分组描述表 {{table:groups}}。描述论文在结果一节加：\n"
                      "  *Table 2. Group means.*\n  {{table:groups}}\n"
                      f"  （{manuscript.GROUPS_MAIN_TABLE}）", "export.main_table"),
                      code=4, data={"log": log})
    if text0 is not None and not grouped and "{{table:main}}" not in text0:
        raise Refused(Finding("不给导出：正文没有主结果表 {{table:main}}。在结果一节加：\n"
                      "  *Table 2. Main estimates.*\n  {{table:main}}\n"
                      "  （描述统计 {{table:descriptives}}、稳健性 {{table:robustness}} 同理）", "export.main_table"),
                      code=4, data={"log": log})

    # 交付物的门。七次真跑没有一次闭合过审稿循环——审稿人说换估计量，
    # 写进「下一步」就交付了。**这里机械地不放行。**
    st = loop_mod.loop_status(rundir.path)
    # 合格的门，只拦硬伤：没审过、或最近一轮有 FAIL/ERROR 还没复审。
    # 因果审计 FAIL、设计级意见未清零——**允许导出，但必须写进局限**，
    # 「写没写」由「局限写全没有」那位审稿人对着审计报告核。
    if st.status == "no_reviews":
        raise Refused(Finding("不给导出：这篇稿子没经过任何跨模型审稿。至少审一轮：\n"
                      f"  oss review --paper --run {run}", "export.review"), code=5, data={"log": log})
    if st.identification_gate == "fail_untried":
        log.append(["out", "  ⚠ 因果审计判 FAIL、没有换设计重跑——允许，但审计的每条意见都要在局限里交代"])
    if st.status == "in_progress":
        raise Refused(Finding(f"不给导出：第 {st.round} 轮有 FAIL/ERROR 判词"
                      f"（{st.max_rounds} 轮上限未到）。改稿、重渲染、"
                      f"用 --round {st.round + 1} 复审", "export.review"),
                      code=3, problems=[Finding(p, "export.review") for p in st.blockers], data={"log": log})
    if st.status == "exhausted":
        # 条数取 decide()，不自己数最近一轮：复审缩水时最近一轮会凭空归零，
        # 提醒就变成「遗留 0 条」，等于告诉模型没什么可写。
        log.append(["err", f"  ⚠ 轮次耗尽（{st.max_rounds}），遗留设计级意见 "
                           f"{st.decision['open_design_items']} 条——"
                           f"**导出可以，但论文必须写明遗留**"])
    prescriptions = route_mod.blockers(route_mod.build(rundir.path))
    if prescriptions:
        # 原先在循环里 raise，**只报第一条**——散文不许变，所以门面只打 summary，
        # 剩下的几条在 `problems` 里给机器看
        raise Refused(Finding("不给导出：" + prescriptions[0], "export.route"), code=6,
                      problems=[Finding(p, "export.route") for p in prescriptions],
                      data={"log": log})
    # 风格包那道门（1）照旧排在处方门后面、兜底路径那两道门前面，所以也在查 pandoc 之前：
    # 走不走 citeproc 要看它定的参考文献样式，兜底路径的门得先知道走哪条路
    src_text = src.read_text(encoding="utf-8")
    zh = not manuscript._is_english(src_text)
    reference_doc: Path | None = None
    style_layout = None      # 风格包声明了页面版式时放它的 rules，等 pandoc 解析完再建模板
    csl_name = default_csl_name(src_text)   # 英文 Chicago、中文 GB/T 著者-出版年；须知另有说法就按须知
    if style:
        pk = style_api.pack_dir(style)
        rules, profile, texts = style_mod.load_pack(pk)
        problems, warnings = style_mod.check_paper(src.read_text(encoding="utf-8"), rules, profile, texts)
        for w in warnings:
            log.append(["out", f"  ⚠ {w}"])
        if problems:
            raise Refused(f"不合 {style} 的规矩，不给导出：", code=1, problems=problems,
                          data={"log": log})
        if (pk / "template.docx").exists():
            # 手工做的模板能表达的东西比 `layout` 多，有就以它为准
            reference_doc = pk / "template.docx"
        elif rules is not None and rules.layout is not None:
            # **声明出来就自动建**，但不在这里建：`pandoc` 要到下面才解析，
            # 而那个顺序是故意的（没装 pandoc 不该挡在一篇本来就该拒的稿子前面）。
            # 这里只记下来，等 pandoc 确认存在之后再建。
            style_layout = rules
        if rules is not None and rules.reference_style:
            csl_name = style_mod.csl_for(rules.reference_style)
    csl = csl_path(csl_name)

    # 引用交给 citeproc：从源稿再渲染一份 pandoc 版（引用键而不是文字），
    # 书目从语料生成（只列被引的），按目标期刊的 CSL 样式排。
    # paper_rendered.md 仍是给人看、给审稿人看的版本，不动。
    paper_src = rundir.file(ARTIFACTS["paper"])
    lit_path = rundir.file(ARTIFACTS["literature"])
    citeproc = paper_src.exists() and lit_path.exists() and csl is not None and res is not None
    if not citeproc:
        # 文本版：转的是 paper_rendered.md（上面那道措辞门、AR 硬门查的都是 paper.md）——
        # 只改 paper.md 不重渲染，旧渲染稿里的因果说法或没引 AR 区间会从这条路径漏出去，
        # 这里补查一遍。措辞门查占位符转过来的散文；AR 硬门这条渲染稿里已经没有
        # `{{diag:AR 区间}}` 这个占位符了，查的是那段值的字符串（`paper_problems_rendered`）
        if wtype:
            hard = causal_wording.hard_problems(
                causal_wording.scan(src_text, wtype), wtype, downgraded=wdown)
            if hard:
                raise Refused(Finding("不给导出：" + hard[0] + "——改完 paper.md 要重跑 oss paper render", "export.causal_family"),
                              code=8, problems=[Finding(p, "export.causal_family") for p in hard], data={"log": log})
        if res is not None:
            from oss.core import causal_checks
            gate_rendered = causal_checks.paper_problems_rendered(res.diagnostics, src_text)
            if gate_rendered:
                raise Refused(Finding("不给导出：" + gate_rendered[0] + "——改完 paper.md 要重跑 oss paper render", "export.causal_family"),
                              code=8, problems=[Finding(p, "export.causal_family") for p in gate_rendered], data={"log": log})

    # ── 交付前的两道门（设计稿甲 5）。**排在查 pandoc 之前**，理由跟上面那两道一样：
    # CI 不装 pandoc，排在后面等于在最该用的环境里不生效。
    #
    # 补的是交付层的洞：正常路径（有源稿、有语料、有 CSL）下 `.docx` **不是**从
    # paper_rendered.md 转的——下面那段从 paper.md 重渲第二份 paper_pandoc.md 再交给 pandoc。
    # 于是最终交付的那份文字上，`manuscript.check` / `check_claims` 从头到尾没跑过；
    # 渲染后唯一的内容门（因果措辞、AR 区间）只在上面没有语料的兜底路径上生效。
    #
    # **这里重渲一遍，不把下面那份挪上来**：门要排在查 pandoc 之前，而下面那份渲不出来时
    # 抛的是 ManuscriptRejected（退出码 1，另一条失败路径）——挪上来会把「稿子渲不出来」
    # 提前成另一副样子。渲染是纯函数、稿子只有几 KB，为此打乱既有的失败路径不值当。
    #
    # **`labels` 一定要跟 `oss paper render` 当时传的那一份一样**（都走 `labels_of`）：
    # 表和图里印的是人话标签，不传就重渲成列名，跟渲染稿逐行对不上——这道门会把一篇
    # 干干净净的稿子按退出码 9 拦下来。
    fresh = delivered = None
    pw = corpus = None
    figures: set[str] = set()
    labels = labels_of(rundir)
    src_paper = ""
    if paper_src.exists() and res is not None:
        pw_path = rundir.file(ARTIFACTS["power"])
        pw = PowerReport.load(pw_path) if pw_path.exists() else None
        corpus = LiteratureCorpus.load(lit_path) if lit_path.exists() else None
        figures = figures_of(rundir)
        src_paper = paper_src.read_text(encoding="utf-8")
        try:
            fresh = manuscript.render(src_paper, res, pw, corpus, figures=figures, labels=labels)
            if citeproc:
                delivered = manuscript.render(src_paper, res, pw, corpus, figures=figures,
                                              labels=labels,
                                              cite_mode="pandoc", fig_captions="alt",
                                              numeric_citations=_is_numeric_csl(csl_name))
        except manuscript.ManuscriptRejected:
            fresh = delivered = None        # 渲不出来：下面那段照旧报它自己的话，这里不抢话
    if fresh is not None and fresh != src_text:
        diffs = _first_diffs(fresh, src_text)
        # 指纹对不上 = 审稿人看的那一份被人改过；对得上 = 源稿或产出在渲染之后变了、渲染稿只是过期。
        # 后者只提醒不拦：它要的处置是重跑一次 render，跟「稿子被动过」是两件事
        if (fp := rendered_fingerprint(rundir)) and fp != hashlib.sha256(src_text.encode("utf-8")).hexdigest():
            problems = [Finding(f"第 {ln} 行：从 paper.md 重渲是「{a or '（没有这一行）'}」，"
                        f"paper_rendered.md 里是「{b or '（没有这一行）'}」", "export.rendered_stale")
                        for ln, a, b in diffs]
            raise Refused(Finding("不给导出：paper_rendered.md 不是 oss paper render 写下的那一份——"
                          "审稿人看的是它，而交付的 .docx 是从 paper.md 重渲的，两者不是同一份东西。"
                          f"前 {len(problems)} 处差异见 problems；"
                          "要么撤掉对渲染稿的改动，要么把改动写回 paper.md 再重跑 oss paper render",
                          "export.rendered_stale"), code=9, problems=problems, data={"log": log})
        log.append(["err", "  ⚠ paper_rendered.md 跟此刻从 paper.md 重渲的结果对不上"
                           "（源稿或产出在渲染之后变过）——审稿人看的是旧的那一份，交付前重跑一次 oss paper render"])
    if delivered is None and not citeproc:
        delivered = manuscript.fold_captions(src_text)      # 兜底路径交付的就是渲染稿
    if delivered is not None:
        # 渲不出源稿（没有 paper.md，或占位符解析不了）时**不判数字回账**：那时说不出哪些数
        # 是渲染器写的、哪些是手打的，判了等于全篇误报。其余三条不依赖这个，照跑
        known = (known_numbers(rundir) | rendered_numbers(src_paper, res, pw, corpus, figures, labels)
                 if fresh is not None else None)
        probs = delivered_problems(delivered, known=known, results=res, power=pw,
                                   wtype=wtype, downgraded=wdown)
        if probs:
            raise Refused(Finding("不给导出：" + probs[0], "export.delivered_check"), code=10,
                          problems=probs, data={"log": log})

    # 上面这些门都在查 pandoc 之前判完：没装 pandoc 不该挡在一篇本来就该拒的稿子前面
    # （装上再跑照样被拒，退出码还从 8 变成了 1）。CI 不装 pandoc，兜底路径那两道门
    # 原先排在这后面，两条测试一直红
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise Unavailable("没装 pandoc（markdown 转 Word 靠它）。装：brew install pandoc",
                          code=1, data={"log": log})
    if reference_doc is None and style_layout is not None:
        reference_doc = style_reference_doc(pandoc, style, style_layout, zh, src_text)
        if reference_doc is None:
            log.append(["out", "  ⚠ 没装 python-docx，风格包声明的页面版式这次没套上"])
    if reference_doc is None:
        # 没有期刊模板就用标准稿件排版：英文 Times New Roman 双倍行距；中文宋体 1.5 倍、首行缩进
        reference_doc = default_reference_doc_zh(pandoc) if zh else default_reference_doc(pandoc)

    target = _export_target(rundir, out)
    import subprocess as _sp

    args: list[str]
    if citeproc:
        text = paper_src.read_text(encoding="utf-8")
        # export 直接 render，不走 check——正式结果用上面查过戳的那一份，别再读一遍
        pw_path = rundir.file(ARTIFACTS["power"])
        pw = PowerReport.load(pw_path) if pw_path.exists() else None
        corpus = LiteratureCorpus.load(lit_path)
        try:
            body = manuscript.render(text, res, pw, corpus, figures=figures_of(rundir),
                                     labels=labels_of(rundir),
                                     cite_mode="pandoc", fig_captions="alt",
                                     numeric_citations=_is_numeric_csl(csl_name))
        except manuscript.ManuscriptRejected as exc:
            raise Refused(str(exc), code=1, data={"log": log}) from exc
        body, title = split_title(body)
        pandoc_md = rundir.file("paper_pandoc.md")
        pandoc_md.write_text((f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\n---\n\n" if title else "")
                             + body, encoding="utf-8")
        bib = rundir.file("references.json")
        bib.write_text(json.dumps(manuscript.csl_json(text, corpus), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        args = [pandoc, pandoc_md.name, "-f", "markdown", "--citeproc",
                "--csl", str(csl), "--bibliography", bib.name]
        log.append(["out", f"  参考文献样式 {csl_name}（citeproc）"])
    else:
        # 文本版（兜底路径的两道门在上面查 pandoc 之前已经判完）：图注行并进替代文字，Word 里题注只出现一次
        folded = rundir.file("paper_pandoc.md")
        folded.write_text(manuscript.fold_captions(src.read_text(encoding="utf-8")), encoding="utf-8")
        args = [pandoc, folded.name, "-f", "gfm"]
        log.append(["out", "  ⚠ 没有源稿或语料，参考文献按文本版导出（不套引用样式）"])

    # cwd 设到运行目录：正文里的图片是 `fig_xxx.png` 相对引用，
    # pandoc 按当前目录解析——**在别处跑图会静默丢失**（Word 里只剩占位框）。
    # 输出路径必须转绝对——cwd 已切进运行目录，相对路径会拼到错的地方
    # `--columns=1`：pandoc 只给「有一行超过 --columns」的管道表定列宽、撑满版心，短表按内容收窄，
    # LibreOffice 转出来区间那一栏会折成两行（差序格局 demo 的表 2）。设成 1 让每张表都撑满
    r = _sp.run(
        args + ["--columns=1", "-o", str(target.resolve()), "--resource-path", str(rundir.path.resolve())]
        + (["--reference-doc", str(reference_doc.resolve())] if reference_doc else []),
        cwd=rundir.path, capture_output=True, text=True)
    if r.returncode != 0:
        raise Unavailable(f"pandoc 转换失败：{(r.stderr or r.stdout)[:400]}", code=1,
                          data={"log": log})

    if style_layout is not None and style_layout.layout.footnote_restart_each_page:
        _patch_footnote_restart(target)

    # 转出来的 Word 里图有没有真的嵌进去——**转换成功不等于图在**。
    # docx 是 zip，嵌入的图片在 word/media/ 下，数一下就知道。
    import zipfile as _zf

    n_ref = src.read_text(encoding="utf-8").count("![")
    with _zf.ZipFile(target) as z:
        n_embedded = sum(1 for n in z.namelist() if n.startswith("word/media/"))
    fig_note = f"正文引用 {n_ref} 张图，嵌入 {n_embedded} 张"

    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="paper", command="oss paper export",
        outcome="ok", artifacts=[target.name],
        note=f"导出 Word：{target.name}（{fig_note}）"))
    # 走到这里才算交付：docx 出来了，链条闭合。stage=done 让接手的模型知道不用再跑一遍。
    state_mod.set_stage(rundir.path, "done", "done")
    return result(f"{out}  →  {target}", {
        "run": str(rundir.path), "out": out, "path": str(target), "name": target.name,
        "fig_note": fig_note, "figures_missing": n_ref > n_embedded,
        "citeproc": citeproc, "csl": csl_name if citeproc else "",
        "style": style, "log": log,
    })
