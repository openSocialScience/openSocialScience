"""拼两份数据。

拼本身 pandas 一行就够。这些测试全都在测**拼完之后**——
丢了什么、为什么丢、丢掉的那批是不是不一样。

来源是真数据上量的一次：QoG 民主化数据拼 Our World in Data 互联网普及率，
按国家名直接拼 → 丢 25.3%、30 个国家整个消失、**没有任何报错**。
而丢掉那批的民主国家比例比留下的低 10 个百分点，
**那份数据研究的正是民主化**。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.merge import DEFAULT_MAX_LOSS, MergeRefused, merge_tables


def _left(n_units: int = 40, years=range(2000, 2010)) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = [(f"U{u:02d}", y) for u in range(n_units) for y in years]
    df = pd.DataFrame(rows, columns=["unit", "year"])
    df["y"] = rng.normal(size=len(df))
    return df


def _right(units, years=range(2000, 2010)) -> pd.DataFrame:
    rng = np.random.default_rng(12)
    rows = [(u, y) for u in units for y in years]
    df = pd.DataFrame(rows, columns=["entity", "year"])
    df["x"] = rng.normal(size=len(df))
    return df


def _merge(L, R, **kw):
    kw.setdefault("left_on", ["unit", "year"])
    kw.setdefault("right_on", ["entity", "year"])
    return merge_tables(L, R, **kw)


# --------------------------------------------------- 拦得住吗


def test_丢太多就拒绝拼():
    """**说不该拼就说不该拼。**丢一成以上通常是键的口径不一致，不是真没数据。"""
    L = _left()
    R = _right([f"U{u:02d}" for u in range(30)])       # 少 10 个单位 = 丢 25%
    with pytest.raises(MergeRefused) as e:
        _merge(L, R)
    msg = str(e.value)
    assert "拒绝拼" in msg
    assert "--map" in msg and "--allow-loss" in msg, "拦下来必须给出路"


def test_显式接受损失才放行():
    """要接受就得**显式说出来**——数字写在命令里，事后查得到。"""
    L = _left()
    R = _right([f"U{u:02d}" for u in range(30)])
    out, notes = _merge(L, R, allow_loss=0.3)
    assert len(out) == 300
    assert any("没配上" in n for n in notes)


def test_丢得少就不拦():
    """**警报天天响就等于没有警报。**丢 5% 不该挡路。"""
    L = _left()
    R = _right([f"U{u:02d}" for u in range(38)])       # 丢 5%
    out, _ = _merge(L, R)
    assert len(out) == 380


def test_门槛就是那个常数():
    assert 0 < DEFAULT_MAX_LOSS < 0.5


# --------------------------------------------------- 为什么丢


def test_单个键对不上时要点名是哪个键():
    """「丢了 56%」既吓人又没法行动。

    真数据上那个 56% 是两件事叠在一起：国家名对不上 1,517 行、
    年份不重叠 4,073 行。**混成一个数，人就不知道该去改哪个。**
    """
    L = _left()
    R = _right([f"U{u:02d}" for u in range(40)], years=range(2005, 2010))  # 少一半年份
    with pytest.raises(MergeRefused) as e:
        _merge(L, R)
    msg = str(e.value)
    assert "`year`" in msg and "2000–2004" in msg


def test_两个键都对得上时要说是组合缺():
    """每个键单独看都在，缺的是「这个单位的这一年」。

    **这跟口径不一致出路完全相反**：口径问题做对照表能救，
    组合缺失是那一格真没数据，做多少对照表都没用。
    分不清的话，人会一直去改对照表，改到放弃。
    """
    L = _left(n_units=20)
    R = _right([f"U{u:02d}" for u in range(20)])
    # **每隔几行挖一格**，不能挖连续的——挖掉整整四个单位的话，
    # 那四个单位的名字就整个消失了，变成「键对不上」，测的就不是这条了。
    R = R.drop(R.index[::7])
    out, notes = _merge(L, R, allow_loss=0.5)
    note = " ".join(notes)
    assert "组合" in note and "对照表救不回来" in note


# --------------------------------------------------- 丢掉的那批一样吗


def test_丢掉的那批系统性不同要报出来():
    """**这一条是这个模块真正的价值。**

    「丢了 25%」只是个数字；「丢掉的那批民主国家比例低 10 个百分点，
    而本研究研究的正是民主化」才是能改变结论的事实。
    """
    L = _left(n_units=40)
    L["gdp"] = L.unit.str[1:].astype(int) * 1.0       # 单位编号越大越富
    R = _right([f"U{u:02d}" for u in range(30)])      # 丢掉的正好是最富的十个
    with pytest.raises(MergeRefused) as e:
        _merge(L, R)
    msg = str(e.value)
    assert "系统性地不同" in msg and "`gdp`" in msg
    assert "样本被按某种特征筛过一遍" in msg


def test_丢得随机就不该报系统性差异():
    """随机丢不是偏样本。**这条不成立的话，上一条就只是在天天报警。**"""
    rng = np.random.default_rng(5)
    L = _left(n_units=40)
    L["z"] = rng.normal(size=len(L))
    drop = set(rng.choice([f"U{u:02d}" for u in range(40)], 10, replace=False))
    R = _right([u for u in L.unit.unique() if u not in drop])
    with pytest.raises(MergeRefused) as e:
        _merge(L, R)
    assert "系统性地不同" not in str(e.value)


# --------------------------------------------------- 绝不替人认定


def test_对不上的名字只给候选不自动套用():
    """**「Korea」到底是韩国还是朝鲜，猜错了比不拼更糟。**

    这是引用核验那次学到的：一个「帮你改正错误」的工具，
    如果替人做了它不该做的认定，**它制造的错误比它拦下的更难被发现**。
    """
    L = pd.DataFrame({"unit": ["Bahamas (the)"] * 12, "year": range(2000, 2012), "y": 1.0})
    R = pd.DataFrame({"entity": ["Bahamas"] * 12, "year": range(2000, 2012), "x": 2.0})
    with pytest.raises(MergeRefused) as e:
        _merge(L, R)
    msg = str(e.value)
    assert "待你确认的候选，不是答案" in msg
    assert "Bahamas (the) → Bahamas" in msg
    assert "不要让工具替你认定" in msg


def test_显式对照表才会真的改名():
    L = pd.DataFrame({"unit": ["Bahamas (the)"] * 12, "year": range(2000, 2012), "y": 1.0})
    R = pd.DataFrame({"entity": ["Bahamas"] * 12, "year": range(2000, 2012), "x": 2.0})
    out, notes = _merge(L, R, crosswalk={"Bahamas (the)": "Bahamas"})
    assert len(out) == 12
    assert any("显式对照" in n for n in notes)
    assert "Bahamas (the)" in set(out.unit), "原来的写法要保留，否则事后查不到出处"


def test_大小写和空白折叠但不做别的():
    """`USA` 和 `usa` 是同一个国家，这条没有歧义。

    再往下（去掉 `(the)`、`Korea, Rep.` → `South Korea`）就是判断，
    **必须由人显式给对照表**。
    """
    L = pd.DataFrame({"unit": [" USA "] * 12, "year": range(2000, 2012), "y": 1.0})
    R = pd.DataFrame({"entity": ["usa"] * 12, "year": range(2000, 2012), "x": 2.0})
    out, _ = _merge(L, R)
    assert len(out) == 12


# --------------------------------------------------- 另一种静默失败


def test_右表键重复会让行数变多要警告():
    """「国家-年」拼「国家-年」本该一对一。

    **行数变多也是静默失败**，而且比变少更难发现——
    没人会想到「拼完之后数据变多了」。
    """
    L = _left(n_units=12)
    R = _right([f"U{u:02d}" for u in range(12)])
    R = pd.concat([R, R.head(20)], ignore_index=True)      # 造重复键
    out, notes = _merge(L, R)
    assert len(out) > len(L)
    assert any("重复" in n and "多于" in n for n in notes)


def test_键的列名写错要当场说清楚():
    L, R = _left(), _right([f"U{u:02d}" for u in range(40)])
    with pytest.raises(MergeRefused, match="没有这些列"):
        _merge(L, R, left_on=["contry", "year"])          # 打错一个字母


def test_两边键数目对不上要拒绝():
    L, R = _left(), _right([f"U{u:02d}" for u in range(40)])
    with pytest.raises(MergeRefused, match="键数目对不上"):
        _merge(L, R, left_on=["unit", "year"], right_on=["entity"])


# ─────────────────────────── 拼完不留右表键列的后缀副本


def test_右表同名键列不留后缀副本():
    """验证跑：`--on country,year` 拼完留下 country_wb_…、year_wb_…，只在 1990+ 有值。
    画像把它们认成了个体/时间键，190 国 × 61 年的面板判成 189 × 31，
    执行者只好手写脚本删列——工具留下的垃圾不该让人收拾。"""
    L = _left()
    R = _right([f"U{u:02d}" for u in range(40)]).rename(columns={"entity": "unit"})
    merged, notes = merge_tables(L, R, left_on=["unit", "year"], right_on=["unit", "year"],
                                 left_name="qog", right_name="wb")
    assert not [c for c in merged.columns if c.endswith("_wb")], list(merged.columns)
    assert {"unit", "year", "y", "x"} <= set(merged.columns)
    assert any("只保留左表那份" in n for n in notes)
