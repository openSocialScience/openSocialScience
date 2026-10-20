"""测量骨架 Task 1：计划契约（设计稿 docs/design/2026-09-17-measurement-skeleton.md 第六节）。

这一批的原则（设计稿第二节）：**门只核「声明了什么 + 产出在不在」，不读论文里的论证**。
契约是这条原则的落点——效度、信度、构念全是语义判断，程序判不了；
所以作者在结构化字段里自己声明，程序只核声明的合法性与完整性。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oss.core.contracts import AnalysisPlan, Estimand, MeasurementSpec


# ══════════════════════════════ 第七个绕行洞：写了这一块，kind 就得填


def test_写了测量块就必须填kind() -> None:
    """**留空比标错还轻**：造尺子那一路的门全都要求 `kind == "instrument"`，
    标成 `application` 至少还有 `kind_conflict` 兜着，留空则一道门都不响。"""
    with pytest.raises(ValidationError) as err:
        MeasurementSpec(construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"])
    msg = str(err.value)
    assert "instrument" in msg and "application" in msg, msg     # 只说「缺 kind」不够：要说该填哪个


def test_kind填了的照常过() -> None:
    for kind in ("instrument", "application"):
        m = MeasurementSpec(kind=kind, construct="对陌生人的一般信任",
                            indicators=["q1", "q2", "q3"], validity_evidence=["structural"])
        assert m.kind == kind


# ══════════════════════════════ 两栏必填


def test_测量块两栏必填() -> None:
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="instrument")                          # 缺 construct 与 indicators
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="application", construct="社会信任")     # 缺 indicators
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="application", indicators=["q1"])      # 缺 construct
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="application", construct="  ", indicators=["q1"])   # 只有空白也算没写


def test_测量块不认得的键直接拒() -> None:
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="application", construct="社会信任",
                        indicator=["q1"])                           # 少了一个 s：照抄错的键不许悄悄变成空指标


# ══════════════════════════════ 效度证据按类给，不给整体判断


def test_造尺子必须声明效度证据类别() -> None:
    with pytest.raises(ValidationError) as err:
        MeasurementSpec(kind="instrument", construct="社会信任", indicators=["q1", "q2", "q3"])
    assert "效度" in str(err.value)


def test_效度证据只认四个类别() -> None:
    with pytest.raises(ValidationError) as err:
        MeasurementSpec(kind="instrument", construct="X", indicators=["a", "b", "c"],
                        validity_evidence=["效度良好"])               # 整体判断，不是证据类别
    msg = str(err.value)
    for cat in ("content", "structural", "criterion", "discriminant"):
        assert cat in msg, msg      # 只说「取值非法」不够：作者要知道该写哪四类之一


def test_四个类别都收得下() -> None:
    m = MeasurementSpec(kind="instrument", construct="对陌生人的一般信任",
                        indicators=["q12a", "q12b", "q12c"],
                        validity_evidence=["content", "structural", "criterion", "discriminant"])
    assert len(m.validity_evidence) == 4


def test_用尺子不强制效度证据() -> None:
    m = MeasurementSpec(kind="application", construct="社会信任", indicators=["q1"])
    assert m.validity_evidence == [] and m.source_quality == "" and m.groups_compared == []


def test_数据来源只认四种口径() -> None:
    assert MeasurementSpec(kind="application", construct="每周工作时长",
                           indicators=["hours"], source_quality="self_report").source_quality == "self_report"
    with pytest.raises(ValidationError):
        MeasurementSpec(kind="application", construct="每周工作时长", indicators=["hours"],
                        source_quality="问卷自己填的")                 # 散文不是口径


# ══════════════════════════════ 计划带得上这一块


def test_计划带得上测量块(tmp_path: Path) -> None:
    spec = MeasurementSpec(kind="instrument", construct="对陌生人的一般信任",
                           indicators=["q12a", "q12b", "q12c", "q12d"],
                           validity_evidence=["structural", "criterion"],
                           groups_compared=["urban"], source_quality="self_report")
    plan = AnalysisPlan(plan_id="p", idea_id="i", research_type="measurement",
                        estimator="describe", outcome="q12a",
                        estimand=Estimand(unit_quantity="量表得分", target_population="城镇成年人",
                                          link="因子得分对上一般信任"),
                        measurement=spec)
    f = tmp_path / "plan.json"
    plan.save(f)
    back = AnalysisPlan.load(f)
    assert back.measurement == spec
    assert json.loads(f.read_text(encoding="utf-8"))["measurement"]["kind"] == "instrument"


def test_老计划没有measurement照样读得进(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "p", "idea_id": "i", "outcome": "y", "treatment": "x"}),
                 encoding="utf-8")
    assert AnalysisPlan.load(p).measurement is None


# ══════════════════════════════ 样例：造尺子一份、用尺子一份


def test_样例里有造尺子与用尺子两个测量块() -> None:
    from oss.api import schema as schema_api

    d = schema_api.describe("plan", example=True)["data"]
    assert json.loads(d["example_json"]) == d["example"]         # 样例本身仍是一份合法 JSON
    text = d["measurement_examples"]
    assert '"kind": "instrument"' in text and '"kind": "application"' in text
    assert '"validity_evidence"' in text and '"groups_compared"' in text
