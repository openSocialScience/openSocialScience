"""计划体检：数据里现成的东西，计划漏没漏用（docs/design/2026-09-23-plan-check.md）。

`oss run` 正式跑之前在分析帧上算，落 `plan_check.json`，由 route 开成 must 级处方。
**只看数据的形状，不算答案**：配对只看列名与类型；嵌套只看分组列与另一分类列的类别对应，
结局列的取值一个都不读。

跟 `survey_checks`、`descriptive_checks` 同一类文件：判据与定数在这里，开方在 route。
定数是初值：先实现、再在真数据上数误报、再请用户定。
"""
from __future__ import annotations

import re

import pandas as pd

from oss.core.analysis import parts_of
from oss.core.contracts import AnalysisPlan, DataProfile, NestedColumns, PairedColumns, PlanCheck
from oss.core.profiler import nests_in

#: 体检结果的文件名。**不进 `ARTIFACTS`**：`oss smoke` 要求那张表里每份产出都在，
#: 而它造假结果时不走 `oss run`。跟仿真的 `sim_state.json` 同一个做法
ARTIFACT = "plan_check.json"

#: 处方 id 的前缀：`oss run` 只拦这一族
PREFIX = "plan_"

BARE_MIN_PAIRS = 3      # 不带分隔符的配对（ro/rr），同一种标记至少这么多组才认——一组可能是巧合，成批出现才是命名习惯
MAX_LEVELS = 30         # 分组列与拿来比的列最多这么多类
MIN_COVERAGE = 0.9      # 两列同时有值的行占分组列有值行的比例；低于它多半是跳问造成的巧合
SAME_ROWS_MIN = 5       # 「同一批行」至少这么多行
_SHOW = 6               # 对应关系最多列几句
_GROUPABLE = ("categorical", "binary", "ordinal")

#: 配对标记：(这一边, 那一边)。只从左边找起，所以每对只认一次
MARKERS = (("o", "r"), ("orig", "rep"), ("original", "replication"),
           ("pre", "post"), ("before", "after"), ("t0", "t1"))
_SEPS = (".", "_", "")


def paired_columns(frame: pd.DataFrame) -> list[PairedColumns]:
    """整份数据里的配对列，跟计划无关。两列要同为数值或同为非数值。"""
    by_lower = {str(c).lower(): str(c) for c in frame.columns}
    with_sep: list[PairedColumns] = []
    bare: dict[str, list[PairedColumns]] = {}
    for col in map(str, frame.columns):
        low = col.lower()
        for a, b in MARKERS:
            for sep in _SEPS:
                tail = sep + a
                if not low.endswith(tail) or len(low) == len(tail):
                    continue
                other = by_lower.get((col[: -len(tail)] + sep + b).lower())
                if other is None:
                    continue
                if (pd.api.types.is_numeric_dtype(frame[col])
                        != pd.api.types.is_numeric_dtype(frame[other])):
                    break
                pair = PairedColumns(a=col, b=other, marker=f"{sep}{a}/{sep}{b}")
                (with_sep if sep else bare.setdefault(pair.marker, [])).append(pair)
                break
    return with_sep + [p for group in bare.values() if len(group) >= BARE_MIN_PAIRS for p in group]


def mentions(expr: str, col: str) -> bool:
    """筛选式里有没有用到这一列（`a == 1`、`` `a.b` == 1 ``）。"""
    if not expr:
        return False
    return (f"`{col}`" in expr
            or re.search(rf"(?<![\w.`]){re.escape(col)}(?![\w.`])", expr) is not None)


def group_columns(plan: AnalysisPlan, columns) -> list[str]:
    """计划拿来分组或筛样本的列：描述分组，加上主规格筛选式里出现的列。"""
    out = list(plan.description.groups) if plan.description else []
    out += [c for c in map(str, columns) if c not in out and mentions(plan.sample_filter, c)]
    return out


def _filled(col: pd.Series) -> pd.Series:
    """有值的行。**空串也是没填**——CGSS 的「请注明」一类文本列九成九是空串，
    当成一类就成了「每种教育程度都落在空白这一类里」。"""
    has = col.notna()
    if not pd.api.types.is_numeric_dtype(col):      # object 与 str 两种文本列都要管（.sav 读进来是 str）
        has &= col.astype(str).str.strip() != ""
    return has


def _label(v) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _grouped(both: pd.DataFrame, *, by: str, members: str) -> list[str]:
    """`members` 的类落进 `by` 的哪一类：「a、b → x（n 行）」。"""
    return [f"{'、'.join(sorted(map(_label, part[members].unique())))} → {_label(key)}（{len(part)} 行）"
            for key, part in both.groupby(by, observed=True)]


def _is_indicator(col: pd.Series) -> bool:
    """0/1 哑变量（或真假值）。"""
    if not (pd.api.types.is_numeric_dtype(col) or pd.api.types.is_bool_dtype(col)):
        return False
    return set(pd.unique(col.dropna())) <= {0, 1, True, False}


def _relation(both: pd.DataFrame, g: str, h: str) -> tuple[str, list[str] | None] | None:
    fine, coarse = nests_in(both, g, h), nests_in(both, h, g)
    if fine and coarse:
        # 一一对应 = 同一列换了个编码（urban 0/1 与「城市 / 农村」）：两种分法分出来的是同一批组，
        # 结果一模一样，没有要定的事。开处方只会逼人写一句废话——真数据里重编码的副本到处都是
        return None
    # 粗的那一边是 0/1 哑变量、又正好是细的那一边的函数：那是有人拿分组列算出来的重编码
    # （GSS 的 black = race==2、college = degree>=3；元科学的 us_lab.o = 实验国家是不是美国），
    # **不是另一种分法**。真数据上剩下的噪声几乎全是这一类，2026-09-23 用户定：不开
    if fine:
        return None if _is_indicator(both[h]) else ("finer", _grouped(both, by=h, members=g))
    if coarse:
        return None if _is_indicator(both[g]) else ("coarser", _grouped(both, by=g, members=h))
    n_g, n_h = both[g].value_counts(), both[h].value_counts()
    cells = both.groupby([g, h], observed=True).size()
    same = [f"{_label(a)} = {_label(b)}（{n} 行）" for (a, b), n in cells.items()
            if n >= SAME_ROWS_MIN and n == n_g[a] == n_h[b]]
    return ("same_rows", same) if same else None


def nested_columns(plan: AnalysisPlan, frame: pd.DataFrame, profile: DataProfile) -> list[NestedColumns]:
    """计划的分组列跟另一分类列：嵌套（粗细不同的两种分法），或有一类恰好是同一批行。

    拿来比的列只要画像判为分类型、2–30 类、不是结局或处理、不是计划自己的分组列。
    **结局列的取值一个都不读。**
    """
    groups = group_columns(plan, frame.columns)
    skip = {plan.outcome, plan.treatment, *groups}
    others = [v.name for v in profile.variables
              if v.semantic_type in _GROUPABLE and 2 <= v.n_unique <= MAX_LEVELS
              and v.name in frame.columns and v.name not in skip]
    out: list[NestedColumns] = []
    for g in groups:
        if g not in frame.columns or not 2 <= frame[g].nunique() <= MAX_LEVELS:
            continue
        has_g = _filled(frame[g])
        for h in others:
            both = frame.loc[has_g & _filled(frame[h]), [g, h]]
            if both.empty or len(both) < MIN_COVERAGE * has_g.sum():
                continue
            if (found := _relation(both, g, h)) is not None:
                out.append(NestedColumns(column=g, other=h, relation=found[0], mapping=found[1][:_SHOW]))
    return out


def uses(plan: AnalysisPlan, col: str) -> bool:
    """计划的分析字段里有没有真用到这一列。

    **写在 rationale 这类说明文字里不算**——门挂在「真算没算」这个事实上，不挂在声明上。
    """
    specs = [plan.outcome, plan.treatment, plan.running, *plan.controls, *plan.instruments,
             *plan.interactions, *plan.fixed_effects]
    if plan.description:
        specs += plan.description.groups
    filters = [plan.sample_filter]
    for r in plan.robustness:
        specs += [r.outcome or "", r.treatment or "", *(r.controls or []),
                  *(r.interactions or []), *(r.fixed_effects or [])]
        filters.append(r.sample_filter or "")
    return (col in {p for s in specs if s for p in parts_of(s)}
            or any(mentions(f, col) for f in filters))


def check(plan: AnalysisPlan, frame: pd.DataFrame, profile: DataProfile) -> PlanCheck:
    """`oss run` 正式跑之前的体检，结果落 `ARTIFACT`。"""
    return PlanCheck(pairs=paired_columns(frame), nested=nested_columns(plan, frame, profile))
