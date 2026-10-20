"""AgentSociety 插头：replay JSONL 分片 + 问卷 artifact，翻译成货箱两张表。

**只读输出文件，不 import agentsociety**——纯标准库（glob + json），不引 duckdb
（设计稿已决：分片按 `replay/{table}.*.jsonl` glob，不按 crc32 重算分片号）。
档案锁 agentsociety2==2.8.4；上游改落盘格式才要动这里。

**箱规**（跟 OASIS 的「一场跑一个 .db」不同）：AgentSociety 一场跑的原始输出
是**一个目录**，runs.csv 的 file 列就指这个目录（如 `raw/A_1`），里面要有：

    raw/<条件>_<第几次>/
      replay/                      # replay_sink 的落盘目录
        {table}.{分片}.jsonl       # 逐行 JSON；本插头只认 social_media_event、
                                   # *_agent_state 两类表，core_agent_profile 等
                                   # 其余分片不读但照记哈希（都是原始证据）
        _schema.json               # 表清单侧车，兼作「这真是回放目录」的哨兵
                                   # （必须有 tables 段——replay_sink 一定写它）
      artifacts/
        questionnaire_step_{步号}_{时刻}.json   # 问卷 artifact，一波一份

file 列指目录而不指文件，依据是 simbox 的实际校验：check_box 对 file 列只做
`(box / f).exists()`（Path.exists() 对目录同样放行）与「不许绝对路径/..」两条，
没有 is_file 要求——2026-09-03 读 oss/core/simbox.py 核实；core 一个字没改
（只收货铁律：core 不认识任何模拟器）。哪天 core 把这条尺子收紧到「必须是
文件」，改这里的箱规去指 `raw/<C>_<r>/replay/_schema.json` 哨兵文件即可。

五步翻译：
1. **波（wave）= 问卷 artifact 的序号**（0 起）。问卷不推进 step，同一 step 可以
   有多波——排序钥匙是（文件名里的步号, artifact 里的 simulation_time, 文件名），
   设计稿已决的消歧法。responses 为空的 artifact 照样占一个波号，只是不产行。
2. **states.csv**：每 (condition, rep, wave, agent_id) 一行；测量列 =
   `responses[].answers[].parsed_value` 按 question_id 展开（列名把 question_id
   里的非字词字符换成 `_`；净化后撞名直接拒收，不静默合并）。
   `parse_success=false` 或 parsed_value 为 null 的答案**标缺失不猜**（空格子，
   计入 packed 的 n_parse_miss）。另外把该波对应 step 的 `{模块}_agent_state`
   快照并成列（列名 = 模块前缀 + 列名，如 social_media_followers_count）：
   问卷 artifact 的 step_count=N 表示已跑完 N 步，对应快照是第 N 步末尾写的
   step=N-1 那份；N=0 的基线波还没有快照，留空。连接钥匙 agent_id 两侧都验
   整型（"1" 与 1 连不上会让快照列静默全空，验货还全绿——门口就拒）。
   该并快照（snap_step≥0 且**这箱**真有快照列——门是箱级不是场级，丢了表的
   场次不豁免）却一张表都没接上的行计入 n_snap_miss；只出现在快照表、从没答过卷的小人（他们进不了 states.csv，
   快照数据整个人蒸发）计入 n_snap_only_agents——丢失可以，沉默不行。
3. **events.csv**：只出 `social_media_event` 分片（设计稿已决：它是 v2 唯一
   完整的事件流；其他模块只有状态快照，没有事件分片不算错）。列映射：
   sender_id→agent_id，step→step（轮=step，tick 换算记在配料单 interview_plan），
   id→seq，t→sim_time；target 取 target_id、缺了退 receiver_id（`is not None`
   判断——id 可以是 0）；行按 (step, id) 排序后写。
4. **target_kind 由 action 推断**（设计稿已决）：like/comment/repost→"post"、
   follow→"agent"（OASIS 插头同列写 "user"，那是它的 user_id 命名空间；这里
   按设计稿措辞用 "agent"）、post→''（发帖压根没有「指着谁」这回事，target
   恒空**不算**进 n_blank_targets——只数 like/comment/repost/follow 这些本该有
   target 却两个 id 都空的行）。认不出的 action 照收，target_kind 留空。
   第 10 列 content 原样带走（货箱契约只要求 8 列，target_kind 与 content
   是本插头的信息列）。
5. condition/rep 从 runs.csv 补（框架无此概念，跑单外层循环承载）。

replay_sink 的 _normalize 把 dict/list 列存成 JSON 字符串（如 core_agent_profile
的 profile 列）——本插头读的三类列全是标量，不需要二次解析；哪天要读 profile，
记得先 json.loads 那一列。

packed 八项：n_state_rows / n_event_rows / n_blank_targets / n_parse_miss /
n_snap_miss / n_snap_only_agents / n_runs_no_events（一条社媒事件都没有的场数
——无事件流的模块合法，但要看得见）/ file_sha256（**一场跑一个摘要哈希**，
键 = runs.csv 的 file 值；构造见 _hash_run 的 docstring，没读的分片也在内）。

一切读不进、对不上的输入抛 PackError（人话，不是 traceback）：
宁可停下也不出一张猜出来的表——跟 OASIS 插头同一条老规矩。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

#: 事件流唯一的来源表（设计稿已决：事件流只绑社交媒体模块）
_EVENT_TABLE = "social_media_event"

#: target_kind 推断表；键集合同时就是「本该有 target」的动作名单
_KIND_BY_ACTION = {"like": "post", "comment": "post", "repost": "post",
                   "follow": "agent"}

#: 问卷 artifact 的文件名样子（society/cli.py 写死的格式）
_ARTIFACT_RE = re.compile(r"questionnaire_step_(\d+)_.+\.json")

#: states.csv 的钥匙列——问题列、快照列都不许撞上它们
_KEY_COLS = ("condition", "rep", "wave", "agent_id")


class PackError(Exception):
    """装箱失败：缺目录、缺文件、分片或问卷不合格、配料单读不进。"""


def _clean_col(qid: str) -> str:
    """question_id → 列名：非字词字符（空格、括号、逗号…）换成下划线。

    中文、字母、数字、下划线原样保留（\\w 在 str 上是 Unicode 口径）。
    """
    return re.sub(r"\W", "_", qid)


def _render(v) -> str:
    """一个 JSON 值进 CSV 格子：字符串原样，None 空，其余按 JSON 写
    （数字不变样，list/dict/bool 落成 JSON 文本——不猜怎么拍平）。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _require_int(d: dict, key: str, where: str) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool):  # bool 是 int 的子类，同 simbox 一把尺子
        raise PackError(f"{where} 的 {key} 必须是整数，现在是 {v!r}")
    return v


def _read_jsonl(path: Path, box: Path) -> list[tuple[str, dict]]:
    """整读一个分片再按行拆（分片是人扛得动的规模，不必流式），
    返回 [(出处短语, 行 dict)]。坏行点名文件与行号。"""
    rel = path.relative_to(box).as_posix()
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{rel} 第 {i} 行"
        try:
            d = json.loads(line)
        except ValueError as exc:
            raise PackError(f"{where} 不是合法 JSON：{exc}") from exc
        if not isinstance(d, dict):
            raise PackError(f"{where} 不是一个对象 {{…}}——replay 分片每行应是一条记录")
        out.append((where, d))
    return out


def _read_events(replay: Path, box: Path) -> tuple[list[tuple], int]:
    """social_media_event 分片 → 按 (step, id) 排好的事件元组 + 空 target 计数。

    没有任何分片不算错：设计稿已决，事件流只绑社交媒体模块，别的模块的箱子
    本来就没有这张表。分片有了但行不合格才拒。
    """
    rows: dict[int, tuple] = {}
    n_blank = 0
    for path in sorted(replay.glob(f"{_EVENT_TABLE}.*.jsonl")):
        for where, d in _read_jsonl(path, box):
            eid = _require_int(d, "id", where)
            step = _require_int(d, "step", where)
            agent = _require_int(d, "sender_id", where)
            action = d.get("action")
            if not isinstance(action, str) or not action:
                raise PackError(f"{where} 的 action 必须是非空字符串，现在是 {action!r}")
            if eid in rows:
                raise PackError(
                    f"{where} 的事件 id={eid} 撞号——同一场跑里 id 是主键，"
                    f"写重了多半是上游重复刷写，箱子先别装")
            target_val = d.get("target_id")
            if target_val is None:               # is None 判断——id 可以是 0
                target_val = d.get("receiver_id")
            target = _render(target_val)
            if target == "":
                kind = ""
                if action in _KIND_BY_ACTION:    # 本该有 target 却解不出来
                    n_blank += 1
            else:
                kind = _KIND_BY_ACTION.get(action, "")
            rows[eid] = (step, eid, _render(d.get("t")), agent, action,
                         target, kind, _render(d.get("content")))
    return sorted(rows.values()), n_blank


def _read_agent_states(replay: Path, box: Path) -> dict[str, dict]:
    """`*_agent_state` 分片 → {表名: {(step, agent_id): {列: 值}}}。

    同一 (step, agent) 出现两份且值不一致就拒——两份里挑哪份都是猜。
    """
    tables: dict[str, dict] = {}
    for path in sorted(replay.glob("*_agent_state.*.jsonl")):
        tname = path.name.split(".", 1)[0]
        tbl = tables.setdefault(tname, {})
        for where, d in _read_jsonl(path, box):
            step = _require_int(d, "step", where)
            agent = _require_int(d, "agent_id", where)
            data = {k: v for k, v in d.items()
                    if k not in ("agent_id", "step", "t")}
            key = (step, agent)
            if key in tbl and tbl[key] != data:
                raise PackError(
                    f"{where}：{tname} 里 step {step} 小人 {agent} 有两份不一致的"
                    f"快照——上游写重了，箱子先别装")
            tbl[key] = data
    return tables


def _read_artifacts(run_dir: Path, box: Path) -> list[dict]:
    """问卷 artifact → 按波次排好的列表。排序钥匙：(文件名步号, simulation_time,
    文件名)——问卷不推进 step，同 step 多波靠 simulation_time 消歧（设计稿已决）。
    """
    art_dir = run_dir / "artifacts"
    if not art_dir.is_dir():
        return []
    out = []
    for path in sorted(art_dir.glob("questionnaire_step_*.json")):
        rel = path.relative_to(box).as_posix()
        m = _ARTIFACT_RE.fullmatch(path.name)
        if not m:
            raise PackError(
                f"{rel} 的文件名对不上 questionnaire_step_{{步号}}_{{时刻}}.json "
                f"的样子——波次靠文件名里的步号排序，手改过名字就改回去")
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise PackError(f"{rel} 不是合法 JSON：{exc}") from exc
        if not isinstance(d, dict):
            raise PackError(f"{rel} 顶层必须是一个对象 {{…}}，不是列表或字符串")
        step_count = _require_int(d, "step_count", rel)
        responses = d.get("responses")
        if not isinstance(responses, list):
            raise PackError(f"{rel} 缺 responses 列表——问卷 artifact 的回答都在这里")
        sim_time = d.get("simulation_time")
        out.append({"step_idx": int(m.group(1)),
                    "sim_time": str(sim_time) if sim_time is not None else "",
                    "name": path.name, "rel": rel,
                    "step_count": step_count, "doc": d})
    out.sort(key=lambda a: (a["step_idx"], a["sim_time"], a["name"]))
    return out


def _register_q_col(q_cols: dict, qid: str, rel: str) -> str:
    """登记一个问题列。净化后撞名（两个不同 question_id 变成同一列）就拒——
    静默合并等于把两道题的数据搅在一起。"""
    col = _clean_col(qid)
    if col in _KEY_COLS:
        raise PackError(f"{rel} 的问题 id「{qid}」净化后撞上钥匙列「{col}」——换个 id")
    prev = q_cols.get(col)
    if prev is None:
        q_cols[col] = qid
    elif prev != qid:
        raise PackError(
            f"{rel} 的问题 id「{qid}」与「{prev}」净化后撞成同一列名「{col}」"
            f"——改问卷里的 question id 再装箱")
    return col


def _parse_waves(arts: list[dict], q_cols: dict) -> tuple[list[dict], int]:
    """artifact 列表 → 每波 {snap_step, rows:[(agent, {列: 格})]}；顺带数 parse 缺失。"""
    waves = []
    n_miss = 0
    for a in arts:
        rel, doc = a["rel"], a["doc"]
        for q in doc.get("questions") or []:
            if isinstance(q, dict) and isinstance(q.get("id"), str) and q["id"]:
                _register_q_col(q_cols, q["id"], rel)   # 先按问卷题序登记列
        seen: set = set()
        rows = []
        for resp in doc["responses"]:
            if not isinstance(resp, dict):
                raise PackError(f"{rel} 的 responses 里有一条不是对象 {{…}}——回答对不上人")
            # 验型不是挑剔：agent_id 是跟快照表连接的钥匙，"1" 与 1 连不上会让
            # 整行快照列静默留空、pack 与验货照样全绿——OASIS 44% target 丢失
            # 事故的同款缝，必须在门口拒掉（缺键时 v 是 None，同一句话报出来）
            agent = _require_int(resp, "agent_id", rel)
            if agent in seen:
                raise PackError(f"{rel} 里小人 {agent} 出现两次——同一波两份回答，翻译必错")
            seen.add(agent)
            cells: dict = {}
            for ans in resp.get("answers") or []:
                if not isinstance(ans, dict) or not ans.get("question_id"):
                    raise PackError(f"{rel} 里小人 {agent} 有一条答案缺 question_id")
                qid = str(ans["question_id"])
                col = _register_q_col(q_cols, qid, rel)
                if col in cells:
                    raise PackError(f"{rel} 里小人 {agent} 对问题「{qid}」有两个答案")
                pv = ans.get("parsed_value")
                if not ans.get("parse_success", True) or pv is None:
                    cells[col] = ""       # 解析不了标缺失，不猜
                    n_miss += 1
                else:
                    cells[col] = _render(pv)
            rows.append((agent, cells))
        # step_count=N：已跑完 N 步，对应第 N 步末尾写的 step=N-1 快照；
        # N=0 是开跑前的基线波，还没有任何快照
        waves.append({"snap_step": a["step_count"] - 1, "rows": rows})
    return waves, n_miss


def _hash_run(run_dir: Path, box: Path) -> str:
    """一场跑一个摘要哈希（与 OASIS「一场跑一个哈希」对齐——正式规模逐文件记
    要上千个键，配料单会被清单撑爆）。

    构造可复现：对每个证据文件算 sha256（整读；分片是人扛得动的规模，不必学
    core/datapacks 的分块 _sha——那是人家的私有帮手，跨层借私名不是好先例；
    更要命的是 datapacks 顶层 import pandas+httpx，借它会把两个重依赖拖进
    这只纯标准库插头，破了设计稿「纯标准库读取」的已决事项），
    拼成「箱内相对路径:哈希\\n」逐行文本、按行排序，再对整段文本取 sha256。
    证据文件 = replay/ 下所有 *.jsonl 与 _schema.json、artifacts/ 下所有问卷——
    没读的分片（core_agent_profile 等）也在内：任何一个文件改一个字节，摘要就变。
    """
    files = sorted((run_dir / "replay").glob("*.jsonl"))
    files.append(run_dir / "replay" / "_schema.json")
    art_dir = run_dir / "artifacts"
    if art_dir.is_dir():
        files += sorted(art_dir.glob("questionnaire_step_*.json"))
    lines = sorted(
        f"{f.relative_to(box).as_posix()}:{hashlib.sha256(f.read_bytes()).hexdigest()}\n"
        for f in files)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _load_run(box: Path, r: dict, q_cols: dict, shas: dict) -> dict:
    f = r["file"]
    run_dir = box / f
    if not run_dir.exists():
        raise PackError(f"runs.csv 里的 {f} 不在箱子里")
    if not run_dir.is_dir():
        raise PackError(
            f"runs.csv 里的 {f} 应是一场跑的目录（AgentSociety 的原始输出是"
            f"整个目录：replay/ 分片 + artifacts/ 问卷），现在是个文件")
    replay = run_dir / "replay"
    if not replay.is_dir():
        raise PackError(
            f"{f} 里缺 replay/ 目录——AgentSociety 的回放分片都落在这里，"
            f"多半是 file 列指错了地方或这场跑没落完")
    schema_path = replay / "_schema.json"
    if not schema_path.exists():
        raise PackError(
            f"{f}/replay 里缺 _schema.json——真跑出来的回放目录一定有这份表清单，"
            f"缺了多半不是 AgentSociety 的输出目录")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PackError(f"{f}/replay/_schema.json 不是合法 JSON：{exc}") from exc
    if not isinstance(schema, dict):
        raise PackError(f"{f}/replay/_schema.json 顶层必须是一个对象 {{…}}")
    if "tables" not in schema:
        # 哨兵要真干哨兵的活：replay_sink 落的表清单一定有 tables 段，
        # 光是「合法 JSON 对象」拦不住随手指错的目录
        raise PackError(
            f"{f}/replay/_schema.json 里没有 tables 段——replay_sink 落的表清单"
            f"一定有它，这多半不是 AgentSociety 的回放目录")
    shas[f] = _hash_run(run_dir, box)
    events, n_blank = _read_events(replay, box)
    waves, n_miss = _parse_waves(_read_artifacts(run_dir, box), q_cols)
    return {"run": r, "events": events, "waves": waves,
            "snap": _read_agent_states(replay, box),
            "n_blank": n_blank, "n_miss": n_miss}


def pack(box: Path | str) -> dict:
    box = Path(box)
    runs_path = box / "runs.csv"
    if not runs_path.exists():
        raise PackError(f"缺 runs.csv：{box}")
    with runs_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
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

    # 先把每场跑全部读进内存再落表——列集合（问题列、快照列）要看完全箱才定得下来
    q_cols: dict[str, str] = {}    # 净化后的列名 → 原 question_id（保问卷题序）
    shas: dict[str, str] = {}
    loaded = [_load_run(box, r, q_cols, shas) for r in runs]

    # 快照列：表名排序，表内列名排序；列名 = 模块前缀 + 列名（social_media_followers_count）
    snap_cols: list[tuple[str, str, str]] = []
    for tname in sorted({t for ld in loaded for t in ld["snap"]}):
        prefix = tname[: -len("_agent_state")]
        raw_cols = sorted({c for ld in loaded
                           for cells in ld["snap"].get(tname, {}).values()
                           for c in cells})
        for c in raw_cols:
            full = f"{prefix}_{c}"
            if full in q_cols or full in _KEY_COLS:
                raise PackError(
                    f"快照列「{full}」与问卷列或钥匙列撞名——改问卷的 question id "
                    f"再装箱（快照列名来自模拟器，改不了）")
            snap_cols.append((full, tname, c))

    n_states = n_events = n_snap_miss = 0
    n_blank = sum(ld["n_blank"] for ld in loaded)
    n_miss = sum(ld["n_miss"] for ld in loaded)
    # 三个「沉默的缺席」计数（丢失可以，沉默不行——OASIS n_blank_targets 的老规矩）：
    # 一条社媒事件都没有的场数；只在快照表出现、从没答过卷的小人数（他们一行都
    # 进不了 states.csv，快照数据整个人蒸发）；n_snap_miss 在写表时逐行数
    n_runs_no_events = sum(1 for ld in loaded if not ld["events"])
    n_snap_only = 0
    for ld in loaded:
        snap_agents = {a for tbl in ld["snap"].values() for (_s, a) in tbl}
        q_agents = {a for w in ld["waves"] for (a, _c) in w["rows"]}
        n_snap_only += len(snap_agents - q_agents)
    tmp_states, tmp_events = box / "states.csv.tmp", box / "events.csv.tmp"
    try:
        with tmp_states.open("w", encoding="utf-8", newline="") as sf, \
             tmp_events.open("w", encoding="utf-8", newline="") as ef:
            sw, ew = csv.writer(sf), csv.writer(ef)
            sw.writerow(list(_KEY_COLS) + list(q_cols)
                        + [full for full, _, _ in snap_cols])
            ew.writerow(["condition", "rep", "step", "seq", "sim_time",
                         "agent_id", "action", "target", "target_kind",
                         "content"])
            for ld in loaded:
                cond, rep = ld["run"]["condition"], ld["run"]["rep"]
                for wave, w in enumerate(ld["waves"]):
                    for agent, cells in w["rows"]:
                        snap_vals = []
                        for _, tname, c in snap_cols:
                            got = ld["snap"].get(tname, {}).get(
                                (w["snap_step"], agent), {})
                            snap_vals.append(_render(got.get(c)))
                        # 该并快照却一张表都没接上的行：基线波（snap_step<0）
                        # 与「整箱压根没有快照表」不算——没有不是丢。门是箱级
                        # snap_cols 不是场级 ld["snap"]：丢了表的场次（兄弟场
                        # 还留着表）恰恰是最该数出来的丢失，按场判会整场豁免
                        if w["snap_step"] >= 0 and snap_cols and not any(
                                (w["snap_step"], agent) in tbl
                                for tbl in ld["snap"].values()):
                            n_snap_miss += 1
                        sw.writerow([cond, rep, wave, agent]
                                    + [cells.get(col, "") for col in q_cols]
                                    + snap_vals)
                        n_states += 1
                for step, eid, t, agent, action, target, kind, content in ld["events"]:
                    ew.writerow([cond, rep, step, eid, t, agent, action,
                                 target, kind, content])
                    n_events += 1
    except Exception:
        tmp_states.unlink(missing_ok=True)
        tmp_events.unlink(missing_ok=True)
        raise
    tmp_states.replace(box / "states.csv")
    tmp_events.replace(box / "events.csv")
    meta["packed"] = {"n_state_rows": n_states, "n_event_rows": n_events,
                      "n_blank_targets": n_blank, "n_parse_miss": n_miss,
                      "n_snap_miss": n_snap_miss,
                      "n_snap_only_agents": n_snap_only,
                      "n_runs_no_events": n_runs_no_events,
                      "file_sha256": shas}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return dict(meta["packed"])
