"""`--json`：stdout 上**只有一行**合法 JSON，退出码跟散文模式一模一样。

为什么是「一行、且只有这一行」：这一行要被 MCP 客户端和 dsh 直接 json.loads。
掺一句 `⚠ …` 进去，每个调用方就都得自己猜哪一行是数据——
而猜错的表现是「偶尔解析失败」，最难查的那一类。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.cli.main import app

runner = CliRunner()


def _one_json(output: str) -> dict:
    lines = [l for l in output.splitlines() if l.strip()]
    assert len(lines) == 1, f"--json 下 stdout 不止一行：{lines}"
    return json.loads(lines[0])


def _plan(rundir: Path) -> None:
    (rundir / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "edu_001", "estimator": "ols_fe",
         "outcome": "y", "treatment": "x"}), encoding="utf-8")


def _review(rundir: Path, name: str, verdict: str, findings: list[str], round_: int) -> None:
    (rundir / name).write_text(json.dumps(
        {"verdict": verdict, "findings": findings, "round": round_}, ensure_ascii=False),
        encoding="utf-8")


def test_loop_status的json是一行且退出码不变(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _plan(run)
    _review(run, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], 1)
    _review(run, "review_paper_rendered_a_r2.json", "FAIL", ["[设计] y"], 2)

    prose = runner.invoke(app, ["loop", "status", "--run", str(run)])
    js = runner.invoke(app, ["loop", "status", "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 4

    d = _one_json(js.stdout)
    assert d["ok"] is False
    assert d["decision"]["decision"] == "export_with_open_items"
    assert d["data"]["exit_code"] == 4
    assert d["summary"].startswith("export_with_open_items｜")


def test_json下没有决定也没有散文(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    js = runner.invoke(app, ["loop", "status", "--run", str(run), "--json"])
    assert js.exit_code == 0
    d = _one_json(js.stdout)
    assert d["decision"]["decision"] == "review_first"
    assert "决定 " not in js.output and "轮次 " not in js.output


def test_json模式下stderr也是空的(tmp_path):
    """`--json` 的约定是 stdout 一行、别处什么都不打——掺一句到 stderr，
    宿主的日志里就会出现「JSON 之外还有话」，而那句话没人解析得了。"""
    run = tmp_path / "r"; run.mkdir()
    js = runner.invoke(app, ["loop", "status", "--run", str(run), "--json"])
    assert js.stderr == ""


def test_目录不存在时json也只有一行且退出码1(tmp_path):
    """打错路径不该被静默地建成一个空运行目录——散文和 JSON 两侧都拒绝。"""
    missing = tmp_path / "没有这个目录"
    prose = runner.invoke(app, ["loop", "status", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists(), "读的命令不许把目录建出来"

    js = runner.invoke(app, ["loop", "status", "--run", str(missing), "--json"])
    assert js.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "missing"
    assert d["data"]["exit_code"] == 1
    assert not missing.exists()


# ─────────────────────────── schema：纯查询，不碰运行目录


def test_schema的json是一行且schema原样在data里():
    prose = runner.invoke(app, ["schema", "plan"])
    js = runner.invoke(app, ["schema", "plan", "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["kind"] == "schema" and d["data"]["schema"]["title"] == "AnalysisPlan"
    assert js.stderr == "", "散文那一侧往 stderr 打的提示，--json 下不许出现"


def test_schema的example在json里是对象不是字符串():
    js = runner.invoke(app, ["schema", "plan", "--example", "--json"])
    d = _one_json(js.stdout)
    assert d["data"]["example"]["estimator"]          # 调用方不必再 json.loads 一次


def test_schema认不出的产出物json下也退1():
    prose = runner.invoke(app, ["schema", "nonexistent"])
    js = runner.invoke(app, ["schema", "nonexistent", "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "bad_input"


# ─────────────────────────── eval：坏消息也是一行 JSON，不是异常


def test_eval的json退出码仍是7且列出没过的规则(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    prose = runner.invoke(app, ["eval", "--run", str(run)])
    js = runner.invoke(app, ["eval", "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 7
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["exit_code"] == 7
    assert d["problems"] == d["data"]["must_failed"] and d["problems"]
    assert (run / "eval_report.json").exists(), "--json 下也照样落盘"


def test_eval列规则的json不需要运行目录():
    js = runner.invoke(app, ["eval", "--list", "--json"])
    assert js.exit_code == 0
    assert len(_one_json(js.stdout)["data"]["rules"]) > 20
    js = runner.invoke(app, ["eval", "--list", "--behavior", "--json"])
    assert len(_one_json(js.stdout)["data"]["rules"]) == 9


def test_eval目录不存在时两侧都退1且不建目录(tmp_path):
    missing = tmp_path / "没有这个目录"
    prose = runner.invoke(app, ["eval", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists(), "核账不许自己把目录建出来再判分"
    js = runner.invoke(app, ["eval", "--run", str(missing), "--json"])
    assert js.exit_code == 1
    assert _one_json(js.stdout)["data"]["kind"] == "missing"
    assert not missing.exists()


def test_eval_behavior的json带表也带指标(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "trace.jsonl").write_text(json.dumps(
        {"seq": 1, "step": "profile", "command": "oss profile panel.csv", "args": {},
         "outcome": "ok"}, ensure_ascii=False) + "\n", encoding="utf-8")
    prose = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    js = runner.invoke(app, ["eval", "--behavior", "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["metrics"]["topic_switch"]["value"] == 0.0
    assert d["data"]["table"] in prose.output, "散文打的那张表，JSON 里原样有一份"


def test_eval_behavior全是na时json也退1(tmp_path):
    """报告已经落盘，所以这是「成功地报告了一个坏消息」——一行 JSON，退出码 1。"""
    run = tmp_path / "r"; run.mkdir()
    js = runner.invoke(app, ["eval", "--behavior", "--run", str(run), "--json"])
    assert js.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["undecidable"] is True
    assert (run / "eval_behavior.json").exists()


# ─────────────────────────── anchor switch：被拒也是一行 JSON，也照样留痕


def _frozen(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "ideas.json").write_text(json.dumps({"direction": "A", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")
    _plan(run)
    from oss.core import state as state_mod
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))


def test_换题被拒时json退2且problems给理由(tmp_path):
    run = tmp_path / "r"; _frozen(run)
    argv = ["anchor", "switch", "--to", "edu_007", "--why", "试试", "--run", str(run)]
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == 2
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"
    assert "用户授权" in d["summary"]
    rows = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows[-1]["step"] == "topic_switch" and rows[-1]["outcome"] == "refused", \
        "--json 那一侧也要留下被拒的证据"


def test_授权换题的json带上从哪换到哪(tmp_path):
    run = tmp_path / "r"; _frozen(run)
    js = runner.invoke(app, ["anchor", "switch", "--to", "edu_007", "--why", "数据回答不了",
                             "--authorized-by", "user", "--run", str(run), "--json"])
    assert js.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["from"] == "edu_001" and d["data"]["to"] == "edu_007"
    assert d["data"]["direction_changed"] is None


# ─────────────────────────── route：○/✓/✗ 那些标记背后的状态都在 data 里


def _pretrend(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "did", "outcome": "y", "unit": "unit",
         "time": "year", "treat_time": "treat_time", "cluster_level": "unit"}), encoding="utf-8")
    (run / "results.json").write_text(json.dumps(
        {"plan_id": "p", "n": 100,
         "estimates": {"main": {"term": "t", "coef": 1.0, "se": 0.2, "p": 0.001}},
         "diagnostics": {"平行趋势": "**不成立**：处理前联合检验 p = 0.005"}},
        ensure_ascii=False), encoding="utf-8")


def test_route的json退出码仍是6且逐条状态都在(tmp_path):
    run = tmp_path / "r"; _pretrend(run)
    prose = runner.invoke(app, ["route", "--run", str(run)])
    js = runner.invoke(app, ["route", "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 6
    d = _one_json(js.stdout)
    assert d["problems"] == d["data"]["pending_must"] == ["pretrend_fail"]
    it = d["data"]["items"][0]
    # 散文打的 ○ / 依据 / 处置 三样，data 里各有出处
    assert it["status"] == "pending" and it["evidence"] and it["advice"]
    assert f"○ [{it['priority']}] {it['id']}  ← {it['trigger']}" in prose.output


def test_route处置之后json退0且说清做了哪一步(tmp_path):
    run = tmp_path / "r"; _pretrend(run)
    js = runner.invoke(app, ["route", "--disclose", "pretrend_fail", "--why", "局限写了",
                             "--run", str(run), "--json"])
    assert js.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["action"] == "disclose" and d["data"]["action_why"] == "局限写了"
    assert d["data"]["pending_must"] == []


def test_route认不出的处方json下也退1(tmp_path):
    run = tmp_path / "r"; _pretrend(run)
    prose = runner.invoke(app, ["route", "--dismiss", "nope", "--why", "x", "--run", str(run)])
    js = runner.invoke(app, ["route", "--dismiss", "nope", "--why", "x",
                             "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input"


def test_route和换题都不许把目录建出来(tmp_path):
    for argv in (["route", "--run"], ["anchor", "switch", "--to", "x", "--why", "y",
                                      "--authorized-by", "user", "--run"]):
        missing = tmp_path / f"没这个目录{argv[0]}"
        r = runner.invoke(app, [*argv, str(missing)])
        assert r.exit_code == 1 and "运行目录不存在" in r.output, r.output
        assert not missing.exists()


# ─────────────────────────── profile / merge / reshape：散文打的数，data 里都有

FIXTURES = Path(__file__).parent / "fixtures"


def test_profile的json一行且警告与形状都在data里(tmp_path):
    run = tmp_path / "r"
    data = str(FIXTURES / "gss_like.csv")
    prose = runner.invoke(app, ["profile", data, "--run", str(run)])
    js = runner.invoke(app, ["profile", data, "--run", str(run), "--resume", "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["artifact"] == "profile.json" and d["data"]["n_rows"] > 0
    # 散文里那几行 `⚠ …` 一条不少地在 data.warnings 里
    for w in d["data"]["warnings"]:
        assert f"  ⚠ {w}" in prose.output
    assert js.stderr == ""


def test_profile的shape在json下也不落盘(tmp_path):
    run = tmp_path / "r"
    js = runner.invoke(app, ["profile", str(FIXTURES / "gss_like.csv"),
                             "--run", str(run), "--shape", "--json"])
    assert js.exit_code == 0
    assert _one_json(js.stdout)["data"]["shape_summary"]
    assert not (run / "profile.json").exists()


def test_往跑完的目录再画像两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "results.json").write_text("{}", encoding="utf-8")
    argv = ["profile", str(FIXTURES / "gss_like.csv"), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "新研究开新目录" in prose.output
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"


def _two_tables(run: Path) -> list[str]:
    run.mkdir(parents=True, exist_ok=True)
    (run / "left.csv").write_text("country,year,y\nCHN,2000,1.0\nUSA,2000,2.0\n", encoding="utf-8")
    (run / "right.csv").write_text("country,year,z\nCHN,2000,9.0\nJPN,2000,8.0\n", encoding="utf-8")
    return ["merge", str(run / "left.csv"), str(run / "right.csv"),
            "--on", "country,year", "--run", str(run)]


def test_merge丢太多时json退1且候选没被自动套用(tmp_path):
    """拒绝理由里列着「谁没配上」，但**工具一个字都没替你改**——merged.csv 不存在。"""
    run = tmp_path / "r"
    argv = _two_tables(run)
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"
    assert "拒绝拼" in d["summary"] and "USA" in d["summary"] and "JPN" in d["summary"]
    assert not (run / "merged.csv").exists(), "被拒的拼接不许留下产物"


def test_merge拼成时散文的每一条结论都在data里(tmp_path):
    run = tmp_path / "r"
    argv = [*_two_tables(run), "--allow-loss", "0.6"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["rows_in"] == 2 and d["data"]["rows_out"] == 1
    assert d["data"]["loss_rate"] == 0.5
    for note in d["data"]["notes"]:
        assert f"  · {note}" in prose.output
    assert f"  ↳ 接着跑：{d['data']['next_command']}" in prose.output


def test_reshape不是宽表时两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "flat.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    argv = ["reshape", str(run / "flat.csv"), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"
    assert "这不像宽表面板" in d["summary"]


def test_stack摞不齐时两侧都退1(tmp_path):
    """映射文件读不了：散文那侧和 --json 那侧必须是同一个退出码、同一句话。"""
    run = tmp_path / "r"; run.mkdir()
    argv = ["stack", str(run / "nope.json"), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"
    assert any("读不了映射文件" in p for p in d["problems"])


def test_stack编码翻转在problems里点名(tmp_path):
    """**这是这条命令存在的头号理由**：同一个码在两轮指着相反的东西。

    直接摞不会报错，只会把有工作的人算成没工作——所以这条走拒绝，
    而且 problems 里要指出是哪个概念、哪两轮、哪个码。
    """
    import json as _json
    import pandas as pd
    pyreadstat = pytest.importorskip("pyreadstat")
    run = tmp_path / "r"; run.mkdir()
    for name, labels in (("w13.dta", {1.0: "是", 2.0: "否"}),
                         ("w17.dta", {1.0: "否", 4.0: "是"})):
        pyreadstat.write_dta(pd.DataFrame({"a53": list(labels)}), str(run / name),
                             variable_value_labels={"a53": labels})
    spec = run / "m.json"
    spec.write_text(_json.dumps({"sources": [
        {"path": str(run / "w13.dta"), "wave": 2013, "columns": {"employed": "a53"}},
        {"path": str(run / "w17.dta"), "wave": 2017, "columns": {"employed": "a53"}},
    ]}), encoding="utf-8")

    argv = ["stack", str(spec), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert any("编码跨轮翻转" in p and "employed" in p for p in d["problems"])


def test_stack摞成时行数与警告都在data里(tmp_path):
    """2023 那半个电访样本整个模块没问——「没问」和「答了但缺失」要分得开。"""
    import json as _json
    run = tmp_path / "r"; run.mkdir()
    (run / "a.csv").write_text("h,e\n40,3\n50,4\n", encoding="utf-8")
    (run / "b.csv").write_text("e\n5\n", encoding="utf-8")
    spec = run / "m.json"
    spec.write_text(_json.dumps({"wave_column": "year", "sources": [
        {"path": str(run / "a.csv"), "wave": 2018, "columns": {"hours": "h", "educ": "e"}},
        {"path": str(run / "b.csv"), "wave": 2023, "columns": {"educ": "e"}},
    ]}), encoding="utf-8")

    argv = ["stack", str(spec), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["rows_in"] == {"2018": 2, "2023": 1} and d["rows_out"] == 3
    assert d["per_variable"]["hours"]["缺席"] == ["2023"]
    assert any("没有映射" in w for w in d["warnings"])
    assert (run / "stacked.csv").exists()


def test_reshape转成时行数在data里(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "wide.csv").write_text(
        "pid,y_w1,y_w2,x_w1,x_w2,z_w1,z_w2\n1,1,2,3,4,5,6\n2,7,8,9,1,2,3\n", encoding="utf-8")
    argv = ["reshape", str(run / "wide.csv"), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["data"]["rows_in"] == 2 and d["data"]["rows_out"] == 4
    assert (run / "long.csv").exists()


# ─────────────────────────── bind / power：候选、绑定、判词都在 data 里


def _profiled(run: Path, fixture: str, constructs: list[str]) -> None:
    run.mkdir(parents=True, exist_ok=True)
    r = runner.invoke(app, ["profile", str(FIXTURES / fixture), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "ideas.json").write_text(json.dumps({
        "direction": "D", "ideas": [{"idea_id": "edu_001", "title": "T",
                                     "research_question": "Q",
                                     "constructs_needed": constructs}]},
        ensure_ascii=False), encoding="utf-8")


def test_bind候选的json里每个候选列都有分数与理由(tmp_path):
    run = tmp_path / "r"
    _profiled(run, "gss_like.csv", ["教育年限", "社会经济地位"])
    argv = ["bind", "--run", str(run), "--idea", "edu_001"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    for c in d["data"]["constructs"]:
        assert f"  {c['construct']}" in prose.output
        for cand in c["matches"]:
            assert {"column", "score", "why"} == set(cand)
            assert cand["column"] in prose.output
    assert not (run / "binding.json").exists(), "只列候选不该落盘"


def test_bind规则给不出候选时全表也在json里(tmp_path):
    run = tmp_path / "r"
    _profiled(run, "did_clean.csv", ["处理时点", "结局"])
    argv = ["bind", "--run", str(run), "--idea", "edu_001"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["rule_failed"] is True and d["all_variables"]
    for v in d["all_variables"]:
        assert v["name"] in prose.output
    assert "有构念规则匹配不上" in prose.output


def test_bind绑不上的构念两侧都说这个idea做不了(tmp_path):
    """构念指到一个不存在的列：散文打「这个 idea 做不了」，data 里是 blocking。"""
    run = tmp_path / "r"
    _profiled(run, "did_clean.csv", ["处理时点", "结局"])
    argv = ["bind", "--run", str(run), "--idea", "edu_001",
            "--map", "处理时点=没这列:treatment", "--map", "结局=y:outcome"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["core_ok"] is False and d["blocking"]
    assert "这个 idea 做不了：" in prose.output
    for x in d["blocking"]:
        assert f"    · {x}" in prose.output


def test_bind的map看不懂时两侧都退1(tmp_path):
    run = tmp_path / "r"
    _profiled(run, "gss_like.csv", ["教育年限"])
    argv = ["bind", "--run", str(run), "--idea", "edu_001", "--map", "乱写"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "看不懂" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input"
    assert not (run / "binding.json").exists()


def test_power判fail时json退0且判词逐条与散文一致(tmp_path):
    """**判死不是命令失败**：power.json 写出来了，判词是 data 里的一个字段。

    要测的不变量是「JSON 与散文逐条一致」，不是「一共几条」——
    原先写死成 4 条，加一条理由（效应量无出处那条）就误伤，
    而条数本来就不是这条测试关心的东西。
    """
    run = tmp_path / "r"
    _profiled(run, "did_clean.csv", ["处理时点", "结局"])
    argv = ["power", "--run", str(run), "--idea", "edu_001"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["verdict"] == "fail" and d["gray"] is False
    assert f"  {d['verdict'].upper()}  功效 {d['power']:.2f}" in prose.output
    lines = d["reason_lines"]
    assert lines and any(b.startswith("⚠") for b in lines), lines
    assert [b for b in prose.output.splitlines() if b.startswith("    · ")] == \
        [f"    · {b}" for b in lines], "散文与 JSON 的理由必须逐条对得上"
    assert (run / "power.json").exists()


def test_power另存的路径也在json里(tmp_path):
    run = tmp_path / "r"
    _profiled(run, "did_clean.csv", ["处理时点", "结局"])
    js = runner.invoke(app, ["power", "--run", str(run), "--idea", "edu_001",
                             "--out", "power_interaction.json", "--json"])
    assert js.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["extra_path"].endswith("power_interaction.json")
    assert (run / "power_interaction.json").exists()


def test_power算不出ρx时json下不掺一句到stderr(tmp_path):
    """散文往 stderr 打的那句 `⚠ …`，`--json` 下只出现在 data.warnings 里。"""
    run = tmp_path / "r"
    _profiled(run, "did_clean.csv", ["处理时点", "结局"])
    argv = ["power", "--run", str(run), "--idea", "edu_001",
            "--cluster-col", "没这列", "--treatment-col", "也没这列"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["warnings"] and "ρx 用假定值" in d["warnings"][0]
    assert f"  ⚠ {d['warnings'][0]}" in prose.output
    assert js.stderr == ""


def test_bind和power都不许把目录建出来(tmp_path):
    for argv in (["bind", "--run"], ["power", "--run"]):
        missing = tmp_path / f"没这个目录{argv[0]}"
        r = runner.invoke(app, [*argv, str(missing)])
        assert r.exit_code == 1 and "运行目录不存在" in r.output, r.output
        assert not missing.exists()
        js = runner.invoke(app, [*argv, str(missing), "--json"])
        assert js.exit_code == 1
        assert _one_json(js.stdout)["data"]["kind"] == "missing"
        assert not missing.exists()


# ─────────────────────────── run：系数、诊断、逐年展开都在 data 里

def _did(run: Path) -> None:
    import shutil
    run.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "did_clean.csv", run / "data.csv")
    r = runner.invoke(app, ["profile", str(run / "data.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "ideas.json").write_text(json.dumps({"direction": "政策与产出", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit"}), encoding="utf-8")


def test_run的json一行且散文那几段都在data里(tmp_path):
    run = tmp_path / "r"; _did(run)
    prose = runner.invoke(app, ["run", "--run", str(run)])
    js = runner.invoke(app, ["run", "--run", str(run), "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert d["artifact"] == "results.json" and d["n"] > 0
    for e in d["estimates"]:
        assert e["term"] in prose.output
    for w in d["warnings"]:
        assert f"  ⚠ {w}" in prose.output
    for k, v in d["diagnostics"].items():
        assert f"  {k}：{v}" in prose.output
    assert d["event_study"] and "逐年展开" in prose.output
    assert js.stderr == ""


def test_探索门拒绝时json退1且problems给理由(tmp_path):
    run = tmp_path / "r"; _did(run)
    argv = ["run", "--explore", "--for", "pretrend_fail", "--plan", str(run / "plan.json"),
            "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused"
    assert d["problems"] and "探索检测未通过" in d["summary"]
    assert not (run / "results.json").exists()


def test_忘了explore的plan两侧都退1(tmp_path):
    run = tmp_path / "r"; _did(run)
    argv = ["run", "--plan", str(run / "plan.json"), "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "只在 --explore 下有效" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input"


def test_run不许把目录建出来(tmp_path):
    missing = tmp_path / "没这个目录"
    prose = runner.invoke(app, ["run", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists()
    js = runner.invoke(app, ["run", "--run", str(missing), "--json"])
    assert js.exit_code == 1
    assert _one_json(js.stdout)["data"]["kind"] == "missing"
    assert not missing.exists()


# ─────────────────────────── figure：图注、语言、路径都在 data 里

def test_figure的json一行且散文那几行都在data里(tmp_path):
    import pytest
    pytest.importorskip("matplotlib")
    run = tmp_path / "r"; _did(run)
    assert runner.invoke(app, ["run", "--run", str(run)]).exit_code == 0
    argv = ["figure", "coef", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert d["kind"] == "coef" and d["name"] == "fig_coef.png"
    assert f"  语言 {d['lang']}｜矢量版 {d['pdf'] or '无'}" in prose.output
    assert f"  图注（建议，照它改写）：{d['caption']}" in prose.output
    assert f"✓ {d['name']}  →  {d['path']}    （{d['kind_label']}）" in prose.output
    assert js.stderr == ""


def test_figure认不出的图名两侧都退2(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    argv = ["figure", "nope", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 2
    assert "不认识的图" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input"


def test_figure不许把目录建出来(tmp_path):
    missing = tmp_path / "没这个目录"
    prose = runner.invoke(app, ["figure", "coef", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists()
    js = runner.invoke(app, ["figure", "coef", "--run", str(missing), "--json"])
    assert js.exit_code == 1
    assert _one_json(js.stdout)["data"]["kind"] == "missing"
    assert not missing.exists()


def test_figure画图依赖缺席时分箱那行仍排在叉之前(tmp_path, monkeypatch):
    """老代码那一行是**当场打**的，画图依赖缺席时它已经在屏幕上、✗ 排在它后面。

    抽出之后这一行只活在 `data["log"]` 里；失败路径不排空 log 的话，
    散文就少了一行而且顺序反了。golden 盖不到：要 plan.binned 与 results.binned
    同时命中，还要 matplotlib 恰好缺席。
    """
    from oss.core import figures as figures_mod
    run = tmp_path / "r"; _did(run)
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "ols_fe", "outcome": "y",
        "treatment": "x", "fixed_effects": ["unit"], "cluster_level": "unit",
        "binned": {"by": "year", "bins": 3}}), encoding="utf-8")
    assert runner.invoke(app, ["run", "--run", str(run)]).exit_code == 0

    def _boom(*a, **k):
        raise figures_mod.FiguresUnavailable("画图要 matplotlib")
    monkeypatch.setattr(figures_mod, "marginal", _boom)

    argv = ["figure", "marginal", "--run", str(run), "--by", "year"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert prose.exit_code == js.exit_code == 1, prose.output

    note = "  分箱斜率取自 results.json（各箱内含主规格的控制变量与固定效应）"
    lines = prose.output.splitlines()
    assert note in lines, prose.output
    cross = next(i for i, l in enumerate(lines) if l.startswith("✗"))
    assert lines.index(note) < cross, f"回显被排到 ✗ 后面了：{lines}"
    assert ["out", note] in _one_json(js.stdout)["data"]["log"]


# ─────────────────────────── route：一次一处置，给两个是参数错

def test_route同时给两个处置两侧都退2(tmp_path):
    """从前是 `if apply / elif dismiss`：同时给两个**静默只做第一个还退 0**，
    调用方以为两件事都办了。一次一处置是这条命令的规矩，那就让它出声。"""
    run = tmp_path / "r"; run.mkdir()
    argv = ["route", "--run", str(run), "--apply", "a", "--dismiss", "b", "--why", "w"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert prose.exit_code == js.exit_code == 2, prose.output
    assert "一次只做一种处置" in prose.output
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "bad_input"
    assert d["problems"] == ["--apply a", "--dismiss b"]


def test_route只给一个处置照旧(tmp_path):
    """拒绝的是「给了不止一个」，单个旗标一条都不许受影响。"""
    run = tmp_path / "r"; run.mkdir()
    js = runner.invoke(app, ["route", "--run", str(run), "--dismiss", "没这条", "--why", "w",
                             "--json"])
    assert js.exit_code == 1, js.output
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input"


# ─────────────────────────── paper：七道门的退出码，--json 那侧一个都不许并

_PAPER_OK = ("# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n"
             "## Introduction\nx\n\n## Data and Methods\nx\n\n"
             "## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n")


def _min_results(run: Path) -> None:
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
    run.mkdir(parents=True, exist_ok=True)
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")


def test_paper_check通过时散文那两行都在data里(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    argv = ["paper", "check", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert f"✓ 校验通过：{d['n_sections']} 节齐全，正文零裸数字" in prose.output
    assert f"  引用了 {len(d['placeholders'])} 个结果：" in prose.output
    assert js.stderr == ""


def test_paper_check没过时两侧都退1(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    (run / "paper.md").write_text("# T\n\n## 摘要\n效应是 0.47391。\n", encoding="utf-8")
    argv = ["paper", "check", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "refused" and d["problems"]
    assert js.stderr == ""


def test_paper_check缺稿时两侧都退1且不建目录(tmp_path):
    missing = tmp_path / "没有这个目录"
    prose = runner.invoke(app, ["paper", "check", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists()
    js = runner.invoke(app, ["paper", "check", "--run", str(missing), "--json"])
    assert js.exit_code == 1 and _one_json(js.stdout)["data"]["kind"] == "missing"
    assert js.stderr == "" and not missing.exists()


def test_paper_render的json一行且路径在data里(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    js = runner.invoke(app, ["paper", "render", "--run", str(run), "--json"])
    assert js.exit_code == 0, js.output
    d = _one_json(js.stdout)["data"]
    assert Path(d["path"]).exists() and js.stderr == ""


def test_paper_export非因果论文下了因果结论时两侧都退8(tmp_path):
    from oss.core.contracts import AnalysisPlan
    run = tmp_path / "r"; _min_results(run)
    AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
                 research_type="descriptive").save(run / "plan.json")
    (run / "paper.md").write_text(
        _PAPER_OK.replace("## Discussion\nx", "## Discussion\nThe reform causes higher output."),
        encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 8, prose.output
    assert _one_json(js.stdout)["ok"] is False and js.stderr == ""


def test_paper_export弱工具不引AR区间时也退8_引了就放行到下一道门(tmp_path):
    """因果骨架硬门（设计稿 4.1）沿用研究类型/措辞门的退出码——不新增退出码。"""
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
    run = tmp_path / "r"; run.mkdir(parents=True, exist_ok=True)
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe"), diagnostics={"有效 F": "4.20"}).save(run / "results.json")
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 8, prose.output
    assert "AR 区间" in prose.output
    assert _one_json(js.stdout)["ok"] is False and js.stderr == ""

    # 正文引了 AR 区间：这道门放行，落到下一道门（没审过，退 5）——不是新退出码，是往后走
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe"),
                    diagnostics={"有效 F": "4.20", "AR 区间": "[0.01, 0.20]"}).save(run / "results.json")
    (run / "paper.md").write_text(
        _PAPER_OK.replace("## Discussion\nx", "## Discussion\n区间见 {{diag:AR 区间}}。"), encoding="utf-8")
    prose2 = runner.invoke(app, argv)
    assert prose2.exit_code == 5, prose2.output


def test_paper_export兜底路径按渲染稿查AR区间(tmp_path):
    """没有 literature.json 时导出走文本兜底路径，转的是 paper_rendered.md（上面那道
    AR 硬门查的是 paper.md）——只改 paper.md 不重渲染，旧渲染稿里没有 AR 区间的值也会
    从这条路径漏出去，这里补查一遍（模仿 `test_导出兜底路径按渲染稿查因果措辞` 的措辞门写法）。"""
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
    run = tmp_path / "r"; run.mkdir(parents=True, exist_ok=True)
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe"),
                    diagnostics={"有效 F": "4.20", "AR 区间": "[0.01, 0.20]"}).save(run / "results.json")
    # paper.md 引了占位符，过上游那道门（查的是占位符）
    (run / "paper.md").write_text(
        _PAPER_OK.replace("## Discussion\nx", "## Discussion\n区间见 {{diag:AR 区间}}。"), encoding="utf-8")
    # 旧渲染稿：占位符已经换成了别的实际值，但没有 AR 区间那段值——重跑前留下的，没重新渲染
    (run / "paper_rendered.md").write_text(
        _PAPER_OK.replace("Effect {{result:main.coef}} (n = {{result:main.n}}).",
                          "Effect 0.100 (n = 100)."), encoding="utf-8")
    for who in ("a", "b", "c"):
        (run / f"review_paper_rendered_{who}.json").write_text(
            json.dumps({"verdict": "PASS", "findings": [], "round": 1}), encoding="utf-8")
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 8, prose.output
    assert "AR 区间" in prose.output and "重跑 oss paper render" in prose.output
    assert _one_json(js.stdout)["ok"] is False and js.stderr == ""


def _hide_pandoc(monkeypatch) -> None:
    """照 CI 的样子：只让 pandoc 找不到，别的命令照常找得到。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    assert shutil.which("pandoc") is None


def test_paper_export兜底路径按渲染稿查AR区间_没装pandoc也先报(tmp_path, monkeypatch):
    """CI 不装 pandoc：兜底路径这道 AR 硬门也在查 pandoc 之前判完，两侧照样退 8。"""
    _hide_pandoc(monkeypatch)
    test_paper_export兜底路径按渲染稿查AR区间(tmp_path)


def test_paper_export兜底路径的门都过了_没装pandoc才退1(tmp_path, monkeypatch):
    """反过来：渲染稿里有 AR 区间的值，兜底路径的门都放行——这时才轮到「没装 pandoc」，
    两侧都退 1，是 unavailable 不是 refused；也不留半截的 paper_pandoc.md。"""
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
    run = tmp_path / "r"; run.mkdir(parents=True, exist_ok=True)
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe"),
                    diagnostics={"有效 F": "4.20", "AR 区间": "[0.01, 0.20]"}).save(run / "results.json")
    (run / "paper.md").write_text(
        _PAPER_OK.replace("## Discussion\nx", "## Discussion\n区间见 {{diag:AR 区间}}。"), encoding="utf-8")
    (run / "paper_rendered.md").write_text(
        _PAPER_OK.replace("Effect {{result:main.coef}} (n = {{result:main.n}}).", "Effect 0.100 (n = 100).")
                 .replace("## Discussion\nx", "## Discussion\n区间见 [0.01, 0.20]。"), encoding="utf-8")
    for who in ("a", "b", "c"):
        (run / f"review_paper_rendered_{who}.json").write_text(
            json.dumps({"verdict": "PASS", "findings": [], "round": 1}), encoding="utf-8")
    _hide_pandoc(monkeypatch)
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1, prose.output
    assert "没装 pandoc" in prose.output
    d = _one_json(js.stdout)
    assert d["ok"] is False and d["data"]["kind"] == "unavailable" and d["data"]["exit_code"] == 1
    assert js.stderr == ""
    assert not (run / "paper_pandoc.md").exists()


def test_paper_export没审过时两侧都退5(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 5, prose.output
    assert "没经过任何跨模型审稿" in prose.output
    d = _one_json(js.stdout)
    assert d["data"]["kind"] == "refused" and d["data"]["exit_code"] == 5
    assert js.stderr == ""


def test_paper_export缺主结果表时两侧都退4(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    (run / "paper.md").write_text("# T\n\n没有表\n", encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 4, prose.output
    assert _one_json(js.stdout)["data"]["exit_code"] == 4
    assert js.stderr == ""


def test_paper_export缺渲染稿时两侧都退1(tmp_path):
    run = tmp_path / "r"; _min_results(run)
    argv = ["paper", "export", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "先跑 oss paper render" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "missing"
    assert js.stderr == ""


# ═════════════════════════ lit / data —— 网络那几条一律 monkeypatch

from oss.api import data as data_api                  # noqa: E402
from oss.api import lit as lit_api                    # noqa: E402
from oss.core.contracts import LiteratureCorpus       # noqa: E402


def test_lit_search的json一行且散文那几行都在data里(tmp_path, monkeypatch):
    run = tmp_path / "r"; run.mkdir()
    fake = LiteratureCorpus(records=[], hits_by_source={"openalex": 0},
                            hits_by_query={"q": 3}, camps={"q": "阵营A"}, notes=["一条提醒"])
    monkeypatch.setattr(lit_api.lit_module, "search", lambda *a, **k: fake)
    argv = ["lit", "search", "q", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert f"  {d['n_records']} 条｜命中 {d['hits_by_source']}" in prose.output
    assert "「阵营A」← q（3 条）" in prose.output and "一条提醒" in prose.output
    assert js.stderr == ""


def test_lit_search不许把目录建出来(tmp_path, monkeypatch):
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: pytest.fail("目录都不存在，不该去连网"))
    missing = tmp_path / "没有这个目录"
    prose = runner.invoke(app, ["lit", "search", "q", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists()
    js = runner.invoke(app, ["lit", "search", "q", "--run", str(missing), "--json"])
    assert js.exit_code == 1 and _one_json(js.stdout)["data"]["kind"] == "missing"
    assert js.stderr == "" and not missing.exists()


def test_lit_add缺语料时两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    argv = ["lit", "add", "10.1/x", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "先跑 oss lit search" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "missing" and js.stderr == ""


def test_lit_fetch缺语料时两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    argv = ["lit", "fetch", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert _one_json(js.stdout)["data"]["artifact"] == "literature" and js.stderr == ""


def test_lit_cards格式不对时两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "reading_cards.json").write_text("{ 不是 JSON", encoding="utf-8")
    argv = ["lit", "cards", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "格式不对" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input" and js.stderr == ""


def test_lit_cards过不了校验时警告与逐条理由都在json里(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "reading").mkdir()
    (run / "reading" / "W1.txt").write_text("太短", encoding="utf-8")
    (run / "reading_cards.json").write_text(json.dumps({
        "direction": "d", "cards": [
            {"paper_id": "W1", "source_level": "fulltext", "limitations": [{"quote": "太短"}]},
            {"paper_id": "W2", "source_level": "fulltext"}]},
        ensure_ascii=False), encoding="utf-8")
    argv = ["lit", "cards", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    # 警告原先打在 stdout、且排在 ✗ 前面
    assert prose.stdout.index("⚠") < prose.output.index("✗ 读书卡过不了校验")
    d = _one_json(js.stdout)
    assert d["data"]["kind"] == "refused" and d["problems"] and d["data"]["warnings"]
    assert js.stderr == ""


def _cited(run: Path) -> None:
    """带三种判词的稿子 + 语料。三条引用分别落在 支持 / 存疑 / 核不了 上，
    第四条指向语料里没有的编号（跳过）。"""
    (run / "paper.md").write_text(
        "# 引言\n\n"
        "已有研究指出，教育扩张提高了社会流动 {{cite:W001}}。\n\n"
        "也有人认为，最低工资上调并不减少就业 {{cite:W002}}。\n\n"
        "另有综述给出相反的证据 {{cite:W003}}。\n\n"
        "还有一条指向语料里没有的编号 {{cite:W404}}。\n",
        encoding="utf-8")
    (run / "literature.json").write_text(json.dumps({
        "corpus_id": "c001", "queries": ["educational expansion social mobility"],
        "records": [
            {"paper_id": "W001", "title": "Educational expansion and social mobility",
             "year": 1997, "authors": ["Breen, R."], "doi": "10.1000/w001",
             "abstract": "We ask whether educational expansion raises social mobility "
                         "across cohorts.", "source": "openalex"},
            {"paper_id": "W002", "title": "A field experiment on commuting",
             "year": 1994, "authors": ["Card, D."], "doi": "10.1000/w002",
             "abstract": "A case study of coastal weather patterns.", "source": "openalex"},
            {"paper_id": "W003", "title": "A review without an abstract",
             "year": 2001, "authors": ["Duncan, O. D."], "doi": "10.1000/w003",
             "source": "crossref"}],
        "hits_by_source": {"openalex": 2, "crossref": 1},
        "abstract_coverage": 2 / 3, "citation_coverage": 0.0,
    }, ensure_ascii=False), encoding="utf-8")


def test_lit_verify的json一行且三种判词都在data里(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _cited(run)
    argv = ["lit", "verify", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert d["tally"] == {"支持": 1, "存疑": 1, "核不了": 1}
    # 散文只打「不是支持」的那几条，判词与理由**全都**在 data 里
    for k, v in d["tally"].items():
        assert f"  {k} {v} 处" in prose.output
    assert d["n_pairs"] == 3 and all(p["理由"] for p in d["pairs"])
    assert d["next"] in prose.output
    assert js.stderr == ""


def test_lit_verify没有引用时两侧都退0(tmp_path):
    """稿子里不写引用不是错误。两侧都得退 0，`--json` 那侧还要说清没什么可核的。"""
    run = tmp_path / "r"; run.mkdir()
    _cited(run)
    (run / "paper.md").write_text("# 引言\n\n没有任何引用。\n", encoding="utf-8")
    argv = ["lit", "verify", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)
    assert d["ok"] is True and d["data"]["n_pairs"] == 0 and d["data"]["path"] == ""
    assert d["summary"] in prose.output and js.stderr == ""


def test_lit_verify不许把目录建出来(tmp_path):
    """`verify` 只读 paper.md 和 literature.json，两个都不该由它自己造。"""
    missing = tmp_path / "没有这个目录"
    prose = runner.invoke(app, ["lit", "verify", "--run", str(missing)])
    assert prose.exit_code == 1 and "运行目录不存在" in prose.output
    assert not missing.exists()
    js = runner.invoke(app, ["lit", "verify", "--run", str(missing), "--json"])
    assert js.exit_code == 1 and _one_json(js.stdout)["data"]["kind"] == "missing"
    assert js.stderr == "" and not missing.exists()


def test_lit_verify缺稿子时两侧都退1(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _cited(run)
    (run / "paper.md").unlink()
    argv = ["lit", "verify", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "缺 paper.md" in prose.output
    assert _one_json(js.stdout)["data"]["artifact"] == "paper" and js.stderr == ""


def test_data_list的json不需要运行目录(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prose = runner.invoke(app, ["data", "list"])
    js = runner.invoke(app, ["data", "list", "--json"])
    assert js.exit_code == prose.exit_code == 0
    packs = _one_json(js.stdout)["data"]["packs"]
    assert packs and all(p["name"] in prose.output for p in packs)
    assert js.stderr == ""


def test_data_info认不出的名字两侧都退2():
    prose = runner.invoke(app, ["data", "info", "nope"])
    js = runner.invoke(app, ["data", "info", "nope", "--json"])
    assert js.exit_code == prose.exit_code == 2
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input" and js.stderr == ""


def test_data_info的json带码本引用与怎么拿(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prose = runner.invoke(app, ["data", "info", "cgss"])
    js = runner.invoke(app, ["data", "info", "cgss", "--json"])
    assert js.exit_code == prose.exit_code == 0
    d = _one_json(js.stdout)["data"]
    assert d["citation"] in prose.output and d["manual"] is True
    assert js.stderr == ""


def test_data_columns要申请的两侧都退3(monkeypatch):
    def _boom(*a, **k):
        raise data_api.packs_mod.NeedsManualDownload("要先去申请")

    monkeypatch.setattr(data_api.packs_mod, "source_file", _boom)
    argv = ["data", "columns", "cgss"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 3, "要申请是 3，不是参数错的 2"
    assert _one_json(js.stdout)["data"]["kind"] == "unavailable" and js.stderr == ""


def test_data_columns按词找列的json里有列名与标签(tmp_path, monkeypatch):
    src = tmp_path / "x.csv"
    src.write_text("educ,paeduc,wtssall\n1,2,3\n", encoding="utf-8")
    monkeypatch.setattr(data_api.packs_mod, "source_file", lambda *a, **k: src)
    argv = ["data", "columns", "gss", "--search", "educ"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    d = _one_json(js.stdout)["data"]
    assert {r["name"] for r in d["rows"]} == {"educ", "paeduc"}
    assert f"命中 {d['n_rows']} 列" in prose.output and js.stderr == ""


def test_data_get要申请的两侧都退3(monkeypatch, tmp_path):
    run = tmp_path / "r"; run.mkdir()

    def _boom(*a, **k):
        raise data_api.packs_mod.NeedsManualDownload("要先去申请")

    monkeypatch.setattr(data_api.packs_mod, "get", _boom)
    argv = ["data", "get", "cgss", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 3
    assert "要先去申请" in prose.output
    assert _one_json(js.stdout)["data"]["exit_code"] == 3 and js.stderr == ""


def test_data_get名字不对两侧都退2(monkeypatch):
    monkeypatch.setattr(data_api.packs_mod, "get",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("没有 nope")))
    prose = runner.invoke(app, ["data", "get", "nope"])
    js = runner.invoke(app, ["data", "get", "nope", "--json"])
    assert js.exit_code == prose.exit_code == 2
    assert _one_json(js.stdout)["data"]["kind"] == "bad_input" and js.stderr == ""


def test_data_search搜不到时两侧都退0(monkeypatch):
    monkeypatch.setattr(data_api.data_mod.Catalog, "search", lambda *a, **k: [])
    argv = ["data", "search", "没有这种指标"]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 0
    assert "搜不到不等于没有" in prose.output
    assert _one_json(js.stdout)["data"]["candidates"] == [] and js.stderr == ""


def test_data_fetch拉不下来时两侧都退1(monkeypatch, tmp_path):
    run = tmp_path / "r"; run.mkdir()

    def _boom(*a, **k):
        raise RuntimeError("404")

    monkeypatch.setattr(data_api.data_mod, "fetch", _boom)
    argv = ["data", "fetch", "wb", "NOPE", "--run", str(run)]
    prose = runner.invoke(app, argv)
    js = runner.invoke(app, [*argv, "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "拉不下来" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "unavailable" and js.stderr == ""


# ─────────────────────────── review：两侧退出码一样，中途回显在 data.log 里

import oss.cli.main as cli_main                          # noqa: E402
from oss.host.reviewer import ReviewVerdict, ReviewerUnavailable   # noqa: E402


def _review_run(tmp_path: Path, name: str) -> Path:
    run = tmp_path / name; run.mkdir()
    (run / "paper_rendered.md").write_text("# T\n\n## Abstract\nx", encoding="utf-8")
    _plan(run)
    (run / "results.json").write_text(json.dumps(
        {"plan_id": "p", "n": 10, "estimates": {}}), encoding="utf-8")
    (run / "power.json").write_text("{}", encoding="utf-8")
    return run


class _OldSigReviewer:
    """**老签名**：不认 round_ / previous_findings。桌审带上它们就 TypeError。"""

    def __init__(self, *a, **k) -> None:
        pass

    def review(self, files, level="normal", kind="generic", emphasis=""):
        v = ReviewVerdict(verdict="WARN", reason="ok", findings=["[文字] x"])
        v.reviewer_model, v.executor_model = "deepseek-v4-flash", "claude-opus-4"
        v.reviewer_family, v.executor_family = "deepseek", "anthropic"
        return v


class _NewSigReviewer:
    def __init__(self, *a, **k) -> None:
        pass

    def review(self, files, level="normal", kind="generic", emphasis="",
               round_=1, previous_findings=None):
        v = ReviewVerdict(verdict="PASS", reason="ok", findings=["[设计] 1 处置了"])
        v.reviewer_model, v.executor_model = "deepseek-v4-flash", "claude-opus-4"
        v.reviewer_family, v.executor_family = "deepseek", "anthropic"
        return v


def test_review桌审两侧都退0且没有复审简报(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main, "Reviewer", _OldSigReviewer)
    p_run, j_run = _review_run(tmp_path, "p"), _review_run(tmp_path, "j")
    for r in (p_run, j_run):
        _review(r, "review_paper_rendered_识别.json", "FAIL", ["[设计] 平行趋势不成立"], 1)
    prose = runner.invoke(app, ["review", "--desk", "--round", "2", "--run", str(p_run)])
    js = runner.invoke(app, ["review", "--desk", "--round", "2", "--run", str(j_run), "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    assert "只判处置了没有" not in prose.output
    d = _one_json(js.stdout)["data"]
    assert d["verdict"] == "WARN" and d["log"] == [] and js.stderr == ""
    assert Path(d["path"]).name == "review_desk_r2.json"


def test_review论文复审两侧都带简报_那一行在log里(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main, "Reviewer", _NewSigReviewer)
    p_run, j_run = _review_run(tmp_path, "p"), _review_run(tmp_path, "j")
    for r in (p_run, j_run):
        _review(r, "review_paper_rendered_识别.json", "FAIL",
                ["[设计] 平行趋势不成立", "[新发现] 旧的新发现"], 1)
    argv = ["review", "--paper", "--round", "2", "--run"]
    prose = runner.invoke(app, [*argv, str(p_run)])
    js = runner.invoke(app, [*argv, str(j_run), "--json"])
    assert js.exit_code == prose.exit_code == 0, prose.output
    assert "第 2 轮复审：带上第 1 轮的 1 条意见，**只判处置了没有**" in prose.output
    d = _one_json(js.stdout)["data"]
    assert any("只判处置了没有" in t for _, t in d["log"]) and js.stderr == ""
    assert d["verdict"] == "PASS" and d["kinds"]["设计"] == 1


def test_review轮次门两侧都退4(tmp_path):
    p_run, j_run = _review_run(tmp_path, "p"), _review_run(tmp_path, "j")
    argv = ["review", "--paper", "--round", "3", "--run"]
    prose = runner.invoke(app, [*argv, str(p_run)])
    js = runner.invoke(app, [*argv, str(j_run), "--json"])
    assert js.exit_code == prose.exit_code == 4
    assert "超过上限" in prose.output and "当前决定：" in prose.output
    assert _one_json(js.stdout)["data"]["kind"] == "refused" and js.stderr == ""


def test_review后端不可用两侧都退1且都不算通过(monkeypatch, tmp_path):
    class _Dead:
        def __init__(self, *a, **k) -> None:
            pass

        def review(self, *a, **k):
            raise ReviewerUnavailable("HTTP 500")

    monkeypatch.setattr(cli_main, "Reviewer", _Dead)
    p_run, j_run = _review_run(tmp_path, "p"), _review_run(tmp_path, "j")
    argv = ["review", "--paper", "--run"]
    prose = runner.invoke(app, [*argv, str(p_run)])
    js = runner.invoke(app, [*argv, str(j_run), "--json"])
    assert js.exit_code == prose.exit_code == 1
    assert "审稿后端不可用：HTTP 500" in prose.output
    # 措辞 2026-09-07 由「任何跨模型裁决」改成「任何裁决」：缺省已允许同模型自审，
    # 后端挂了的时候连自审都没拿到，说「没得到跨模型裁决」反而漏掉了更严重的那一半
    assert "  **这不是「通过」**——本次没有得到任何裁决" in prose.output
    r = _one_json(js.stdout)
    assert r["data"]["kind"] == "unavailable" and js.stderr == ""
    assert any("不是「通过」" in x for x in r["problems"])


# ═════════════════════════ 35 条命令，逐条验：一行 JSON、退出码不变
#
# 上面那些用例一条命令验一件事；这一节反过来——**一条命令都不许漏**。
# 前面几个任务是一条一条搬的，`--json` 也就一条一条补；漏掉一条不会有任何测试变红，
# 只会在 MCP 那一侧变成「这个工具偶尔返回散文」。所以这里的名单由
# `_json_commands(app)` **从 typer 自己身上数出来**，跟 JSON_CASES 对表：
# 新加一条带 `--json` 的命令而忘了写用例，`test_每条带json的命令都在名单里` 当场红。

import inspect                                          # noqa: E402


def _json_commands(typer_app, prefix: tuple[str, ...] = ()) -> set[str]:
    """走一遍 typer 的注册表，挑出**参数里有 `json_out` 的**命令，返回 `"lit verify"` 这样的全名。"""
    found = set()
    for cmd in typer_app.registered_commands:
        name = cmd.name or cmd.callback.__name__.replace("_", "-")
        if "json_out" in inspect.signature(cmd.callback).parameters:
            found.add(" ".join([*prefix, name]))
    for grp in typer_app.registered_groups:
        found |= _json_commands(grp.typer_instance, (*prefix, grp.name))
    return found


#: (命令全名, 参数)。`<RUN>` 换成一个**空的**运行目录。
#:
#: 大多数用例走的是**拒绝路径**——那正是最要紧的一半：门拒绝时 MCP 那一侧也必须
#: 拿到一个能解析的对象，而不是一段散文加一个非零退出码。挑用例的规矩是
#: **一条命令一个用例、能不打网络就不打**：三条绕不开网络的（lit search /
#: data search / data fetch）在这一节里 monkeypatch 掉，另外三条（lit add /
#: lit fetch / review）由拒绝路径挡在网络之前，用一个「碰到就失败」的桩钉住这件事。
JSON_CASES: list[tuple[str, list[str]]] = [
    ("profile",       ["profile", "<RUN>/nope.csv", "--run", "<RUN>"]),
    ("reshape",       ["reshape", "<RUN>/nope.csv", "--run", "<RUN>"]),
    ("stack",         ["stack", "<RUN>/nope.json", "--run", "<RUN>"]),
    ("merge",         ["merge", "<RUN>/a.csv", "<RUN>/b.csv", "--run", "<RUN>"]),
    ("bind",          ["bind", "--run", "<RUN>"]),
    ("figure",        ["figure", "nope", "--run", "<RUN>"]),
    ("power",         ["power", "--run", "<RUN>"]),
    ("run",           ["run", "--run", "<RUN>"]),
    ("route",         ["route", "--run", "<RUN>"]),
    ("eval",          ["eval", "--run", "<RUN>"]),
    ("review",        ["review", "--paper", "--run", "<RUN>"]),
    ("schema",        ["schema", "plan"]),
    ("paper check",   ["paper", "check", "--run", "<RUN>"]),
    ("paper render",  ["paper", "render", "--run", "<RUN>"]),
    ("paper export",  ["paper", "export", "--run", "<RUN>"]),
    ("loop status",   ["loop", "status", "--run", "<RUN>"]),
    ("anchor switch", ["anchor", "switch", "--to", "x", "--why", "y", "--run", "<RUN>"]),
    ("lit search",    ["lit", "search", "q", "--run", "<RUN>"]),
    ("lit fetch",     ["lit", "fetch", "--run", "<RUN>"]),
    ("lit cards",     ["lit", "cards", "--run", "<RUN>"]),
    ("lit screen",    ["lit", "screen", "--run", "<RUN>"]),
    ("lit snowball",  ["lit", "snowball", "--run", "<RUN>"]),
    ("lit import",    ["lit", "import", "x.txt", "--run", "<RUN>"]),
    ("lit add",       ["lit", "add", "10.1/x", "--run", "<RUN>"]),
    ("lit verify",    ["lit", "verify", "--run", "<RUN>"]),
    ("data list",     ["data", "list"]),
    ("data search",   ["data", "search", "没有这种指标"]),
    ("data fetch",    ["data", "fetch", "wb", "NOPE", "--run", "<RUN>"]),
    ("data get",      ["data", "get", "nope"]),
    ("data columns",  ["data", "columns", "nope"]),
    ("data info",     ["data", "info", "nope"]),
    ("data needs",    ["data", "needs", "--run", "<RUN>/nope"]),
    ("results import", ["results", "import", "<RUN>/nope.json", "--raw", "<RUN>/nope.txt", "--run", "<RUN>"]),
    ("sim init",   ["sim", "init", "<RUN>"]),
    ("sim check",  ["sim", "check", "<RUN>/nope"]),
    # --from 是 --simulator 的别名——这条钉住别名不回归，正名 --simulator 走其他用例
    ("sim pack",   ["sim", "pack", "--from", "oasis", "<RUN>/nope"]),
    ("sim import", ["sim", "import", "<RUN>/nope", "--run", "<RUN>"]),
    ("sim doctor", ["sim", "doctor", "--simulator", "nope"]),
]


def test_每条带json的命令都在名单里():
    """名单**从 typer 自己身上数**，不是手抄的。手抄的名单漏一条不会红。

    37 条的由来：链条上的子命令全在这里；`doctor` / `smoke` / `install-skills` /
    `trace save` / `style *` 这七条是给人用的运维命令，不进 MCP 的工具表，也不铺 `--json`。
    （2026-09-07 加 `data needs`、`results import`，35 → 37；
    2026-09-09 加 `stack`——多轮调查竖着摞，37 → 38。）
    """
    registered = _json_commands(app)
    assert len(registered) == 38, sorted(registered)
    assert registered == {name for name, _ in JSON_CASES}, (
        "带 --json 的命令跟 JSON_CASES 对不上："
        f"漏了 {sorted(registered - {n for n, _ in JSON_CASES})}，"
        f"多了 {sorted({n for n, _ in JSON_CASES} - registered)}")


@pytest.fixture()
def _offline(monkeypatch, tmp_path):
    """把这一节的网络接缝全部堵死。**堵在 api 模块的命名空间上**——打的补丁必须是被调用的那个名字。

    `_never` 的那几条是「拒绝路径应该把它挡在网络之前」的钉子：真被调到就当场红，
    而不是安静地去连一次网。
    """
    def _never(*_a, **_k):
        pytest.fail("这条用例该在打网络之前就被拒绝")

    monkeypatch.chdir(tmp_path)  # data list 读的是**当前目录**下的 data/
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: LiteratureCorpus(records=[]))
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi", _never)
    monkeypatch.setattr(lit_api.reading_mod, "fetch", _never)
    monkeypatch.setattr(data_api.data_mod.Catalog, "search", lambda *a, **k: [])
    monkeypatch.setattr(data_api.data_mod, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("离线")))

    class _NeverReviewer:
        def __init__(self, *a, **k) -> None:
            pass

        review = staticmethod(_never)

    monkeypatch.setattr(cli_main, "Reviewer", _NeverReviewer)


@pytest.mark.parametrize("name,argv", JSON_CASES, ids=[c[0] for c in JSON_CASES])
def test_每条命令的json都是一行且退出码不变(name, argv, tmp_path, _offline):
    run = tmp_path / "r"; run.mkdir()
    args = [a.replace("<RUN>", str(run)) for a in argv]

    prose = runner.invoke(app, args)
    js = runner.invoke(app, [*args, "--json"])
    assert js.exit_code == prose.exit_code, (
        f"{name} 的退出码变了：散文 {prose.exit_code} / JSON {js.exit_code}")

    d = _one_json(js.stdout)
    assert set(d) <= {"ok", "summary", "data", "decision", "problems"}, f"{name} 多了键"
    assert d["data"]["exit_code"] == js.exit_code, f"{name} 的 data.exit_code 跟退出码对不上"
    assert d["ok"] == (d["data"]["exit_code"] == 0), f"{name} 的 ok 不等于「退出码为零」"
    assert isinstance(d["summary"], str) and d["summary"], f"{name} 没给 summary"
    # `--json` 下 stderr 一个字都不许有：宿主的日志里出现「JSON 之外还有话」，
    # 那句话没人解析得了，而它往往正是失败的原因
    assert js.stderr == "", f"{name} 在 --json 下往 stderr 打了东西：{js.stderr!r}"
