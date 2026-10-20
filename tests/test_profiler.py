"""数据画像的单元测试。

**为什么这个文件必须存在**：`detect_structure` 这类函数错了**不会报错**——
它会一路产出识别分很低的设计，看起来像「想法不够好」。
旧实现只有端到端测试间接碰到它（PLAN.md §7 问题 ③），等于没测。

每个用例都对应一种真实会犯的错。
"""

from __future__ import annotations

import pandas as pd
import pytest

from oss.core.contracts import VariableProfile
from oss.core.profiler import (
    _is_time_like,
    _name_hits,
    _pick,
    _semantic_type,
    detect_nesting,
    detect_structure,
    profile_table,
)
from oss.core.loaders import LoadedTable


def table(frame: pd.DataFrame, **kw) -> LoadedTable:
    import pathlib

    p = pathlib.Path(__file__).parent / "fixtures" / "tiny.csv"
    return LoadedTable(frame=frame, path=p, fmt="csv", **kw)


def var(name: str, semantic: str = "continuous", n_unique: int = 10) -> VariableProfile:
    return VariableProfile(name=name, semantic_type=semantic, n_unique=n_unique)


# ------------------------------------------------------- 列名匹配的两个陷阱


def test_列名匹配按词元而不是裸子串() -> None:
    """`"id" in "resident_income"` 为真——裸子串会把收入当成个体编号。

    绑定阶段一旦把结果变量误判成 ID，整个研究设计就废了。
    """
    assert _name_hits("person_id", ("id",))
    assert _name_hits("pid", ("pid",))
    assert not _name_hits("resident_income", ("id",))
    assert not _name_hits("residence", ("id",))


def test_名字像时间还不够取值也得像() -> None:
    """`edu_years`（受教育年限，取值 6-16）名字里有 year，但它不是时间索引。

    误判成时间列，整条识别策略就建立在错误的面板结构上。
    """
    years = pd.Series([2016, 2018, 2020, 2022] * 25)
    edu = pd.Series(list(range(6, 19)) * 8)

    assert _is_time_like("year", years, years.nunique())
    assert not _is_time_like("edu_years", edu, edu.nunique())


# --------------------------------------------------- _pick：阶段 1 修的那个 bug


def test_pick_按唯一值挑而不是取第一个() -> None:
    """旧实现直接 `named[0].name`，谁在列序上靠前就选谁。

    数据里同时有 province_code 和 person_id 时选错了，整个面板判定跟着错。
    """
    variables = [
        var("province_code", "identifier", n_unique=31),
        var("person_id", "identifier", n_unique=12800),
    ]
    # 个体标识取唯一值最多的
    assert _pick(variables, ("id", "code"), {"identifier"}, prefer="most_unique") == "person_id"
    # 时间列取唯一值最少的
    assert _pick(variables, ("id", "code"), {"identifier"}, prefer="fewest_unique") == "province_code"


# ----------------------------------------------------------------- 结构判定


def test_同一个体只出现一次的判为重复截面而非面板() -> None:
    """CGSS 这类重复截面极易被误判成面板。误判之后会一路产出做不了的 DID 设计。"""
    frame = pd.DataFrame(
        {
            "person_id": range(100),          # 每个 id 只出现一次
            "year": [2016] * 50 + [2018] * 50,
            "income": range(100),
        }
    )
    prof = profile_table(table(frame))
    assert prof.structure.kind == "repeated_cross_section"
    assert prof.structure.unit_id is None, "重复截面不该有个体标识"


def test_同一个体跨多期出现的判为面板() -> None:
    frame = pd.DataFrame(
        {
            "person_id": list(range(25)) * 4,
            "year": sum(([y] * 25 for y in (2016, 2018, 2020, 2022)), []),
            "income": range(100),
        }
    )
    prof = profile_table(table(frame))
    assert prof.structure.kind == "panel_balanced"
    assert prof.structure.unit_id == "person_id"
    assert prof.structure.time_id == "year"
    assert prof.structure.n_units == 25
    assert prof.structure.n_periods == 4


def test_缺个体维度的判为时序() -> None:
    frame = pd.DataFrame({"year": range(1990, 2020), "gdp": [1e4 + i * 137.5 for i in range(30)]})
    assert profile_table(table(frame)).structure.kind == "time_series"


def test_缺时间维度的判为截面() -> None:
    frame = pd.DataFrame({"person_id": range(100), "income": range(100)})
    assert profile_table(table(frame)).structure.kind == "cross_section"


# -------------------------------------------------- 嵌套层级：阶段 1 新补的


def test_嵌套层级从细到粗() -> None:
    """个体嵌套于县，县嵌套于省。这是多层模型与层次匹配检查的前提。"""
    frame = pd.DataFrame(
        {
            "person_id": range(60),
            "county": [f"C{i // 10}" for i in range(60)],     # 6 个县，每县 10 人
            "province": [f"P{i // 20}" for i in range(60)],   # 3 个省，每省 2 个县
            "income": [50.0 + i for i in range(60)],
        }
    )
    variables = profile_table(table(frame)).variables
    assert detect_nesting(frame, variables) == ["person_id", "county", "province"]


def test_交叉分类不算嵌套() -> None:
    """性别和年份互相交叉，谁也不嵌套于谁——不该被当成层级。"""
    frame = pd.DataFrame(
        {
            "gender": ["M", "F"] * 50,
            "year": [2016, 2016, 2018, 2018] * 25,
            "income": [50.0 + i * 1.3 for i in range(100)],
        }
    )
    variables = profile_table(table(frame)).variables
    assert detect_nesting(frame, variables) == []


# ----------------------------------------------------------------- 组内变异


def test_不随时间变化的列组内变异为零() -> None:
    """为 0 意味着固定效应会把这一列整个吃掉——回归里它是常数。"""
    frame = pd.DataFrame(
        {
            "person_id": list(range(25)) * 4,
            "year": sum(([y] * 25 for y in (2016, 2018, 2020, 2022)), []),
            "gender": [i % 2 for i in range(25)] * 4,     # 个体内恒定
            "income": [50.0 + i * 1.3 for i in range(100)],  # 个体内变化
        }
    )
    prof = profile_table(table(frame))
    assert prof.var("gender").within_variation == 0.0
    assert prof.var("income").within_variation > 0


def test_整列缺失不触发计算错误() -> None:
    frame = pd.DataFrame(
        {
            "person_id": list(range(25)) * 4,
            "year": sum(([y] * 25 for y in (2016, 2018, 2020, 2022)), []),
            "empty_col": [None] * 100,
        }
    )
    prof = profile_table(table(frame))
    assert "empty_col" in prof.all_missing_columns


def test_全唯一的整数列会被判成标识符() -> None:
    """这条启发式有代价，记录下来。

    `n_unique > 0.95 * n 且是整数` → identifier。真实的收入通常是浮点或有并列，
    所以一般不会误判；但**如果某个真实变量恰好是全唯一的整数，它会被跳过组内变异计算**。
    发现这类误判时，看 `distribution` 里的取值范围来区分。
    """
    s = pd.Series(range(100))
    assert _semantic_type("income", s, s.nunique(), False) == "identifier"
    # 浮点就不会
    f = pd.Series([50.0 + i * 1.3 for i in range(100)])
    assert _semantic_type("income", f, f.nunique(), False) == "continuous"


# ----------------------------------------------------------------- 语义类型


@pytest.mark.parametrize(
    "name,values,expected",
    [
        ("gender", [0, 1] * 50, "binary"),
        ("income", [float(i) * 1.5 for i in range(100)], "continuous"),
        ("year", [2016, 2018, 2020, 2022] * 25, "date"),
        ("province", [f"P{i % 5}" for i in range(100)], "categorical"),
        ("sample_weight", [1.0 + i / 100 for i in range(100)], "weight"),
    ],
)
def test_语义类型判定(name: str, values: list, expected: str) -> None:
    s = pd.Series(values)
    assert _semantic_type(name, s.dropna(), s.nunique(), False) == expected


# ------------------------------------------------------------------- 其它


def test_恒定列与敏感列被标出来() -> None:
    frame = pd.DataFrame(
        {
            "person_id": range(100),
            "phone": [f"138{i:08d}" for i in range(100)],
            "const": [1] * 100,
        }
    )
    prof = profile_table(table(frame))
    assert "const" in prof.constant_columns
    assert "phone" in prof.sensitive_columns


def test_内容哈希进画像() -> None:
    """复现时用它确认「用的是同一份数据」。"""
    from oss.core.profiler import profile_file

    prof = profile_file("tests/fixtures/tiny.csv")
    assert len(prof.file.sha256) == 32
    assert prof.profile_id.startswith("prof_")


def test_变量标签被抽出来() -> None:
    """Stata / SPSS 自带标签，是免费的语义信息——直接决定绑定准确率。"""
    frame = pd.DataFrame({"q23a": range(100), "person_id": range(100)})
    prof = profile_table(table(frame, variable_labels={"q23a": "家庭年总收入（元）"}))
    assert prof.var("q23a").label == "家庭年总收入（元）"
