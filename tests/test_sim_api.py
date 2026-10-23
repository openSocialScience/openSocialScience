"""sim api：吃路径与值，吐字典；不 import typer、不打印、不退出。退出码是契约。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.api import errors
from oss.api import sim as sim_api
from tests.test_simbox import make_box


def test_init产出空白配料单与运行清单(tmp_path):
    res = sim_api.init(str(tmp_path / "box"))
    assert res["ok"] is True
    box = Path(res["data"]["box"])
    meta = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))
    assert meta["simulator"] == ""            # 必填项都在、都是空
    assert (box / "runs.csv").read_text(encoding="utf-8").startswith("file,condition,rep,complete")
    # 2026-08-31 质量审：raw/ 顺手建好，社科仿真说明书的 cp/另存命令才能直接贴、
    # 不用先手动 mkdir
    assert (box / "raw").is_dir()


def test_init父目录不存在也能建(tmp_path):
    res = sim_api.init(str(tmp_path / "深一层" / "box"))
    assert res["ok"] is True and Path(res["data"]["box"]).is_dir()


def test_init拒绝已存在的箱子(tmp_path):
    sim_api.init(str(tmp_path / "box"))
    with pytest.raises(errors.ApiError) as e:
        sim_api.init(str(tmp_path / "box"))
    assert e.value.code == 1


def test_init目标是文件时说清楚(tmp_path):
    (tmp_path / "box").write_text("x", encoding="utf-8")
    with pytest.raises(errors.ApiError) as e:
        sim_api.init(str(tmp_path / "box"))
    assert e.value.code == 1 and "不是目录" in e.value.summary


def test_check合格箱子ok(tmp_path):
    res = sim_api.check(str(make_box(tmp_path)))
    assert res["ok"] is True and res["data"]["exit_code"] == 0


def test_check返回绝对路径且能吃相对路径(tmp_path, monkeypatch):
    make_box(tmp_path)
    monkeypatch.chdir(tmp_path)
    res = sim_api.check("box")
    assert res["data"]["box"] == str((tmp_path / "box").resolve())


def test_check拒收走退出码7且problems逐条(tmp_path):
    box = make_box(tmp_path, drop_meta_key="prompts")
    with pytest.raises(errors.ApiError) as e:
        sim_api.check(str(box))
    assert e.value.code == 7 and any("prompts" in p for p in e.value.problems)


def test_check目录不存在走退出码2(tmp_path):
    with pytest.raises(errors.ApiError) as e:
        sim_api.check(str(tmp_path / "nowhere"))
    assert e.value.code == 2


def test_check非法JSON走退出码1(tmp_path):
    box = make_box(tmp_path)
    (box / "sim.meta.json").write_text("{坏的", encoding="utf-8")
    with pytest.raises(errors.ApiError) as e:
        sim_api.check(str(box))
    assert e.value.code == 1


def test_check降级信息在data里(tmp_path):
    box = make_box(tmp_path, reps=("1",), meta_overrides={"n_reps": 1})
    res = sim_api.check(str(box))
    assert res["ok"] is True and res["data"]["downgrade"] is True


def test_import入库记留痕并写sim_state(tmp_path):
    box = make_box(tmp_path)
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    res = sim_api.import_box(str(box), run=str(run))
    assert res["ok"] is True
    state = json.loads((run / "sim_state.json").read_text(encoding="utf-8"))
    assert state["box"] == str(Path(box).resolve()) and state["downgrade"] is False
    trace = (run / "trace.jsonl").read_text(encoding="utf-8")
    assert "sim import" in trace


def test_import运行目录不存在走退出码1(tmp_path):
    box = make_box(tmp_path)
    with pytest.raises(errors.ApiError) as e:
        sim_api.import_box(str(box), run=str(tmp_path / "runs" / "nope"))
    assert e.value.code == 1


def test_import拒收的箱子进不了库且目录未被碰(tmp_path):
    box = make_box(tmp_path, drop_meta_key="model")
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    with pytest.raises(errors.ApiError) as e:
        sim_api.import_box(str(box), run=str(run))
    assert e.value.code == 7
    assert list(run.iterdir()) == []          # sim_state 和 trace 都不许出现


from tests.test_sim_oasis_adapter import make_raw


def test_pack走插头并回报行数(tmp_path):
    box = make_raw(tmp_path)
    res = sim_api.pack("oasis", box=str(box))
    assert res["ok"] is True and res["data"]["n_state_rows"] == 8


def test_pack不认识的模拟器点名现有插头(tmp_path):
    with pytest.raises(errors.ApiError) as e:
        sim_api.pack("netlogo", box=str(make_raw(tmp_path)))
    assert "oasis" in e.value.summary


def test_pack空清单拒绝而不是成功清空(tmp_path):
    box = make_raw(tmp_path)
    sim_api.pack("oasis", box=str(box))
    (box / "runs.csv").write_text("file,condition,rep,complete\n", encoding="utf-8")
    with pytest.raises(errors.ApiError) as e:
        sim_api.pack("oasis", box=str(box))
    assert e.value.code == 1 and "一条跑都没有" in e.value.summary


def test_pack装箱失败走退出码1(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").unlink()
    with pytest.raises(errors.ApiError) as e:
        sim_api.pack("oasis", box=str(box))
    assert e.value.code == 1 and "装箱失败" in e.value.summary


def test_pack缺箱目录走退出码2(tmp_path):
    with pytest.raises(errors.ApiError) as e:
        sim_api.pack("oasis", box=str(tmp_path / "nowhere"))
    assert e.value.code == 2


def test_doctor档案顶层不是对象走退出码1():
    """profile.json 是随包分发的数据文件——坏了是发布事故，但要报人话不要裸 TypeError。"""
    prof_path = Path(sim_api.__file__).resolve().parent.parent / "sims" / "oasis" / "profile.json"
    original = prof_path.read_text(encoding="utf-8")
    try:
        prof_path.write_text("[1]", encoding="utf-8")
        with pytest.raises(errors.ApiError) as e:
            sim_api.doctor("oasis")
        assert e.value.code == 1 and "对象" in e.value.summary
    finally:
        prof_path.write_text(original, encoding="utf-8")


def test_doctor报告版本锁与检查结果():
    res = sim_api.doctor("oasis")
    data = res["data"]
    assert data["version_locked"] == "0.2.5"
    assert {c["name"] for c in data["checks"]} == {"包可导入且版本相符", "样本数据在", "网关变量在"}
    # 本机装没装 OASIS 都不许让测试挂：只查形状，通过与否如实报
    assert all(isinstance(c["ok"], bool) and c["note"] for c in data["checks"])


# ── agentsociety 的 doctor 三查（T3；样本解析解耦是停车场 5 的原案）


def _check(res: dict, name: str) -> dict:
    return {c["name"]: c for c in res["data"]["checks"]}[name]


def test_doctor_agentsociety三查齐且形状同oasis():
    res = sim_api.doctor("agentsociety")
    data = res["data"]
    assert data["version_locked"] == "2.8.4"
    assert {c["name"] for c in data["checks"]} == {"包可导入且版本相符", "样本数据在", "网关变量在"}
    # 本机装没装 agentsociety2 都不许让测试挂：只查形状，通过与否如实报
    assert all(isinstance(c["ok"], bool) and c["note"] for c in data["checks"])


def test_doctor版本不符点名两串版本号(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "9.9.9")
    c = _check(sim_api.doctor("agentsociety"), "包可导入且版本相符")
    assert c["ok"] is False and "9.9.9" in c["note"] and "2.8.4" in c["note"]


def test_doctor样本按各插头自持基路径解析_oasis缺席不连坐(tmp_path, monkeypatch):
    """停车场 5 的原案：AgentSociety 的样本检查曾借相对上跳路径迁就
    ~/Documents/oasis 基路径，OASIS 克隆一缺席就误败。解耦后各插头只看
    自家 profile.json 的 sample_base。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    f = (tmp_path / "Documents" / "agentsociety" / "packages" / "agentsociety2"
         / "profiles.json")
    f.parent.mkdir(parents=True)
    f.write_text("[]", encoding="utf-8")
    # 故意不建 ~/Documents/oasis——耦合若还在，这一条就会误败
    c = _check(sim_api.doctor("agentsociety"), "样本数据在")
    assert c["ok"] is True and "profiles.json" in c["note"]


def test_doctor样本缺席打叉报全路径(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _check(sim_api.doctor("agentsociety"), "样本数据在")
    assert c["ok"] is False and "profiles.json" in c["note"] and "不在" in c["note"]


def test_doctor_oasis样本仍拼老基路径不回归(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = (tmp_path / "Documents" / "oasis" / "data" / "twitter_dataset"
         / "anonymous_topic_200_1h" / "False_Business_0.csv")
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    c = _check(sim_api.doctor("oasis"), "样本数据在")
    assert c["ok"] is True


def test_两插头档案各自持样本基路径():
    sims = Path(sim_api.__file__).resolve().parent.parent / "sims"
    for sim, base in (("oasis", "~/Documents/oasis"),
                      ("agentsociety", "~/Documents/agentsociety")):
        prof = json.loads((sims / sim / "profile.json").read_text(encoding="utf-8"))
        assert prof["sample_base"] == base
        # 相对上跳借道别家克隆是停车场 5 的原案——不许再犯
        assert not prof["sample_data"].startswith("..")


def test_doctor中转站_BASE_KEY齐即直接能跑(monkeypatch):
    monkeypatch.setenv("BASE", "https://relay.example/v1")
    monkeypatch.setenv("KEY", "sk-test")
    c = _check(sim_api.doctor("agentsociety"), "网关变量在")
    assert c["ok"] is True and "AGENTSOCIETY_LLM_API" in c["note"]


def test_doctor中转站_只有坐标可抄时绿但提醒还差export(tmp_path, monkeypatch):
    for k in ("BASE", "KEY", "REVIEWER_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AGENTSOCIETY_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("AGENTSOCIETY_LLM_API_BASE", "https://relay.example/v1")
    monkeypatch.chdir(tmp_path)          # 避开项目根的 .env
    c = _check(sim_api.doctor("agentsociety"), "网关变量在")
    assert c["ok"] is True and "export" in c["note"] and "BASE" in c["note"]


def test_doctor中转站全无线索打叉(tmp_path, monkeypatch):
    from oss.core import envfile

    for k in ("BASE", "KEY", "REVIEWER_BASE_URL",
              "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)          # 避开项目根的 .env（cwd 向上那一档）
    # 源码检出这一档认的是 oss 包自己的位置，跟 cwd 无关——这条测试跑在本仓库
    # 的开发态检出里，不关掉它就永远会在这里找到仓库自己那份真 .env，
    # 让「全无线索」这个前提假不成立。
    monkeypatch.setattr(envfile, "_source_checkout_root", lambda: tmp_path / "不是仓库")
    # 用户级这一档认的是真实 HOME，跟 cwd、源码检出都无关——开发者一旦照 Fix 1
    # 的说明建了 ~/.config/oss/.env，这条测试就会在真机器上假装「全无线索」，
    # 实际早已被那份文件戳穿。指到一个不存在的路径，把这一档也关掉。
    monkeypatch.setattr(envfile, "USER_ENV", tmp_path / "home" / ".config" / "oss" / ".env")
    c = _check(sim_api.doctor("agentsociety"), "网关变量在")
    assert c["ok"] is False and ".env" in c["note"]
