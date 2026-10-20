"""多轮调查摞成一张表。**同一个问题，每轮的列名和编码都可能不一样。**

`oss merge` 是横着拼（按国家-年把两份数据并到一起），这里是**竖着摞**：
11 轮 CGSS 叠成一张，每轮不同的列名对齐成同一个名字。

**为什么这件事必须归工具箱。**

真跑一次 11 轮 CGSS 之前，这一段只能落在模型手写的 `prep.py` 里，
而 `prep.py` 是这条链上唯一没人核的计算。偏偏这篇论文最危险的东西全在这一段：

- **编码翻转**：同一道是非题，前几轮 `1 = 是`，改版之后 `4 = 是`
- **逐轮改名**：同一个问题，每轮的列名都不一样，还可能换大小写
- **整轮没问**：某一轮换了调查方式，一整个模块的取值全是同一个负数码

**这三件错了都不报错**，只是把有工作的人当成没工作、把没问过的当成缺失、
把一千万当成收入。摞出来的表看着完全正常。

所以这里做机械的那一半：**按声明摞行，并且把摞不齐的地方全部喊出来**。

判断的那一半留给 skill：哪一列对应哪个概念、编码怎么对齐、要不要保留某一轮
——这些写进映射文件，是**可审计的声明**，不是埋在脚本里的一行代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from oss.core.loaders import LoadedTable, load_any
from oss.core.profiler import labeled_missing


class StackRejected(ValueError):
    """摞不了。**逐条说清哪里摞不齐，不要硬摞。**"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class SourceSpec:
    """一份来源数据：哪个文件、属于哪一轮、每个概念取哪一列。"""

    path: str
    wave: object
    columns: dict[str, "ColumnSpec"]


@dataclass
class ColumnSpec:
    """一个概念在这一轮取哪一列，以及要不要重编码。

    `recode` 是**声明式的**：`{"4": 1, "1": 0}` 表示这一轮的 4 记作 1、1 记作 0。
    写在映射文件里，任何人都能看见、能核；写在脚本里就只有作者知道。
    没列进 `recode` 的取值会变成缺失，**而且必须报出来**——
    默默变缺失正是这一段最贵的失败方式。
    """

    source: str
    recode: dict[str, object] | None = None


@dataclass
class StackReport:
    rows_in: dict[str, int] = field(default_factory=dict)
    rows_out: int = 0
    per_variable: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _as_column(v: object) -> ColumnSpec:
    if isinstance(v, str):
        return ColumnSpec(source=v)
    if isinstance(v, dict) and "from" in v:
        rc = v.get("recode")
        return ColumnSpec(source=str(v["from"]),
                          recode={str(k): x for k, x in rc.items()} if rc else None)
    raise StackRejected([
        f"列的写法只认两种：`\"q31b\"`，或 `{{\"from\": \"q30\", \"recode\": {{...}}}}`。"
        f"收到的是 {v!r}"
    ])


def load_spec(path: str | Path) -> tuple[list[SourceSpec], str]:
    """读映射文件。格式错就直接拒，**不要猜用户想写什么**。"""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StackRejected([f"读不了映射文件 {p}：{exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise StackRejected([f"映射文件不是合法 JSON（第 {exc.lineno} 行）：{exc.msg}"]) from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise StackRejected(["映射文件要有 `sources` 数组。`oss schema stack --example` 看样例"])
    if not raw["sources"]:
        raise StackRejected(["`sources` 是空的——一份都没有就没什么可摞的"])

    wave_col = str(raw.get("wave_column") or "wave")
    sources: list[SourceSpec] = []
    for i, s in enumerate(raw["sources"], start=1):
        if not isinstance(s, dict) or "path" not in s:
            raise StackRejected([f"第 {i} 份来源缺 `path`"])
        cols = s.get("columns")
        if not isinstance(cols, dict) or not cols:
            raise StackRejected([f"第 {i} 份来源（{s['path']}）缺 `columns`"])
        if wave_col in cols:
            raise StackRejected([
                f"第 {i} 份来源把 `{wave_col}` 也列进了 `columns`——"
                f"轮次列由 `wave` 字段生成，不要自己再映一个同名的"])
        sources.append(SourceSpec(
            path=str(s["path"]), wave=s.get("wave", i),
            columns={str(k): _as_column(v) for k, v in cols.items()}))

    waves = [s.wave for s in sources]
    if len(set(map(str, waves))) != len(waves):
        raise StackRejected([f"`wave` 有重复：{waves}。每份来源要有自己的轮次标识"])
    return sources, wave_col


def _labels_of(table: LoadedTable, col: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in (table.value_labels.get(col) or {}).items()}


def _apply_recode(s: pd.Series, spec: ColumnSpec) -> tuple[pd.Series, list[object]]:
    """按声明重编码。返回 `(新列, 落空的取值)`。

    落空的取值会变成缺失。**这必须报出来**——「这一轮的 3 我没打算要」
    和「我忘了写 3」在结果上完全一样，而后者是错的。
    """
    if not spec.recode:
        return s, []
    mapped = s.map(lambda v: spec.recode.get(_key(v), pd.NA))
    fell = sorted({_key(v) for v in s.dropna().unique() if _key(v) not in spec.recode})
    try:
        return pd.to_numeric(mapped), fell
    except (TypeError, ValueError):
        return mapped, fell               # 重编码到字符串（"城"/"乡"）也是正当的


def _key(v: object) -> str:
    """把取值写成映射表里的键。`4.0` 与 `4` 是同一个码。

    **判浮点不能只认 `float`。**`np.float64` 恰好是 Python `float` 的子类，
    `np.float32` 不是——于是同一份映射，float64 的列一切正常，
    float32 的列每个码都写成 `'11.0'`，跟映射表里的 `'11'` 对不上。

    后果不在结果上（重编码走 `Series.map`，pandas 会先转成 Python float，
    映射是对的），而在**告警**上：11 轮 CGSS 真跑时报出「`province` 在 2006：
    28 个取值没写进 recode，会变成缺失」——那 28 个全部映射成功，一个没丢。

    **一道天天喊狼来了的门，等于没有门。**而这类告警正是整个摞轮次模块的全部产出。
    """
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v)


def stack(sources: list[SourceSpec], wave_col: str = "wave") -> tuple[pd.DataFrame, StackReport]:
    """按声明把多份数据摞成一张。摞不齐就拒绝，并逐条说清哪里摞不齐。"""
    rep = StackReport()
    frames: list[pd.DataFrame] = []
    labels_seen: dict[str, dict[str, tuple[str, dict[str, str]]]] = {}
    problems: list[str] = []
    all_names = sorted({n for s in sources for n in s.columns})

    for src in sources:
        try:
            table = load_any(src.path)
        except Exception as exc:                       # noqa: BLE001 —— 逐份报，不要一份坏了全崩
            problems.append(f"{src.wave}：读不了 `{src.path}`——{exc}")
            continue
        frame = table.frame
        rep.rows_in[str(src.wave)] = len(frame)

        picked: dict[str, pd.Series] = {}
        for name, spec in src.columns.items():
            if spec.source not in frame.columns:
                near = [c for c in frame.columns if str(c).lower() == spec.source.lower()]
                problems.append(
                    f"{src.wave}：`{src.path}` 里没有列 `{spec.source}`（要映成 `{name}`）"
                    + (f"。大小写不同的同名列：{near}" if near else ""))
                continue
            col, fell = _apply_recode(frame[spec.source], spec)
            picked[name] = col
            info = rep.per_variable.setdefault(name, {"来源": {}, "缺席": []})
            info["来源"][str(src.wave)] = spec.source + (
                f"（重编码 {len(spec.recode)} 项）" if spec.recode else "")
            if fell:
                rep.warnings.append(
                    f"`{name}` 在 {src.wave}：{len(fell)} 个取值没写进 recode，"
                    f"会变成缺失——{'、'.join(map(str, fell[:8]))}"
                    f"{'…' if len(fell) > 8 else ''}。**是有意丢掉就写进 notes，"
                    f"是漏了就补进映射文件**")
            # 编码翻转的哨兵：同一个概念，各轮的取值标签必须能对上。
            if spec.recode is None and (lab := _labels_of(table, spec.source)):
                labels_seen.setdefault(name, {})[str(src.wave)] = (spec.source, lab)

        for missing in [n for n in all_names if n not in src.columns]:
            rep.per_variable.setdefault(missing, {"来源": {}, "缺席": []})["缺席"].append(str(src.wave))

        if picked:
            part = pd.DataFrame(picked)
            part[wave_col] = src.wave
            frames.append(part)

    flips, reworded = _flip_problems(labels_seen)
    problems.extend(flips)
    rep.warnings.extend(reworded)
    if problems:
        raise StackRejected(problems)
    if not frames:
        raise StackRejected(["一份数据都没摞成"])

    out = pd.concat(frames, ignore_index=True)
    rep.rows_out = len(out)
    for name, info in rep.per_variable.items():
        if info["缺席"]:
            rep.warnings.append(
                f"`{name}` 在 {'、'.join(info['缺席'])} 这几轮**没有映射**，整轮为缺失。"
                f"「那一轮根本没问」和「答了但缺失」在表里长得一模一样——"
                f"分析时若不区分，这几轮会被当成随机缺失悄悄删掉")
    rep.notes.append(
        f"{len(rep.rows_in)} 份来源、{sum(rep.rows_in.values())} 行进，{len(out)} 行出；"
        f"{len(all_names)} 个概念，轮次列 `{wave_col}`")
    return out, rep


def _flip_problems(
    seen: dict[str, dict[str, tuple[str, dict[str, str]]]],
) -> tuple[list[str], list[str]]:
    """**编码翻转的哨兵。**返回 `(拒绝, 警告)`。

    判据不是「同一个码的说法变了没有」，是**同一个说法搬没搬家**：

        翻转： 「是」在 2013 挂 1、在 2017 挂 4        ← 拒绝
        改词： 13 在 2013 是「研究生以上」、
               2017 是「研究生及以上」                  ← 只警告

    两者在「同一个码的标签不相等」这条粗判据下长得一模一样，而后果差着天：
    翻转直接摞就是把有工作的人算成没工作；改词摞起来一点事没有。
    第一版按粗判据写，真跑两轮 CGSS 立刻误报 `educ`——**判据不准的门，
    人会学会绕过去**，绕的办法就是给每一列都糊一个 recode，那这道门就白设了。

    所以拒绝只留给**说法搬了家**的：某个标签文本两轮都出现，却挂在不同的码上。
    这是可以机械判定的，也正是「一类人被算成另一类人」的那个形状。
    """
    problems: list[str] = []
    warnings: list[str] = []
    for name, per_wave in seen.items():
        waves = sorted(per_wave)
        if len(waves) < 2:
            continue
        base_w = waves[0]
        base_col, base_lab = per_wave[base_w]
        base_by_text = _by_text(base_lab)
        for w in waves[1:]:
            col, lab = per_wave[w]
            by_text = _by_text(lab)

            moved = [
                (text, base_by_text[text], by_text[text])
                for text in sorted(base_by_text.keys() & by_text.keys())
                if base_by_text[text] != by_text[text]
            ]
            if moved:
                bits = "；".join(f"「{lab[c2] if c2 in lab else t}」在 {base_w} 是码 {c1}、"
                                f"在 {w} 是码 {c2}" for t, c1, c2 in moved[:4])
                problems.append(
                    f"**`{name}` 的编码跨轮翻转**（{base_w} 的 `{base_col}` vs "
                    f"{w} 的 `{col}`）：{bits}"
                    f"{f'，还有 {len(moved) - 4} 处' if len(moved) > 4 else ''}。"
                    f"直接摞起来不会报错，只会把一类人算成另一类人——"
                    f"在映射文件里给其中一轮写 `recode` 把编码对齐")
                continue

            reworded = [
                (code, base_lab[code], lab[code])
                for code in sorted(base_lab.keys() & lab.keys())
                if _norm(base_lab[code]) != _norm(lab[code])
            ]
            if reworded:
                bits = "；".join(f"码 {c}「{a}」→「{b}」" for c, a, b in reworded[:3])
                warnings.append(
                    f"`{name}` 的选项措辞跨轮有出入（{base_w} vs {w}）：{bits}"
                    f"{f'，还有 {len(reworded) - 3} 处' if len(reworded) > 3 else ''}。"
                    f"**码没搬家，按同一个类别摞了**——确实是同一类就不用管，"
                    f"是问卷改了口径就要自己合并类别")
    return problems, warnings


def _by_text(labels: dict[str, str]) -> dict[str, str]:
    """标签文本 → 码。同一个文本挂了多个码的整个不参与判断（判不出搬没搬家）。"""
    out: dict[str, str] = {}
    dup: set[str] = set()
    for code, text in labels.items():
        t = _norm(text)
        if t in out:
            dup.add(t)
        out[t] = code
    return {t: c for t, c in out.items() if t not in dup}


def _norm(s: str) -> str:
    """比标签只比实质内容：`[1 我国的…]` 这种题干前缀、空格、全半角括号不算数。"""
    return "".join(str(s).split()).replace("（", "(").replace("）", ")").lower()


def missing_code_hint(sources: list[SourceSpec]) -> list[str]:
    """**这一步是缺失码最后一次看得见的机会。**

    两件事撞在一起：

    ① 各轮的缺失码不一样——2017 年工时是 998/999，2023 年是 −5/−3/−2/−1。
       摞成一张表之后它们混在同一列里，**再想按轮次分别处理就晚了**。
    ② 摞出来的是 CSV，**取值标签不跟着走**。而「9999997 = 不适用」这句话
       正是画像认出缺失码的依据（`profiler.labeled_missing`）——
       标签一丢，下游就只剩按分布猜，而大数码（9999997）恰恰猜不出来。

    所以这些码必须**在这里被看见、在 `prep.py` 里被处理**，
    不能指望对摞好的表再跑一次画像会替你发现。
    """
    out: list[str] = []
    for src in sources:
        try:
            table = load_any(src.path)
        except Exception:                              # noqa: BLE001
            continue
        for name, spec in src.columns.items():
            if spec.recode:
                continue                    # 重编码过的，落空值已经单独报过了
            if not (known := labeled_missing(table.value_labels.get(spec.source))):
                continue
            col = pd.to_numeric(table.frame[spec.source], errors="coerce")
            hits = [(c, int((col == c).sum())) for c in sorted(known)]
            hits = [(c, n) for c, n in hits if n]
            if not hits:
                continue
            codes = "、".join(f"{c:g}={known[c]}（{n} 行）" for c, n in hits[:4])
            out.append(
                f"{src.wave} 的 `{name}`（{spec.source}）带缺失码：{codes}"
                f"{f'，还有 {len(hits) - 4} 个' if len(hits) > 4 else ''}")
    if out:
        out.append(
            "**摞出来的 CSV 不带取值标签，对它再跑画像也认不出上面这些码**——"
            "「9999997=不适用」那句话是画像的唯一依据，标签一丢就只剩按分布猜，"
            "而大数码恰恰猜不出来。这些码要在 `prep.py` 里逐轮置成缺失，"
            "**现在处理，不要等到画像**")
    return out
