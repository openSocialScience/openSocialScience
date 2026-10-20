"""OASIS 插头：读 `.db` 的 trace 表 + 逐轮行号日志，翻译成货箱两张表。

**只读输出文件，不 import oasis**——OASIS 改接口伤不到这里，
只有它改数据库表结构才要动（档案锁 0.2.5；V0 与真库 shakedown 各验证过一次）。

五步翻译（V0 findings 3/6 的落地）：
1. user_id → agent_id；2. 轮次只按 steps.jsonl 的 max_rowid 区间划（不猜时间戳——
库内时间是加速 60 倍的模拟时间）；3. info JSON 拆 target；4. 访谈行进状态表，
答案只认 1-7 单数字，解析不了标缺失**不猜**；5. condition/rep 从 runs.csv 补。

跑单模板 F5 起另可选落一份 `<db 名>.run_summary.json`（模型错误数、访谈缺失数）
在 db 旁——见到就把两个数汇总进配料单 `packed`（`model_errors`/`interview_miss`），
不是这五步翻译之一，是额外的拾取；缺这份文件不报错，不加这两个键（老箱子没有）。

target 的语义（0.2.5 真库实测的键名；44% 丢失事故后钉死，终审又抓出一版误判）：
帖上直给的动作 = 被作用的帖 id（`post_id` 用于 like_post 等；`reposted_id` /
`quoted_id` 分别用于 repost / quote_post）；`create_post` 自己 info 里的
`post_id` 是新产物不是 target，跟 `new_post_id` 一个道理。`create_comment`
的 `comment_id` 也是新产物——它评论的是哪条帖要**联 comment 表换成 post_id**
（同 follow 联 followee_id 一个道理）。follow 的 trace 只记 follow_id
（关注记录的编号），**要联 follow 表换成被关注者的 followee_id**。
`create_post` / `do_nothing` 两个动作本来就没有「指着谁」这回事，target 为空是
设计如此，**不算进 n_blank_targets**——这个计数器现在只数「本该有 target
却解不出来」的行：丢失可以，沉默不行，但没有 target 这回事不该也被算成丢失。

`post_id` 与 `user_id` 是两条各自 autoincrement 的编号，数值上会撞号（同一个数字
既可能是某条帖也可能是某个人）——events.csv 第九列 `target_kind`
（`"post"` / `"user"` / `""`）标出这一列数字到底是哪条命名空间的；
target 为空时 target_kind 也为空。

一切读不进、对不上的输入抛 PackError：宁可停下也不出一张猜出来的表。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

#: 不进事件表的动作：注册与系统刷新不是行为；访谈单独进状态表
_SKIP = {"interview", "sign_up", "refresh"}

#: info JSON 里按顺序找 target 的键（直给帖 id 的动作）。用 in 判断，不用 or 链——
#: id 可以是 0。comment_id 不在这里：那是 create_comment 自己新产物的编号，不是
#: target，target 要联 comment 表换成 post_id（见下面 pack() 里的联表逻辑）
_TARGET_KEYS = ("post_id", "reposted_id", "quoted_id")

#: 这两个动作本来就没有「指着谁」这回事：create_post 创的是新帖，
#: do_nothing 是空转。它们的 target 恒为空，且**不计入** n_blank_targets——
#: 空不是丢，是这个动作压根没有 target 这个概念
_NO_TARGET_BY_DESIGN = {"create_post", "do_nothing"}


class PackError(Exception):
    """装箱失败：缺日志、日志不合格、库打不开、schema 不对、缺文件。"""


def _steps(path: Path) -> list[dict]:
    if not path.exists():
        raise PackError(f"缺逐轮行号日志 {path.name}——没有它「第几轮」只能靠猜，拒绝猜")
    entries = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError as exc:
            raise PackError(f"{path.name} 第 {i} 行不是合法 JSON：{exc}") from exc
        mr = e.get("max_rowid")
        if not isinstance(mr, int) or isinstance(mr, bool):  # bool 是 int 的子类，同 simbox 的 n_reps 一把尺子
            raise PackError(
                f"{path.name} 第 {i} 行缺整数 max_rowid——V0 老格式的跑日志没有"
                f"这个字段，用新跑单模板重跑，或对着库补记")
        entries.append(e)
    if not entries:
        raise PackError(f"{path.name} 是空的——没有轮次区间就只能猜，拒绝猜")
    return entries


def _run_summary(path: Path) -> dict | None:
    """读跑单模板可选落的 `<db 名>.run_summary.json`（F5：模型错误数/访谈缺失数）。

    不存在返回 None——老箱子（V1 模板跑出来的、或没走本模板的其他跑法）没有
    这份文件是正常情况，不报错。存在但读不进/对不上就跟别的输入一个待遇：
    拒收，不悄悄跳过（模块开头「宁可停下也不出一张猜出来的表」那条老规矩）。
    """
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PackError(f"{path.name} 不是合法 JSON：{exc}") from exc
    if not isinstance(d, dict):
        raise PackError(f"{path.name} 顶层必须是一个对象 {{…}}，不是列表或字符串")
    out: dict = {}
    for key in ("model_errors", "interview_miss"):
        v = d.get(key, 0)
        if not isinstance(v, int) or isinstance(v, bool):  # bool 是 int 的子类，同 max_rowid 一把尺子
            raise PackError(f"{path.name} 的 {key} 必须是整数")
        out[key] = v
    return out


def _locate(rowid: int, steps: list[dict], key: str) -> str:
    """rowid 落在哪一段：返回该段的 wave（访谈）或 round（行动）编号，找不到给空。"""
    prev = 0
    for s in steps:
        if prev < rowid <= s["max_rowid"]:
            return str(s.get(key, "")) if key in s else ""
        prev = s["max_rowid"]
    return ""


def pack(box: Path | str) -> dict:
    box = Path(box)
    runs_path = box / "runs.csv"
    if not runs_path.exists():
        raise PackError(f"缺 runs.csv：{box}")
    with runs_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        runs = list(reader)
    missing = [k for k in ("file", "condition", "rep") if k not in header]
    if missing:
        raise PackError(f"runs.csv 列不齐，缺 {'、'.join(missing)}")
    if not runs:
        raise PackError("runs.csv 里一条跑都没有——空清单不装箱（与验货同一条规矩）")
    meta_path = box / "sim.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackError(f"缺 sim.meta.json：{box}") from exc
    except ValueError as exc:
        raise PackError(f"sim.meta.json 不是合法 JSON：{exc}") from exc
    if not isinstance(meta, dict):
        raise PackError("sim.meta.json 顶层必须是一个对象 {…}，不是列表或字符串")
    n_states = n_events = n_blank_targets = n_unplaced = 0
    n_model_errors = n_interview_miss = n_runs_with_summary = 0
    shas: dict[str, str] = {}
    tmp_states, tmp_events = box / "states.csv.tmp", box / "events.csv.tmp"
    try:
        with tmp_states.open("w", encoding="utf-8", newline="") as sf, \
             tmp_events.open("w", encoding="utf-8", newline="") as ef:
            sw, ew = csv.writer(sf), csv.writer(ef)
            sw.writerow(["condition", "rep", "wave", "agent_id", "stance"])
            ew.writerow(["condition", "rep", "step", "seq", "sim_time",
                         "agent_id", "action", "target", "target_kind"])
            for r in runs:
                db = box / r["file"]
                if not db.exists():
                    raise PackError(f"runs.csv 里的 {r['file']} 不在箱子里")
                shas[r["file"]] = hashlib.sha256(db.read_bytes()).hexdigest()
                steps = _steps(db.with_name(db.stem + ".steps.jsonl"))
                rs = _run_summary(db.with_name(db.stem + ".run_summary.json"))
                if rs is not None:
                    n_runs_with_summary += 1
                    n_model_errors += rs["model_errors"]
                    n_interview_miss += rs["interview_miss"]
                try:
                    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                    rows = con.execute(
                        "SELECT rowid, user_id, created_at, action, info "
                        "FROM trace ORDER BY rowid").fetchall()
                except sqlite3.Error as exc:
                    raise PackError(f"{r['file']} 读不进：{exc}") from exc
                followee: dict = {}
                if any(row[3] == "follow" for row in rows):
                    # follow 表只在这次跑真有 follow 动作时才联——没有的话不该
                    # 为一张用不上的表报错，报了也该是「缺 follow 表」而不是「读不进」
                    try:
                        followee = dict(con.execute(
                            "SELECT follow_id, followee_id FROM follow").fetchall())
                    except sqlite3.Error as exc:
                        con.close()
                        raise PackError(
                            f"{r['file']} 有 follow 动作但缺 follow 表：{exc}") from exc
                comment_post: dict = {}
                if any(row[3] == "create_comment" for row in rows):
                    # 同 follow 一个道理：comment 表只在真有 create_comment 动作时才联
                    try:
                        comment_post = dict(con.execute(
                            "SELECT comment_id, post_id FROM comment").fetchall())
                    except sqlite3.Error as exc:
                        con.close()
                        raise PackError(
                            f"{r['file']} 有 create_comment 动作但缺 comment 表：{exc}"
                        ) from exc
                con.close()
                for rowid, uid, ts, action, info in rows:
                    d = json.loads(info) if info and info.startswith("{") else {}
                    if action == "interview":
                        m = re.fullmatch(r"\s*([1-7])\s*", str(d.get("response", "")))
                        wave = _locate(rowid, steps, "wave")
                        if not wave:
                            n_unplaced += 1
                        sw.writerow([r["condition"], r["rep"], wave, uid,
                                     m.group(1) if m else ""])
                        n_states += 1
                    elif action not in _SKIP:
                        if action == "follow":
                            target = followee.get(d.get("follow_id"), "")
                            kind = "user"
                        elif action == "create_comment":
                            target = comment_post.get(d.get("comment_id"), "")
                            kind = "post"
                        elif action in _NO_TARGET_BY_DESIGN:
                            target, kind = "", ""
                        else:
                            target = next((d[k] for k in _TARGET_KEYS if k in d), "")
                            kind = "post"
                        if target == "":
                            kind = ""
                            if action not in _NO_TARGET_BY_DESIGN:
                                n_blank_targets += 1
                        step = _locate(rowid, steps, "round")
                        if not step:
                            n_unplaced += 1
                        ew.writerow([r["condition"], r["rep"], step, rowid, ts,
                                     uid, action, target, kind])
                        n_events += 1
    except Exception:
        tmp_states.unlink(missing_ok=True)
        tmp_events.unlink(missing_ok=True)
        raise
    tmp_states.replace(box / "states.csv")
    tmp_events.replace(box / "events.csv")
    meta["packed"] = {"n_state_rows": n_states, "n_event_rows": n_events,
                      "n_blank_targets": n_blank_targets,
                      "n_unplaced": n_unplaced, "file_sha256": shas}
    if n_runs_with_summary:
        # 至少一场跑落了 run_summary.json 才加这三个键——老箱子（没有任何一场
        # 落这份可选文件）的 packed 保持字节不变，不无中生有几个 0。
        # n_runs_with_summary 单独留痕「这两个数是几场跑凑出来的」——部分采用
        # （只有一部分跑用了新模板）时，读者能看出这是不完整的合计而不是全箱
        # 总数，不用去反查每场跑目录里有没有这份文件。
        meta["packed"]["model_errors"] = n_model_errors
        meta["packed"]["interview_miss"] = n_interview_miss
        meta["packed"]["n_runs_with_summary"] = n_runs_with_summary
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return dict(meta["packed"])
