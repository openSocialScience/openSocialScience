"""宽表面板 → 长表。

**为什么这件事必须归工具箱。**

画像早就认得出宽表面板了，也会提示「分析前请重塑成长表」——
然后就没有下文。真跑一次三轮 GSS 面板时，模型只能**自己手写一个重塑脚本**。

那违反了这个项目的分工（DECISIONS.md 1.2）：**算归工具箱，判归 skill。**
现在是模型在干算的活。它那次写对了，但——

- 没有任何检查会去核它写对没有
- 下一次可能把 `age_1/age_2/age_3` 拼错轮次，回归照跑，结果照出
- **这正是「最贵的静默失败」的形状**：不报错，只是量错了东西

所以这里只做**机械的那一半**：把带轮次后缀的列摞成每人每轮一行。

**不做判断的那一半**，那些留给 skill：
量表要不要反向（`4 - happy`）、收入要不要取对数、性别要不要转哑变量——
每一条都是研究者的决定，工具箱替人做了就是越权。
"""

from __future__ import annotations

import pandas as pd

from oss.core.profiler import _UNIT_HINTS, _name_hits, wave_groups


class NotWidePanel(ValueError):
    """这份数据看不出宽表面板的样子。**说不像就说不像，不要硬转。**"""


def _unit_column(frame: pd.DataFrame, wave_cols: set[str]) -> tuple[str | None, str]:
    """哪一列是个体编号。返回 `(列名, 说明)`。

    「全表唯一」是**必要条件，不是充分条件**。第一版只写了这一条，
    于是取第一个全表唯一的列——测试里 40 个人的 `birth_year` 恰好互不相同，
    它就被当成了人的编号。真数据里 `income` 也会这样。

    **唯一性挑不出编号，名字才行。**所以两条一起用：
    先在唯一的列里找名字像编号的（`pid`/`id`/`编号`…），
    找不到才退回第一个唯一的列——**并且说明那是猜的**。
    """
    uniq = [
        str(c) for c in frame.columns
        if str(c) not in wave_cols
        and frame[c].notna().all()
        and frame[c].nunique() == len(frame)
    ]
    if not uniq:
        return None, ""
    named = [c for c in uniq if _name_hits(c, _UNIT_HINTS)]
    if named:
        return named[0], ""
    return uniq[0], (
        f"**`{uniq[0]}` 是猜的**：它每行取值都不同，但名字看不出是编号。"
        f"其余候选：{'、'.join(f'`{c}`' for c in uniq[1:]) or '无'}。"
        f"猜错的话每一行会挂到错的人身上，**个体固定效应会安静地量错东西**——"
        f"用 --unit 指定"
    )


def wide_to_long(
    frame: pd.DataFrame,
    *,
    unit: str | None = None,
    wave_name: str = "wave",
) -> tuple[pd.DataFrame, list[str]]:
    """把宽表面板摞成长表。返回 `(长表, 结论)`。

    结论里每一条都是**读者需要知道才能信这份长表**的事：
    认出了哪些组、哪些组缺轮次、哪些列当成了不随时间变的、编号是从哪来的。
    """
    groups = wave_groups(frame)
    if len(groups) < 3:
        raise NotWidePanel(
            f"只找到 {len(groups)} 组带轮次后缀的变量（至少要 3 组）。"
            f"**这不像宽表面板，所以不转**——硬转出来的长表比不转更危险："
            f"它看着像面板，个体固定效应会照跑，而量的东西是错的"
        )

    wave_cols = {c for g in groups.values() for c in g.values()}
    waves = sorted({w for g in groups.values() for w in g})
    notes: list[str] = []

    if unit and unit not in frame.columns:
        raise NotWidePanel(f"指定的个体编号列 {unit!r} 不在数据里")
    guessed = ""
    if unit:
        unit_col: str | None = unit
    else:
        unit_col, guessed = _unit_column(frame, wave_cols)
    if unit_col is None:
        unit_col = "_row"
        frame = frame.assign(_row=range(len(frame)))
        notes.append(
            "**没有找到个体编号列**（不带轮次后缀、且每行唯一的列），"
            "已用行号代替。行号只在这份文件内部有效，**跟别的数据对不上**"
        )
    else:
        notes.append(f"个体编号用 `{unit_col}`（{frame[unit_col].nunique():,} 个）")
        if guessed:
            notes.append(guessed)

    # 不带轮次后缀的列 = 不随时间变的，每一轮原样复制一份
    constant = [
        str(c) for c in frame.columns
        if str(c) not in wave_cols and str(c) != unit_col
    ]

    parts: list[pd.DataFrame] = []
    for w in waves:
        part = pd.DataFrame({unit_col: frame[unit_col].to_numpy()})
        for stem, cols in sorted(groups.items()):
            src = cols.get(w)
            # 某个变量缺这一轮 → 整列填缺失。**不能跳过这一轮**，
            # 否则各变量的行数对不齐，摞出来的长表会错位。
            part[stem] = frame[src].to_numpy() if src else pd.NA
        for c in constant:
            part[c] = frame[c].to_numpy()
        part[wave_name] = w
        parts.append(part)

    long = pd.concat(parts, ignore_index=True)
    long[wave_name] = pd.to_numeric(long[wave_name], errors="coerce")
    long = long.sort_values([unit_col, wave_name]).reset_index(drop=True)

    notes.append(
        f"{len(groups)} 组随轮次变化的变量 × {len(waves)} 轮 "
        f"→ {len(frame):,} 行宽表变成 {len(long):,} 行长表"
    )

    ragged = {k: sorted(set(waves) - set(v)) for k, v in groups.items() if len(v) < len(waves)}
    if ragged:
        detail = "；".join(f"`{k}` 缺第 {'、'.join(v)} 轮" for k, v in sorted(ragged.items())[:6])
        notes.append(
            f"**有 {len(ragged)} 组不是每轮都有**（{detail}）——"
            f"缺的那几轮填了缺失值。**这不是数据缺失，是这个变量那一轮根本没问**，"
            f"两者在分析里要区别对待"
        )
    if constant:
        shown = "、".join(f"`{c}`" for c in constant[:6])
        more = f" 等 {len(constant)} 列" if len(constant) > 6 else ""
        notes.append(
            f"当成不随时间变、每轮复制了一份：{shown}{more}。"
            f"**个体固定效应会把这些整个吃掉**，它们的系数估不出来"
        )
    notes.append(
        "**只做了摞行这一件事。**量表反向、取对数、转哑变量都没做——"
        "那些是研究者的决定，工具箱不替人做"
    )
    return long, notes
