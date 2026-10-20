"""仿真骨架 T1：计划契约的仿真块。

对应因果的「估计量三步」、描述的「描述量 + 目标总体」、测量的「构念 + 指标 + 效度证据」。
仿真研究答的是「这个模型里，这套机制会不会产生这个模式」——机制与模式两栏必填。

**主张合成样本代表真人的，必须给出真实对照数据**：合成样本的方差会被压扁
（Bisbee 2024），拿它当调查数据用，标准误会假小、显著性会假强；没有对照就核不出这件事。
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from oss.api import schema as schema_api
from oss.cli.main import app
from oss.core.contracts import AnalysisPlan, RealBenchmark, SimulationSpec

runner = CliRunner()

OK = {"mechanism": "邻里同质化偏好使住户迁出异质社区", "pattern": "隔离指数随阈值单调上升"}
#: 只做「机制能不能产生这个模式」的那一档——**这一栏必须表态，没有缺省**
OK_F = {**OK, "stands_in_for_humans": False}


# ═══════════════════════ 一、两栏必填


def test_机制与模式都给了就合法() -> None:
    spec = SimulationSpec(**OK_F)
    assert spec.mechanism and spec.pattern
    assert spec.stands_in_for_humans is False and spec.real_benchmark is None


@pytest.mark.parametrize("missing", ["mechanism", "pattern"])
def test_缺一栏就拒(missing: str) -> None:
    bad = dict(OK) | {missing: "   "}
    with pytest.raises(ValidationError) as ei:
        SimulationSpec(**bad)
    assert missing in str(ei.value), ei.value


def test_报错里要说清这一栏是什么_不能只说非法() -> None:
    """作者看见「缺 pattern」也不知道该写什么——报错要带上那一栏的意思。"""
    with pytest.raises(ValidationError) as ei:
        SimulationSpec(mechanism="x", pattern="")
    assert "该重现的已知模式" in str(ei.value), ei.value


# ═══════════════════════ 二、主张代表真人就得给对照物


def test_主张代表真人却没给对照_拒() -> None:
    with pytest.raises(ValidationError) as ei:
        SimulationSpec(**OK, stands_in_for_humans=True)
    msg = str(ei.value)
    assert "real_benchmark" in msg and "方差会被压扁" in msg, msg


def test_给了对照数据却一对列都没配_照样拒() -> None:
    """「有 data 没 pairs」读起来像是配过了——对账其实无从下手。"""
    with pytest.raises(ValidationError):
        SimulationSpec(**OK, stands_in_for_humans=True,
                       real_benchmark=RealBenchmark(data="cgss2017", pairs=[]))


def test_对照列配对少一边_拒() -> None:
    with pytest.raises(ValidationError) as ei:
        RealBenchmark(data="cgss2017", pairs=[("trust_syn", "")])
    assert "两边都给" in str(ei.value), ei.value


def test_主张代表真人且对照齐全_合法() -> None:
    spec = SimulationSpec(**OK, stands_in_for_humans=True,
                          real_benchmark=RealBenchmark(
                              data="cgss2017", pairs=[("trust_syn", "a33")]))
    assert spec.real_benchmark is not None and spec.real_benchmark.pairs == [("trust_syn", "a33")]


def test_不主张代表真人的不强制对照() -> None:
    """很多 ABM 本来就不主张小人等于真人——逼它去找真人数据是错的。"""
    assert SimulationSpec(**OK, stands_in_for_humans=False).real_benchmark is None


# ═══════════════════════ 三、不收多余的栏


def test_货箱路径不进契约() -> None:
    """货箱由 oss sim import 写进 sim_state.json，计划里再抄一遍就是两处真相。"""
    with pytest.raises(ValidationError):
        SimulationSpec(**OK_F, box="/tmp/box")


def test_计划上挂得住仿真块_缺省是空() -> None:
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="y")
    assert plan.simulation is None
    plan2 = AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="y",
                         research_type="simulation", simulation=SimulationSpec(**OK_F))
    assert plan2.simulation is not None and plan2.simulation.pattern


# ═══════════════════════ 四、样例由契约自己写出来——例子违规就在导入这一刻炸


def test_样例里有仿真块的两种填法() -> None:
    text = schema_api.SIMULATION_EXAMPLES
    assert "不主张代表真人" in text and "主张这批合成样本代表真人" in text
    # 两份样例都必须是合法的 SimulationSpec（它们本来就是由 SimulationSpec 写出来的，
    # 这一条防的是日后有人改成手写字面量）
    dec, found = json.JSONDecoder(), []
    for i, ch in enumerate(text):
        if ch == "{" and (i == 0 or text[i - 1] == "\n"):
            obj, _ = dec.raw_decode(text[i:])
            found.append(obj)
    assert len(found) == 2, found
    for obj in found:
        SimulationSpec(**obj)


def test_schema_plan的样例带上仿真块() -> None:
    d = schema_api.describe("plan", example=True)["data"]
    assert d["simulation_examples"] and d["measurement_examples"]


def test_CLI_样例的标准输出仍是合法JSON() -> None:
    """两段说明都走 stderr——stdout 必须还是能直接喂给 json.loads 的一份。"""
    res = runner.invoke(app, ["schema", "plan", "--example"])
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)


def test_这一栏不许留空() -> None:
    """**留空与 false 原先是同一个值。**于是「声明了不代表真人」与「压根没想过」
    分不出来，而后者零代价地绕过了跟真实调查对账那道门——
    填 true 要给对照物、要过方差比那几条，留空什么都不用做。

    **这不是一栏猜不出答案的字段**（那种该从别处找迹象，像 `source_quality` 那样）：
    小人代不代表真人，是作者对自己主张的表态，没有不知道的道理。所以这里的修法是
    「必须表态」，两个洞同形而解法不同。
    """
    import pytest

    with pytest.raises(ValueError, match="没表态"):
        SimulationSpec(**OK)
    # 表了态就放行——false 也是一个主张，论文里相应只能说「在本模型内」的话
    assert SimulationSpec(**OK, stands_in_for_humans=False).stands_in_for_humans is False


def test_字段说明不许承诺做不到的检查() -> None:
    """这一栏的说明原先写着「留空/false 不等于免检：货箱与真实数据包同时在，
    照样按主张代表真人算」——**而那个检查故意没做**（手边有真实数据不等于主张
    小人代表真人，会把正当的 ABM 反复误报，见 P 类待办第 55 条）。
    **说明在骗作者**：他照着读会以为有一道门兜着，其实没有。
    """
    desc = SimulationSpec.model_fields["stands_in_for_humans"].description
    assert "不等于免检" not in desc, "说明又在承诺一个不存在的检查"
    assert "必须表态" in desc
