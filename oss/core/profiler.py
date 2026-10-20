"""数据画像。把一个数据文件变成 DataProfile。

全部是**确定性的程序判断**，不调模型。理由：这些事实（有多少行、是不是面板、
组内变异是多少）程序算得又快又准，让模型来猜只会引入不必要的错误和成本。
模型的用武之地在下游——把这些事实解释成"这份数据适合做什么研究"。
"""

from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Mapping
from typing import Literal

import numpy as np
import pandas as pd

from oss.core.contracts import (
    DataProfile,
    DataStructure,
    FileInfo,
    SemanticType,
    SurveySignals,
    VariableProfile,
)
from oss.core.loaders import LoadedTable, load_any

# 列名里出现这些词，很可能是权重 / 个体 ID / 时间
#: **强信号**：名字里带这些词元的，可以直接认定是权重列。
#:
#: `wt_` / `_wt` 曾经在这里，但它们被 `_name_hits` 的「带下划线走裸子串」分支
#: 匹配到了 `wt_kg`（体重）。**体重不是抽样权重**，而误判的后果是这一列
#: 被整个排除出绑定候选，使用者不会知道为什么。已降级为下面的弱信号。
_WEIGHT_HINTS = ("weight", "权重", "pweight")

#: **弱信号**：以这些开头的**可能**是权重，但光看名字不够。
#:
#: 真实调查数据的权重列名往往是一整个不可切分的词元——GSS 的 `wtssall`、
#: `wtssps`、`wtssnr`，CHARLS 的 `wtind`。按词元匹配一条都命中不了，
#: 于是抽样权重被当成实质变量的候选列（GSS 真跑时 `wtssps` 就成了
#: 「本人职业地位」的候选，PLAN.md 九之一 ⑦）。
#:
#: 但光看名字不能认定——`wtp` 可能是 willingness-to-pay，`wt_kg` 是体重。
#: 所以弱信号必须配上取值检验才算数。
#: `rswt` / `fswt` 是 CFPS 的个人 / 家庭权重（`rswt_natcs16`、`fswt_nat`），同样要过取值检验。
_WEIGHT_PREFIXES = ("wt", "wgt", "swgt", "pw", "rswt", "fswt")
_WEIGHT_TOKENS = ("wt", "wgt", "swgt", "pw")
#: 调查层（设计稿 3.1）：分层、初级抽样单位、插补标记的名字线索。**认到的都只进候选、标疑似**——
#: 列级谨慎，不确认就不当真。`层` 不单独当线索：「主观阶层」里也有这个字
#:
#: 分强弱两档：强信号能单独让 `survey.likely` 为真；弱信号只进候选、不单独算数。区县 × 年的面板、
#: 只带 `imp` 的列、取值恒定的标记，单凭它们就拒计划是误报（审查第 1、2 批）
_STRATA_HINTS = ("strata", "stratum", "vstrat", "分层", "抽样层")
#: 弱信号：CFPS 的分层变量叫 `subpopulation`，这个名字在别的数据里也常见
_STRATA_WEAK_HINTS = ("subpopulation",)
_PSU_HINTS = ("psu", "vpsu", "初级抽样单位", "抽样单位")
#: 弱信号：中国的调查常以区县为初级抽样单位（CGSS 2017 的 `county`）。名字命中还要过取值形态
_PSU_WEAK_HINTS = ("county", "区县")
_IMPUTE_HINTS = ("imp", "imputed", "impute", "插补")
#: 插补标记的强线索：光一个 `imp` 词元也可能是别的缩写，只算弱信号
_IMPUTE_STRONG_HINTS = ("imputed", "impute", "插补")
#: 个体标识的线索词。**只用于语义判定**（判一列是不是 ID）。
#: 保持窄——`province` 在多数数据里是分组变量而不是 ID。
_UNIT_HINTS = ("id", "pid", "hid", "fid", "code", "个体", "编号", "样本")

#: **面板个体**的线索词，比上面宽。只用于结构判定，不影响语义类型。
#:
#: 社科面板的个体标识常常是名字而不是编号——国家-年、省-年、企业-年
#: 是这个领域最常见的形态之一（QoG 那份真数据上踩到的：`country` 认不出来，
#: 190 国 × 79 年的面板被判成了重复截面）。
#:
#: **但光靠名字不够。**`region` 在 GSS 里是 4 个普查区，是分组不是个体。
#: 所以还要过一道硬判据：**个体 + 时间必须几乎唯一确定一行**——
#: 那就是「面板」的定义。
_PANEL_UNIT_HINTS = _UNIT_HINTS + (
    "country", "nation", "province", "state", "city", "county", "district",
    "region", "prefecture", "municipality", "firm", "school", "hospital", "unit",
    "国家", "省份", "城市", "县", "地区", "学校", "企业", "单位",
)
_TIME_HINTS = ("year", "wave", "time", "period", "date", "年份", "年度", "月份")
_SENSITIVE_HINTS = ("name", "phone", "mobile", "idcard", "id_card", "address", "email",
                    "姓名", "电话", "手机", "身份证", "住址", "邮箱")


def profile_table(table: LoadedTable, *, max_unique_for_labels: int = 50) -> DataProfile:
    frame = table.frame
    profile = DataProfile(
        profile_id=f"prof_{table.sha256[:8]}",
        file=FileInfo(
            path=str(table.path),
            format=table.fmt,
            n_rows=int(len(frame)),
            n_cols=int(frame.shape[1]),
            size_bytes=table.size_bytes,
            sha256=table.sha256,
        ),
    )

    profile.variables = [
        _profile_column(frame[col], col, table, max_unique_for_labels) for col in frame.columns
    ]
    profile.structure = detect_structure(frame, profile.variables)
    profile.structure.nesting_levels = detect_nesting(frame, profile.variables)
    _quality(frame, profile, table.value_labels)
    profile.weight_column = _detect_weight(profile.variables)
    profile.survey = _detect_survey(frame, profile.variables)
    if profile.survey.likely:
        profile.warnings.append(_survey_warning(profile.survey))
    profile.sensitive_columns = _sensitive_columns(profile.variables)
    profile.warnings.extend(table.warnings)

    if (wide := _wide_panel_hint(frame)) is not None:
        profile.warnings.append(wide)

    # 判成面板、但追踪性核验没做成——把这个洞说出来，别让它静悄悄过去。
    # 见 `_tracks_anything` 的「残余风险」。
    if profile.structure.kind.startswith("panel") and (
        "核验未能进行" in profile.structure.detection_note
        or "核验做不了" in profile.structure.detection_note
    ):
        profile.warnings.append(
            "判成面板，但**没能核验编号是否真的在追踪同一个个体**（实质列或多期个体太少）。"
            "如果编号是每期重编的序号，上个体固定效应会把不同的人当成同一个人——"
            "请自行确认后再用个体固定效应"
        )

    # 面板才谈得上组内变异，所以要在结构判定之后补算
    if profile.structure.unit_id:
        _annotate_within_variation(frame, profile)

    return profile


# --------------------------------------------------------------------- 列


def _profile_column(
    series: pd.Series, name: str, table: LoadedTable, max_unique: int
) -> VariableProfile:
    n = len(series)
    n_missing = int(series.isna().sum())
    non_null = series.dropna()
    n_unique = int(non_null.nunique())

    value_labels = table.value_labels.get(name, {})
    label = table.variable_labels.get(name, "")
    semantic = _semantic_type(name, non_null, n_unique, bool(value_labels))

    distribution: dict[str, float | int | str] = {"n_unique": n_unique}
    if semantic in ("continuous", "ordinal") and pd.api.types.is_numeric_dtype(non_null):
        described = non_null.astype(float)
        if len(described):
            distribution.update(
                {
                    "min": float(described.min()),
                    "p25": float(described.quantile(0.25)),
                    "median": float(described.median()),
                    "p75": float(described.quantile(0.75)),
                    "max": float(described.max()),
                    "mean": float(described.mean()),
                    "std": float(described.std()) if len(described) > 1 else 0.0,
                }
            )
    elif n_unique and n_unique <= max_unique:
        top = non_null.value_counts().head(5)
        distribution["top_values"] = "; ".join(f"{k}={v}" for k, v in top.items())

    return VariableProfile(
        name=name,
        label=label,
        dtype=str(series.dtype),
        semantic_type=semantic,
        n_missing=n_missing,
        missing_rate=round(n_missing / n, 4) if n else 0.0,
        missing_pattern=_missing_pattern(n_missing, n),
        distribution=distribution,
        value_labels=value_labels if len(value_labels) <= max_unique else {},
        n_unique=n_unique,
    )


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z一-鿿]+", name.lower()) if t]


def _weak_weight(name: str, non_null: pd.Series, n_unique: int) -> bool:
    """名字只有弱信号时，用取值来定夺。

    抽样权重的取值特征很硬：**全为正、连续、没有负数或零**。
    `wtp`（willingness to pay）会有零，`wt_kg` 之类也不连续到这个程度。

    两条都满足才认——**宁可漏认，不可错认**：把实质变量误判成权重，
    它就会从绑定候选里被整个排除掉，而使用者不会知道为什么。
    """
    lowered = name.lower()
    hit = any(lowered.startswith(px) for px in _WEIGHT_PREFIXES) or any(
        t in _WEIGHT_TOKENS for t in _tokens(name)
    )
    if not hit:
        return False
    return _weight_values_ok(non_null, n_unique)


def _weight_values_ok(non_null: pd.Series, n_unique: int) -> bool:
    """抽样权重的取值检验：数值、每个非缺失值都大于 0、至少 10 个不同的取值。

    `_weak_weight` 与调查信号的权重候选共用这一把尺子：名字带 weight 的 `overweight`（0/1）、
    带零的「权重」列都过不了，就不会单凭名字让计划被调查层的门拒掉。
    """
    if not pd.api.types.is_numeric_dtype(non_null) or len(non_null) == 0:
        return False
    if n_unique < 10:
        return False  # 权重是连续的；取值没几个的多半是别的东西
    return bool((non_null > 0).all())


#: 中日韩统一表意文字。用来判断一条 hint 该按词元匹配还是按子串匹配。
_CJK = re.compile(r"[一-鿿]")


def _name_hits(name: str, hints: tuple[str, ...]) -> bool:
    """名字里有没有这些线索词。

    **英文按词元匹配，中文按子串匹配。**

    英文不能用裸子串：`"id" in "resident_income"` 为真，于是收入被当成个体编号。
    绑定阶段一旦把结果变量误判成 ID，整个研究设计就废了。

    **但中文必须用子串**——中文没有分隔符，`抽样权重` 切出来是一整个词元，
    跟 hint `权重` 永远不相等；而裸子串那条分支要求 hint 长度 ≥ 4，
    中文词只有两三个字，也够不着。结果是 `_UNIT_HINTS` / `_TIME_HINTS` /
    `_WEIGHT_HINTS` 里所有中文线索词**一条都不会命中**。

    这对一个面向中文社科数据的项目是要害：中文列名的个体标识、时间列、
    权重列全都识别不出来，而且不报错。发现于修「权重列认不出来」时的顺带核查。
    """
    toks = _tokens(name)
    lowered = name.lower()
    for hint in hints:
        if _CJK.search(hint):
            if hint in name:      # 中文：子串
                return True
            continue
        if any(t == hint for t in toks):
            return True
        if len(hint) >= 4 and hint in lowered:
            return True
        if "_" in hint and hint in lowered:
            return True
    return False


def _looks_like_id(name: str) -> bool:
    toks = _tokens(name)
    return _name_hits(name, _UNIT_HINTS) or any(
        t.endswith("id") and len(t) <= 6 for t in toks
    )


def _is_time_like(name: str, non_null: pd.Series, n_unique: int) -> bool:
    """名字像时间还不够，取值也得像。

    `edu_years`（受教育年限，取值 6–16）的名字里有 "year"，但它不是时间索引。
    误判成时间列会让结构判定把它当成面板的时间维度，后面整条识别策略都建立在
    错误的面板结构上。所以要求：单词元的名字直接命中，或者数值落在合理的年份/
    轮次区间内。
    """
    if n_unique > 200:
        return False
    toks = _tokens(name)
    if len(toks) == 1 and toks[0] in _TIME_HINTS:
        return True
    if not _name_hits(name, _TIME_HINTS):
        return False
    if not pd.api.types.is_numeric_dtype(non_null):
        return True  # 形如 "survey_date" 的字符串日期
    lo, hi = float(non_null.min()), float(non_null.max())
    looks_like_calendar_year = 1800 <= lo <= 2100 and 1800 <= hi <= 2100
    looks_like_wave = 0 <= lo <= 30 and 0 <= hi <= 30 and n_unique <= 30 and "wave" in name.lower()
    return looks_like_calendar_year or looks_like_wave


def _semantic_type(name: str, non_null: pd.Series, n_unique: int, has_labels: bool) -> SemanticType:
    if n_unique == 0:
        return "categorical"  # 整列缺失，语义无从谈起，交给质量报告去告警
    if _name_hits(name, _WEIGHT_HINTS) or _weak_weight(name, non_null, n_unique):
        return "weight"
    if pd.api.types.is_datetime64_any_dtype(non_null):
        return "date"
    if _is_time_like(name, non_null, n_unique):
        return "date"

    # 名字像 ID 且取值大量重复不了的，先判 ID——面板里个体编号的唯一值远少于行数，
    # 只靠"唯一值接近行数"这条规则会把它误判成连续变量。
    if _looks_like_id(name) and n_unique > 1:
        return "identifier"

    if pd.api.types.is_numeric_dtype(non_null):
        if n_unique <= 2:
            return "binary"
        if has_labels or n_unique <= 10:
            return "ordinal"
        if n_unique > 0.95 * max(len(non_null), 1) and pd.api.types.is_integer_dtype(non_null):
            return "identifier"
        return "continuous"
    if n_unique <= 20:
        return "categorical"
    avg_len = float(non_null.astype(str).str.len().mean()) if len(non_null) else 0.0
    return "text" if avg_len > 40 else "categorical"


def _missing_pattern(n_missing: int, n: int) -> str:
    if not n or not n_missing:
        return "无缺失"
    rate = n_missing / n
    if rate > 0.5:
        return "缺失过半，慎用"
    if rate > 0.2:
        return "缺失较多，需检查是否随机"
    return "少量缺失"


# --------------------------------------------------------------------- 结构


def _tracks_anything(
    frame: pd.DataFrame, unit: str, time: str, *, min_units: int = 10, min_cols: int = 3
) -> tuple[bool, str]:
    """这个「个体标识」到底在追踪一个东西，还是只是个被回收的序号？

    **判据**：真面板里，至少有一列在个体内是不变的——性别、出生年、种族。
    编号被回收时（同一个号每期指向不同的人），**没有任何一列在「个体」内不变**。

    这条完全不依赖列名，也不依赖语言，所以中英文数据一视同仁。

    **不要用「编号每期从 1 重编」当判据**——真实的平衡面板里编号也是每期 1..N，
    这个信号区分不了两者。区分得了的是「它到底追踪住了什么没有」。

    **两个不下结论的条件**，都是「证据不足」而不是「判它是面板」：

    - 多期个体少于 `min_units`：样本不够
    - 实质列少于 `min_cols`：**「没有一列稳定」只在本来就有好几列可能稳定时才说明问题**。
      只有一列实质变量（比如整张表就是 id / year / income），
      那一列碰巧每行都不同是完全正常的，不能据此判它是回收编号。

    **残余风险**：一份只有两三列、且编号被回收的面板，这里判不出来。
    这种情况会在 `warnings` 里注明「面板判定未经追踪性核验」，让人自己看一眼。
    """
    per_unit = frame.groupby(unit)[time].nunique()
    multi = per_unit[per_unit > 1].index
    if len(multi) < min_units:
        return True, ""
    candidates = [c for c in frame.columns if c not in (unit, time)]
    if len(candidates) < min_cols:
        return True, f"只有 {len(candidates)} 列实质变量，追踪性核验做不了"

    sub = frame[frame[unit].isin(multi)]
    best_col, best_share = "", 0.0
    for col in candidates:
        share = float((sub.groupby(unit)[col].nunique(dropna=True) <= 1).mean())
        if share > best_share:
            best_col, best_share = col, share

    if best_share >= 0.5:
        return True, f"「{best_col}」在 {best_share:.0%} 的个体内保持不变"
    return False, (
        f"没有任何一列在个体内稳定（最稳的是「{best_col}」，也只有 {best_share:.0%}）"
        f"——说明「{unit}」不是在追踪同一个人，而是每期被回收重编的序号"
    )


def detect_structure(frame: pd.DataFrame, variables: list[VariableProfile]) -> DataStructure:
    """判断截面 / 面板 / 重复截面 / 时序。

    识别策略的可行性完全依赖这个判断：没有面板就不能做双重差分，
    这是后面"方法配不配数据"的硬依据，所以宁可保守也不要误判成面板。

    **两条防线，防的是反方向的两种错**（PLAN.md 九之一 ①，GSS 真跑暴露）：

    ① `repeated` 太低 → 个体基本只出现一次，是重复截面不是面板。
       防的是「一次性抽样被当成面板」。

    ② `repeated` 很高、但**没有任何一列在个体内稳定** → 编号被回收了。
       防的是「每期重编的序号被当成个体标识」——GSS 的 `id` 就是这样，
       `id=1` 在 1972 年是 23 岁女性、1973 年是 54 岁男性。
       这一侧的漏判后果最重：会授权个体固定效应，
       把互不相识的人当成同一个人做组内比较，**系数是垃圾但零报错**。
    """
    # **个体与时间要成对地定，不能各定各的。**
    # 用个体才能验证「这一列是不是真的时间轴」（时间轴必须在个体内变化），
    # 而用时间才能验证「这一列是不是真的个体」（个体+时间几乎唯一确定一行）。
    # 单向定会卡死：QoG 那份数据里 `dem_year` 唯一值最少而被先选为时间列，
    # 它在国家内是常数，于是 country 又验不过——一份面板被判成了重复截面。
    time, unit = None, None
    for cand in _time_candidates(variables):
        u = _panel_unit(frame, variables, cand)
        if u is not None:
            time, unit = cand, u
            break
    if time is None:
        time = _pick(variables, _TIME_HINTS, {"date"}, prefer="fewest_unique")
        unit = _panel_unit(frame, variables, time)

    if unit and time and unit != time:
        n_units = int(frame[unit].nunique())
        n_periods = int(frame[time].nunique())
        if n_units > 1 and n_periods > 1:
            # 同一个体在多期出现才是真面板；否则只是重复截面
            per_unit = frame.groupby(unit)[time].nunique()
            repeated = float((per_unit > 1).mean())
            if repeated < 0.2:
                return DataStructure(
                    kind="repeated_cross_section",
                    unit_id=None,
                    time_id=time,
                    n_periods=n_periods,
                    detection_note=f"仅 {repeated:.0%} 的个体出现在多期，判为重复截面而非面板",
                )

            tracks, evidence = _tracks_anything(frame, unit, time)
            if not tracks:
                return DataStructure(
                    kind="repeated_cross_section",
                    unit_id=None,
                    time_id=time,
                    n_periods=n_periods,
                    detection_note=(
                        f"「{unit}」有 {repeated:.0%} 跨期重复，看着像面板，但{evidence}。"
                        f"判为重复截面，**不提供个体标识**——上个体固定效应会把不同的人"
                        f"当成同一个人"
                    ),
                )

            expected = n_units * n_periods
            balance = round(len(frame) / expected, 4) if expected else None
            note = f"依据列「{unit}」与「{time}」判定；{repeated:.0%} 的个体跨期出现"
            note += f"；{evidence}" if evidence else "；追踪性核验未能进行"
            return DataStructure(
                kind="panel_balanced" if balance and balance >= 0.98 else "panel_unbalanced",
                unit_id=unit,
                time_id=time,
                n_units=n_units,
                n_periods=n_periods,
                balance_ratio=balance,
                detection_note=note,
            )

    if time and not unit:
        n_periods = int(frame[time].nunique())
        per_period = len(frame) / max(n_periods, 1)
        # 时间序列每期约一行。每期几十上百行的是重复截面——
        # 这个分支原先完全没有检查每期行数，于是 75,699 行 / 35 期被判成了时序。
        if per_period > 1.5:
            return DataStructure(
                kind="repeated_cross_section",
                time_id=time,
                n_periods=n_periods,
                detection_note=(
                    f"有时间列「{time}」但没有个体标识，且每期约 {per_period:.0f} 行"
                    f"（时间序列每期约 1 行）——判为重复截面"
                ),
            )
        return DataStructure(
            kind="time_series",
            time_id=time,
            n_periods=n_periods,
            detection_note=f"只找到时间列「{time}」，每期约 {per_period:.1f} 行",
        )

    return DataStructure(
        kind="cross_section",
        unit_id=unit,
        n_units=int(len(frame)),
        detection_note="未找到可用的时间维度，按截面处理",
    )


def _time_candidates(variables: list[VariableProfile]) -> list[str]:
    """所有可能的时间列，按「唯一值少的优先」排。

    要排成一串而不是只取第一个，是因为社科面板里常常带着几列
    「该单位的某个事件发生在哪一年」（`dem_year` = 该国哪一年民主化）——
    它名字像年份、唯一值又比真年份少，**会把真时间列挤掉**。
    """
    named = [v for v in variables if _name_hits(v.name, _TIME_HINTS)]
    typed = [v for v in variables if v.semantic_type == "date" and v not in named]
    return [v.name for v in sorted(named + typed, key=lambda v: v.n_unique)]


#: 「这两列构成面板」的两条门槛：一格一行的比例、在多期出现的个体占比。
#: `_panel_unit` 挑个体标识用它，`analysis._unit_time` 认单位与时间列也用它——**只有这一处**。
_PANEL_CELL_MIN, _PANEL_REPEAT_MIN = 0.8, 0.5


def panel_key_stats(frame: pd.DataFrame, unit: str, time: str) -> tuple[float, float]:
    """`(一格一行的比例, 在多期出现的个体占比)`——「面板」这个词的全部内容。"""
    cells = frame.groupby([unit, time], dropna=False).size()
    per_unit = frame.groupby(unit)[time].nunique(dropna=False)
    return float((cells == 1).mean()), float((per_unit > 1).mean())


def is_panel_key(frame: pd.DataFrame, unit: str, time: str) -> bool:
    """这两列是不是这份数据的面板键。**判据跟画像挑个体标识时用的是同一条**（`_panel_unit`）。

    调用方给的是**列名**（计划里写的 unit / time、或两个固定效应），不是画像挑出来的——
    所以这里只判「这一对撑不撑得起面板」，不管它们叫什么名字。
    """
    if not unit or not time or unit == time:
        return False
    if unit not in frame.columns or time not in frame.columns:
        return False
    ratio, repeated = panel_key_stats(frame, unit, time)
    return ratio >= _PANEL_CELL_MIN and repeated >= _PANEL_REPEAT_MIN


def _panel_unit(
    frame: pd.DataFrame, variables: list[VariableProfile], time: str | None
) -> str | None:
    """挑面板的个体标识。**判据是「个体 + 时间几乎唯一确定一行」。**

    光看名字不够：`country` 在国家-年面板里是个体，
    而 `region` 在 GSS 里是 4 个普查区、每个区每年几百人——是分组不是个体。
    两者名字都像，**只有这条判据分得开**，而它正是「面板」的定义。

    还要第二条：**个体必须在多期出现**。否则一列「每行都不同」的连续变量
    （收入）也满足「一格一行」，会被当成个体标识——
    而它每个取值只出现在一期里，根本不构成面板。

    名字命中的优先于只靠语义类型进来的：`person_id` 该赢过 `income`。
    """
    cands = [
        v for v in variables
        if (_name_hits(v.name, _PANEL_UNIT_HINTS) or v.semantic_type == "identifier")
        and v.name != time and v.n_unique > 1 and v.name in frame.columns
    ]
    if not cands or not time or time not in frame.columns:
        return None
    best, best_score = None, ()
    for v in cands:
        ratio, repeated = panel_key_stats(frame, v.name, time)
        # 一格一行：GSS 的 region×year 每格几百人，被这条挡住
        # 多期出现：「每行都不同」的收入列被这条挡住
        if ratio < _PANEL_CELL_MIN or repeated < _PANEL_REPEAT_MIN:
            continue
        score = (1 if _name_hits(v.name, _PANEL_UNIT_HINTS) else 0, repeated, ratio)
        if score > best_score:
            best, best_score = v.name, score
    return best


def _pick(
    variables: list[VariableProfile],
    hints: tuple[str, ...],
    types: set[str],
    must_vary_within: tuple[pd.DataFrame, str | None] | None = None,
    *,
    prefer: Literal["fewest_unique", "most_unique"],
) -> str | None:
    """先按列名命中，再按语义类型兜底。命中多个时按 `prefer` 挑。

    **`prefer` 不是可选的。**旧实现的 docstring 写着「取唯一值最少的（时间）/
    最多的（ID）」，代码却是 `named[0].name`——直接取第一个。数据里同时有
    `province_code` 和 `person_id` 时，谁在列序上靠前就选谁当个体标识，
    **选错了整个面板判定跟着错**（阶段 1 抢救时修）。

    时间列取唯一值**最少**的：年份就那么几个。
    个体标识取唯一值**最多**的：个体编号远多于省份编号。

    **`must_vary_within` 是为时间列加的一道硬约束：时间轴必须在个体内变化。**
    社科面板里常常带着几列「该单位的某个事件发生在哪一年」
    （`dem_year` = 该国哪一年民主化）。这类列名字像年份、唯一值又比真年份少，
    于是「取唯一值最少」这条偏好会**选中它**——
    QoG 那份真数据上就这么把一份 190 国 × 79 年的面板判成了重复截面。
    而它在每个国家内是常数：**它是属性，不是时间轴**。
    """
    key = (lambda v: v.n_unique) if prefer == "fewest_unique" else (lambda v: -v.n_unique)

    def ok(v: VariableProfile) -> bool:
        if must_vary_within is None:
            return True
        frame, unit = must_vary_within
        if not unit or unit not in frame.columns or v.name not in frame.columns:
            return True
        try:
            return bool((frame.groupby(unit)[v.name].nunique(dropna=True) > 1).any())
        except Exception:  # noqa: BLE001 —— 判不了就不拦，宁可保守
            return True

    named = sorted((v for v in variables
                    if _name_hits(v.name, hints) and ok(v)), key=key)
    if named:
        return named[0].name
    typed = sorted((v for v in variables if v.semantic_type in types and ok(v)), key=key)
    return typed[0].name if typed else None


def _annotate_within_variation(frame: pd.DataFrame, profile: DataProfile) -> None:
    """组内变异为 0 的变量会被固定效应吃掉——这是识别策略审计要用的事实。"""
    unit = profile.structure.unit_id
    if unit is None or unit not in frame.columns:
        return
    for var in profile.variables:
        if var.semantic_type not in ("continuous", "ordinal", "binary"):
            continue
        if var.name == unit or var.name not in frame.columns:
            continue
        if var.n_unique == 0:  # 整列缺失，算组内变异没有意义且会触发 numpy 警告
            continue
        try:
            within = frame.groupby(unit)[var.name].transform(lambda s: s - s.mean())
            var.within_variation = round(float(np.nanstd(within.to_numpy(dtype=float))), 6)
        except (TypeError, ValueError):
            continue


# --------------------------------------------------------------------- 嵌套


def nests_in(frame: pd.DataFrame, fine: str, coarse: str) -> bool:
    """`fine` 的每一个取值都只对应 `coarse` 的一个取值（在两列都有值的行上判）。

    `detect_nesting` 与计划体检（`plan_checks`）共用这一个判据，不各写一份。
    """
    if fine == coarse:
        return False
    pair = frame[[fine, coarse]].dropna()
    if pair.empty:
        return False
    return bool((pair.groupby(fine)[coarse].nunique() == 1).all())


def detect_nesting(
    frame: pd.DataFrame, variables: list[VariableProfile], *, max_levels: int = 4
) -> list[str]:
    """检出嵌套层级，从细到粗，例如 ['person_id', 'household_id', 'county'].

    **旧实现从来没填过这个字段**——契约里定义了，profiler 里一次都没赋值
    （阶段 1 抢救时补）。它是两件事的前提：

    - **多层模型**：个人嵌套在家庭、家庭嵌套在社区，才谈得上随机截距
    - **层次匹配检查**：「城乡收入差距」是地区层面的分布性质，
      个体层面的数据构造不出来。有了层级表，这个判断才有依据

    判据：A 嵌套于 B，当且仅当 A 的每一个取值都只对应 B 的一个取值。
    个体编号能唯一决定他所在的县，反过来不行——所以 person 嵌套于 county。
    """
    n_rows = len(frame)
    candidates = [
        v.name
        for v in variables
        if v.semantic_type in ("identifier", "categorical")
        and v.n_unique > 1
        and v.name in frame.columns
        # 逐行唯一的列会「嵌套于」一切——每个取值只出现一次，
        # 自然只对应一个上层取值。所以这类列必须名字也像 ID 才算数：
        # 截面里的 person_id 是合法的最细层，恰好全唯一的 income 不是。
        and (v.n_unique < n_rows or _looks_like_id(v.name))
    ]
    if len(candidates) < 2:
        return []

    # 唯一值多的在细粒度一侧
    candidates.sort(key=lambda c: -int(frame[c].nunique(dropna=True)))
    candidates = candidates[:8]  # 列很多时不做全量两两比对

    levels: list[str] = []
    for name in candidates:
        if not levels:
            levels.append(name)
            continue
        if nests_in(frame, levels[-1], name):
            levels.append(name)
        if len(levels) >= max_levels:
            break

    return levels if len(levels) >= 2 else []


# --------------------------------------------------------------------- 质量


#: 调查数据里常见的缺失值约定。**在文件里它们就是普通数字**：
#: 99 = 拒答、98 = 不适用、-99 = 缺失、9 = 不知道（李克特量表）。
#: 算进平均数就全错，而且**不会报任何错**——这是用户带自己的调查数据时几乎必踩的坑。
#:
#: -1 是国内调查数据里最常用的负缺失码之一（-1 = 不适用），原先不在这张表里，
#: 陷阱集坑 3 的 -1 变体因此是**诚实的 0**。
_MISSING_CODES = (-9999, -999, -99, -98, -97, -9, -8, -1, 8, 9, 97, 98, 99, 999, 9998, 9999)

#: -1 一进码表就必然误报的那一类：取值只有 -1 与 1 的 **±1 效应编码**（男 = 1、女 = -1，
#: 或对比编码）。-1 在最下端、空档正好 2、量也够，上面三条判据全过。
#: 豁免：-1 这个码之外还剩不到这么多个不同取值时不报。
#:
#: **只挂在 -1 上，不做成通用规则**——别的码离 0 与 1 都远，撞不上这一类编码，
#: 而通用化会把「0/1 哑变量加一个 -9」这种该报的也放过。2026-09-16 在 139 个真实
#: 数据文件、14532 个数值列上对拍：加 -1 一列都不多报，这条豁免也一列都没压掉。
_NEG1_MIN_OTHER_VALUES = 3

#: 取值标签里出现这些说法，这个码就是缺失值代码——**不是推断，是文件的原话**。
#:
#: `.sav` / `.dta` 里每个码都带一句人话：`9999997 = 不适用`、`98 = 不知道`、
#: `8 = 无法选择`。**答案一直躺在文件里**，而这份列表出现之前，探测器在旁边
#: 靠分布形状猜——猜中了小数（8、98、99 恰好在候选名单里），猜不中大数：
#: CGSS2017 真跑时，个人年收入的 9999997/8/9 共 681 人一个没抓到，
#: 画像报出来的年收入均值是 **58 万元**（剔掉代码后是 4.1 万）。
#:
#: 收窄到确凿的说法，**宁可漏，不可把真实选项误判成缺失**：
#: 李克特量表里的「说不上同意不同意」是实质选项，不在此列。
_MISSING_LABEL_PHRASES = (
    "不知道", "不清楚", "说不清", "拒绝回答", "拒答", "不予回答", "不愿回答",
    "无法回答", "无法选择", "未回答", "无回答", "未作答", "不作答",
    "不适用", "缺失", "跳答", "跳过", "未问", "无效",
    "refused to answer", "refuse to answer", "don't know", "dont know",
    "do not know", "doesn't know", "no answer", "not answered", "refus",
    "not applicable", "inapplicable", "no response", "missing", "skipped",
    "not asked", "can't choose", "cannot choose", "cant choose",
    "declined", "uncodeable", "no opinion given",
)
#: 短到不能做子串匹配的写法（`na` 会命中 national、name），只认整段相等。
_MISSING_LABEL_EXACT = frozenset({"na", "n/a", "nap", "iap", "dk", "rf", ".", "-", "--"})

#: 标点、空格、数字——判断「掐掉那句话之后还剩不剩实质内容」时，这些不算内容。
_LABEL_NOISE = re.compile(r"[\s\d\W_]+", re.UNICODE)

#: 取值标签的分段符号：括号里的括注、斜杠或顿号分开的并列项，各算一段。
#:
#: CGSS 的真实写法是「不适用（上周没有工作）」——**那句话本身是完整的一段，
#: 后面跟的是括注**；整串看剩下六个实字，会被判成实质选项（评测第 1 步·陷阱集坑 4）。
#: 而「不知道什么是上网」是一整句话、切不开——这两者的区别正是分段符号。
_LABEL_SEP = re.compile(r"[（）()【】\[\]{}、，,;；:：/／|｜]+")


def _phrase_only(text: str) -> bool:
    """这一段掐掉命中的说法之后，还剩不剩实质内容。

    剩的是标点、编号、词尾（`refused` 掐掉 `refus` 剩 `ed`）就是缺失码；
    剩下「什么是上网」这种实词，那就是一个答案。
    """
    t = text.strip()
    if not t:
        return False
    hit = [p for p in _MISSING_LABEL_PHRASES if p in t]
    if not hit:
        return False
    for phrase in sorted(hit, key=len, reverse=True):   # 长的先掐，免得短的把它拆了
        t = t.replace(phrase, " ")
    return len(_LABEL_NOISE.sub("", t)) <= 3


def _label_says_missing(label: str) -> bool:
    """这句取值标签是不是在说「这不是答案」。

    **光看包不包含那句话是不够的。**CGSS2017 里有一个选项叫
    「不知道什么是上网」——那是**实质回答**，不是拒答，而它含着「不知道」。
    把它抹成缺失，等于把一整类受访者悄悄删掉。

    所以先看整串，再**逐段看**（括注、并列项各算一段）：
    「不适用（上周没有工作）」的第一段就是那句话，认得出；
    「不知道什么是上网」切不开，掐掉「不知道」还剩实词，照旧判成答案。
    """
    t = str(label).strip().lower()
    if not t:
        return False
    if t in _MISSING_LABEL_EXACT:   # 只在整串上认：`na` 做子串会命中 national、Sodium, Na
        return True
    return any(_phrase_only(seg) for seg in (t, *_LABEL_SEP.split(t)))


def _fmt_code(value: float) -> str:
    """把码写成字符串。**不能用 `:g`**——它只留 6 位有效数字，
    CGSS 收入的 9999997/9999998/9999999 会一起塌成 `1e+07`，
    当字典键用就是三个码并成一个，两个码连同它们的人数一起消失。
    """
    v = float(value)
    return str(int(v)) if v.is_integer() else repr(v)


def labeled_missing(value_labels: Mapping[object, object] | None) -> dict[float, str]:
    """取值标签自己写明是缺失的那些码：`{取值: 标签原文}`。"""
    out: dict[float, str] = {}
    for code, label in (value_labels or {}).items():
        try:
            num = float(code)
        except (TypeError, ValueError):
            continue                    # 字符串码（"a"/"b"）不参与数值污染
        if _label_says_missing(label):
            out[num] = str(label)
    return out


#: 一段哨兵码至多几个不同取值。再长就不是一小撮代码，而是真数据的一片——
#: 0–100 的百分比连成一整段，它跟一个 999 比也「隔得远」，不设这条会把整列报成缺失。
_MAX_CODE_BLOCK = 5


def _code_blocks(counts: pd.Series, labelled: set[float], floor: float) -> list[list[float]]:
    """疑似缺失码的「段」：连号的取值并成一段，**整段**再跟其余取值比空档。

    CGSS 里职业编码的 9997 / 9998 / 9999 是挨着出现的，逐个跟其余取值比永远只差 1，
    「隔 2 以上」那条把三个一起放过（评测第 1 步·陷阱集坑 3）。段里只要有一个
    在 `_MISSING_CODES` 里、而且有量，整段就一起判——**9997 本身不在码表里，
    是靠挨着 9998 被带出来的**；1–5 量表后面的 8 与 9 同理，从前两个都漏。

    并段是**只准往严处收的加法**，三道闸一个都不能少——真实数据上量过，
    少一道就把整类实质取值报成缺失（`runs/r20260209_compulsory_edu`：政体类型
    取 1–5 与 9–12，占四成行的第 9 类差点被报成缺失码）：

    1. 至多 `_MAX_CODE_BLOCK` 个不同取值：再长就是真数据的一片；
    2. **段最外面那个码必须在 `_MISSING_CODES` 里**——哨兵岛的锚是 9999、99、−9
       这类约定码，往里捎带的才是 9997；最外面是 12、10 这种，那是类别号；
    3. **空档要比段本身宽**：岛离数据得比岛自己还远。9997–9999 离真码 368，
       而 {5,6,7,8,9} 离 {1,2} 只有 3——后者是量表的一截，不是一撮代码。
       单个码的段跨度是 0，这一条对它没有影响。

    外加 -1 专有的第四道闸（见 `_NEG1_MIN_OTHER_VALUES`）：-1 之外还剩不到
    3 个不同取值就不报，压住取值只有 -1 与 1 的 ±1 效应编码。
    """
    u = counts.index.to_numpy(dtype=float)
    present = set(u.tolist())
    hi_all, lo_all = float(u.max()), float(u.min())
    codes = {float(c) for c in _MISSING_CODES}
    blocks: dict[tuple[float, ...], list[float]] = {}
    for cand in _MISSING_CODES:
        c = float(cand)
        if c not in present or c in labelled or counts.get(c, 0) < floor:
            continue
        run = [c]
        while len(run) <= _MAX_CODE_BLOCK and (x := run[0] - 1) in present:
            run.insert(0, x)
        while len(run) <= _MAX_CODE_BLOCK and (x := run[-1] + 1) in present:
            run.append(x)
        if len(run) > _MAX_CODE_BLOCK:
            continue                    # 连成一长片：那是真数据，不是哨兵码
        lo, hi = run[0], run[-1]
        if hi == hi_all and hi in codes and (below := u[u < lo]).size:
            gap = lo - float(below.max())
        elif lo == lo_all and lo in codes and (above := u[u > hi]).size:
            gap = float(above.min()) - hi
        else:
            continue                    # 不在一头、或最外面那个不是约定码：是正常取值
        if -1.0 in run and len(present - set(run)) < _NEG1_MIN_OTHER_VALUES:
            continue                    # ±1 效应编码：-1 是编码的一半，不是缺失码
        if gap >= 2 and gap > hi - lo:
            # 段里够量的都报。文件已经说过含义的（labelled）不在这里猜——标签那一路管
            blocks[tuple(run)] = [x for x in run if x not in labelled and counts.get(x, 0) >= floor]
    return list(blocks.values())


def _sentinel_hits(
    s: pd.Series, value_labels: Mapping[object, object] | None = None
) -> list[tuple[float, int]]:
    """这一列里疑似「缺失值代码」的取值，按 (取值, 出现次数) 返回。

    **两条路，先看文件怎么说，说不了才猜。**

    ① **取值标签写明的**（`98 = 不知道`）——直接算数，不设任何门槛。
       文件里白纸黑字的东西没有「猜得准不准」的问题，出现一次就报一次。
       反过来也成立：**标签写着实质含义的码（`8 = 技校`）不许再被猜成缺失**，
       文件已经回答过的问题不该再由分布形状去推翻。

    ② **没有标签可依的**，才走原来那套分布判据，三条缺一不可
       ——**宁可漏报，不可天天喊狼来了**：

       1. 取值在 `_MISSING_CODES` 里；
       2. **有量**：至少 10 个、且占非空的 0.5% 以上（真有人 99 岁，那是零星几个）；
       3. **是一头一尾，且跟其余取值之间有空档**（至少差 2）。
          百分比里的 99 是真值，因为 98、97 都在，没有空档；
          年龄里的 99 跟 85 之间空了一大段，那就是代码。
          **比空档的是「段」不是单个码**（`_code_blocks`）：9997/9998/9999 挨着出现时
          逐个比永远只差 1，三个会一起漏掉。

    只报，不改。**改动数据是用户的判断**——工具没有资格替他把一列数据抹成缺失。
    """
    v = pd.to_numeric(s, errors="coerce").dropna()
    if v.empty:
        return []
    counts = v.value_counts()

    known = labeled_missing(value_labels)
    hits: list[tuple[float, int]] = [
        (code, int(counts.get(code, 0))) for code in sorted(known) if counts.get(code, 0)
    ]

    if len(v) < 100:
        return hits                     # 样本太小看不出空档，猜的那条不跑
    labelled = {float(c) for c in (value_labels or {}) if _is_number(c)}
    floor = max(10, 0.005 * len(v))
    for block in _code_blocks(counts, labelled, floor):
        hits += [(x, int(counts[x])) for x in block]
    return sorted(hits)


def missing_codes(
    s: pd.Series, value_labels: Mapping[object, object] | None = None
) -> list[tuple[float, int, str]]:
    """这一列疑似缺失值代码的全部事实：`(取值, 出现次数, 取值标签原文)`。

    标签原文是空串 = 文件没说，这一条是按分布猜的。**两种把握的处置不同**：
    画像分开报（`_quality`），`analysis.validate_plan` 里前者拒跑、后者只警告。

    画像与计划的门共用这一处判据——**两边不许各写一套**。
    """
    known = labeled_missing(value_labels)
    return [(c, n, known.get(c, "")) for c, n in _sentinel_hits(s, value_labels)]


def _is_number(x: object) -> bool:
    try:
        float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True


def _quality(
    frame: pd.DataFrame,
    profile: DataProfile,
    value_labels: Mapping[str, Mapping[object, object]] | None = None,
) -> None:
    """把质量事实填进 profile。不单独成一个报告对象——字段少，扁平更好读。"""
    n = len(frame)
    profile.all_missing_columns = [c for c in frame.columns if frame[c].isna().all()]
    profile.constant_columns = [
        c
        for c in frame.columns
        if c not in profile.all_missing_columns and frame[c].nunique(dropna=True) <= 1
    ]
    if n < 30:
        profile.warnings.append(f"只有 {n} 行，几乎不可能支撑任何因果推断")
    if profile.all_missing_columns:
        profile.warnings.append(f"{len(profile.all_missing_columns)} 个变量全为缺失")
    if profile.constant_columns:
        profile.warnings.append(
            f"{len(profile.constant_columns)} 个变量取值恒定，进不了回归："
            + "、".join(profile.constant_columns[:5])
        )
    dup = int(frame.duplicated().sum())
    if dup:
        profile.warnings.append(f"{dup} 行完全重复")

    # 疑似缺失值代码。**这是问句不是判决**——工具没资格替用户把一列抹成缺失。
    labels = value_labels or {}
    for col in frame.columns:
        if col in profile.all_missing_columns or col in profile.constant_columns:
            continue
        facts = missing_codes(frame[col], labels.get(col, {}))
        if not facts:
            continue
        profile.suspected_missing_codes[col] = [(c, n) for c, n, _ in facts]
        if named := {_fmt_code(c): text for c, _, text in facts if text}:
            profile.labeled_missing_codes[col] = named
    if profile.suspected_missing_codes:
        profile.warnings.append(_missing_code_warning(profile))


def _missing_code_warning(profile: DataProfile) -> str:
    """把缺失值代码报成一句话——**两种把握分开说**。

    混在一起报，模型只能一律「问用户」，于是 400 列全堆到人面前，
    没人看得完，最后一列都不改。可这两类的处置根本不同：

    · **文件写明的**（`9999997 = 不适用`）没有疑问，照着改成缺失就是了；
    · **按分布猜的**（`a53a` 里 4 个 999，没有任何标签）才需要人来定夺。
    """
    labeled = profile.labeled_missing_codes
    guessed = {
        c: [(v, n) for v, n in hits if _fmt_code(v) not in labeled.get(c, {})]
        for c, hits in profile.suspected_missing_codes.items()
    }
    guessed = {c: h for c, h in guessed.items() if h}

    def sample(col: str, hits: list[tuple[float, int]], n: int = 2) -> str:
        named = labeled.get(col, {})
        bits = ", ".join(
            _fmt_code(v) + (f"={named[_fmt_code(v)]}" if _fmt_code(v) in named else "")
            + f"（{c} 个）"
            for v, c in hits[:n]
        )
        return f"「{col}」的 {bits}"

    parts: list[str] = [f"**疑似缺失值代码**：{len(profile.suspected_missing_codes)} 列。"]
    if labeled:
        ex = next(iter(labeled))
        parts.append(
            f"其中 **{len(labeled)} 列是文件自己写明的**——取值标签的原文就是"
            f"「不适用」「拒绝回答」这类，例如 {sample(ex, profile.suspected_missing_codes[ex])}。"
            f"**这一类不用问**，照 `profile.json` 的 `labeled_missing_codes` "
            f"在 prep.py 里改成缺失即可。"
        )
    if guessed:
        ex = next(iter(guessed))
        parts.append(
            f"另有 **{len(guessed)} 列是按分布猜的**（没有标签可依），"
            f"例如 {sample(ex, guessed[ex])}——**这一类要问用户**，可能是真值。"
        )
    parts.append(
        "**在文件里它们就是普通数字，算进平均数就全错且不会报错**——"
        "改之前先算一遍剔除后的均值，跟画像里的对一下。"
    )
    return "".join(parts)


def _detect_weight(variables: list[VariableProfile]) -> str | None:
    """检出抽样权重列。调查数据不加权的估计通常是有偏的。"""
    for var in variables:
        if var.semantic_type == "weight":
            return var.name
    return None


def _design_like(s: pd.Series, n_rows: int) -> bool:
    """分层 / 抽样单位列的取值形态：是编码（整数或字符串），取值个数在 2 到行数一半之间，每个取值至少两行。"""
    nn = s.dropna()
    if nn.empty:
        return False
    k = int(nn.nunique())
    if not 2 <= k <= max(2, n_rows // 2):
        return False
    if pd.api.types.is_float_dtype(nn) and not bool((nn == nn.round()).all()):
        return False
    return int(nn.value_counts().min()) >= 2


def _imputation_flags(frame: pd.DataFrame) -> dict[str, str]:
    """插补标记：名字带 imp / imputed / 插补 的 0/1 列。去掉线索词剩下的词干是一列的名字，就记它标的是那一列。"""
    lower = {str(c).lower(): str(c) for c in frame.columns}
    out: dict[str, str] = {}
    for c in map(str, frame.columns):
        if not _name_hits(c, _IMPUTE_HINTS):
            continue
        vals = set(pd.to_numeric(frame[c], errors="coerce").dropna().unique())
        if not vals or not vals <= {0.0, 1.0}:
            continue
        stem = "_".join(t for t in _tokens(c) if t not in _IMPUTE_HINTS)
        out[c] = lower.get(stem, "")
    return out


def _detect_survey(frame: pd.DataFrame, variables: list[VariableProfile]) -> SurveySignals:
    """调查信号（设计稿 3.1）：权重、分层、初级抽样单位、插补标记。认到的都进候选，**强信号才让 `likely` 为真**。

    强信号：权重候选（语义类型是权重、且过了 `_weight_values_ok`）；名字是分层 / 初级抽样单位的
    强线索；名字带 imputed / impute / 插补、取值有 0 有 1 的插补标记。弱信号只进候选：区县、
    `subpopulation`、只带 `imp` 的标记、取值恒定的标记——区县 × 年的面板单凭一列区县就被当成调查、
    计划被门拒掉，是审查第 1、2 批抓到的误报。

    多轮编号不单独算信号：国家—年面板也有年份列（实施计划第零节第 2 条）。
    """
    n = len(frame)
    weights: list[str] = []
    for v in variables:
        if v.semantic_type == "weight" and v.name in frame.columns:
            nn = frame[v.name].dropna()
            if _weight_values_ok(nn, int(nn.nunique())):
                weights.append(v.name)
    strata: list[str] = []
    psu: list[str] = []
    strong = bool(weights)
    for v in variables:
        if v.semantic_type == "weight" or v.name not in frame.columns:
            continue
        strong_strata, strong_psu = _name_hits(v.name, _STRATA_HINTS), _name_hits(v.name, _PSU_HINTS)
        if (strong_strata or _name_hits(v.name, _STRATA_WEAK_HINTS)) and _design_like(frame[v.name], n):
            strata.append(v.name)
            strong = strong or strong_strata
        elif (strong_psu or _name_hits(v.name, _PSU_WEAK_HINTS)) and _design_like(frame[v.name], n):
            psu.append(v.name)
            strong = strong or strong_psu
    flags = _imputation_flags(frame)
    strong = strong or any(
        _name_hits(c, _IMPUTE_STRONG_HINTS) and pd.to_numeric(frame[c], errors="coerce").dropna().nunique() == 2
        for c in flags)
    return SurveySignals(likely=strong, weight_candidates=weights,
                         strata_candidates=strata, psu_candidates=psu, imputation_flags=flags)


def _survey_warning(s: SurveySignals) -> str:
    bits = [("权重候选", s.weight_candidates), ("分层候选", s.strata_candidates),
            ("初级抽样单位候选", s.psu_candidates), ("插补标记", list(s.imputation_flags))]
    listed = "；".join(f"{k} {'、'.join(v)}" for k, v in bits if v)
    return (f"疑似抽样调查数据（都是疑似，要确认）：{listed}。写计划时在 plan.survey 里确认抽样设计"
            f"（design_source 设计从哪来、weight_use 权重怎么用，有分层 / 初级抽样单位就写上），"
            f"或写不按调查处理的理由（not_survey_reason）——调查层设计稿第三、四节")


#: 轮次后缀的常见写法：`happy_1` / `happy1` / `happy_w2` / `happy.3` / `happyT1`
_WAVE_SUFFIX = re.compile(r"^(?P<stem>.+?)[._]?(?:w|wave|t)?(?P<wave>\d)$", re.I)


def wave_groups(frame: pd.DataFrame) -> dict[str, dict[str, str]]:
    """找出宽表里的轮次变量组：`{词干: {轮次: 原列名}}`。

    **提示和转换必须共用这一个判据。**两处各写一份，早晚会分叉成
    「提示说是宽表面板、转换说不是」——那种矛盾最难查。

    原先这里挂着一张按名字排除的清单（age/log/sq/x/y/z…），注释说
    「只在词干孤零零一个时生效」，代码却无条件生效：`age_1/2/3` 明明
    有兄弟也被踢掉。GSS 三轮面板真跑时靠另外 7 组撑住了判定，
    所以一直没暴露——**注释和代码不一致时，挡住它的是运气，不是设计。**

    按名字排除本身也是治标，清单永远漏得掉。真正要区分的是
    **幂次**和**轮次**，而这两件事有一个机械可查的差别——

        幂次： age, age2, age3       ← 一次项就是**光秃秃的词干本身**
        轮次： income_1, income_2    ← 没有光秃秃的 income 这一列

    所以判据是：**光秃秃的词干也在列里，那这组编号就是幂次，不是轮次。**
    不依赖任何名字，`sei2/sei3` 这种没进过清单的也一样挡得住。
    """
    groups: dict[str, dict[str, str]] = {}
    for col in frame.columns:
        m = _WAVE_SUFFIX.match(str(col))
        if not m:
            continue
        stem = m.group("stem").rstrip("._")
        if not stem:
            continue
        groups.setdefault(stem, {})[m.group("wave")] = str(col)

    bare = {str(c).lower() for c in frame.columns}
    groups = {k: v for k, v in groups.items() if len(v) >= 2 and k.lower() not in bare}
    return groups if _waves_cohere(groups) else {}


#: 认作轮次的编号最多这么多。真宽表面板极少超过六轮还并排放在一张表里，
#: 而问卷题号动辄编到 9。
_MAX_WAVES = 6
#: 各组的轮次编号必须有这么大比例**完全一致**，才认这是轮次而不是题号。
_WAVE_AGREEMENT = 0.6


def _waves_cohere(groups: dict[str, dict[str, str]]) -> bool:
    """这些编号看着像**轮次**，还是像**题号**？

    上面那条判据（词干相同、只差一个末尾数字）在真宽表面板上没问题，
    **在问卷数据上会整个失灵**：CGSS2017 的列名是 `a1`/`a12`/`a121`——
    a、b、c 是问卷分节，后面的数字是第几题。真跑一次，786 列里
    被凑出 **93 组「轮次变量」**，画像于是劝人「重塑成长表」——
    可这份数据是单轮截面，**那个长表根本不存在**。
    照做的下场是模型去做一次凭空的重塑，或者干脆不敢往下走。

    区分它们不用认名字，看编号自己的形状就够——

        轮次： income_1, income_2, income_3   ← 连号、从 1 起、各组**一模一样**
        题号： a121…a128, a2/a4/a6/a9, c31…c35 ← 长短不一、断号、各组各的

    所以三条：**连号**、**不超过 `_MAX_WAVES` 个**、**六成以上的组编号完全相同**。
    CGSS2017 实测最大的一致组只占 28%，三条各自都过不去；
    GSS 三轮宽表面板（happy/health/income/educ 各 1-2-3）是 100%。
    """
    def digits(g: dict[str, str]) -> tuple[int, ...]:
        return tuple(sorted(int(w) for w in g))

    tidy = [
        d
        for d in map(digits, groups.values())
        if d[0] in (0, 1) and len(d) <= _MAX_WAVES and d == tuple(range(d[0], d[0] + len(d)))
    ]
    if len(tidy) < 2:
        return False
    agree = max(tidy.count(d) for d in set(tidy))
    return agree >= max(2, _WAVE_AGREEMENT * len(groups))


def _wide_panel_hint(frame: pd.DataFrame) -> str | None:
    """这份数据是不是**宽表面板**（每人一行，各轮的变量用后缀并排）。

    **真面板数据很多是这种形态**，而画像会判它 `cross_section`——
    技术上没错，但完全没用：拿着真面板数据的人会照这个做截面分析，
    **把面板结构整个扔掉**，个体固定效应、个体内变化全用不上。

    判据是机械的：多个变量共享同一个词干、只差一个轮次后缀。
    要求至少 3 组、每组至少 2 轮——**一两组巧合不算**，
    否则 `age2`/`hh_size2` 这类名字会让提示天天响。
    """
    multi = wave_groups(frame)
    if len(multi) < 3:
        return None
    waves = sorted({w for v in multi.values() for w in v})
    shown = "、".join(sorted(multi)[:6])
    more = f" 等 {len(multi)} 组" if len(multi) > 6 else ""
    return (
        f"**这看起来是宽表面板**：{len(multi)} 组变量带着轮次后缀"
        f"（{shown}{more}；轮次 {'/'.join(waves)}）。"
        f"**画像把它判成了截面，那是按文件当前形状说的**——"
        f"照截面分析会把面板结构整个扔掉，个体固定效应、个体内变化全用不上。"
        f"**分析前请重塑成长表**（每人每轮一行，另加一列轮次）"
    )


def _sensitive_columns(variables: list[VariableProfile]) -> list[str]:
    """疑似个人可识别信息。伦理审查要看，也提醒用户确认脱敏与授权。"""
    return [
        var.name
        for var in variables
        if any(h in f"{var.name} {var.label}".lower() for h in _SENSITIVE_HINTS)
    ]


def profile_file(path: str | Path) -> DataProfile:
    """从文件路径直接出画像。CLI 的入口。"""
    return profile_table(load_any(path))
