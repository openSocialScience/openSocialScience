"""仿真骨架 T2：环节 4 判「这个模型跑得出可判的东西吗」，不算功效。

因果算功效（能不能测出效应）、描述判精度、测量判「证据做不做得出来」；
**仿真的可行性根本不是样本量问题**——想跑多少个小人就跑多少个，跑十万个也不会让结论更可信。
真正的可行性是跑出来的东西够不够分辨条件之间的差别（仿真骨架设计稿第三节）。

改这一支之前，一份标成 simulation 的计划在环节 4 拿到的是「功效 0.25，判词 fail」——
被当成因果研究算了统计功效然后判不合格。跟描述骨架做之前「描述类被功效门误伤」同一件事。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from oss.api import power as power_api
from oss.api import profile as profile_api
from oss.cli.main import app
from oss.core.contracts import AnalysisPlan, Idea, IdeaSet, RealBenchmark, SimulationSpec
from oss.core.findings import code_of

runner = CliRunner()

SPEC = {"mechanism": "邻里同质化偏好使住户迁出异质社区",
        "pattern": "隔离指数随偏好阈值单调上升", "stands_in_for_humans": False}


# ─────────────────────────── 夹具


def _run(tmp_path: Path, rtype: str = "simulation") -> Path:
    run = tmp_path / "r"
    run.mkdir()
    rng = np.random.default_rng(3)
    pd.DataFrame({"segregation": rng.normal(size=200), "threshold": rng.integers(0, 2, 200),
                  "trust_syn": rng.integers(1, 6, 200)}).to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id="i1", title="T", research_question="Q", research_type=rtype)]
            ).save(run / "ideas.json")
    return run


def _plan(run: Path, *, rtype: str = "simulation", **sim) -> None:
    AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="segregation",
                 research_type=rtype,
                 simulation=SimulationSpec(**(SPEC | sim)) if sim is not None else None,
                 ).save(run / "plan.json")


def _box(run: Path, *, downgrade: bool = False, single: bool = False,
         conditions: dict | None = None, **meta) -> None:
    """落一份入库档案。字段跟 `oss sim import` 写的那份同形。"""
    state = {
        "box": str(run / "box"), "downgrade": downgrade, "single_condition": single,
        "warnings": [],
        "meta": {"simulator": "oasis", "simulator_version": "0.3.1", "model": "gpt-4o-2024-08-06",
                 "model_provider": "openai", "temperature": 0.7, "prompts": "见附录",
                 "population": "1000 名模拟居民", "n_reps": 5, "rounds": 20,
                 "interview_plan": "每 5 轮一次", "contamination_note": "不确定",
                 "conditions": conditions if conditions is not None
                 else {"threshold_low": "阈值 0.2", "threshold_high": "阈值 0.5"}} | meta,
    }
    (run / "sim_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ═══════════════════════ 一、不再算功效


def test_仿真类不再出功效判词(tmp_path: Path) -> None:
    """改之前这里是「功效 0.25，判词 fail」——仿真研究被当成因果研究判了不合格。"""
    run = _run(tmp_path)
    _plan(run)
    _box(run)
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["mode"] == "readiness" and rep["verdict"] == "pass"
    # 不算效应量：折价后的效应量、最小可测效应、功效三个数都不出
    assert rep["effect_size_raw"] == 0.0 and rep["effect_size_adjusted"] == 0.0
    assert rep["mde"] == 0.0 and rep["power"] == 0.0
    assert "功效" not in out["summary"], out["summary"]
    cli = runner.invoke(app, ["power", "--run", str(run), "--idea", "i1"])
    assert cli.exit_code == 0 and "功效 0.00" not in cli.output, cli.output
    # 没给效应量出处也不封顶灰区——那条封顶是功效那一路的
    assert not [w for w in out["data"]["warnings"] if "效应量没给出处" in w]


def test_同一份数据的因果题照旧算功效(tmp_path: Path) -> None:
    """分路只加了一支：别的类型一行不动。"""
    run = _run(tmp_path, rtype="causal")
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power" and out["data"]["power"] > 0


# ═══════════════════════ 二、还没入库：三条核不了，不停


def test_还没入库的_三条核不了但不停(tmp_path: Path) -> None:
    """环节 4 常常在花钱跑模拟之前——三条判据核不了，说出来，不停。"""
    run = _run(tmp_path)
    _plan(run)
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["mode"] == "readiness" and out["data"]["verdict"] == "pass"
    assert any("sim_state.json" in w for w in out["data"]["warnings"]), out["data"]["warnings"]
    assert "还没判" in "\n".join(out["data"]["reason_lines"])


# ═══════════════════════ 三、条件够不够


def test_单条件_警告不停(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _plan(run)
    _box(run, single=True, conditions={"baseline": "只有这一条"})
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["verdict"] == "pass", "只该警告，不该停"
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.single_condition"]
    assert hit, out["data"]["warnings"]
    # 处方要直接指向药，不能只描述病
    assert "模式展示" in hit[0] and "conditions 加一条" in hit[0]


def test_两个条件_不警告(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _plan(run)
    _box(run)
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"]
                if code_of(w) == "plan.simulation.single_condition"]


# ═══════════════════════ 四、重复够不够


def test_重复不足_警告不停且给降级原句(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _plan(run)
    _box(run, downgrade=True)
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["verdict"] == "pass"
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.low_reps"]
    assert hit, out["data"]["warnings"]
    assert "单次模拟无法区分随机波动与真实规律" in hit[0], "降级句要逐字给，不能只说「去降级」"


def test_判据不重算_读的是货箱算好的那两个字段(tmp_path: Path) -> None:
    """条件数与重复数由 simbox 验货时算，环节 4 只读 sim_state.json——判据只有一处。

    这里把 `meta.n_reps` 写成充足的 5，只把 `downgrade` 置真：若环节 4 自己重算，
    这条就不会响。
    """
    run = _run(tmp_path)
    _plan(run)
    _box(run, downgrade=True, n_reps=5)
    out = power_api.power(str(run), idea="i1")
    assert [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.low_reps"]


# ═══════════════════════ 五、对照物在不在


def test_主张代表真人而对照数据不在_警告不停(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _plan(run, stands_in_for_humans=True,
          real_benchmark=RealBenchmark(data="真实调查数据.csv", pairs=[("trust_syn", "a33")]))
    _box(run)
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["verdict"] == "pass"
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.no_benchmark"]
    assert hit, out["data"]["warnings"]
    assert "方差会被压扁" in hit[0]


def test_对照数据就在运行目录里_不警告(tmp_path: Path) -> None:
    run = _run(tmp_path)
    (run / "real.csv").write_text("a33\n3\n", encoding="utf-8")
    _plan(run, stands_in_for_humans=True,
          real_benchmark=RealBenchmark(data="real.csv", pairs=[("trust_syn", "a33")]))
    _box(run)
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"]
                if code_of(w) == "plan.simulation.no_benchmark"]


def test_没主张代表真人的_这一条不核(tmp_path: Path) -> None:
    """面向模式的仿真本来就不需要真人对照——逼它去找真人数据是错的。"""
    run = _run(tmp_path)
    _plan(run, stands_in_for_humans=False)
    _box(run)
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"]
                if code_of(w) == "plan.simulation.no_benchmark"]
    assert "这一条不核" in "\n".join(out["data"]["reason_lines"])


def test_把数据包的标题当名字写_也算不在(tmp_path: Path) -> None:
    """作者最常见的错是写标题（「中国综合社会调查」）而不是包名（cgss2017）。"""
    run = _run(tmp_path)
    _plan(run, stands_in_for_humans=True,
          real_benchmark=RealBenchmark(data="中国综合社会调查", pairs=[("trust_syn", "a33")]))
    _box(run)
    out = power_api.power(str(run), idea="i1")
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.no_benchmark"]
    assert hit and "既不是数据包名" in hit[0], out["data"]["warnings"]


# ═══════════════════════ 六、三条一起响也不停


def test_三条全不合格_照样落盘不停(tmp_path: Path) -> None:
    """单条件与重复不足都是正当研究，正确答案是把话说软，不是不许做。"""
    run = _run(tmp_path)
    _plan(run, stands_in_for_humans=True,
          real_benchmark=RealBenchmark(data="没有这份.csv", pairs=[("trust_syn", "a33")]))
    _box(run, downgrade=True, single=True, conditions={"baseline": "只有这一条"})
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["verdict"] == "pass" and (run / "power.json").exists()
    codes = {code_of(w) for w in out["data"]["warnings"]}
    assert {"plan.simulation.single_condition", "plan.simulation.low_reps",
            "plan.simulation.no_benchmark"} <= codes, codes


@pytest.mark.parametrize("rtype", ["causal", "descriptive", "measurement"])
def test_别的类型不受这一支影响(rtype: str, tmp_path: Path) -> None:
    run = _run(tmp_path, rtype=rtype)
    _box(run, single=True, downgrade=True)   # 盘上有货箱也不该让别的类型改判法
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] != "readiness"


# ═══════════════════════ 七、入库档案会过期——三条判据全读它


def test_货箱配料单改过而没重新入库_要报(tmp_path: Path) -> None:
    """**三条判据读的是 sim_state.json，而它只是盘上一个普通 JSON。**

    货箱重跑过、配料单改过而没重新入库，它就停在入库那一刻：`downgrade` 还是旧的 false，
    三条一声不响。判据不能只信档案——货箱还在盘上就拿它现在的配料单对一遍。
    """
    run = _run(tmp_path)
    _plan(run)
    _box(run)                                   # 入库时 n_reps=5、不降级
    box = run / "box"
    box.mkdir()
    state = json.loads((run / "sim_state.json").read_text(encoding="utf-8"))
    state["box"] = str(box)
    (run / "sim_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    now = dict(state["meta"], n_reps=1)         # 货箱后来改成了只跑一次
    (box / "sim.meta.json").write_text(json.dumps(now, ensure_ascii=False), encoding="utf-8")

    out = power_api.power(str(run), idea="i1")
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.state_stale"]
    assert hit, out["data"]["warnings"]
    assert "n_reps" in hit[0] and "oss sim import" in hit[0]


def test_货箱没搬走且配料单没改_不报(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _plan(run)
    _box(run)
    box = run / "box"
    box.mkdir()
    state = json.loads((run / "sim_state.json").read_text(encoding="utf-8"))
    state["box"] = str(box)
    (run / "sim_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (box / "sim.meta.json").write_text(json.dumps(state["meta"], ensure_ascii=False), encoding="utf-8")
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.state_stale"]


def test_货箱已经搬走的_不冤枉(tmp_path: Path) -> None:
    """核不了就别报——货箱不在盘上时说「对不上」是冤枉。"""
    run = _run(tmp_path)
    _plan(run)
    _box(run)                                   # state["box"] 指向一个不存在的目录
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"] if code_of(w) == "plan.simulation.state_stale"]
