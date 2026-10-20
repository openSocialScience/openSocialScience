"""货箱验货：设计稿第三节的规则逐条钉死。

拒收规则（must，进 problems）：配料单缺必填项或 n_reps 类型不对；表钥匙列不齐；
钥匙组合不唯一；runs.csv 空文件、空表、列不齐、行截断、重复登记、引用的文件
不在或指到箱外、与配料单对不上；数据表出现未登记的 condition×rep；
数据表整张为空而清单不空。
结构性缺失（目录、必需文件、非法 JSON）抛异常，不进 problems。
降级规则（进 warnings，2026-08-31 用户拍板）：rep 不足 3 收但降级、点名短的条件；
单条件收但记；未完整跑收但记；登记的跑缺数据行收但记。
缺陷一次报全：多处坏要一次全点名，不许挤牙膏。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.core import simbox


def make_box(tmp_path: Path, *, reps=("1", "2", "3"), conditions=("A", "B"),
             meta_overrides: dict | None = None, drop_meta_key: str = "",
             states=True, states_header="condition,rep,wave,agent_id,stance",
             dup_row=False,
             events=False,
             events_header="condition,rep,step,seq,sim_time,agent_id,action,target",
             events_rows: list[str] | None = None,
             incomplete=0, runs_header="file,condition,rep,complete",
             runs_rows: list[str] | None = None, raw_files=True) -> Path:
    box = tmp_path / "box"
    box.mkdir()
    if states:
        lines = [states_header]
        for c in conditions:
            for r in reps:
                for w in ("0", "5"):
                    for a in ("0", "1"):
                        lines.append(f"{c},{r},{w},{a},4")
        if dup_row:
            lines.append(lines[-1])
        (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if events:
        if events_rows is None:
            events_rows = [f"{c},{r},1,{i + 1},2026-08-31 11:00:{i + 1:02d},0,like_post,1"
                           for i, (c, r) in enumerate(
                               [(c, r) for c in conditions for r in reps])]
        (box / "events.csv").write_text(
            "\n".join([events_header] + events_rows) + "\n", encoding="utf-8")
    if runs_rows is None:
        todo = [(c, r) for c in conditions for r in reps]
        runs_rows = [f"raw/{c}_{r}.db,{c},{r},"
                     + ("no" if i >= len(todo) - incomplete else "yes")
                     for i, (c, r) in enumerate(todo)]
    (box / "runs.csv").write_text("\n".join([runs_header] + runs_rows) + "\n",
                                  encoding="utf-8")
    if raw_files:
        for line in runs_rows:
            f = line.split(",")[0]
            if f and ".." not in f:
                (box / f).parent.mkdir(parents=True, exist_ok=True)
                (box / f).touch()
    meta = {
        "simulator": "oasis", "simulator_version": "0.2.5",
        "model": "deepseek-v4-flash", "model_provider": "ctis-relay",
        "temperature": "provider_default", "prompts": "见 runner.py",
        "population": "OASIS 匿名样本 46 人", "conditions": {c: f"条件{c}" for c in conditions},
        "n_reps": len(reps), "rounds": 10, "interview_plan": "第 0/5/10 轮",
        "contamination_note": "议题为虚构，模型不可能见过结局",
    }
    meta.update(meta_overrides or {})
    if drop_meta_key:
        meta.pop(drop_meta_key)
    (box / "sim.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (box / "runner.py").write_text("# 占位跑脚本\n", encoding="utf-8")
    return box


# ── 通过与结构性缺失


def test_合格货箱通过且无警告无降级(tmp_path):
    v = simbox.check_box(make_box(tmp_path))
    assert v.problems == [] and v.warnings == []
    assert v.downgrade is False and v.single_condition is False


def test_箱子目录不存在抛异常(tmp_path):
    with pytest.raises(FileNotFoundError):
        simbox.check_box(tmp_path / "nowhere")


def test_两张表都没有抛异常(tmp_path):
    box = make_box(tmp_path, states=False, events=False)
    with pytest.raises(FileNotFoundError, match="至少要有一张"):
        simbox.check_box(box)


def test_只有raw下脚本没有根runner照样通过(tmp_path):
    """跑脚本两种合法布局都算数：根目录 runner.py，或 raw/ 下模板另存的 *.py。"""
    box = make_box(tmp_path)
    (box / "runner.py").unlink()
    (box / "raw" / "A_1.py").write_text("# 模板另存的脚本\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []


def test_两种跑脚本布局都没有抛异常(tmp_path):
    box = make_box(tmp_path)
    (box / "runner.py").unlink()
    with pytest.raises(FileNotFoundError, match="货箱缺跑脚本"):
        simbox.check_box(box)


def test_非法JSON抛ValueError(tmp_path):
    box = make_box(tmp_path)
    (box / "sim.meta.json").write_text("{坏的", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        simbox.check_box(box)


def test_meta顶层是列表抛ValueError(tmp_path):
    box = make_box(tmp_path)
    (box / "sim.meta.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="对象"):
        simbox.check_box(box)


# ── 配料单


def test_配料单缺必填项拒收并点名(tmp_path):
    v = simbox.check_box(make_box(tmp_path, drop_meta_key="temperature"))
    assert any("temperature" in p for p in v.problems)


def test_必填项空列表也算缺(tmp_path):
    v = simbox.check_box(make_box(tmp_path, meta_overrides={"prompts": []}))
    assert any("prompts" in p for p in v.problems)


def test_n_reps是字符串拒收而不是跳过检查(tmp_path):
    v = simbox.check_box(make_box(tmp_path, meta_overrides={"n_reps": "3"}))
    assert any("正整数" in p for p in v.problems)


def test_缺n_reps只报一条不重复(tmp_path):
    v = simbox.check_box(make_box(tmp_path, drop_meta_key="n_reps"))
    assert sum("n_reps" in p for p in v.problems) == 1


def test_runs清单与配料单rep数对不上拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, meta_overrides={"n_reps": 5}))
    assert any("runs.csv 与配料单对不上" in p for p in v.problems)


# ── runs.csv


def test_runs空文件报空文件(tmp_path):
    box = make_box(tmp_path)
    (box / "runs.csv").write_text("", encoding="utf-8")
    v = simbox.check_box(box)
    assert any("空文件" in p for p in v.problems)


def test_runs只有表头拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, runs_rows=[]))
    assert any("一条跑都没有" in p for p in v.problems)


def test_runs列不齐拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, runs_header="file,rep,complete",
                                  runs_rows=["raw/A_1.db,1,yes"]))
    assert any("runs.csv 列不齐" in p and "condition" in p for p in v.problems)


def test_runs行截断或漏填拒收并报条号(tmp_path):
    rows = ["raw/A_1.db,A,1,yes", "raw/A_2.db,A"]
    v = simbox.check_box(make_box(tmp_path, conditions=("A",), reps=("1", "2"),
                                  runs_rows=rows))
    assert any("第 3 条必填列有空" in p for p in v.problems)


def test_runs重复登记拒收(tmp_path):
    rows = ["raw/A_1.db,A,1,yes", "raw/A_1b.db,A,1,yes"]
    v = simbox.check_box(make_box(tmp_path, conditions=("A",), reps=("1",),
                                  runs_rows=rows, meta_overrides={"n_reps": 1}))
    assert any("登记了两遍" in p for p in v.problems)


def test_runs引用的文件不在拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, raw_files=False))
    assert any("不在箱子里" in p for p in v.problems)


def test_runs的file指到箱外拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, conditions=("A",), reps=("1",),
                                  runs_rows=["../evil.db,A,1,yes"], raw_files=False))
    assert any("箱子外" in p for p in v.problems)


def test_多个文件不在箱子里合并成一条(tmp_path):
    v = simbox.check_box(make_box(tmp_path, raw_files=False))  # 默认 2 条件×3 rep=6 个文件
    hits = [p for p in v.problems if "不在箱子里" in p]
    assert len(hits) == 1
    assert hits[0].count("、") == 5   # 6 个文件名，5 个顿号分隔——合并成一条不是六条


def test_键列带空格照识别(tmp_path):
    v = simbox.check_box(make_box(tmp_path, conditions=("A",), reps=("1",),
                                  runs_rows=["raw/A_1.db,A, 1,yes"]))
    assert v.problems == []


# ── 数据表与对账


def test_钥匙列不齐拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, states_header="condition,rep,agent_id,stance"))
    assert any("states.csv 钥匙列不齐" in p and "wave" in p for p in v.problems)


def test_键不唯一拒收(tmp_path):
    v = simbox.check_box(make_box(tmp_path, dup_row=True))
    assert any("states.csv" in p and "不唯一" in p for p in v.problems)


def test_只有事件表也验钥匙列(tmp_path):
    v = simbox.check_box(make_box(tmp_path, states=False, events=True,
                                  events_header="condition,rep,seq,agent_id,action"))
    assert any("events.csv 钥匙列不齐" in p and "step" in p for p in v.problems)


def test_Excel带BOM的表照读(tmp_path):
    box = make_box(tmp_path)
    (box / "states.csv").write_bytes(b"\xef\xbb\xbf" + (box / "states.csv").read_bytes())
    assert simbox.check_box(box).problems == []


def test_states出现未登记的组合拒收(tmp_path):
    rows = [f"raw/A_{r}.db,A,{r},yes" for r in ("1", "2", "3")]
    v = simbox.check_box(make_box(tmp_path, runs_rows=rows))  # states 有 B，runs 只有 A
    assert any("没登记" in p for p in v.problems)


def test_事件表也对账未登记组合(tmp_path):
    rows = [f"raw/A_{r}.db,A,{r},yes" for r in ("1", "2", "3")]
    v = simbox.check_box(make_box(tmp_path, states=False, events=True, runs_rows=rows))
    assert any("events.csv" in p and "没登记" in p for p in v.problems)


def test_数据表全空而清单不空拒收(tmp_path):
    box = make_box(tmp_path)
    (box / "states.csv").write_text("condition,rep,wave,agent_id,stance\n",
                                    encoding="utf-8")
    v = simbox.check_box(box)
    assert any("一行数据都没有，而 runs.csv 登记了" in p for p in v.problems)


def test_登记的跑缺数据行只警告(tmp_path):
    box = make_box(tmp_path)
    lines = (box / "states.csv").read_text(encoding="utf-8").splitlines()
    keep = [lines[0]] + [x for x in lines[1:] if x.startswith("A,")]
    (box / "states.csv").write_text("\n".join(keep) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert any("一行数据都没有" in w and "B/1" in w for w in v.warnings)


def test_一次报全多处缺陷(tmp_path):
    box = make_box(tmp_path, runs_rows=[],
                   states_header="condition,rep,agent_id,stance")
    v = simbox.check_box(box)
    assert any("一条跑都没有" in p for p in v.problems)
    assert any("states.csv 钥匙列不齐" in p for p in v.problems)


# ── 降级与记录（收但不拒）


def test_rep少于3收但降级(tmp_path):
    v = simbox.check_box(make_box(tmp_path, reps=("1",), meta_overrides={"n_reps": 1}))
    assert v.problems == [] and v.downgrade is True
    assert any("重复不足" in w and "初步" in w for w in v.warnings)


def test_不平衡时点名短的那个条件(tmp_path):
    rows = [f"raw/A_{r}.db,A,{r},yes" for r in ("1", "2", "3")] + \
           [f"raw/B_{r}.db,B,{r},yes" for r in ("1", "2")]
    ev = [f"A,{r},1,{i + 1},2026-08-31 11:00:{i + 1:02d},0,like_post,1"
          for i, r in enumerate(("1", "2", "3"))] + \
         [f"B,{r},1,{i + 4},2026-08-31 11:00:{i + 4:02d},0,like_post,1"
          for i, r in enumerate(("1", "2"))]
    v = simbox.check_box(make_box(tmp_path, states=False, events=True,
                                  events_rows=ev, runs_rows=rows))
    assert v.problems == [] and v.downgrade is True
    assert any("条件 B" in w for w in v.warnings)


def test_单条件收但记(tmp_path):
    v = simbox.check_box(make_box(tmp_path, conditions=("A",)))
    assert v.problems == [] and v.single_condition is True
    assert any("只有一个条件" in w for w in v.warnings)


def test_未完整跑收但记(tmp_path):
    v = simbox.check_box(make_box(tmp_path, incomplete=1))
    assert v.problems == [] and any("未完整" in w for w in v.warnings)


def test_complete空格子算未完整(tmp_path):
    v = simbox.check_box(make_box(tmp_path, conditions=("A",), reps=("1",),
                                  runs_rows=["raw/A_1.db,A,1,"]))
    assert v.problems == [] and any("未完整" in w for w in v.warnings)


def test_complete为no且无数据行不重复警告(tmp_path):
    """complete=no 的跑本来就该没数据——absent 方向不该替它再报一遍「没数据」。"""
    box = make_box(tmp_path, conditions=("A",), reps=("1", "2", "3"), incomplete=1)
    lines = (box / "states.csv").read_text(encoding="utf-8").splitlines()
    keep = [lines[0]] + [x for x in lines[1:] if not x.startswith("A,3,")]
    (box / "states.csv").write_text("\n".join(keep) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert not any("一行数据都没有" in w for w in v.warnings)
    assert sum("未完整" in w for w in v.warnings) == 1


def test_全部complete为no且数据表为空拒收(tmp_path):
    """两个信号叠在一起时不是「收但降级」能糊过去的：没有一行可用数据。"""
    box = make_box(tmp_path, conditions=("A",), reps=("1",), incomplete=1)
    (box / "states.csv").write_text("condition,rep,wave,agent_id,stance\n",
                                    encoding="utf-8")
    v = simbox.check_box(box)
    assert any("所有跑都标了未完成，数据表又是空的" in p for p in v.problems)


def test_全部complete为no但数据表有行仍只警告(tmp_path):
    """全 complete=no 不等于没数据——表里真有数据行时不该被新规则误伤。"""
    box = make_box(tmp_path, conditions=("A",), reps=("1", "2", "3"), incomplete=3)
    v = simbox.check_box(box)
    assert not any("没有可用数据" in p for p in v.problems)
    assert sum("未完整" in w for w in v.warnings) == 1


def test_两表一空一不空全complete为no只警告(tmp_path):
    """states 24 行、events 只有表头，全 complete=no——要 ALL 张表都空才该拒收，
    这里只空了一张，不该被「所有跑都标了未完成」那条更硬的规则误伤。"""
    box = make_box(tmp_path, events=True, events_rows=[], incomplete=6)
    v = simbox.check_box(box)
    states_rows = (box / "states.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert len(states_rows) == 24
    assert v.problems == []
    assert any("未完整" in w for w in v.warnings)


# ── 波次覆盖对账（F1：断粮或中途崩的跑，wave 集合比 runs.csv 更诚实）


def test_complete组间波次不齐拒收(tmp_path):
    """A 三波、B 断粮只剩 wave 0，两组 runs.csv 都标 complete=yes——不能放行。"""
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",),
                   meta_overrides={"n_reps": 1})
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1", "2"):
        lines.append(f"A,1,{w},0,4")
    lines.append("B,1,0,0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert any("各次跑的访谈波次对不齐" in p and "B" in p for p in v.problems)


def test_未完成组缺波只警告(tmp_path):
    """同样的形状，但 B 那行 runs.csv 标 complete=no——缺波不拒收，只收但记。"""
    runs_rows = ["raw/A_1.db,A,1,yes", "raw/B_1.db,B,1,no"]
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",),
                   meta_overrides={"n_reps": 1}, runs_rows=runs_rows)
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1", "2"):
        lines.append(f"A,1,{w},0,4")
    lines.append("B,1,0,0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert any("B/1" in w and "缺" in w and "wave" in w for w in v.warnings)


def test_单条件不触发波次对账(tmp_path):
    """只有一个 (condition, rep) 组时没有对照对象，波次对账不应发声。"""
    box = make_box(tmp_path, conditions=("A",), reps=("1",),
                   meta_overrides={"n_reps": 1})
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1", "2"):
        lines.append(f"A,1,{w},0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert not any("波次对不齐" in p for p in v.problems)
    assert not any("缺 wave" in w for w in v.warnings)


def test_三组反转案众数参照不冤枉健康多数(tmp_path):
    """A 多跑出一波（异常），B/C 是健康多数——参照该取众数集，不取最大集：
    否则最大集规则会把 A 追认成「标准」，反过来点名健康的 B、C 要求整改。"""
    box = make_box(tmp_path, conditions=("A", "B", "C"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1", "2", "3"):
        lines.append(f"A,1,{w},0,4")
    for c in ("B", "C"):
        for w in ("0", "1", "2"):
            lines.append(f"{c},1,{w},0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    msg = next(p for p in v.problems if "波次对不齐" in p)
    assert "A" in msg and "有" in msg
    assert "B" not in msg and "C" not in msg


def test_平票复数持有不称多数逐个点名(tmp_path):
    """2-2-1 平票案：A/B 持 {0,1,2}、C/D 持 {0,1}、E 只有 {0}，五组全 complete——
    参照集靠平票比大小胜出，只占 2/5，不是严格多数（>1/2），措辞不许说「多数跑」
    （那是谎报确信度），改为逐个点名持有组 A/1、B/1。"""
    box = make_box(tmp_path, conditions=("A", "B", "C", "D", "E"), reps=("1",),
                   meta_overrides={"n_reps": 1})
    lines = ["condition,rep,wave,agent_id,stance"]
    for c, waves in (("A", "012"), ("B", "012"), ("C", "01"), ("D", "01"), ("E", "0")):
        for w in waves:
            lines.append(f"{c},1,{w},0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    msg = next(p for p in v.problems if "波次对不齐" in p)
    assert "多数" not in msg
    assert "A/1、B/1 有 wave" in msg
    assert "C/1" in msg and "D/1" in msg and "E/1" in msg


def test_严格多数才许说多数跑(tmp_path):
    """三组里两组同集（2/3 > 1/2，严格多数）——这时「多数跑」名副其实，该用；
    与上一条平票案合起来钉住边界：过半才叫多数，平票胜出不叫。"""
    box = make_box(tmp_path, conditions=("A", "B", "C"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for c, waves in (("A", "012"), ("B", "012"), ("C", "0")):
        for w in waves:
            lines.append(f"{c},1,{w},0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    msg = next(p for p in v.problems if "波次对不齐" in p)
    assert "多数跑有 wave 0、1、2" in msg
    assert "C/1 只有 wave 0" in msg


def test_不相交波次集两边都点名且都用有(tmp_path):
    """A={0,1}、B={2,3} 互不相交——谁都不是谁的子集，措辞该两边都用「有」，
    不该有一边被扣上「只有」的帽子（那暗示它是残的，其实它只是不同）。"""
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1"):
        lines.append(f"A,1,{w},0,4")
    for w in ("2", "3"):
        lines.append(f"B,1,{w},0,4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    msg = next(p for p in v.problems if "波次对不齐" in p)
    assert "A" in msg and "B" in msg
    assert "只有" not in msg


# ── 波内人数覆盖（T2b：兜底网让 miss 的小人不写 trace 行，wave 集合看不见半波）


def test_波内缺人警告(tmp_path):
    """A/1 三波都 5 人齐全；B/1 wave 1 被兜底网救活但只剩 2 人——
    两组 wave 集合都是 {0,1,2}，T1 的波次对账不该发声，这正是它的盲区。"""
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for c in ("A", "B"):
        for w in ("0", "1", "2"):
            n = 2 if (c == "B" and w == "1") else 5
            for a in range(n):
                lines.append(f"{c},1,{w},{a},4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert any("B/1" in w and "wave 1" in w and "2/5" in w for w in v.warnings)


def test_波内人数齐全静默(tmp_path):
    """所有 cell 都是 5/5——没有短缺，人数覆盖检查不该发声。"""
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for c in ("A", "B"):
        for w in ("0", "1", "2"):
            for a in range(5):
                lines.append(f"{c},1,{w},{a},4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert not any("个小人" in w for w in v.warnings)


def test_不等池但各自齐整静默(tmp_path):
    """A 用 5 人样本池、B 用 10 人样本池——两组各自内部整整齐齐，只是池子大小
    不同。众数若跨组混算会把「不一样大」误判成「缺人」；组内各算就不会。"""
    box = make_box(tmp_path, conditions=("A", "B"), reps=("1",))
    lines = ["condition,rep,wave,agent_id,stance"]
    for w in ("0", "1", "2"):
        for a in range(5):
            lines.append(f"A,1,{w},{a},4")
    for w in ("0", "1", "2"):
        for a in range(10):
            lines.append(f"B,1,{w},{a},4")
    (box / "states.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []
    assert not any("个小人" in w for w in v.warnings)
