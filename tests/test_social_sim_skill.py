"""`/social-sim` 说明书的锚点测试——keyless，只读文件、不跑仿真、不碰网络。

照 `tests/test_stage1_skill_rules.py` 的路数：句子点名到具体文件，不是「整份技能里
找找看」。这份技能只有三个文件（SKILL.md / manifest.yaml / reference/run-order.md），
没有 `stages/`、没有 `contract.md`，所以 `_all()` 把三份 `.md` 拼起来断言就够，
不需要像 social-research 那样跨文件搬家的容错写法。

同时借 `tests/test_validate_skills.py` 已验证过的写法：`importlib` 直接加载
`scripts/validate_skills.py`，调用 `validate_all()`——那是这份说明书自己的门，
必须跟全仓库其它说明书一样合法。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
SKILL_DIR = SKILLS / "social-sim"

SKILL_MD = SKILL_DIR / "SKILL.md"
MANIFEST = SKILL_DIR / "manifest.yaml"
RUN_ORDER = SKILL_DIR / "reference" / "run-order.md"

_FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _mod():
    spec = importlib.util.spec_from_file_location(
        "validate_skills", ROOT / "scripts" / "validate_skills.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


V = _mod()


def _read(p: Path) -> str:
    assert p.exists(), f"缺 {p}"
    return p.read_text(encoding="utf-8")


def _all() -> str:
    """三份 `.md` 拼起来（SKILL.md + reference/run-order.md）。"""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILL_DIR.rglob("*.md")))


# ────────────────────────────── 文件存在 ──────────────────────────────

def test_三份文件都在():
    assert SKILL_MD.exists(), "缺 SKILL.md"
    assert MANIFEST.exists(), "缺 manifest.yaml"
    assert RUN_ORDER.exists(), "缺 reference/run-order.md"


# ────────────────────────────── frontmatter ──────────────────────────────

def test_frontmatter四个字段齐():
    t = _read(SKILL_MD)
    m = _FM.match(t)
    assert m, "SKILL.md 没有 frontmatter"
    body = m.group(1)
    for key in ("name", "description", "argument-hint", "allowed-tools"):
        assert re.search(rf"^{key}:", body, re.M), f"frontmatter 缺 {key}"
    name = re.search(r"^name:\s*(\S+)", body, re.M)
    assert name and name.group(1) == "social-sim", "name 跟目录名对不上"


def test_触发词在描述里():
    t = _read(SKILL_MD)
    m = _FM.match(t)
    body = m.group(1)
    desc = re.search(r"^description:\s*(.*)$", body, re.M).group(1)
    for trigger in ("跑仿真", "仿真实验", "/social-sim"):
        assert trigger in desc, f"description 里没有触发词「{trigger}」"


def test_allowed_tools带Bash的oss和python():
    t = _read(SKILL_MD)
    m = _FM.match(t)
    body = m.group(1)
    line = re.search(r"^allowed-tools:\s*(.*)$", body, re.M).group(1)
    assert "Bash(oss *)" in line
    assert "Bash(python *)" in line
    assert "Read" in line
    assert "Write" in line


def test_有ARGUMENTS占位符():
    assert "$ARGUMENTS" in _read(SKILL_MD)


# ────────────────────────────── 拆参数 ──────────────────────────────

def test_拆参数格式在SKILLmd里():
    t = _read(SKILL_MD)
    assert "simulator:" in t
    assert "design:" in t
    assert "budget:" in t
    assert "checkpoint:" in t and "design|none" in t


# ────────────────────────────── 铁律五条 ──────────────────────────────

def test_铁律不花一分钱():
    t = _all()
    assert "不花一分钱" in t
    assert "checkpoint: none" in t


def test_铁律check不过不许import():
    t = _all()
    assert "不跑 `oss sim check` 不许 `import`" in t


def test_铁律402立即全停不重试():
    t = _all()
    assert "402" in t
    assert "立即全停" in t
    assert "重试无用" in t


def test_铁律模板只许填槽位():
    """锚句钉的是那句真正限制性的话——不是散词「SEED_CLAIM」凑巧出现就算数。"""
    t = _all()
    assert "SEED_CLAIM" in t
    assert "整理稿" in t
    # 禁止在生成跑单这一步顺手改常量的那句话，至少要在其中一处原文出现
    restrictive = ("征得用户同意，不能在生成跑单这一步顺手改掉",
                   "征得用户同意，\n不能在这一步顺手改掉")
    assert any(s in t for s in restrictive), "限制性措辞的原句没找到"


def test_铁律入库后同一run():
    """锚句钉的是「后续每条命令都带同一个 --run」这句话，不是随便哪里出现的 --run。"""
    t = _all()
    assert "--run" in t
    assert "后续（包括交给 `/social-research` 续接）的每条命令都带这同一个" in t


# ────────────────────────────── run-order 七步的关键措辞 ──────────────────────────────

def test_整理稿步骤():
    t = _read(RUN_ORDER)
    assert "整理稿" in t
    assert "预计费用" in t or "费用估算" in t
    assert "budget" in t or "预算" in t


def test_体检步骤_doctor永远退出0():
    t = _read(RUN_ORDER)
    assert "oss sim doctor --simulator oasis" in t
    assert "退出码永远是 0" in t or "永远是 0" in t or "始终是 0" in t


def test_建箱步骤命令():
    t = _read(RUN_ORDER)
    assert "oss sim init" in t
    assert "profiles_full.csv" in t


def test_生成跑单_盲替换与七槽():
    t = _read(RUN_ORDER)
    assert "盲替换" in t
    assert "八个空位（七必填一可选）" in t
    for slot in ("CONDITION", "REP", "RECSYS", "ROUNDS", "N_AGENTS",
                 "INTERVIEW_Q", "INTERVIEW_WAVES", "SEMAPHORE"):
        assert slot in t, f"跑单槽位 {slot} 没提到"
    assert "ast" in t.lower()


def test_逐场执行_一次一个与失败处置梯度():
    t = _read(RUN_ORDER)
    assert "一次一个" in t
    assert "整场重跑一次" in t
    assert "减半" in t
    assert "标 complete=no 继续" in t
    assert "run_summary" in t


def test_登记装箱验货入库_complete如实与三信号():
    t = _read(RUN_ORDER)
    assert "complete=no" in t
    assert "oss sim pack --simulator oasis" in t
    assert "oss sim check" in t
    assert "oss sim import" in t and "--run" in t
    # 三信号故事
    assert "run_summary" in t
    assert "packed" in t
    assert ("海关" in t) or ("check" in t)


def test_收尾报告下一步命令():
    t = _read(RUN_ORDER)
    assert "/social-research" in t
    assert "sim:" in t


def test_收尾报告运行目录交接沿用不新建():
    """质量审修订②（T4+5 复核）的发送端锚句：收尾报告必须把 `oss sim import`
    实际用的运行目录路径写进去，并明说续接 `/social-research` 时环节 1 沿用
    这个目录、不新建——铁律⑤"每条命令带同一个 --run"靠这句话在这里兑现。
    接收端（`social-research/stages/1-profile.md` 的分叉句）见
    `tests/test_sim_mainchain_skill.py::test_sim分支步2按来源分叉沿用运行目录`。"""
    t = _read(RUN_ORDER)
    assert "**运行目录交接**" in t
    assert "续接 `/social-research` 时环节 1 **沿用本运行目录**，" in t
    assert "不要新建" in t


def test_信号判别三句_不误杀miss不漏402():
    """spec 复审的关键修复：单独一行 interview-miss 不是重试阶梯的触发条件，
    阶梯只认进程有没有打印 [done]；但 miss 行里的 402/余额字样哪怕进程还活着
    也要立即全停。三句缺一都算说明书没把这道口子讲清楚。"""
    t = _read(RUN_ORDER)
    assert "**不是**" in t
    assert "触发重试阶梯的条件" in t
    assert "打印 `[done]` 之前退出" in t
    assert "哪怕进程还活着" in t
    # 阶梯第一级的触发条件要跟②统一口径，不能再写回「连接类失败」那种含糊措辞
    assert "连接类失败（跑单中途报连接异常、进程死掉）" not in t


def test_体检修完要复检():
    """步 2 的修订：处理完 ✗ 项不能凭印象往下走，要重新跑一遍 doctor 确认。
    T4 起两插头共用一句（--simulator <名>），带哪家体检就重跑哪条。"""
    t = _read(RUN_ORDER)
    assert "重新跑一遍同一条 `oss sim doctor --simulator <名>` 确认这条也变成 `✓` 了" in t


def test_check拒收修复上限两轮():
    """步 6 的修订：修复尝试至多两轮，两轮还拒收就停下来问用户、原样念 problems。"""
    t = _read(RUN_ORDER)
    assert "修复尝试至多 2 轮" in t
    assert "把 problems 原样念给用户看" in t


def test_必问清单_contamination_note与provider():
    """质量审的补漏：这两项 design 原文读不出、也不能替用户猜，必须直接问。"""
    t = _read(RUN_ORDER)
    assert "`contamination_note`" in t
    assert "只有用户能声明" in t
    assert "`model_provider`" in t


def test_十二项其余三项来源固定():
    """simulator_version/model/temperature 不用问用户，来源写死在这三处。"""
    t = _read(RUN_ORDER)
    assert "version_locked" in t
    assert "MODEL_TYPE" in t and "deepseek-v4-flash" in t
    assert "provider_default" in t


def test_费用基准纠偏_单场约1点5元():
    """质量审发现：V0 的 3 元是两场合计，单场基准得除二，不是照抄 3 元。"""
    t = _read(RUN_ORDER)
    assert "约 1.5 元" in t
    assert "两场加起来" in t
    assert "1.5 元 × (本场人数 × 本场轮数)" in t   # 公式底数用纠正后的值


def test_悬挂守卫_十分钟无新行按阶梯第一级():
    t = _read(RUN_ORDER)
    assert "连续 10 分钟没有新行" in t
    assert "阶梯第 1 级" in t


def test_预算复核每场跑完都做且如实说是估算():
    t = _read(RUN_ORDER)
    assert "预算复核" in t
    assert "已经完成的场次数" in t
    assert "网关自己的用量页" in t


def test_miss行新格式带异常文字_传感器落地在模板():
    """判别③要求读 miss 行里的异常文字判 402——模板必须真的打印这段文字，
    这一测把说明书的措辞和模板的实现绑在一起，防止两边其中一边漂移。"""
    tmpl = (ROOT / "oss" / "sims" / "oasis" / "runner_template.py").read_text(encoding="utf-8")
    assert "str(exc)[:200]" in tmpl
    t = _read(RUN_ORDER)
    assert "异常文字前200字" in t


def test_docs用户引用全部指得到真文件():
    """正文散文里裸写的 `docs/user/*.md`（不是 Markdown 链接语法，
    validate_skills 的裸链检查认不出这种写法）逐一核实文件真的在——
    堵的是「文档改名/挪走，说明书里的引用悄悄烂掉」这个盲区。"""
    pattern = re.compile(r"docs/user/[\w.\-]+\.md")
    matches = pattern.findall(_read(SKILL_MD)) + pattern.findall(_read(RUN_ORDER))
    assert len(matches) >= 5, f"引用数量看着不对，只找到 {len(matches)} 处：{matches}"
    for rel in matches:
        assert (ROOT / rel).exists(), f"引用 {rel} 指不到真文件"


# ────────────────────────────── 多模拟器：agentsociety 岔路口（T4） ──────────────────────────────

def test_simulator枚举_两插头且KNOWN是权威():
    """拆参数的 simulator 行不再写「目前只有 oasis」——枚举两插头，
    名单权威仍指 oss/sims/__init__.py 的 KNOWN。"""
    t = _read(SKILL_MD)
    assert "`oasis` 和 `agentsociety` 两个插头" in t
    assert "`KNOWN` 为准" in t
    assert "目前只有 `oasis`" not in t


def test_一份手册不是两份():
    """设计意图：七步只有一套，岔路口按「分模拟器」标注，没标的段落两家一样办。"""
    t = _read(RUN_ORDER)
    assert "**这是一份手册，不是两份**" in t
    assert "docs/user/sim-agentsociety.md" in t


def test_整理稿_agentsociety三个新旋钮行():
    """步 1 表格：并发旋钮分家（SEMAPHORE vs CONCURRENCY→RAY 环境变量），
    CONDITION_KNOB 与 TICK 两行仅 agentsociety。"""
    t = _read(RUN_ORDER)
    assert "OASIS 是第八空位 `SEMAPHORE`" in t
    assert "agentsociety 是第九空位 `CONCURRENCY`" in t
    assert "AGENTSOCIETY_LLM_RAY_CONCURRENCY" in t
    assert "CONDITION_KNOB（仅 agentsociety）" in t
    assert "`feed_source`" in t and "`global`" in t and "`following`" in t
    assert "TICK（仅 agentsociety）" in t
    assert "缺省 900" in t


def test_费用_agentsociety未实测框架与先小场铁则():
    """agentsociety 费用未实测：只给 N_AGENTS×ROUNDS×k 框架与 3-10 倍量级预警，
    不编元数；先小场试水+回填指路 sim-agentsociety.md §七。"""
    t = _read(RUN_ORDER)
    assert "N_AGENTS × ROUNDS × k" in t
    assert "k 取 3-10" in t
    assert "3-10 倍量级" in t
    assert "不许出现具体元数" in t
    assert "先小规模试水（如 5 人 × 2 轮）" in t
    assert "`docs/user/sim-agentsociety.md` §七" in t


def test_体检_分模拟器命令与装机单指路():
    t = _read(RUN_ORDER)
    assert "oss sim doctor --simulator agentsociety" in t
    assert "doctor 在错的环境里找包" in t


def test_建箱_agentsociety样本档案与侧录():
    """步 3：样本拷贝分模拟器（profiles_full.json，从 agentsociety 克隆拷）；
    model 等固定来源可对 meta_sidecar.json 照抄核对。"""
    t = _read(RUN_ORDER)
    assert "profiles_full.json" in t
    assert "packages/agentsociety2/profiles.json" in t
    assert "`load_profiles()`" in t
    assert "`meta_sidecar.json`" in t


def test_生成跑单_agentsociety九空位与并排跑目录():
    t = _read(RUN_ORDER)
    assert "九个空位（八必填一可选）" in t
    assert "oss/sims/agentsociety/runner_template.py" in t
    assert "并排的跑目录" in t
    assert "`KNOB_MODULE`/`KNOB_KEY`" in t


def test_逐场执行_agentsociety信号表如实():
    """步 5：agentsociety 没有 steps.jsonl、不逐行报 miss；[done] 只在 CLI
    成功退出后打印；run_summary 落在跑目录里，两个同名字段口径注记。"""
    t = _read(RUN_ORDER)
    assert "没有 steps.jsonl" in t
    assert "也没有逐条的 `[interview-miss]` 行" in t
    assert "`[cost hint]`" in t
    assert "只在 CLI 成功退出后才打印" in t
    assert "raw/<条件>_<rep>/run_summary.json" in t
    assert "ERROR 级日志行数" in t
    assert "含框架内部重试" in t


def test_钱类词表加长版_403quota马甲两家通用():
    """判别③的词表升级：403+quota、PermissionDenied、token quota is not enough
    都算断粮——2026-09-01 OASIS 实跑撞过的马甲，两家场次通用。"""
    t = _read(RUN_ORDER)
    assert "token quota is not enough" in t
    assert "403 配 quota" in t
    assert "PermissionDenied" in t
    assert "马甲" in t


def test_装箱_两家pack同关卡不同配料单():
    """步 6：pack 命令按模拟器换名；check/import 不带 --simulator（海关不认识
    模拟器——多一个模拟器不多一套海关）；agentsociety 的 packed 是独立重数。"""
    t = _read(RUN_ORDER)
    assert "oss sim pack --simulator agentsociety" in t
    assert "n_parse_miss" in t
    assert "独立重数" in t
    assert "多一个模拟器，不多一套海关" in t
    assert "`file` 列也分模拟器" in t
    assert "一场跑的**目录**" in t


# ────────────────────────────── manifest ──────────────────────────────

def test_manifest三段式且name对得上():
    d = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert d.get("name") == "social-sim"
    assert "always_load" in d
    assert "axes" in d
    assert "references" in d and "on_demand" in d["references"]
    assert any("run-order.md" in p for p in d["always_load"])


def test_manifest指到货箱标准与安装单():
    d = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    paths = [r.get("path", "") for r in d["references"]["on_demand"]]
    assert any("sim-box.md" in p for p in paths)
    assert any("sim-oasis.md" in p for p in paths)
    assert any("sim-agentsociety.md" in p for p in paths)


def test_manifest链接全部指得到真文件():
    problems = V._check_manifest(SKILL_DIR)
    assert problems == [], "\n".join(problems)


# ────────────────────────────── 全仓库说明书校验器 ──────────────────────────────

def test_validate_skills全仓库通过():
    problems = V.validate_all(SKILLS)
    assert problems == [], "\n".join(problems)
