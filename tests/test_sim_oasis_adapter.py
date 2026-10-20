"""OASIS 插头：SQLite trace + 逐轮行号日志 + runs.csv → 货箱两张表。

fixture 照 0.2.5 真库现场造微型库：trace(user_id, created_at, action, info)
+ follow(follow_id, follower_id, followee_id, created_at)，每种动作各一行、
键名与真库 shakedown（2026-08-31）逐一核对过。上游改 schema 要连 V0 记录一起改。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from oss import sims
from oss.sims.oasis import adapter

#: 仓库根——tests/ 的上一级；跑单模板测试要从任意 CWD 都能定位到它（同
#: test_byo_idea_gates.py 的 REPO 惯例），不能靠相对路径 "oss/..." 蒙混过关。
REPO = Path(__file__).resolve().parents[1]

Q = {"prompt": "立场1-7", "interview_id": "x"}


def make_oasis_db(path: Path, *, interview_answer="5", zero_target=False) -> None:
    """写一个微型 OASIS 库 + 配套 .steps.jsonl。每种真实动作各一行。

    comment/post 两张表的编号是特意设的撞号陷阱：trace 里 create_comment 的
    comment_id=2，comment 表里 comment_id=2 真正对应的是 post_id=1（评论的是
    种子帖）；但库里另外存在一条真实的 post_id=2，跟这条评论毫无关系。
    如果插头退回「直接把 comment_id 当 target」的老 bug，算出来的 target 是
    "2"——而 "2" 恰好是一个存在的帖子 id，看起来完全正常，不会被任何
    「target 是不是一个合理数字」式的检查抓到。只有真的按 comment 表联出
    post_id=1 才是对的——fixture 必须能抓住这个 bug，不能只是凑巧躲过去。
    """
    rows = [
        (0, "interview", Q | {"response": interview_answer}),
        (1, "interview", Q | {"response": "3"}),
        (0, "create_post", {"content": "c", "post_id": 1}),
        (1, "like_post", {"like_id": 1, "post_id": 1}),
        (1, "follow", {"follow_id": 1}),
        (1, "repost", {"reposted_id": 1, "new_post_id": 5}),
        (0, "quote_post", {"quoted_id": 1, "new_post_id": 6}),
        (1, "create_comment", {"comment_id": 2, "content": "x"}),
    ]
    if zero_target:
        rows.append((1, "like_post", {"like_id": 9, "post_id": 0}))
    rows += [
        (0, "refresh", {}),
        (0, "interview", Q | {"response": "6"}),
        (1, "interview", Q | {"response": "还是3吧"}),
    ]
    path.unlink(missing_ok=True)  # FIX(harness): allow re-creating on an existing path (e.g. zero_target override)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trace (user_id INTEGER, created_at DATETIME, "
                "action TEXT, info TEXT)")
    con.execute("CREATE TABLE follow (follow_id INTEGER PRIMARY KEY, "
                "follower_id INTEGER, followee_id INTEGER, created_at DATETIME)")
    con.execute("INSERT INTO follow VALUES (1, 1, 0, '2026-08-31 11:00:05')")
    con.execute("CREATE TABLE comment (comment_id INTEGER PRIMARY KEY, "
                "post_id INTEGER, user_id INTEGER, created_at DATETIME)")
    con.execute("INSERT INTO comment VALUES (2, 1, 1, '2026-08-31 11:00:08')")
    con.execute("CREATE TABLE post (post_id INTEGER PRIMARY KEY, "
                "user_id INTEGER, created_at DATETIME)")
    con.execute("INSERT INTO post VALUES (1, 0, '2026-08-31 11:00:03')")
    con.execute("INSERT INTO post VALUES (2, 1, '2026-08-31 11:00:04')")  # 撞号陷阱：跟评论无关的另一篇帖
    for i, (uid, action, info) in enumerate(rows, 1):
        con.execute("INSERT INTO trace VALUES (?,?,?,?)",
                    (uid, f"2026-08-31 11:00:{i:02d}", action,
                     json.dumps(info, ensure_ascii=False)))
    con.commit(); con.close()
    total = len(rows)
    path.with_name(path.stem + ".steps.jsonl").write_text("\n".join([
        json.dumps({"kind": "interview", "wave": 0, "max_rowid": 2}),
        json.dumps({"kind": "round", "round": 1, "max_rowid": total - 2}),
        json.dumps({"kind": "interview", "wave": 1, "max_rowid": total}),
    ]) + "\n", encoding="utf-8")


def make_raw(tmp_path: Path) -> Path:
    box = tmp_path / "box"
    (box / "raw").mkdir(parents=True)
    for c in ("A", "B"):
        make_oasis_db(box / "raw" / f"{c}_1.db")
    (box / "runs.csv").write_text(
        "file,condition,rep,complete\nraw/A_1.db,A,1,yes\nraw/B_1.db,B,1,yes\n",
        encoding="utf-8")
    (box / "sim.meta.json").write_text(json.dumps(
        {"simulator": "oasis", "n_reps": 1}, ensure_ascii=False), encoding="utf-8")
    (box / "runner.py").write_text("# 占位\n", encoding="utf-8")
    return box


def events_of(box: Path, condition: str) -> list[list[str]]:
    lines = (box / "events.csv").read_text(encoding="utf-8").splitlines()
    return [x.split(",") for x in lines[1:] if x.startswith(condition + ",")]


# ── 正常翻译


def test_pack产出两张表且钥匙列齐(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    states = (box / "states.csv").read_text(encoding="utf-8").splitlines()
    assert states[0] == "condition,rep,wave,agent_id,stance"
    assert "A,1,0,0,5" in states and "B,1,1,0,6" in states
    events = (box / "events.csv").read_text(encoding="utf-8").splitlines()
    assert events[0] == "condition,rep,step,seq,sim_time,agent_id,action,target,target_kind"
    # create_post 自己 info 里的 post_id 是新产物不是 target——target/target_kind 都空
    assert "A,1,1,3,2026-08-31 11:00:03,0,create_post,," in events
    assert report["n_state_rows"] == 8 and report["n_event_rows"] == 12


def test_真实键名的target全解析且follow换成被关注者(tmp_path):
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    rows = events_of(box, "A")
    targets = {row[6]: row[7] for row in rows}
    kinds = {row[6]: row[8] for row in rows}
    assert targets["follow"] == "0"        # follow_id=1 → followee_id=0，不是记录号
    assert kinds["follow"] == "user"
    assert targets["repost"] == "1" and targets["quote_post"] == "1"
    assert kinds["repost"] == "post" and kinds["quote_post"] == "post"
    assert targets["like_post"] == "1" and kinds["like_post"] == "post"
    # comment_id=2 联 comment 表换成 post_id=1——不是 comment 自己的编号 2
    # （comment 表里另存在一条撞号陷阱：post_id=2 存在但跟这条评论无关）
    assert targets["create_comment"] == "1" and kinds["create_comment"] == "post"
    # create_post 没有「指着谁」这回事：target 与 kind 都空，且不计入 n_blank_targets
    assert targets["create_post"] == "" and kinds["create_post"] == ""
    assert report["n_blank_targets"] == 0 and report["n_unplaced"] == 0


def test_id为0的target不被or链吞掉(tmp_path):
    box = make_raw(tmp_path)
    make_oasis_db(box / "raw" / "A_1.db", zero_target=True)
    report = adapter.pack(box)
    zero_rows = [r for r in events_of(box, "A") if r[7] == "0" and r[6] == "like_post"]
    assert zero_rows and zero_rows[0][8] == "post"   # target=0 是合法值，kind 不该跟着变空
    assert report["n_blank_targets"] == 0


def test_解析不了的访谈答案标缺失不猜(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    states = (box / "states.csv").read_text(encoding="utf-8")
    assert "B,1,1,1,\n" in states or "B,1,1,1,\r\n" in states  # 「还是3吧」→ 空


def test_配料单后半被补上(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    filled = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))["packed"]
    assert filled["n_state_rows"] == 8 and filled["n_event_rows"] == 12
    assert filled["n_blank_targets"] == 0 and filled["n_unplaced"] == 0
    assert len(filled["file_sha256"]) == 2
    assert all(len(h) == 64 for h in filled["file_sha256"].values())


def test_重复装箱幂等(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    first = (box / "events.csv").read_bytes()
    adapter.pack(box)
    assert (box / "events.csv").read_bytes() == first


def test_BOM的runs也能读(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").write_bytes(b"\xef\xbb\xbf" + (box / "runs.csv").read_bytes())
    assert adapter.pack(box)["n_event_rows"] == 12


# ── run_summary.json 拾取（可选，F5）


def test_runsummary存在时并入packed(tmp_path):
    """两场跑各落一份 run_summary.json——packed 里的两个数是跨场求和，不是最后一场覆盖前一场；
    n_runs_with_summary 记两场都供了这份文件，供部分采用时辨认完整度。"""
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.run_summary.json").write_text(json.dumps(
        {"model_errors": 2, "interview_miss": 1, "advice": "无需处理"}), encoding="utf-8")
    (box / "raw" / "B_1.run_summary.json").write_text(json.dumps(
        {"model_errors": 3, "interview_miss": 0, "advice": "无需处理"}), encoding="utf-8")
    report = adapter.pack(box)
    assert report["model_errors"] == 5 and report["interview_miss"] == 1
    assert report["n_runs_with_summary"] == 2
    filled = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))["packed"]
    assert filled["model_errors"] == 5 and filled["interview_miss"] == 1
    assert filled["n_runs_with_summary"] == 2


def test_部分场次有runsummary时n_runs_with_summary露出不完整(tmp_path):
    """只有一场（A）落了 run_summary.json，另一场（B）没有——两个数只是那一场的，
    n_runs_with_summary=1 让读者一眼看出这不是全箱汇总，不用去反查每场目录。"""
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.run_summary.json").write_text(json.dumps(
        {"model_errors": 4, "interview_miss": 2, "advice": "无需处理"}), encoding="utf-8")
    report = adapter.pack(box)
    assert report["model_errors"] == 4 and report["interview_miss"] == 2
    assert report["n_runs_with_summary"] == 1   # 两场里只有一场，不是 2


def test_无runsummary照常装箱(tmp_path):
    """老箱子没有 run_summary.json——packed 不该凭空长出这三个键（字节稳定，不是补 0）。"""
    box = make_raw(tmp_path)
    report = adapter.pack(box)
    assert "model_errors" not in report and "interview_miss" not in report
    assert "n_runs_with_summary" not in report
    filled = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))["packed"]
    assert "model_errors" not in filled and "interview_miss" not in filled
    assert "n_runs_with_summary" not in filled


# ── 计数器不能被写死为 0（防无人知晓）


def test_孤儿follow_id让n_blank_targets等于1(tmp_path):
    """follow 表里没有对应记录：target 解析不出来，得算进 n_blank_targets，不能悄悄写 0。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("UPDATE trace SET info = ? WHERE action = 'follow'",
                (json.dumps({"follow_id": 999}),))  # follow 表里没有 999
    con.commit(); con.close()
    report = adapter.pack(box)
    assert report["n_blank_targets"] == 1


def test_孤儿comment_id让n_blank_targets等于1(tmp_path):
    """comment 表里没有对应记录：target 解析不出来，得算进 n_blank_targets，不能悄悄写 0。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("UPDATE trace SET info = ? WHERE action = 'create_comment'",
                (json.dumps({"comment_id": 999}),))  # comment 表里没有 999
    con.commit(); con.close()
    report = adapter.pack(box)
    assert report["n_blank_targets"] == 1


def test_create_post与do_nothing的空target不计入n_blank_targets(tmp_path):
    """空 target 不是丢——这两个动作压根没有「指着谁」这回事，别把它们算成事故。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("INSERT INTO trace VALUES (0, '2026-08-31 11:00:13', 'do_nothing', '{}')")
    con.commit(); con.close()
    report = adapter.pack(box)
    # 两个文件（A、B）各一条 create_post，A 又手工加了一条 do_nothing——
    # 三条 target 全空，n_blank_targets 一个都不该数进去
    assert report["n_blank_targets"] == 0


def test_截短的steps日志让n_unplaced大于0(tmp_path):
    """steps.jsonl 少记最后一段：段外的行定不了位，得算进 n_unplaced，不能悄悄写 0。"""
    box = make_raw(tmp_path)
    steps_path = box / "raw" / "A_1.steps.jsonl"
    lines = steps_path.read_text(encoding="utf-8").splitlines()
    steps_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # 掐掉最后一段
    report = adapter.pack(box)
    assert report["n_unplaced"] > 0


def test_没有follow动作时不联follow表(tmp_path):
    """这次跑压根没有 follow 动作——不该为一张用不上的表报错。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("DELETE FROM follow")
    con.execute("DELETE FROM trace WHERE action = 'follow'")
    con.commit(); con.close()
    report = adapter.pack(box)   # 不该抛 PackError
    assert report["n_event_rows"] > 0


def test_有follow动作但缺follow表单独报错(tmp_path):
    """这次跑真有 follow 动作，但库里没有 follow 表——该报「缺 follow 表」而不是「读不进」。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("DROP TABLE follow")
    con.commit(); con.close()
    with pytest.raises(adapter.PackError, match="缺 follow 表"):
        adapter.pack(box)


def test_没有create_comment动作时不联comment表(tmp_path):
    """这次跑压根没有 create_comment 动作——不该为一张用不上的表报错。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("DROP TABLE comment")
    con.execute("DELETE FROM trace WHERE action = 'create_comment'")
    con.commit(); con.close()
    report = adapter.pack(box)   # 不该抛 PackError
    assert report["n_event_rows"] > 0


def test_有create_comment动作但缺comment表单独报错(tmp_path):
    """这次跑真有 create_comment 动作，但库里没有 comment 表——单独报错，不混同「读不进」。"""
    box = make_raw(tmp_path)
    con = sqlite3.connect(box / "raw" / "A_1.db")
    con.execute("DROP TABLE comment")
    con.commit(); con.close()
    with pytest.raises(adapter.PackError, match="缺 comment 表"):
        adapter.pack(box)


# ── 拒绝而不是猜


def test_行号日志缺失时拒绝而不是瞎猜轮次(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").unlink()
    with pytest.raises(adapter.PackError, match="steps.jsonl"):
        adapter.pack(box)


def test_老格式日志缺max_rowid拒绝并指路(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").write_text(
        json.dumps({"event": "round_start", "round": 1}) + "\n", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="max_rowid"):
        adapter.pack(box)


def test_max_rowid是布尔值也拒绝(tmp_path):
    """bool 是 int 的子类，跟 simbox 的 n_reps 同一把尺子——不能蒙混过关。"""
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").write_text(
        json.dumps({"kind": "round", "round": 1, "max_rowid": True}) + "\n",
        encoding="utf-8")
    with pytest.raises(adapter.PackError, match="max_rowid"):
        adapter.pack(box)


def test_空日志拒绝(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="空"):
        adapter.pack(box)


def test_日志坏JSON拒绝(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.steps.jsonl").write_text("{坏\n", encoding="utf-8")
    with pytest.raises(adapter.PackError, match="不是合法 JSON"):
        adapter.pack(box)


def test_缺库文件报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.db").unlink()
    with pytest.raises(adapter.PackError, match="不在箱子里"):
        adapter.pack(box)


def test_不是sqlite的库报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.db").write_bytes(b"junk not a database")
    with pytest.raises(adapter.PackError, match="读不进"):
        adapter.pack(box)


def test_缺trace表报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "raw" / "A_1.db").unlink()
    sqlite3.connect(box / "raw" / "A_1.db").close()   # 合法但空的库
    with pytest.raises(adapter.PackError, match="读不进"):
        adapter.pack(box)


def test_中途失败不留半张表(tmp_path):
    box = make_raw(tmp_path)
    adapter.pack(box)
    before = (box / "states.csv").read_bytes()
    (box / "raw" / "B_1.db").write_bytes(b"junk")
    with pytest.raises(adapter.PackError):
        adapter.pack(box)
    assert (box / "states.csv").read_bytes() == before   # 旧表原样，无半成品
    assert not (box / "states.csv.tmp").exists()


def test_缺runs清单报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").unlink()
    with pytest.raises(adapter.PackError, match="缺 runs.csv"):
        adapter.pack(box)


def test_runs列不齐报PackError(tmp_path):
    box = make_raw(tmp_path)
    (box / "runs.csv").write_text("file,rep\nraw/A_1.db,1\n", encoding="utf-8")
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


# ── 注册表与定位


def test_注册表认识oasis不认识别人():
    assert hasattr(sims.get_adapter("oasis"), "pack")
    with pytest.raises(ValueError, match="oasis"):
        sims.get_adapter("netlogo")


def test_locate边界含上界不含下界():
    steps = [{"kind": "interview", "wave": 0, "max_rowid": 2},
             {"kind": "round", "round": 1, "max_rowid": 9},
             {"kind": "interview", "wave": 1, "max_rowid": 11}]
    assert adapter._locate(2, steps, "wave") == "0"
    assert adapter._locate(3, steps, "round") == "1"
    assert adapter._locate(9, steps, "round") == "1"
    assert adapter._locate(10, steps, "wave") == "1"
    assert adapter._locate(3, steps, "wave") == ""     # 行动段没有 wave 键
    assert adapter._locate(99, steps, "round") == ""   # 越界不猜


# ── 跑单模板


def test_runner模板语法合法且逐轮记行号():
    import ast
    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "max_rowid" in src and "steps.jsonl" in src      # V0 发现 6 的落地
    assert "SELECT MAX(rowid) FROM trace" in src
    assert "{{CONDITION}}" in src and "{{RECSYS}}" in src   # 待填空位


def test_runner模板内置访谈兜底与重试标记(): # F2：兜底+重试进本体
    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    assert "[interview-miss]" in src and "perform_interview" in src
    # 按类名匹配，不直接 import openai
    assert "APIConnectionError" in src and "APITimeoutError" in src
    assert "\nimport openai" not in src and "\nfrom openai" not in src
    # 退避序列 1s/2s/4s，重试最多 3 次
    assert "(1, 2, 4)" in src
    assert "asyncio.sleep" in src
    # 2026-08-31 质量审：miss 行光有类名分不出「哪条中转站错误」（402/欠费类
    # 消息常常变成通用类名），必须带上异常文字前 200 字——判别③（跑单说明书
    # 里读 miss 行文字判 402）的传感器由这行格式落地
    assert "str(exc)[:200]" in src


def test_runner模板SEMAPHORE第八槽缺省逻辑与run_summary标记(): # F3/F5
    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    assert '"{{SEMAPHORE}}"' in src   # 第八个可选空位
    assert "SEMAPHORE_RAW.startswith(\"{{\")" in src
    assert "8" in src and "32" in src   # 缺省 8，注释交代压垮中转站的旧值 32
    assert "run_summary" in src and ".run_summary.json" in src
    assert "model_errors" in src and "interview_miss" in src
    assert "advice" in src


def test_runner模板哨兵不抛异常():
    """AST 抽取真正跑的那段 `_safe_interview` 函数体，在受控命名空间里驱动它——
    不 import oasis（顶层还联网/背 camel 依赖）。2026-08-31 质量审的两个场景都要
    钉住：被包裹的原函数本身一直抛连接类异常、重试到头仍败；以及兜底自己那行
    print 也抛（BrokenPipeError 经 gather 会复杀整波，实测过）。两种情况
    `_safe_interview` 都必须**返回哨兵字典，不能把异常甩出去**。
    """
    import ast
    import asyncio

    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_safe_interview")
    func_src = ast.get_source_segment(src, func_node)

    class _FakeAgent:
        social_agent_id = 7

    class APIConnectionError(Exception):   # 按类名匹配，桩类不用真 import openai
        pass

    def _drive(orig_interview, print_fn):
        ns = {
            "asyncio": asyncio,
            "_orig_interview": orig_interview,
            "_RETRYABLE_EXC_NAMES": {"APIConnectionError", "APITimeoutError"},
            "_RETRY_BACKOFF": (0, 0, 0),   # 逻辑跟真值一样，只是测试不真的睡
            "_interview_miss_count": 0,
            "print": print_fn,
        }
        exec(func_src, ns)   # 定义的是模板里那段真代码，不是测试自己另写的一份
        result = asyncio.run(ns["_safe_interview"](_FakeAgent(), "prompt"))
        return result, ns["_interview_miss_count"]

    # 场景一：连接类异常重试到头仍败——不该把异常甩给调用方（旧 gather 会因此杀全场）
    async def _always_conn_error(self, prompt):
        raise APIConnectionError("模拟连接异常，测试桩")

    result1, miss1 = _drive(_always_conn_error, print)
    assert result1 == {"interview_missed": True}
    assert miss1 == 1

    # 场景二：非连接类异常不重试即败，但兜底自己那行 print 也抛（模拟 stdout 断管）
    def _broken_pipe_print(*a, **k):
        raise BrokenPipeError("模拟 stdout 断管，测试桩")

    async def _fails_once(self, prompt):
        raise ValueError("非连接类，第一次就算数")

    result2, miss2 = _drive(_fails_once, _broken_pipe_print)
    assert result2 == {"interview_missed": True}   # print 炸了也不该往外传
    assert miss2 == 1   # 计数先加、打印后包，打印失败不影响计数已经加过


def test_runner模板advice措辞锚句(): # 2026-08-31 质量审：弃「错误占比」框架
    """七槽盲替换之后，advice 的诚实叫法（不是比率）仍要在——防止以后有人
    图省事把「N 条错误日志（含重试，非失败请求数）」改回「错误占比 X%」。
    """
    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    fill = {
        "{{CONDITION}}": "A", "{{REP}}": "1", "{{RECSYS}}": "random",
        "{{ROUNDS}}": "10", "{{N_AGENTS}}": "46",
        "{{INTERVIEW_Q}}": "你在多大程度上同意该说法？请只回答 1 到 7 的一个数字。",
        "{{INTERVIEW_WAVES}}": "5,10",
    }
    filled = src
    for k, v in fill.items():
        filled = filled.replace(k, v)
    assert "含重试，非失败请求数" in filled
    assert "启发式指标，非失败率" in filled
    # 旧框架真正输出的字面量是「错误占比约 N%」——查这个具体短语而不是查
    # 「错误占比」这四个字：上面那行解释性注释里合法提了「错误占比」这个
    # 概念名（说明为什么弃用），不该被这条测试误伤，同「{{...}}」幸存者一个道理
    assert "错误占比约" not in filled


def test_盲替换七槽位后语法合法且解析正确():
    """把「另存并填空位」的盲替换钉成永久测试（T7 终验遗留项 1）。

    只用字符串替换 + ast 静态解析七个赋值槽位，**不 import 模板**——顶层还有
    camel/oasis 的 import 与联网执行代码，import 会连网、会因缺依赖而炸。
    ROUNDS / INTERVIEW_WAVES 的转换表达式直接从填值后的源码里用
    `ast.get_source_segment` 抠出来 eval，不手抄逻辑——测的是模板真跑时
    算的那段代码，不是测试自己写的另一份逻辑。
    """
    import ast

    src = (REPO / "oss/sims/oasis/runner_template.py").read_text(encoding="utf-8")
    fill = {
        "{{CONDITION}}": "A",
        "{{REP}}": "1",
        "{{RECSYS}}": "random",
        "{{ROUNDS}}": "10",
        "{{N_AGENTS}}": "46",
        "{{INTERVIEW_Q}}": "你在多大程度上同意该说法？请只回答 1 到 7 的一个数字。",
        "{{INTERVIEW_WAVES}}": "5,10",
    }
    filled = src
    for k, v in fill.items():
        filled = filled.replace(k, v)

    tree = ast.parse(filled)   # 盲替换后仍必须是合法 Python——引号错位会在这里炸

    assigns = {
        node.targets[0].id: node
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    raw = {}
    for name in ("COND", "REP", "RECSYS", "ROUNDS_RAW", "N_AGENTS_RAW",
                 "INTERVIEW_Q", "INTERVIEW_WAVES_RAW"):
        seg = ast.get_source_segment(filled, assigns[name].value)
        raw[name] = ast.literal_eval(seg)

    assert raw["COND"] == "A" and raw["REP"] == "1" and raw["RECSYS"] == "random"
    assert raw["ROUNDS_RAW"] == "10" and raw["N_AGENTS_RAW"] == "46"
    assert raw["INTERVIEW_WAVES_RAW"] == "5,10"
    assert raw["INTERVIEW_Q"].startswith("你在多大程度上")

    rounds_expr = ast.get_source_segment(filled, assigns["ROUNDS"].value)
    waves_expr = ast.get_source_segment(filled, assigns["INTERVIEW_WAVES"].value)
    rounds_val = eval(rounds_expr, {}, {"ROUNDS_RAW": raw["ROUNDS_RAW"]})
    waves_val = eval(waves_expr, {}, {"INTERVIEW_WAVES_RAW": raw["INTERVIEW_WAVES_RAW"]})

    assert isinstance(rounds_val, int) and rounds_val == 10
    assert waves_val == [5, 10]

    # 第八槽 SEMAPHORE 是可选的：七个必填槽盲替换后没碰它，仍是占位符——
    # 缺省逻辑必须评出 8（V1 真跑验证实测值，见模板注释）。表达式跨两行写，
    # get_source_segment 抠出来的片段不带外层括号，eval 前补一对括号才能当
    # 单个表达式跑（跟 ROUNDS/INTERVIEW_WAVES 单行表达式不同，不是漏加括号）。
    semaphore_raw_seg = ast.get_source_segment(filled, assigns["SEMAPHORE_RAW"].value)
    semaphore_raw = ast.literal_eval(semaphore_raw_seg)
    assert semaphore_raw == "{{SEMAPHORE}}"   # 七槽盲替换不该碰到第八槽
    semaphore_expr = ast.get_source_segment(filled, assigns["SEMAPHORE"].value)
    semaphore_val = eval(f"({semaphore_expr})", {}, {"SEMAPHORE_RAW": semaphore_raw})
    assert semaphore_val == 8

    # 第八槽也填了的话（如整理稿要求更高并发）：换算走 int()，不是缺省 8
    filled16 = filled.replace("{{SEMAPHORE}}", "16")
    tree16 = ast.parse(filled16)
    assigns16 = {
        node.targets[0].id: node
        for node in tree16.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    raw16 = ast.literal_eval(ast.get_source_segment(filled16, assigns16["SEMAPHORE_RAW"].value))
    assert raw16 == "16"
    expr16 = ast.get_source_segment(filled16, assigns16["SEMAPHORE"].value)
    val16 = eval(f"({expr16})", {}, {"SEMAPHORE_RAW": raw16})
    assert val16 == 16

    # 两处合法幸存者，不是「全文件不许剩 {{」：docstring 第 3 行的泛指例子、
    # val.startswith 守卫本身的字面量——这两处盲替换不该也不会碰
    assert "八个 `{{...}}` 花括号空位" in filled
    assert 'val.startswith("{{")' in filled
