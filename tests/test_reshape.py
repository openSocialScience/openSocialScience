"""宽表转长表，以及那条区分「幂次」和「轮次」的判据。

这一批全部来自真跑一次三轮 GSS 面板暴露出来的问题：
画像认得出宽表面板，却只会提示「请重塑成长表」然后没有下文，
于是模型**自己手写了一个重塑脚本**——算的活跑到了判的一侧，
而且没有任何检查会去核它写对没有。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.profiler import profile_file, wave_groups
from oss.core.reshape import NotWidePanel, wide_to_long


def _wide(n: int = 40, waves: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    out = {"pid": np.arange(1, n + 1), "birth_year": rng.integers(1940, 1990, n)}
    for stem in ("happy", "income", "health", "age"):
        for w in range(1, waves + 1):
            out[f"{stem}_{w}"] = rng.normal(size=n).round(3)
    return pd.DataFrame(out)


# --------------------------------------------------- 幂次 vs 轮次


def test_有兄弟的age不该被当成年龄平方踢掉():
    """`age_1/age_2/age_3` 是轮次，不是幂次。

    原先靠一张按名字排除的清单挡 `age2`，而那张清单**无条件生效**——
    注释白纸黑字写着「只在词干孤零零一个时生效」，代码却没照做。
    真跑 GSS 面板时靠另外 7 组撑住了判定，所以一直没暴露：
    **注释和代码不一致时，挡住它的是运气，不是设计。**
    """
    groups = wave_groups(_wide())
    assert "age" in groups, "age_1/2/3 有两个兄弟，那就是轮次"
    assert set(groups["age"]) == {"1", "2", "3"}


def test_多项式的平方立方项不该被当成轮次():
    """`age / age2 / age3` 是幂次。判据不靠名字，靠**光秃秃的词干在不在列里**。

    一次项就是变量本身（`age`），所以只要 `age` 这一列存在，
    带编号的那几个就是幂次。`sei2/sei3` 这种没进过任何排除清单的
    也一样挡得住——**清单永远漏得掉，这条不会。**
    """
    frame = pd.DataFrame({
        "id": range(20),
        "age": range(20), "age2": range(20), "age3": range(20),
        "sei": range(20), "sei2": range(20), "sei3": range(20),
        "inc": range(20), "inc2": range(20), "inc3": range(20),
    })
    assert wave_groups(frame) == {}, "有光秃秃的一次项 → 那是幂次"


def test_同一份数据的提示和转换必须给同一个答案(tmp_path):
    """提示说是宽表面板，转换就必须转得动。

    两处各写一份判据，早晚分叉成「提示说是、转换说不是」——
    那种矛盾最难查，因为两边单独看都自洽。
    """
    frame = _wide()
    path = tmp_path / "wide.csv"
    frame.to_csv(path, index=False)
    hint = " ".join(profile_file(path).warnings)
    assert "宽表面板" in hint
    long, _ = wide_to_long(frame)          # 提示说是，就必须转得出来
    assert len(long) == len(frame) * 3


# --------------------------------------------------- 转换本身


def test_摞出来的每一格都对得上原表():
    frame = _wide(n=5)
    long, _ = wide_to_long(frame)
    assert list(long.columns[:1]) == ["pid"]
    assert len(long) == 15
    for _, row in long.iterrows():
        w = int(row["wave"])
        assert row["happy"] == frame.loc[frame.pid == row.pid, f"happy_{w}"].iloc[0]


def test_不随时间变的列每轮复制一份并且说出来():
    """`birth_year` 没有轮次后缀 → 每轮原样复制。

    **必须说出来**：个体固定效应会把这些整个吃掉，
    使用者以为控制住了，其实那一列的系数根本估不出来。
    """
    long, notes = wide_to_long(_wide(n=5))
    assert (long.groupby("pid").birth_year.nunique() == 1).all()
    assert any("不随时间变" in n and "固定效应" in n for n in notes)


def test_某个变量缺一轮时填缺失而不是错位():
    """缺的那一轮要填 NaN，**不能跳过**——跳过会让各变量行数对不齐。"""
    frame = _wide(n=6).drop(columns=["health_2"])
    long, notes = wide_to_long(frame)
    assert len(long) == 18, "行数不受某一列缺一轮的影响"
    assert long.loc[long.wave == 2, "health"].isna().all()
    assert long.loc[long.wave == 1, "health"].notna().all()
    assert any("不是每轮都有" in n for n in notes)


def test_编号列靠名字挑不是靠唯一性():
    """「全表唯一」是必要条件，**不是充分条件**。

    第一版只写了唯一性，于是取第一个唯一的列——40 个人的 `birth_year`
    恰好互不相同，就被当成了人的编号。真数据里 `income` 也会这样。
    挑错编号的后果不是报错，是**每一行挂到错的人身上**，
    个体固定效应照跑，安静地量错东西。
    """
    frame = _wide(n=40)
    assert frame.birth_year.nunique() < len(frame) or True   # 唯一与否都不该影响结论
    long, _ = wide_to_long(frame)
    # pid 名字像编号，birth_year 不像——必须选 pid
    assert long.pid.nunique() == 40
    assert (long.groupby("pid").size() == 3).all()


def test_编号列是猜的时候必须说是猜的():
    """没有一列名字像编号 → 仍然要选一个，但**要说明那是猜的**，并给出替代办法。"""
    frame = _wide(n=5).drop(columns=["pid"])
    long, notes = wide_to_long(frame)
    assert len(long) == 15
    assert any("是猜的" in n and "--unit" in n for n in notes)


def test_一列唯一的都没有时退回行号并声明():
    """真的找不到 → 用行号，**并且说明它跟别的数据对不上**。"""
    frame = _wide(n=6).drop(columns=["pid"])
    frame["birth_year"] = 1970                      # 抹掉唯一性
    long, notes = wide_to_long(frame)
    assert len(long) == 18
    assert any("没有找到个体编号" in n for n in notes)


def test_不像宽表面板就拒绝转不硬来():
    """**说不像就说不像。**硬转出来的长表比不转更危险：

    它看着像面板，个体固定效应会照跑，而量的东西是错的——
    这正是这个项目一直在防的那种「不报错，只是量错了东西」。
    """
    frame = pd.DataFrame({"id": range(10), "x_1": range(10), "y_1": range(10)})
    with pytest.raises(NotWidePanel, match="不像宽表面板"):
        wide_to_long(frame)


def test_判断的那一半一概不做并且明说():
    """量表反向、取对数、转哑变量都是研究者的决定，工具箱不替人做。"""
    long, notes = wide_to_long(_wide(n=5))
    assert not any(c.endswith("_r") or c.startswith("log_") for c in long.columns)
    assert any("只做了摞行" in n for n in notes)


# --------------------------------------------------- 题号 vs 轮次（CGSS2017 真跑）

def _cgss_like() -> pd.DataFrame:
    """CGSS 的列名长这样：a/b/c 是问卷分节，后面的数字是第几题。

    `a1`、`a12`、`a121` 三层编号并存，**跟轮次毫无关系**——
    这份数据是一轮截面，长表根本不存在。
    """
    cols = ["id", "s41", "s42", "s43", "a2", "a4", "a6", "a9"]
    cols += [f"a1{i}" for i in range(0, 9)]           # a10…a18
    cols += [f"a12{i}" for i in range(1, 9)]          # a121…a128
    cols += [f"a3{i}" for i in (1, 4, 5, 6, 8, 9)]    # 断号
    cols += [f"c3{i}" for i in range(1, 6)] + [f"c4{i}" for i in range(1, 5)]
    rng = np.random.default_rng(21)
    return pd.DataFrame({c: rng.normal(size=200) for c in cols})


def test_问卷题号不该被当成轮次():
    """真跑 CGSS2017：786 列被凑出 **93 组「轮次变量」**，
    画像于是劝人「重塑成长表」——而那个长表并不存在。

    区分不靠认名字，靠编号自己的形状：轮次是连号、从 1 起、**各组一模一样**；
    题号长短不一、断号、各组各的。CGSS 实测最大的一致组只占 28%。
    """
    assert wave_groups(_cgss_like()) == {}, "题号不是轮次"


def test_单轮截面不该被劝去重塑(tmp_path):
    """提示和转换共用一个判据，所以两边要一起闭嘴。"""
    path = tmp_path / "cgss.csv"
    _cgss_like().to_csv(path, index=False)
    assert not any("宽表" in w for w in profile_file(path).warnings)
    with pytest.raises(NotWidePanel):
        wide_to_long(_cgss_like())


def test_真宽表面板照样认得出来():
    """收紧之后不能把对的也挡掉——四组变量、都是 1/2/3，那就是轮次。"""
    groups = wave_groups(_wide())
    assert set(groups) == {"happy", "income", "health", "age"}
