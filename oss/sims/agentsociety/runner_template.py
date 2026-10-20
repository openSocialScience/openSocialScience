"""AgentSociety 跑单模板——一次 condition × rep 的模拟怎么跑，写死在这份文件里。

**此文件是数据不是代码**：九个花括号空位（八必填、第九个 CONCURRENCY 可选）
不是合法的运行时值，由 /social-sim 的说明书照整理稿把它们填好、另存成货箱
`raw/` 下的一份具体脚本才能跑。**别直接 import 这份模板**——顶层守卫见到
没填的空位会当场 SystemExit；就算哪天守卫被绕过，跑出来的也是占位符不是
真实设置，比炸更危险。

**布局约定（与 OASIS「一场跑一个 .db」不同）**：AgentSociety 一场跑的原始
输出是**一个目录** `raw/<条件>_<第几次>/`（replay/ 分片 + artifacts/ 问卷，
见插头 adapter.py 的箱规）。填好的跑单脚本另存为 `raw/<条件>_<第几次>.py`，
与跑目录**并排**——check_box 的跑脚本规矩是「根 runner.py 或 raw/ 下的 *.py
至少一份」（oss/core/simbox.py，glob 只看 raw/ 一层不进子目录），并排的
.py 恰好满足 raw/*.py 这一半，还把每场的真实参数留在箱里当出处。

跑单三步（脚本 main() 干的事）：
1. 生成两份配置进跑目录——`init_config.json`（agents 取样本档案前 N 条记录
   + 唯一环境模块 social_media，条件旋钮注入 kwargs）和 `steps.yaml`
   （run/questionnaire 按波次交错；**内容写的是 JSON 文本**：JSON 是 YAML
   的子集，上游 CLI 用 yaml.safe_load 读，照读不误——跑单因此不用多背
   pyyaml 依赖，纯标准库）；
2. 设 AGENTSOCIETY_* 环境变量（BASE/KEY → API_BASE/API_KEY，与 OASIS 跑单
   同一套操作习惯），子进程调 CLI 正门
   `python -m agentsociety2.society.cli --config … --steps … --run-dir …`，
   逐行转印输出；
3. 跑完从问卷 artifact 汇总 `run_summary.json`（parse 失败计数为 miss 类比）
   + 落 `meta_sidecar.json`（model/api_base/包版本等，装箱人抄进
   sim.meta.json）——两份都在跑目录里。

**与 OASIS 模板的差异及理由**（一条条对着看）：
- **无内置重试网/monkeypatch 兜底**：OASIS 模板包了 perform_interview 加
  重试，是因为 camel-oasis 0.2.5 一个小人的异常经 gather 会杀全场。
  AgentSociety 用不着也包不着——它自带 AIMD 并发自适应（过载自动降并发）、
  CLI 对每个顶层 step 自己 try/except 后继续（society/cli.py 逐步捕获），
  而且我们隔着子进程边界，摸不到它进程里的类；再包一层是重复保险。
- **不 import 库、走 CLI 子进程**：AgentSociety 的正门是 CLI+配置文件，
  不像 camel-oasis 那样以库形态驱动；跑单退到门外，只递配置、收输出。
- **没有 steps.jsonl 行号日志**：OASIS 要 runner 逐轮记 MAX(rowid) 才能
  切轮次；AgentSociety 的 replay 分片自带 step 列、问卷 artifact 自带
  step_count，插头直接读，跑单不用记侧车。
- **model_errors 从 CLI 输出扫描**（而非挂 logging.Handler）：子进程边界
  拿不到人家进程内的 logger 树，只能读它打出来的行——所以这是启发式计数，
  见 _is_error_line 的注释，措辞里必须带 caveat。
- **run_summary.json/meta_sidecar.json 落在跑目录里**（不是 <db>.xxx.json
  侧车）：这里一场跑本来就是一个目录，摘要放进去顺理成章。

**成本预警（未实测）**：AgentSociety 是 ReAct 智能体，每 agent 每 step 约
3-10 次模型调用，同规模成本约为 OASIS 的数倍（设计稿已决预警）——真跑前
先小规模试水（如 5 人×2 轮）+中转站余额确认，超预算就中断。

**钱类信号**：CLI 输出里见到 402/余额类字样（词表见 _MONEY_SIGNS），当场
打醒目横幅并在结尾复吼——中转站断粮是钱的问题，**不自动重试**，立即全停
整个流程去核对余额/充值（/social-sim 说明书铁律③同一口径）。

用法（另存并把八个必填空位换成真实值之后；第九个 CONCURRENCY 可选，
不填缺省 8）：

    BASE=<中转站 URL> KEY=<中转站 key> python raw/A_1.py

九个空位：CONDITION、REP、CONDITION_KNOB、ROUNDS、TICK、N_AGENTS、
INTERVIEW_Q、INTERVIEW_WAVES 八个必填，CONCURRENCY 第九个可选——逐条说明
见下面的赋值处（此处刻意不写花括号包裹的原字面量：另存时的盲替换按每个
名字两侧包一对花括号的写法逐个找目标，写在这里会被替换值把这一行填坏）。
"""
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── 八个待填空位（必填）──────────────────────────────────────────────
COND = "{{CONDITION}}"
# 条件名，如 A / B。只作目录名与 runs.csv 记录用，不改变模拟行为——
# 真正让条件产生差异的是下面的 CONDITION_KNOB（以及研究者在整理稿里定的设置）。
REP = "{{REP}}"
# 第几次重复，如 1 / 2 / 3。同一条件跑多次重复，每次必须换一个 REP，
# 否则跑目录同名互相覆盖（详见货箱标准 docs/user/sim-box.md 的 runs.csv 一节）。
KNOB_VALUE = "{{CONDITION_KNOB}}"
# 条件旋钮的值：注入 social_media 模块 kwargs 的 feed_source——
# "global"=全站帖子池、"following"=只看关注的人+自己（上游缺省 global，
# contrib/env/social_media/social_media_space.py）。要把旋钮换成**别的**
# kwarg（或加第二个），得改下面 KNOB_MODULE/KNOB_KEY 常量连模板一起手改，
# 不能只填空位——与 OASIS 模板 SEED_CLAIM 的「有意简化」同一条纪律。
ROUNDS_RAW = "{{ROUNDS}}"
# 行动轮总数 = steps.yaml 里所有 run 步 num_steps 之和（轮=step，设计稿已决）。
TICK_RAW = "{{TICK}}"
# 每步推进的模拟时钟秒数（RunStep.tick）。上游 experiments/main.py 用 900
# （15 分钟/步）；秒换算连同波次表一起进配料单 interview_plan。
N_AGENTS_RAW = "{{N_AGENTS}}"
# 从样本档案里取前多少人——按 JSON **记录数**取（档案是 JSON 数组，len/切片
# 天然按记录），与 OASIS「按 CSV 记录数不按物理行」同一条纪律。
INTERVIEW_Q = "{{INTERVIEW_Q}}"
# 问卷题面原文，逐字进问卷 artifact 也逐字进论文方法节。本模板 V1 只编排
# **单题整数问卷**（id/response_type 见 QUESTION_ID/RESPONSE_TYPE 常量）；
# 多题要连模板一起手改 _q_step，不能只填空位。
INTERVIEW_WAVES_RAW = "{{INTERVIEW_WAVES}}"
# 除第 0 波（开跑前基线，运行契约同 OASIS 契约 1：DID 的处理前观测）之外，
# 额外问卷的轮次号，逗号分隔，如 "5,10" 表示第 5 轮和第 10 轮结束后各一波。
# 第 0 波是固定动作，不必也不能写进这里。wave 序号按问卷步出现顺序编
# 0,1,2……（问卷不推进 step，设计稿已决）；wave 与真实轮次的对应关系要写进
# 配料单 interview_plan，states.csv 的 wave 列自己不记轮次号。

# ── 第九个空位（可选）────────────────────────────────────────────────
CONCURRENCY_RAW = "{{CONCURRENCY}}"
# 并发初值 → 环境变量 AGENTSOCIETY_LLM_RAY_CONCURRENCY。可选——不填（仍是
# 花括号占位符）时缺省 8。缺省值不是随便取的：上游默认 16 且带 AIMD 自适应
# （压垮了会自己降），但本仓中转站史站在保守一边——OASIS 跑单用旧默认 32
# 两次压垮中转站、改 8 才稳（2026-08-31 真跑），AIMD 只保证「垮了能降」，
# 不保证「垮之前不疼」；故起步 8，要调大就在整理稿层面明确写出这个数字。
# profile.json 的 defaults.concurrency 与此缺省要一起改。

_UNFILLED = [name for name, val in (
    ("CONDITION", COND), ("REP", REP), ("CONDITION_KNOB", KNOB_VALUE),
    ("ROUNDS", ROUNDS_RAW), ("TICK", TICK_RAW), ("N_AGENTS", N_AGENTS_RAW),
    ("INTERVIEW_Q", INTERVIEW_Q), ("INTERVIEW_WAVES", INTERVIEW_WAVES_RAW),
) if val.startswith("{{") and val.endswith("}}")]
# CONCURRENCY 不在上面这份必填清单里——第九个可选空位，没填不算「跑单没填好」，
# 缺省逻辑在下面单独处理（不走这道 SystemExit 拒绝）。
if _UNFILLED:
    raise SystemExit(
        f"{'、'.join(_UNFILLED)} 还是花括号占位符，没有被另存替换——"
        f"这份文件本身是数据不是代码，见顶部 docstring")

# 删除操作的第一道闩：九个槽里唯二拼进文件路径的是 COND/REP（合成 RUN_DIR，
# 而 RUN_DIR 会喂给 shutil.rmtree 清残跑）——别的槽都验了数，这两个必须验名。
# 带 ../ 、绝对路径、点前缀的值能把删除目标指到 raw/ 外面去，当场拒，
# 什么都还没建也没删。\w 是 Unicode 口径，中文条件名（如「热度推荐」）照收。
for _slot, _val in (("CONDITION", COND), ("REP", REP)):
    if not re.fullmatch(r"[\w\-]+", _val):
        raise SystemExit(
            f"{_slot} 只能是简单名字（中文/字母/数字/下划线/连字符），"
            f"现在是 {_val!r}——它要拼进跑目录路径再喂给清场删除，"
            f"斜杠、点、空格都可能把删除指到箱子外面")

ROUNDS = int(ROUNDS_RAW)
TICK = int(TICK_RAW)
N_AGENTS = int(N_AGENTS_RAW)
INTERVIEW_WAVES = [int(x) for x in INTERVIEW_WAVES_RAW.split(",") if x.strip()]
CONCURRENCY = (8 if CONCURRENCY_RAW.startswith("{{")
               and CONCURRENCY_RAW.endswith("}}")
               else int(CONCURRENCY_RAW))

# ── 手改常量（不是空位；改哪个都要连模板一起看）──────────────────────
KNOB_MODULE = "social_media"
# 唯一环境模块（设计稿已决：事件流只绑社交媒体模块；上游 registry 把
# SocialMediaSpace 注册成 "social_media"）。
KNOB_KEY = "feed_source"
# CONDITION_KNOB 注进哪个 kwargs 键。换键=手改模板，见 KNOB_VALUE 的注释。
AGENT_TYPE = "PersonAgent"
QUESTION_ID = "q_main"
RESPONSE_TYPE = "integer"
# 单题问卷的题号与答案类型（integer → parsed_value 直接是整数，1-7 量表
# 解析不了的答案 artifact 里 parse_success=false，插头按缺失处理不猜）。
MODEL_TYPE = "deepseek-v4-flash"
START_T = "2026-01-01T08:00:00"
# 模拟时钟起点（StepsConfig.start_t，ISO 格式）。任意但要固定——写进配料单
# 才能对上 replay 里的 t 列。

RUN_DIR = HERE / f"{COND}_{REP}"
PROFILE_SRC = HERE / "profiles_full.json"
# 未裁剪的样本档案，另存脚本前放到这个位置（同名同目录）——货箱必须自含，
# 不引用箱子外的绝对路径。默认可用 profile.json 里 sample_data 指向的那份
# agentsociety2 仓库自带样本（JSON 数组，100 人：id/age/gender/education/
# occupation/consumption/home/work；**不随 pip 包分发**，得从克隆拷），
# 换成自己的人群档案也可以，只要是 JSON 数组、每条记录带整数 id。
RUN_SUMMARY = RUN_DIR / "run_summary.json"
META_SIDECAR = RUN_DIR / "meta_sidecar.json"

# ── 钱类信号词表 ─────────────────────────────────────────────────────
# 宁漏勿滥：词表只收「几乎必是钱」的信号——裸 "403" 不入表（权限类错误太多，
# 见一条吼一条会把横幅喊成狼来了），必须同行配 "quota" 才算钱；"402" 作为
# HTTP 状态码几乎只在报错行出现，误撞率低，保留裸匹配。漏掉的靠人读日志
# 兜底（/social-sim 说明书要求逐条读异常文字），喊错的没法收回。
_MONEY_SIGNS = ("402", "Insufficient", "余额", "欠费", "PermissionDenied",
                "token quota is not enough")


def _money_hit(line: str) -> bool:
    """这行 CLI 输出是不是钱类信号（断粮/欠费/配额耗尽）。"""
    return (any(s in line for s in _MONEY_SIGNS)
            or ("403" in line and "quota" in line))


def _money_banner(line: str) -> str:
    bar = "!" * 64
    return (f"\n{bar}\n"
            f"!! 钱类信号：{line[:200]}\n"
            f"!! 中转站断粮是钱的问题，重试无用——本跑单不自动重试。\n"
            f"!! 立即全停：别启动下一场，先去中转站核对余额/充值，再决定重跑。\n"
            f"{bar}")


def _is_error_line(line: str) -> bool:
    """ERROR 级日志行的启发式判定：认上游 logger 的固定格式
    `[时间] ERROR  消息`（logger/__init__.py 的 "[%(asctime)s] %(levelname)-7s
    %(message)s"；子进程非 tty，没有色码）。**这是启发式**：不进这棵 logger
    树的错误（如 ray worker 自己的 traceback）数不到，框架内部重试的每次
    ERROR 都会重复入账——run_summary 的措辞必须带这两个 caveat。"""
    return "] ERROR " in line or "] CRITICAL" in line


# ── 纯函数：配置编排（测试直接调这几个，不跑 main）───────────────────
def build_steps(rounds: int, tick: int, waves: list, prompt: str) -> dict:
    """steps.yaml 的内容：问卷波与行动段按 INTERVIEW_WAVES 交错。

    第 0 波固定在 steps[0]（开跑前基线，step_count=0——插头把它记成
    snap_step=-1 的基线波）；waves 里的轮次号处各插一波；波次没到 ROUNDS
    的尾巴补一段纯 run。校验在这里做：波次必须严格递增、都在 1..rounds 内。
    """
    if rounds < 1:
        raise SystemExit(f"ROUNDS 必须 ≥1，现在是 {rounds}")
    if tick < 1:
        raise SystemExit(f"TICK 必须 ≥1（每步的模拟时钟秒数），现在是 {tick}")
    steps = [_q_step(0, prompt)]
    prev = 0
    wave = 1
    for w in waves:
        if w <= prev or w > rounds:
            raise SystemExit(
                f"INTERVIEW_WAVES 必须严格递增、都在 1..ROUNDS 内："
                f"{waves}（ROUNDS={rounds}）")
        steps.append({"type": "run", "num_steps": w - prev, "tick": tick})
        steps.append(_q_step(wave, prompt))
        wave += 1
        prev = w
    if prev < rounds:
        steps.append({"type": "run", "num_steps": rounds - prev, "tick": tick})
    return {"start_t": START_T, "steps": steps}


def _q_step(wave: int, prompt: str) -> dict:
    # 字段名照上游 QuestionItem（society/models.py）：id/prompt/response_type/
    # choices；target_agent_ids 不写 = 发给全部小人
    return {"type": "questionnaire",
            "questionnaire_id": f"wave_{wave}",
            "title": f"第 {wave} 波问卷",
            "questions": [{"id": QUESTION_ID, "prompt": prompt,
                           "response_type": RESPONSE_TYPE, "choices": []}]}


def build_init(profiles, n_agents: int, knob: str) -> dict:
    """init_config.json 的内容：agents 取样本前 n_agents 条 + 唯一环境模块。

    每条样本记录整份进 kwargs（上游 CLI 把 id 之外的键当 profile 用）；
    kwargs 必须带整数 id（InitConfig 的校验，society/models.py）——缺了在
    这里就拒，人话点名，不等 CLI 在子进程里炸成 pydantic 报文。
    """
    if not isinstance(profiles, list):
        raise SystemExit(
            "样本档案顶层必须是 JSON 数组（agentsociety2 自带的 profiles.json "
            "就是这个形状）")
    if len(profiles) < n_agents:
        raise SystemExit(
            f"样本档案只有 {len(profiles)} 条记录，不够 N_AGENTS={n_agents} 人")
    agents = []
    for p in profiles[:n_agents]:
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise SystemExit(
                f"样本记录缺整数 id 字段：{str(p)[:120]}——上游 InitConfig 要求"
                f"每个 agent 的 kwargs 带 id（society/models.py 的校验）")
        agents.append({"agent_id": pid, "agent_type": AGENT_TYPE,
                       "kwargs": dict(p)})
    return {"env_modules": [{"module_type": KNOB_MODULE,
                             "kwargs": {KNOB_KEY: knob}}],
            "agents": agents}


def count_parse_miss(art_dir: Path) -> int:
    """问卷 artifact 里解析失败的答案数（OASIS interview_miss 的类比：那边数
    的是访谈异常，这边模型都答了、答案没解析出来）。

    判定与插头 adapter._parse_waves 逐字同款：`parse_success` 明写 false、或
    `parsed_value` 为 null 的算 miss；**缺 parse_success 旗但值在的按成功算**
    （`get("parse_success", True)`——有可用的 parsed_value 不因缺个旗被丢）。
    口径必须两边一致，双计数器数的才真是同一件事（packed 的 n_parse_miss）；
    这里先数出来，跑单人不用等装箱就能看到。目录不在（CLI 半路崩了没落
    artifact）给 0，不炸——摘要要尽量写出来。
    """
    n = 0
    if not art_dir.is_dir():
        return 0
    for path in sorted(art_dir.glob("questionnaire_step_*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for resp in doc.get("responses", []):
            for a in resp.get("answers", []):
                if not a.get("parse_success", True) or a.get("parsed_value") is None:
                    n += 1
    return n


def load_profiles(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            f"{path.name} 不在——按装机说明 docs/user/sim-agentsociety.md 第四节，"
            f"把样本档案复制到这份跑单脚本同目录、改名成 {path.name} 再跑"
            f"（默认样本的位置见 oss/sims/agentsociety/profile.json 的 "
            f"sample_base + sample_data）")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"{path.name} 不是合法 JSON：{exc}")


print(f"[cost hint] **未实测**：AgentSociety 是 ReAct 智能体，每 agent 每 "
      f"step 约 3-10 次模型调用，同规模成本约为 OASIS 的数倍（设计稿预警）。"
      f"本次 {N_AGENTS} 人×{ROUNDS} 轮、并发 {CONCURRENCY}——真跑前先小规模"
      f"试水（如 5 人×2 轮）+中转站余额确认，超预算就中断。", flush=True)


def main() -> None:
    base = os.environ.get("BASE")
    key = os.environ.get("KEY")
    if not base or not key:
        raise SystemExit(
            "环境变量 BASE 和/或 KEY 没设——中转站的 URL 和 key 得先设好才能跑，"
            "见顶部 docstring 的用法：BASE=<中转站 URL> KEY=<中转站 key> "
            "python raw/A_1.py")

    profiles = load_profiles(PROFILE_SRC)

    # 删除操作的第二道闩（第一道在顶部验 COND/REP 的名）：rmtree 只许删
    # 跑单脚本旁边的直接子目录，路径解析完不是亲子关系就拒——哪怕第一道
    # 闩哪天被改松，这里也不放行
    if RUN_DIR.resolve().parent != HERE.resolve():
        raise SystemExit(
            f"拒绝清场：{RUN_DIR} 解析后不是 {HERE} 的直接子目录——"
            f"COND/REP 拼出的路径越界了，清场删除只许指着跑单旁边的跑目录")
    if RUN_DIR.exists():
        # 残跑目录整个删掉重来：replay 分片是**追加写**，在旧分片后面续写会
        # 出双份行（插头见到两份不一致的快照直接拒收）；要接着上次跑用上游
        # 自家的 --resume，那超出本跑单的职责——这里只认干净的整场。
        print(f"[清场] {RUN_DIR} 已存在（上次的残跑），整个删掉重来", flush=True)
        shutil.rmtree(RUN_DIR)
    RUN_DIR.mkdir(parents=True)

    (RUN_DIR / "init_config.json").write_text(
        json.dumps(build_init(profiles, N_AGENTS, KNOB_VALUE),
                   ensure_ascii=False, indent=2), encoding="utf-8")
    # steps.yaml 内容是 JSON 文本——JSON 是 YAML 子集，CLI 的 yaml.safe_load
    # 照读（见顶部 docstring），跑单不用多背 pyyaml
    (RUN_DIR / "steps.yaml").write_text(
        json.dumps(build_steps(ROUNDS, TICK, INTERVIEW_WAVES, INTERVIEW_Q),
                   ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env["AGENTSOCIETY_LLM_API_KEY"] = key
    env["AGENTSOCIETY_LLM_API_BASE"] = base
    env["AGENTSOCIETY_LLM_MODEL"] = MODEL_TYPE
    env["AGENTSOCIETY_LLM_RAY_CONCURRENCY"] = str(CONCURRENCY)

    cmd = [sys.executable, "-m", "agentsociety2.society.cli",
           "--config", str(RUN_DIR / "init_config.json"),
           "--steps", str(RUN_DIR / "steps.yaml"),
           "--run-dir", str(RUN_DIR)]
    print(f"[启动] {' '.join(cmd)}", flush=True)

    n_error_lines = 0
    n_money_lines = 0
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        if _is_error_line(line):
            n_error_lines += 1
        money = _money_hit(line)
        if money:
            n_money_lines += 1
        try:
            # stdout 断管（BrokenPipeError）不该杀跑单——OASIS 2026-08-31
            # 质量审实测过的教训；转印只是留痕，计数已经加过
            print(line, flush=True)
            if money:
                print(_money_banner(line), flush=True)
        except Exception:
            pass
    rc = proc.wait()

    # ── run_summary.json ────────────────────────────────────────────
    # advice 不用「错误占比」框架（OASIS 质量审的教训原样继承）：分子是
    # ERROR 级日志行（输出扫描启发式，含框架内部重试，非失败请求数），
    # 分母 agents×rounds 也数不全请求（每 agent 每 step 3-10 次调用），
    # 硬凑百分比会骗人——经验线只当启发式指标用，明说不是失败率。
    miss = count_parse_miss(RUN_DIR / "artifacts")
    threshold = max(1, N_AGENTS * ROUNDS) * 0.05
    if n_error_lines > threshold:
        advice = (f"{n_error_lines} 条 ERROR 级日志行（输出扫描启发式，"
                  f"含框架内部重试，非失败请求数）超过经验线 "
                  f"agents×rounds×5%={threshold:.1f}（启发式指标，非失败率）"
                  f"——建议在 runs.csv 把这场标 complete=no")
    else:
        advice = (f"{n_error_lines} 条 ERROR 级日志行（输出扫描启发式，"
                  f"含框架内部重试，非失败请求数）未超过经验线 "
                  f"agents×rounds×5%={threshold:.1f}（启发式指标，非失败率），"
                  f"暂不建议标 complete=no")
    run_summary = {"model_errors": n_error_lines, "interview_miss": miss,
                   "advice": advice}
    RUN_SUMMARY.write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[run summary] {run_summary}", flush=True)

    # ── meta_sidecar.json：装箱人抄进 sim.meta.json 的侧录 ──────────
    try:
        sim_ver = importlib.metadata.version("agentsociety2")
    except importlib.metadata.PackageNotFoundError:
        sim_ver = "未知（agentsociety2 不在当前环境——CLI 能跑起来的话不该见到这行）"
    sidecar = {
        "simulator": "agentsociety", "simulator_version": sim_ver,
        "model": MODEL_TYPE, "api_base": base,
        "temperature": "provider_default",
        "concurrency": CONCURRENCY, "condition": COND, "rep": REP,
        "condition_knob": {KNOB_KEY: KNOB_VALUE},
        "rounds": ROUNDS, "tick": TICK, "n_agents": N_AGENTS,
        "start_t": START_T,
        "interview_plan": (
            f"开跑前基线一波（wave 0，step_count=0）；此后在第 "
            f"{'、'.join(map(str, INTERVIEW_WAVES)) if INTERVIEW_WAVES else '—'} "
            f"轮结束后各一波；轮=step，一步 tick={TICK} 秒（模拟时钟）；"
            f"题面：{INTERVIEW_Q}"),
        "cli_returncode": rc,
    }
    META_SIDECAR.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[meta sidecar] {META_SIDECAR.name} 已写——装箱前把 model/"
          f"model_provider/rounds/interview_plan 等抄进 sim.meta.json", flush=True)

    if n_money_lines:
        print(_money_banner(f"这场共见到 {n_money_lines} 行钱类信号（详见上方"
                            f"横幅）——全停，先去中转站看余额"), flush=True)
    if rc != 0:
        raise SystemExit(
            f"CLI 子进程退出码 {rc}——这场没跑完/没跑好。证据文件已尽量保留"
            f"（replay/artifacts 落了多少是多少，run_summary 已写），"
            f"别急着装箱，先往上翻日志找原因")
    print(f"[done] condition={COND} rep={REP} {KNOB_KEY}={KNOB_VALUE} "
          f"rounds={ROUNDS} agents={N_AGENTS} run_dir={RUN_DIR.name}",
          flush=True)


if __name__ == "__main__":
    main()
