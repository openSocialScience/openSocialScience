"""AgentSociety 插头：replay JSONL 分片 + 问卷 artifact + runs.csv → 货箱两张表。

fixture 照 agentsociety2==2.8.4 的 replay_sink 真实落盘格式手工造微型样张：
`replay/{table}.{分片}.jsonl` 分片（分片名不必按 crc 重算——插头按设计稿只 glob）、
`_schema.json` 清单、`artifacts/questionnaire_step_{步号}_{时刻}.json` 问卷、
`core_agent_profile` 行（profile 列按 _normalize 规矩是 JSON 字符串）。
全程不联网、不装 agentsociety——上游改 schema 要连设计稿侦察记录一起改。
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from oss import sims
from oss.core import simbox
from oss.sims.agentsociety import adapter


def _event(id, step, t, sender, action, *, content=None, receiver=None,
           target=None) -> dict:
    """一行 social_media_event：八列齐全，没给的按上游习惯写 null 不省略。"""
    return {"id": id, "step": step, "t": t, "sender_id": sender,
            "action": action, "content": content, "receiver_id": receiver,
            "target_id": target}


#: 每种动作各至少一行；follow 特意只给 receiver_id（真库里关注就长这样），
#: id=6 的 like 特意指向 target_id=0——0 是合法 id，不许被真值判断吞掉
EVENTS = [
    _event(1, 0, "2026-09-01T08:00:00", 1, "post", content="开张帖"),
    _event(2, 0, "2026-09-01T08:00:05", 2, "like", receiver=1, target=101),
    _event(3, 1, "2026-09-01T09:00:00", 3, "comment", content="有道理",
           receiver=1, target=101),
    _event(4, 1, "2026-09-01T09:00:10", 2, "follow", receiver=1),
    _event(5, 1, "2026-09-01T09:00:20", 3, "repost", target=101),
    _event(6, 1, "2026-09-01T09:00:30", 1, "like", target=0),
]

#: _schema.json 的最小真实形状：tables + datasets 两段（replay_sink 合并出来的样子）
SCHEMA = {
    "tables": {
        "social_media_event": {"columns": [{"name": "id"}, {"name": "step"}],
                               "primary_key": ["id"], "indexes": [["step"]]},
        "core_agent_profile": {"columns": [{"name": "id"}], "primary_key": ["id"],
                               "indexes": [["name"]]},
        "social_media_agent_state": {"columns": [{"name": "agent_id"}],
                                     "primary_key": [], "indexes": []},
    },
    "datasets": {
        "social_media.event": {"dataset_id": "social_media.event",
                               "table_name": "social_media_event",
                               "kind": "event_stream"},
    },
}

#: 配料单必填 12 项全齐的版本——真验货（check_box）要用
FULL_META = {
    "simulator": "agentsociety", "simulator_version": "2.8.4",
    "model": "deepseek-v4-flash", "model_provider": "中转站",
    "temperature": "provider_default",
    "prompts": {"q_stance": "立场1-7", "q_trust": "信任程度"},
    "population": "合成样张 3 小人",
    "conditions": {"A": "热度推荐", "B": "随机推荐"},
    "n_reps": 1, "rounds": 2,
    "interview_plan": "开跑前与第 2 步后各一波问卷；tick 秒换算见跑单",
    "contamination_note": "合成样张，不涉及",
}


def _shard(replay: Path, table: str, shard: str, rows: list[dict]) -> None:
    path = replay / f"{table}.{shard}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def make_questionnaire(art_dir: Path, step_idx: int, sim_time: str,
                       step_count: int, answers_by_agent: dict,
                       *, questions=("q_stance", "q_trust")) -> Path:
    """照 QuestionnaireResponse.model_dump 的全结构造一份问卷 artifact。

    answers_by_agent：{agent_id: {question_id: parsed_value}}；值为 None 表示
    这条答案 parse_success=false（parsed_value 空、带 parse_error），插头该标缺失。
    """
    stamp = sim_time.replace("-", "").replace(":", "").replace("T", "_")
    path = art_dir / f"questionnaire_step_{step_idx}_{stamp}.json"
    doc = {
        "questionnaire_id": "attitude", "title": "态度问卷", "description": "",
        "simulation_time": sim_time, "step_count": step_count,
        "target_agent_ids": sorted(answers_by_agent),
        # 字段名照上游 QuestionItem（society/models.py）逐一对齐：
        # id / prompt / response_type(text|integer|float|choice|json) / choices
        "questions": [
            {"id": q, "prompt": f"请回答{q}",
             "response_type": "integer" if q == "q_stance" else "choice",
             "choices": [] if q == "q_stance" else ["信任", "一般", "不信任"]}
            for q in questions],
        "responses": [
            {"agent_id": a, "agent_name": f"小{a}",
             "answers": [
                 {"question_id": q, "raw_text": "" if v is None else str(v),
                  "raw_response": None, "parsed_value": v,
                  "reason": None if v is None else "有理由",
                  "parse_success": v is not None,
                  "parse_error": "答非所问" if v is None else None}
                 for q, v in qa.items()]}
            for a, qa in sorted(answers_by_agent.items())],
        "context_snapshots": [],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def make_run(run_dir: Path) -> None:
    """一场跑的目录：replay/ 分片 + _schema.json + artifacts/ 两波问卷。

    事件行故意打散进两个分片、分片内不按 (step,id) 排——插头必须排序后再写。
    """
    replay = run_dir / "replay"
    replay.mkdir(parents=True)
    _shard(replay, "social_media_event", "00", [EVENTS[1], EVENTS[4]])
    _shard(replay, "social_media_event", "7f",
           [EVENTS[3], EVENTS[0], EVENTS[2], EVENTS[5]])

    def st(a, s, t, f, g, p):
        return {"agent_id": a, "step": s, "t": t, "followers_count": f,
                "following_count": g, "posts_count": p}

    _shard(replay, "social_media_agent_state", "01", [
        st(1, 0, "2026-09-01T08:30:00", 0, 0, 1),
        st(2, 0, "2026-09-01T08:30:00", 0, 1, 0),
        st(3, 1, "2026-09-01T09:30:00", 0, 0, 1),
    ])
    _shard(replay, "social_media_agent_state", "02", [
        st(3, 0, "2026-09-01T08:30:00", 0, 0, 0),
        st(1, 1, "2026-09-01T09:30:00", 1, 0, 2),
        st(2, 1, "2026-09-01T09:30:00", 0, 2, 0),
    ])
    _shard(replay, "core_agent_profile", "05", [
        {"id": a, "name": f"小{a}",
         "profile": json.dumps({"age": 20 + a}, ensure_ascii=False),
         "created_at": "2026-09-01T08:00:00"}
        for a in (1, 2, 3)])
    (replay / "_schema.json").write_text(
        json.dumps(SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")

    art = run_dir / "artifacts"
    art.mkdir()
    make_questionnaire(art, 0, "2026-09-01T08:00:00", 0, {
        1: {"q_stance": 5, "q_trust": "一般"},
        2: {"q_stance": 3, "q_trust": "信任"},
        3: {"q_stance": 4, "q_trust": "不信任"},
    })
    make_questionnaire(art, 2, "2026-09-01T10:00:00", 2, {
        1: {"q_stance": 6, "q_trust": "信任"},
        2: {"q_stance": 3, "q_trust": None},   # parse_success=false → 缺失不猜
        3: {"q_stance": 4, "q_trust": "不信任"},
    })


def make_raw(tmp_path: Path, *, full_meta: bool = False) -> Path:
    box = tmp_path / "box"
    (box / "raw").mkdir(parents=True)
    for c in ("A", "B"):
        make_run(box / "raw" / f"{c}_1")
    (box / "runs.csv").write_text(
        "file,condition,rep,complete\nraw/A_1,A,1,yes\nraw/B_1,B,1,yes\n",
        encoding="utf-8")
    meta = FULL_META if full_meta else {"simulator": "agentsociety", "n_reps": 1}
    (box / "sim.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (box / "runner.py").write_text("# 占位\n", encoding="utf-8")
    return box


def rows_of(box: Path, name: str, condition: str | None = None) -> list[dict]:
    with (box / name).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if condition is not None:
        rows = [r for r in rows if r["condition"] == condition]
    return rows


STATES_HEADER = ("condition,rep,wave,agent_id,q_stance,q_trust,"
                 "social_media_followers_count,social_media_following_count,"
                 "social_media_posts_count")
EVENTS_HEADER = ("condition,rep,step,seq,sim_time,agent_id,action,target,"
                 "target_kind,content")


# ── 正常翻译


def test_pack产出两张表且钥匙列齐(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    states = (box / "states.csv").read_text(encoding="utf-8").splitlines()
    assert states[0] == STATES_HEADER
    assert "A,1,0,1,5,一般,,," in states          # 波 0 基线：还没有快照可并
    assert "A,1,1,1,6,信任,1,0,2" in states       # 波 1：并入 step 1 的快照
    events = (box / "events.csv").read_text(encoding="utf-8").splitlines()
    assert events[0] == EVENTS_HEADER
    assert report["n_state_rows"] == 12 and report["n_event_rows"] == 12


def test_事件映射_actor轮次target与kind对齐(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    rows = {r["seq"]: r for r in rows_of(box, "events.csv", "A")}
    like = rows["2"]
    assert like["agent_id"] == "2" and like["step"] == "0"    # actor=sender_id，轮=step
    assert like["target"] == "101" and like["target_kind"] == "post"
    assert rows["3"]["target"] == "101" and rows["3"]["target_kind"] == "post"  # comment
    assert rows["5"]["target"] == "101" and rows["5"]["target_kind"] == "post"  # repost
    # post 压根没有「指着谁」这回事：target/kind 都空，且不算进 n_blank_targets
    post = rows["1"]
    assert post["target"] == "" and post["target_kind"] == ""
    assert post["content"] == "开张帖"
    assert post["sim_time"] == "2026-09-01T08:00:00"
    assert report["n_blank_targets"] == 0


def test_follow只有receiver_id也定得到target(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    follow = next(r for r in rows_of(box, "events.csv", "A")
                  if r["action"] == "follow")
    assert follow["target"] == "1"          # target_id 空时退到 receiver_id
    assert follow["target_kind"] == "agent"  # 设计稿已决：follow→agent


def test_target为0不被真值判断吞掉(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    zero = next(r for r in rows_of(box, "events.csv", "A") if r["seq"] == "6")
    assert zero["target"] == "0" and zero["target_kind"] == "post"
    assert report["n_blank_targets"] == 0


def test_事件跨分片按step与id排序且seq取id(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    rows = rows_of(box, "events.csv", "A")
    assert [(r["step"], r["seq"]) for r in rows] == [
        ("0", "1"), ("0", "2"), ("1", "3"), ("1", "4"), ("1", "5"), ("1", "6")]


def test_无社媒事件分片的跑只出状态不报错(tmp_path):
    """事件流只绑社交媒体模块（设计稿已决）：别的模块没有事件分片是常态，不是事故。"""
    box = make_raw(tmp_path)
    for p in (box / "raw" / "B_1" / "replay").glob("social_media_event.*.jsonl"):
        p.unlink()
    report = adapter.pack(box)
    assert report["n_event_rows"] == 6              # 只剩 A 的 6 条
    assert not rows_of(box, "events.csv", "B")
    assert len(rows_of(box, "states.csv", "B")) == 6  # 状态表不受影响
    assert report["n_runs_no_events"] == 1          # 合法，但缺席要看得见


# ── 波次（问卷 artifact 的序号）


def test_波次按文件步号再模拟时间消歧(tmp_path):
    """同 step 多波（问卷不推进 step）靠 simulation_time 排先后——设计稿已决。"""
    box = make_raw(tmp_path)
    make_questionnaire(box / "raw" / "A_1" / "artifacts", 2,
                       "2026-09-01T09:30:00", 2, {1: {"q_stance": 7}})
    adapter.pack(box)
    mine = {r["wave"]: r["q_stance"] for r in rows_of(box, "states.csv", "A")
            if r["agent_id"] == "1"}
    assert mine == {"0": "5", "1": "7", "2": "6"}   # 09:30 的那波插在中间


def test_空responses的问卷占一个波号但不产行(tmp_path):
    box = make_raw(tmp_path)
    make_questionnaire(box / "raw" / "A_1" / "artifacts", 3,
                       "2026-09-01T11:00:00", 2, {})
    report = adapter.pack(box)
    waves = {r["wave"] for r in rows_of(box, "states.csv", "A")}
    assert waves == {"0", "1"}                      # 波 2 存在过，但没有一行
    assert report["n_state_rows"] == 12


def test_parse失败的答案标缺失不猜(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    miss = next(r for r in rows_of(box, "states.csv", "A")
                if r["wave"] == "1" and r["agent_id"] == "2")
    assert miss["q_trust"] == "" and miss["q_stance"] == "3"
    assert report["n_parse_miss"] == 2              # A、B 各一格


def test_答案缺parse_success旗按成功算(tmp_path):
    """缺旗但 parsed_value 在：不是 miss，值照写进表——`get("parse_success",
    True)` 的口径，跑单模板 count_parse_miss 与此逐字对齐（双计数器必须数
    同一件事，有可用的值不因缺个旗被丢）。"""
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob(
        "questionnaire_step_0_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    del doc["responses"][0]["answers"][0]["parse_success"]   # 小人 1 的 q_stance
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    report = adapter.pack(box)
    assert report["n_parse_miss"] == 2              # 与健康箱相同：缺旗不是 miss
    row = next(r for r in rows_of(box, "states.csv", "A")
               if r["wave"] == "0" and r["agent_id"] == "1")
    assert row["q_stance"] == "5"                   # 值照写


def test_快照列并到该波对应的step(tmp_path):
    """波的 step_count=N 时并 step N-1 的快照（第 N 步结束时写的那份）；
    N=0 的基线波还没有任何快照，快照列留空。"""
    box = make_raw(tmp_path)
    adapter.pack(box)
    by_wave = {(r["wave"], r["agent_id"]): r for r in rows_of(box, "states.csv", "A")}
    assert by_wave[("1", "1")]["social_media_followers_count"] == "1"
    assert by_wave[("1", "2")]["social_media_following_count"] == "2"
    assert by_wave[("1", "3")]["social_media_posts_count"] == "1"
    assert by_wave[("0", "1")]["social_media_followers_count"] == ""


def test_问卷agent_id是字符串拒绝不静默空列(tmp_path):
    """"1" 与 1 连不上快照表，整行快照列会静默留空、验货还全绿——
    OASIS 44% target 丢失事故的同款缝，必须在门口拒掉，人话点名 agent_id。"""
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_2_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    doc["responses"][0]["agent_id"] = "1"
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(adapter.PackError, match="agent_id 必须是整数"):
        adapter.pack(box)


def test_快照未命中计数现形(tmp_path):
    """答了卷却在该 step 的任何快照表里都查无此人：不拒（合法的部分缺席），
    但 n_snap_miss 要把这行数出来——丢失可以，沉默不行。"""
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_2_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    doc["responses"].append({"agent_id": 4, "agent_name": "小4", "answers": []})
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    report = adapter.pack(box)
    assert report["n_snap_miss"] == 1               # 小人 4 没有 step 1 的快照
    assert report["n_state_rows"] == 13             # 行还在，只是快照列空着


def test_丢了快照表的场次不再豁免(tmp_path):
    """A 场的快照分片整个丢了、兄弟场 B 还留着：A 的波 1 三行全该计入
    n_snap_miss——门是箱级 snap_cols（这箱有快照列可并），不是场级 ld["snap"]
    （按场判会把「丢了表的场次」整场豁免掉）。整箱一张快照表都没有才是
    「没有不是丢」，计 0。"""
    box = make_raw(tmp_path)
    for shard in (box / "raw" / "A_1" / "replay").glob(
            "social_media_agent_state.*.jsonl"):
        shard.unlink()
    report = adapter.pack(box)
    assert report["n_snap_miss"] == 3               # A 波 1 的三个小人全数出来
    # 整箱都没有快照表：不是丢，是压根没有——计 0
    for shard in (box / "raw" / "B_1" / "replay").glob(
            "social_media_agent_state.*.jsonl"):
        shard.unlink()
    assert adapter.pack(box)["n_snap_miss"] == 0


def test_只在快照表出现的小人计数现形(tmp_path):
    """有快照、从没答过卷的小人一行都进不了 states.csv——快照数据整个人蒸发，
    不拒但要在 n_snap_only_agents 里看得见。"""
    box = make_raw(tmp_path)
    _shard(box / "raw" / "A_1" / "replay", "social_media_agent_state", "ee",
           [{"agent_id": 9, "step": 1, "t": "2026-09-01T09:30:00",
             "followers_count": 7, "following_count": 0, "posts_count": 0}])
    report = adapter.pack(box)
    assert report["n_snap_only_agents"] == 1
    assert not [r for r in rows_of(box, "states.csv", "A") if r["agent_id"] == "9"]


# ── 配料单后半与真验货


def test_配料单后半被补上且一场跑一个摘要哈希(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    filled = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))["packed"]
    assert filled["n_state_rows"] == 12 and filled["n_event_rows"] == 12
    assert filled["n_blank_targets"] == 0 and filled["n_parse_miss"] == 2
    assert filled["n_snap_miss"] == 0               # 健康箱：该并的快照都并上了
    assert filled["n_snap_only_agents"] == 0
    assert filled["n_runs_no_events"] == 0
    # 一场跑一个摘要哈希，键是 runs.csv 的 file 值（OASIS「一场跑一个哈希」对齐）
    assert set(filled["file_sha256"]) == {"raw/A_1", "raw/B_1"}
    assert all(len(h) == 64 for h in filled["file_sha256"].values())


def test_没读的分片改一个字节摘要哈希就变(tmp_path):
    """core_agent_profile 分片插头不解析，但它是原始证据——摘要必须盖住它。"""
    box = make_raw(tmp_path)
    before = adapter.pack(box)["file_sha256"]
    _shard(box / "raw" / "A_1" / "replay", "core_agent_profile", "05",
           [{"id": 4, "name": "小4", "profile": "{}",
             "created_at": "2026-09-01T08:00:00"}])
    after = adapter.pack(box)["file_sha256"]
    assert after["raw/A_1"] != before["raw/A_1"]    # 动了的跑摘要变
    assert after["raw/B_1"] == before["raw/B_1"]    # 没动的跑摘要不变


def test_blank_target只数本该有却解不出的(tmp_path):
    box = make_raw(tmp_path)
    _shard(box / "raw" / "A_1" / "replay", "social_media_event", "aa",
           [_event(7, 1, "2026-09-01T09:00:40", 2, "like")])   # 两个 id 都空
    report = adapter.pack(box)
    assert report["n_blank_targets"] == 1   # A、B 各一条 post 的空 target 不算进去


def test_重复装箱幂等(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    first = (box / "events.csv").read_bytes(), (box / "states.csv").read_bytes()
    adapter.pack(box)
    assert ((box / "events.csv").read_bytes(),
            (box / "states.csv").read_bytes()) == first


def test_BOM的runs也能读(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").write_bytes(b"\xef\xbb\xbf" + (box / "runs.csv").read_bytes())
    assert adapter.pack(box)["n_event_rows"] == 12


def test_装出的箱子过真验货(tmp_path):
    """runs.csv 的 file 列指整个跑目录（raw/A_1）——simbox 用 Path.exists() 校验，
    目录放行；这条测试拿真 check_box 钉死这个约定。"""
    box = make_raw(tmp_path, full_meta=True)
    adapter.pack(box)
    v = simbox.check_box(box)
    assert v.problems == []
    assert v.downgrade is True           # 每条件 1 次重复：收但降级
    assert v.single_condition is False


def test_跑单脚本放raw下与跑目录并排_验货认(tmp_path):
    """T2 跑单形态：填好的跑单另存 raw/<C>_<r>.py，与跑目录 raw/<C>_<r>/ 并排。
    check_box 的跑脚本规矩是「根 runner.py 或 raw/ 下的 *.py 至少一份」
    （oss/core/simbox.py，glob 只看 raw/ 一层不进子目录）——删掉根 runner.py、
    只留并排的 .py 也要过验货，这条测试钉死跑单模板 docstring 写的布局约定。"""
    box = make_raw(tmp_path, full_meta=True)
    adapter.pack(box)
    (box / "runner.py").unlink()
    for c in ("A", "B"):
        (box / "raw" / f"{c}_1.py").write_text(
            "# 填好的跑单（占位）\n", encoding="utf-8")
    v = simbox.check_box(box)
    assert v.problems == []


# ── 拒绝而不是猜


def test_缺跑目录报PackError(tmp_path):
    box = make_raw(tmp_path)
    shutil.rmtree(box / "raw" / "A_1")
    with pytest.raises(adapter.PackError, match="不在箱子里"):
        adapter.pack(box)


def test_file列指到普通文件而不是目录报人话(tmp_path):
    box = make_raw(tmp_path)
    shutil.rmtree(box / "raw" / "A_1")
    (box / "raw" / "A_1").write_text("不是目录", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="目录"):
        adapter.pack(box)


def test_缺replay目录报人话(tmp_path):
    box = make_raw(tmp_path)
    shutil.rmtree(box / "raw" / "A_1" / "replay")
    with pytest.raises(adapter.PackError, match="replay"):
        adapter.pack(box)


def test_缺schema哨兵报人话(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1" / "replay" / "_schema.json").unlink()
    with pytest.raises(adapter.PackError, match="_schema.json"):
        adapter.pack(box)


def test_schema没有tables段报人话(tmp_path):
    """哨兵要真干哨兵的活：光「合法 JSON 对象」拦不住随手指错的目录——
    replay_sink 落的表清单一定有 tables 段。"""
    box = make_raw(tmp_path)
    (box / "raw" / "A_1" / "replay" / "_schema.json").write_text(
        json.dumps({"datasets": {}}), encoding="utf-8")
    with pytest.raises(adapter.PackError, match="tables"):
        adapter.pack(box)


def test_schema坏JSON报人话(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1" / "replay" / "_schema.json").write_text(
        "{坏", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="不是合法 JSON"):
        adapter.pack(box)


def test_分片坏JSON行点名文件与行号(tmp_path):
    box = make_raw(tmp_path)
    shard = box / "raw" / "A_1" / "replay" / "social_media_event.00.jsonl"
    lines = shard.read_text(encoding="utf-8").splitlines()
    lines[1] = "{坏"
    shard.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(adapter.PackError,
                       match=r"social_media_event\.00\.jsonl 第 2 行"):
        adapter.pack(box)


def test_分片行不是对象报人话(tmp_path):
    box = make_raw(tmp_path)
    shard = box / "raw" / "A_1" / "replay" / "social_media_event.00.jsonl"
    shard.write_text("[1]\n", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="对象"):
        adapter.pack(box)


def test_事件行缺step报人话(tmp_path):
    box = make_raw(tmp_path)
    bad = {k: v for k, v in EVENTS[0].items() if k != "step"} | {"id": 9}
    _shard(box / "raw" / "A_1" / "replay", "social_media_event", "bb", [bad])
    with pytest.raises(adapter.PackError, match="step"):
        adapter.pack(box)


def test_事件id撞号报人话(tmp_path):
    box = make_raw(tmp_path)
    _shard(box / "raw" / "A_1" / "replay", "social_media_event", "cc",
           [EVENTS[2]])   # id=3 又写了一遍
    with pytest.raises(adapter.PackError, match="撞"):
        adapter.pack(box)


def test_问卷文件名不合样报人话(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1" / "artifacts" / "questionnaire_step_extra.json").write_text(
        "{}", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="文件名"):
        adapter.pack(box)


def test_问卷坏JSON报人话(tmp_path):
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_0_*.json"))
    art.write_text("{坏", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="不是合法 JSON"):
        adapter.pack(box)


def test_问卷step_count不是整数报人话(tmp_path):
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_2_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    doc["step_count"] = "2"
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(adapter.PackError, match="step_count"):
        adapter.pack(box)


def test_同一小人同一波两份回答报人话(tmp_path):
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_0_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    doc["responses"].append(dict(doc["responses"][0]))
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(adapter.PackError, match="两"):
        adapter.pack(box)


def test_同一问题两个答案报人话(tmp_path):
    box = make_raw(tmp_path)
    art = next((box / "raw" / "A_1" / "artifacts").glob("questionnaire_step_0_*.json"))
    doc = json.loads(art.read_text(encoding="utf-8"))
    doc["responses"][0]["answers"].append(dict(doc["responses"][0]["answers"][0]))
    art.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(adapter.PackError, match="两个答案"):
        adapter.pack(box)


def test_问题id净化撞名拒绝(tmp_path):
    """「q 1」与「q(1」净化后都是 q_1——静默合并等于把两道题的数据搅在一起，拒绝。"""
    box = make_raw(tmp_path)
    make_questionnaire(box / "raw" / "A_1" / "artifacts", 3,
                       "2026-09-01T11:00:00", 2, {1: {"q 1": 5, "q(1": 3}},
                       questions=("q 1", "q(1"))
    with pytest.raises(adapter.PackError, match="撞"):
        adapter.pack(box)


def test_快照两份不一致报人话(tmp_path):
    box = make_raw(tmp_path)
    _shard(box / "raw" / "A_1" / "replay", "social_media_agent_state", "dd",
           [{"agent_id": 1, "step": 1, "t": "2026-09-01T09:30:00",
             "followers_count": 99, "following_count": 0, "posts_count": 2}])
    with pytest.raises(adapter.PackError, match="不一致"):
        adapter.pack(box)


def test_中途失败不留半张表(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    before = (box / "states.csv").read_bytes()
    shard = next((box / "raw" / "B_1" / "replay").glob("social_media_event.*.jsonl"))
    shard.write_text("{坏\n", encoding="utf-8")
    with pytest.raises(adapter.PackError):
        adapter.pack(box)
    assert (box / "states.csv").read_bytes() == before
    assert not (box / "states.csv.tmp").exists()


def test_缺runs清单报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").unlink()
    with pytest.raises(adapter.PackError, match="缺 runs.csv"):
        adapter.pack(box)


def test_runs列不齐报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").write_text("file,rep\nraw/A_1,1\n", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="列不齐"):
        adapter.pack(box)


def test_空清单拒绝装箱且不动旧表(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    before = (box / "states.csv").read_bytes()
    (box / "runs.csv").write_text("file,condition,rep,complete\n", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="一条跑都没有"):
        adapter.pack(box)
    assert (box / "states.csv").read_bytes() == before


def test_缺配料单报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "sim.meta.json").unlink()
    with pytest.raises(adapter.PackError, match="缺 sim.meta.json"):
        adapter.pack(box)


def test_配料单坏JSON报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "sim.meta.json").write_text("{坏", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="不是合法 JSON"):
        adapter.pack(box)


def test_配料单顶层不是对象报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "sim.meta.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="对象"):
        adapter.pack(box)


# ── 注册表


def test_注册表认识agentsociety():
    assert hasattr(sims.get_adapter("agentsociety"), "pack")
    with pytest.raises(ValueError, match="agentsociety"):
        sims.get_adapter("netlogo")   # 报错点名现有插头，新插头也要在名单里
