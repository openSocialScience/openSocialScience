"""多轮调查摞成一张表。**同一个问题，每轮的列名和编码都可能不一样。**

这一批来自要复现一篇 11 轮 CGSS 的论文。作者的清洗日志里记了 11 条红旗，
其中三条是纯机械的、本该由工具挡住的：

- 「上周有没有工作」这题，2010–2013 年 `1 = 是`，2015 年起变成 `4 = 是`
- 出生年在 2010 叫 `a3a`、2015 叫 `a301`、2017 叫 `a31`、2021 叫 `A3_1`
- 2023 年一半样本是电话访问，整个工作模块根本没问

**三件错了都不报错。**摞出来的表看着完全正常，只是把一类人算成了另一类人。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from oss.core.stack import ColumnSpec, SourceSpec, StackRejected, load_spec, stack


def _write(tmp_path, name: str, frame: pd.DataFrame) -> str:
    p = tmp_path / name
    frame.to_csv(p, index=False)
    return str(p)


def _dta(tmp_path, name: str, frame: pd.DataFrame, labels: dict) -> str:
    """写一份带取值标签的 .dta——标签正是编码翻转哨兵的依据。"""
    pytest.importorskip("pyreadstat")
    import pyreadstat

    p = tmp_path / name
    pyreadstat.write_dta(frame, str(p), variable_value_labels=labels)
    return str(p)


# ─────────────────────────────────────────────── 摞得对

def test_逐轮改名摞成一张():
    """出生年每轮叫的名字都不一样，摞完必须是同一列。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"a3a": [1980, 1990], "x": [1, 2]}))
    b = _write(tmp, "w2.csv", pd.DataFrame({"a301": [1975], "x": [3]}))
    out, rep = stack([
        SourceSpec(a, 2010, {"birth": ColumnSpec("a3a"), "x": ColumnSpec("x")}),
        SourceSpec(b, 2015, {"birth": ColumnSpec("a301"), "x": ColumnSpec("x")}),
    ], "year")
    assert list(out.columns) == ["birth", "x", "year"]
    assert out.birth.tolist() == [1980, 1990, 1975]
    assert out.year.tolist() == [2010, 2010, 2015]
    assert rep.rows_in == {"2010": 2, "2015": 1} and rep.rows_out == 3


def test_recode按声明对齐编码():
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"a53": [1, 2, 1]}))
    b = _write(tmp, "w2.csv", pd.DataFrame({"a53": [4, 1, 4]}))
    out, _ = stack([
        SourceSpec(a, 2013, {"employed": ColumnSpec("a53", {"1": 1, "2": 0})}),
        SourceSpec(b, 2017, {"employed": ColumnSpec("a53", {"4": 1, "1": 0})}),
    ])
    assert out.employed.tolist() == [1, 0, 1, 1, 0, 1], "两轮的『是』都要变成 1"


# ─────────────────────────────────────────────── 该拒绝的

def test_编码翻转直接拒绝执行():
    """**这是整个模块存在的头号理由。**

    同一个码在两轮指着相反的东西，直接摞就是把有工作的人算成没工作。
    所以是拒绝，不是警告——警告会被读过去。
    """
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _dta(tmp, "w1.dta", pd.DataFrame({"a53": [1.0, 2.0]}),
             {"a53": {1.0: "是", 2.0: "否"}})
    b = _dta(tmp, "w2.dta", pd.DataFrame({"a53": [1.0, 4.0]}),
             {"a53": {1.0: "否", 4.0: "是"}})
    with pytest.raises(StackRejected) as e:
        stack([SourceSpec(a, 2013, {"employed": ColumnSpec("a53")}),
               SourceSpec(b, 2017, {"employed": ColumnSpec("a53")})])
    msg = "\n".join(e.value.problems)
    assert "编码跨轮翻转" in msg and "employed" in msg
    assert "「是」在 2013 是码 1、在 2017 是码 4" in msg, "要指出说法搬到哪个码去了"


def test_写了recode就不再拦():
    """拒绝的出口是**显式声明怎么对齐**，不是关掉检查。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _dta(tmp, "w1.dta", pd.DataFrame({"a53": [1.0, 2.0]}),
             {"a53": {1.0: "是", 2.0: "否"}})
    b = _dta(tmp, "w2.dta", pd.DataFrame({"a53": [1.0, 4.0]}),
             {"a53": {1.0: "否", 4.0: "是"}})
    out, _ = stack([
        SourceSpec(a, 2013, {"employed": ColumnSpec("a53", {"1": 1, "2": 0})}),
        SourceSpec(b, 2017, {"employed": ColumnSpec("a53", {"4": 1, "1": 0})}),
    ])
    assert out.employed.tolist() == [1, 0, 0, 1]


def test_只改措辞不算翻转只警告():
    """**判据不准的门，人会学会绕过去。**

    第一版按「同一个码的标签变了没有」判，真跑两轮 CGSS 立刻误报教育程度：
    码 13 在 2013 是「研究生以上」、2017 是「研究生及以上」——同一个类别换了个说法。
    照这个报法，人会给每一列都糊一个 recode，那这道门就白设了。

    真正该拦的是**说法搬了家**：「是」从码 1 挪到了码 4。
    """
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _dta(tmp, "w1.dta", pd.DataFrame({"e": [2.0, 13.0]}),
             {"e": {2.0: "私塾", 13.0: "研究生以上"}})
    b = _dta(tmp, "w2.dta", pd.DataFrame({"e": [2.0, 13.0]}),
             {"e": {2.0: "私塾、扫盲班", 13.0: "研究生及以上"}})
    out, rep = stack([SourceSpec(a, 2013, {"educ": ColumnSpec("e")}),
                      SourceSpec(b, 2017, {"educ": ColumnSpec("e")})])
    assert len(out) == 4, "改个说法不该拦下来"
    assert any("措辞跨轮有出入" in w and "educ" in w for w in rep.warnings), "但要说一声"


def test_一轮多个选项不算冲突():
    """问卷改版常有。**只比两轮都出现过的码**，否则天天喊狼来了。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _dta(tmp, "w1.dta", pd.DataFrame({"e": [1.0, 2.0]}),
             {"e": {1.0: "小学", 2.0: "初中"}})
    b = _dta(tmp, "w2.dta", pd.DataFrame({"e": [1.0, 3.0]}),
             {"e": {1.0: "小学", 2.0: "初中", 3.0: "高中"}})
    out, _ = stack([SourceSpec(a, 2013, {"educ": ColumnSpec("e")}),
                    SourceSpec(b, 2017, {"educ": ColumnSpec("e")})])
    assert len(out) == 4


def test_列名打错要点名而不是静默丢列():
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"a53a": [40, 50]}))
    with pytest.raises(StackRejected, match="a53aa"):
        stack([SourceSpec(a, 2017, {"hours": ColumnSpec("a53aa")})])


def test_大小写不同的同名列要提示():
    """2021 波的变量名首字母是大写的 A53aa。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"A53aa": [40]}))
    with pytest.raises(StackRejected) as e:
        stack([SourceSpec(a, 2021, {"hours": ColumnSpec("a53aa")})])
    assert "A53aa" in "\n".join(e.value.problems), "要提示大小写不同的同名列"


# ─────────────────────────────────────────────── 该警告的

def test_整轮没有这个概念要喊出来():
    """2023 年一半样本是电话访问，工作模块整个没问。

    「那一轮根本没问」和「答了但缺失」在表里长得一模一样——
    不区分的话，这一轮会被当成随机缺失悄悄删掉。
    """
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"h": [40], "e": [3]}))
    b = _write(tmp, "w2.csv", pd.DataFrame({"e": [4]}))
    out, rep = stack([
        SourceSpec(a, 2018, {"hours": ColumnSpec("h"), "educ": ColumnSpec("e")}),
        SourceSpec(b, 2023, {"educ": ColumnSpec("e")}),
    ])
    assert out.hours.isna().sum() == 1
    assert rep.per_variable["hours"]["缺席"] == ["2023"]
    assert any("没有映射" in w and "hours" in w for w in rep.warnings)


def test_recode没盖住的取值要报出来():
    """「这一轮的 3 我没打算要」和「我忘了写 3」在结果上完全一样。"""
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a = _write(tmp, "w1.csv", pd.DataFrame({"a53": [1, 2, 3, 9]}))
    out, rep = stack([SourceSpec(a, 2013, {"emp": ColumnSpec("a53", {"1": 1, "2": 0})})])
    assert out.emp.isna().sum() == 2
    assert any("没写进 recode" in w for w in rep.warnings)
    assert any("3" in w and "9" in w for w in rep.warnings)


# ─────────────────────────────────────────────── 映射文件

def test_映射文件读得进来(tmp_path):
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({"wave_column": "year", "sources": [
        {"path": "a.dta", "wave": 2013, "columns": {
            "hours": "a53a", "employed": {"from": "a53", "recode": {"1": 1, "2": 0}}}},
    ]}, ensure_ascii=False), encoding="utf-8")
    sources, wave_col = load_spec(spec)
    assert wave_col == "year"
    assert sources[0].columns["hours"].source == "a53a"
    assert sources[0].columns["employed"].recode == {"1": 1, "2": 0}


def test_映射文件坏了要说清哪儿坏(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(StackRejected, match="不是合法 JSON"):
        load_spec(bad)

    empty = tmp_path / "empty.json"
    empty.write_text('{"sources": []}', encoding="utf-8")
    with pytest.raises(StackRejected, match="空的"):
        load_spec(empty)

    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"sources": [
        {"path": "a", "wave": 2013, "columns": {"h": "x"}},
        {"path": "b", "wave": 2013, "columns": {"h": "x"}}]}), encoding="utf-8")
    with pytest.raises(StackRejected, match="重复"):
        load_spec(dup)


def test_轮次列不许自己再映一个(tmp_path):
    spec = tmp_path / "m.json"
    spec.write_text(json.dumps({"wave_column": "year", "sources": [
        {"path": "a", "wave": 2013, "columns": {"year": "s1"}}]}), encoding="utf-8")
    with pytest.raises(StackRejected, match="轮次列"):
        load_spec(spec)


def test_float32的码不该被写成带小数点():
    """**`np.float64` 是 Python `float` 的子类，`np.float32` 不是。**

    于是同一份映射，float64 的列一切正常，float32 的列每个码都写成 `'11.0'`，
    跟映射表里的 `'11'` 对不上。结果没错（重编码走 `Series.map`，
    pandas 会先转成 Python float），错的是**告警**：11 轮 CGSS 真跑时报出
    「`province` 在 2006：28 个取值没写进 recode，会变成缺失」——
    那 28 个全部映射成功，一个没丢。

    **一道天天喊狼来了的门等于没有门**，而这类告警正是这个模块的全部产出。
    """
    from oss.core.stack import _key

    for v in (np.float32(11.0), np.float64(11.0), 11.0, np.int32(11), 11):
        assert _key(v) == "11", f"{type(v).__name__} 写成了 {_key(v)!r}"
    assert _key(np.float32(1.5)) == "1.5"


def test_float32列不该报假的落空警告():
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    frame = pd.DataFrame({"prov": np.array([11, 12, 13, 11], dtype="float32")})
    path = tmp / "w.csv"
    frame.to_csv(path, index=False)
    # CSV 读回来是 float64，所以直接构造 float32 的表来走同一段逻辑
    from oss.core.stack import _apply_recode, ColumnSpec as CS

    col, fell = _apply_recode(pd.Series(np.array([11, 12, 13], dtype="float32")),
                              CS("prov", {"11": 1, "12": 2, "13": 3}))
    assert fell == [], f"三个码全写进 recode 了，不该报落空：{fell}"
    assert col.tolist() == [1, 2, 3]
