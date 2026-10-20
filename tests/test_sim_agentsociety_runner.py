"""AgentSociety 跑单模板 + profile.json 的自检（仿 OASIS 八空位测那套办法）。

模板是数据不是代码：九个花括号空位（八必填一可选）。测试全程**不执行跑的
那条路**（main()、CLI 子进程、网络一概不碰）——只做三件事：
① 对未填/盲替换后的源码 ast.parse（语法必须一直合法）；
② exec 模块顶层（顶层无文件系统/网络副作用——仅打印一行 [cost hint]；
   其余是常量、守卫、纯函数定义），未填时守卫必须 SystemExit 点名，
   填好后取出纯函数直接调；
③ 纯函数（steps 编排、init 编排、parse-miss 计数、钱类/错误行判定）在
   受控输入上验行为——测的是模板真跑时用的那段代码，不是测试手抄的一份。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "oss/sims/agentsociety/runner_template.py"
PROFILE = REPO / "oss/sims/agentsociety/profile.json"

#: 八个必填空位的盲替换表（第九个 CONCURRENCY 可选，特意不填）
FILL = {
    "{{CONDITION}}": "A",
    "{{REP}}": "1",
    "{{CONDITION_KNOB}}": "global",
    "{{ROUNDS}}": "10",
    "{{TICK}}": "900",
    "{{N_AGENTS}}": "20",
    "{{INTERVIEW_Q}}": "你在多大程度上同意该说法？请只回答 1 到 7 的一个数字。",
    "{{INTERVIEW_WAVES}}": "5,10",
}

MANDATORY_SLOTS = ("CONDITION", "REP", "CONDITION_KNOB", "ROUNDS", "TICK",
                   "N_AGENTS", "INTERVIEW_Q", "INTERVIEW_WAVES")


def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _filled(src: str | None = None, extra: dict | None = None) -> str:
    out = src if src is not None else _src()
    for k, v in (FILL | (extra or {})).items():
        out = out.replace(k, v)
    return out


def _exec_ns(source: str, tmp_path: Path) -> dict:
    """exec 模块顶层进受控命名空间：给假 __file__（顶层无文件系统/网络副作用，
    仅打印——Path 只做算术不碰盘），__name__ 不是 __main__ 所以 main() 不会跑。"""
    ns = {"__name__": "runner_template_under_test",
          "__file__": str(tmp_path / "raw" / "A_1.py")}
    exec(compile(source, "runner_template_filled", "exec"), ns)
    return ns


# ── 槽位清单与守卫


def test_模板语法合法且九槽位齐():
    src = _src()
    ast.parse(src)                        # 未填的模板本身必须是合法 Python
    for name in MANDATORY_SLOTS + ("CONCURRENCY",):
        assert "{{" + name + "}}" in src, f"缺空位字面量 {name}"
    assert "数据不是代码" in src


def test_未填模板exec被守卫拦下点名():
    with pytest.raises(SystemExit) as exc:
        _exec_ns(_src(), Path("/tmp"))
    msg = str(exc.value)
    for name in MANDATORY_SLOTS:
        assert name in msg
    assert "占位符" in msg
    # 第九槽可选，没填不算「跑单没填好」，不该被守卫点名
    assert "CONCURRENCY" not in msg


def test_条件与重复槽验名_路径越界当场拒(tmp_path):
    """九个槽里唯二拼进删除路径的是 COND/REP（RUN_DIR 喂给 rmtree 清残跑）——
    带路径分隔符/点/绝对路径的值能把删除目标指到 raw/ 外面去，必须在顶层
    守卫处当场 SystemExit（什么都还没删），不能等 main()。"""
    with pytest.raises(SystemExit, match="CONDITION"):
        _exec_ns(_filled(extra={"{{CONDITION}}": "../.."}), tmp_path)
    with pytest.raises(SystemExit, match="CONDITION"):
        _exec_ns(_filled(extra={"{{CONDITION}}": "../../../etc"}), tmp_path)
    with pytest.raises(SystemExit, match="REP"):
        _exec_ns(_filled(extra={"{{REP}}": "/etc"}), tmp_path)
    # 正常中文条件名照收（\\w 的 Unicode 口径盖住中文）
    ns = _exec_ns(_filled(extra={"{{CONDITION}}": "热度推荐"}), tmp_path)
    assert ns["COND"] == "热度推荐"
    # 第二道闩也在：rmtree 之前的「只许删亲目录」断言（main() 里，源码字面钉住）
    src = _src()
    assert "RUN_DIR.resolve().parent" in src and "HERE.resolve()" in src


def test_盲替换八槽位后语法合法且解析正确(tmp_path):
    filled = _filled()
    ast.parse(filled)                     # 盲替换后仍必须是合法 Python
    ns = _exec_ns(filled, tmp_path)
    assert ns["COND"] == "A" and ns["REP"] == "1"
    assert ns["KNOB_VALUE"] == "global"
    assert ns["ROUNDS"] == 10 and ns["TICK"] == 900 and ns["N_AGENTS"] == 20
    assert ns["INTERVIEW_WAVES"] == [5, 10]
    assert ns["INTERVIEW_Q"].startswith("你在多大程度上")
    # 第九槽没填：缺省 8（保守值，不是上游的 16——理由见模板注释）
    assert ns["CONCURRENCY"] == 8
    # 第九槽也填了的话：换算走 int()，不是缺省
    ns16 = _exec_ns(_filled(extra={"{{CONCURRENCY}}": "16"}), tmp_path)
    assert ns16["CONCURRENCY"] == 16
    # 合法幸存者：守卫自己的字面量盲替换不该碰
    assert 'val.startswith("{{")' in filled


# ── steps 编排（run/questionnaire 按波次交错）


def test_steps编排_基线波加交错波次(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    doc = ns["build_steps"](10, 900, [5, 10], "题面")
    assert doc["start_t"] == ns["START_T"]
    kinds = [s["type"] for s in doc["steps"]]
    assert kinds == ["questionnaire", "run", "questionnaire", "run",
                     "questionnaire"]
    runs = [s for s in doc["steps"] if s["type"] == "run"]
    assert [s["num_steps"] for s in runs] == [5, 5]     # num_steps 之和 = ROUNDS
    assert all(s["tick"] == 900 for s in runs)
    qs = [s for s in doc["steps"] if s["type"] == "questionnaire"]
    assert [q["questionnaire_id"] for q in qs] == ["wave_0", "wave_1", "wave_2"]
    q0 = qs[0]["questions"][0]
    # 字段名照上游 QuestionItem（society/models.py）：id/prompt/response_type/choices
    assert set(q0) == {"id", "prompt", "response_type", "choices"}
    assert q0["prompt"] == "题面" and q0["response_type"] == "integer"
    # 波次没到头：尾巴补一段 run，问卷只在指定轮后
    doc2 = ns["build_steps"](10, 60, [4], "题面")
    assert [s["type"] for s in doc2["steps"]] == ["questionnaire", "run",
                                                  "questionnaire", "run"]
    assert [s["num_steps"] for s in doc2["steps"] if s["type"] == "run"] == [4, 6]
    # 一波额外问卷都不加：基线 + 一整段 run
    doc3 = ns["build_steps"](3, 60, [], "题面")
    assert [s["type"] for s in doc3["steps"]] == ["questionnaire", "run"]
    assert doc3["steps"][1]["num_steps"] == 3


def test_steps编排_坏波次拒收人话(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    with pytest.raises(SystemExit, match="严格递增"):
        ns["build_steps"](10, 60, [5, 4], "题面")
    with pytest.raises(SystemExit, match="严格递增"):
        ns["build_steps"](10, 60, [0, 5], "题面")
    with pytest.raises(SystemExit, match="严格递增"):
        ns["build_steps"](10, 60, [11], "题面")
    with pytest.raises(SystemExit, match="ROUNDS"):
        ns["build_steps"](0, 60, [], "题面")
    with pytest.raises(SystemExit, match="TICK"):
        ns["build_steps"](10, 0, [], "题面")


# ── init 编排（profiles 切片 + 条件旋钮注入）


def test_init编排_按记录切片且旋钮注入(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    profiles = [{"id": i, "age": 20 + i, "gender": "F"} for i in range(1, 6)]
    doc = ns["build_init"](profiles, 3, "following")
    assert doc["env_modules"] == [{"module_type": "social_media",
                                   "kwargs": {"feed_source": "following"}}]
    assert len(doc["agents"]) == 3
    assert doc["agents"][0] == {"agent_id": 1, "agent_type": "PersonAgent",
                                "kwargs": {"id": 1, "age": 21, "gender": "F"}}


def test_init编排_样本不够或缺id拒收人话(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    profiles = [{"id": i} for i in range(1, 4)]
    with pytest.raises(SystemExit, match="不够"):
        ns["build_init"](profiles, 9, "global")
    with pytest.raises(SystemExit, match="id"):
        ns["build_init"]([{"age": 30}], 1, "global")
    with pytest.raises(SystemExit, match="JSON 数组"):
        ns["build_init"]({"id": 1}, 1, "global")


# ── run_summary 的两个计数（parse 失败为 miss 类比、ERROR 行启发式）


def test_parse失败计数为miss类比(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    art = tmp_path / "artifacts"
    art.mkdir()

    def _art(name, answers):
        (art / name).write_text(json.dumps({
            "responses": [{"agent_id": 1,
                           "answers": [{"question_id": "q_main",
                                        **a} for a in answers]}],
        }, ensure_ascii=False), encoding="utf-8")

    _art("questionnaire_step_0_20260101.json",
         [{"parse_success": True, "parsed_value": 5},
          {"parse_success": False, "parsed_value": None},   # 明旗失败：miss
          {"parse_success": True, "parsed_value": 3}])
    _art("questionnaire_step_2_20260101.json",
         [{"parse_success": False, "parsed_value": None},   # 明旗失败：miss
          # 缺旗但值在（两条）：按成功算——判定与插头 adapter 逐字同款
          # （get("parse_success", True) or parsed_value is None），
          # 有可用的 parsed_value 不因缺个旗被丢；旧口径「缺旗即 miss」
          # 会把总数数成 5，新旧口径在这组样本上分得开
          {"parsed_value": 4},
          {"parsed_value": 7},
          {"parse_success": True, "parsed_value": None}])   # 旗好值空：miss（插头同款）
    (art / "ask_step_1_20260101.md").write_text("不是问卷", encoding="utf-8")
    assert ns["count_parse_miss"](art) == 3
    # artifacts 目录压根不在（CLI 半路崩了）：计数照常给 0，不炸
    assert ns["count_parse_miss"](tmp_path / "没有这个目录") == 0


def test_ERROR行启发式判定(tmp_path):
    ns = _exec_ns(_filled(), tmp_path)
    is_err = ns["_is_error_line"]
    assert is_err("[2026-09-03 10:00:00] ERROR   Error executing step 2")
    assert is_err("[2026-09-03 10:00:00] CRITICAL boom")
    assert is_err("(EnvRouterActor pid=42) [2026-09-03 10:00:00] ERROR   x")
    assert not is_err("[2026-09-03 10:00:00] INFO    Running 5 steps")
    assert not is_err("plain ERROR word without logger prefix")


def test_advice措辞锚句_启发式不是失败率():
    """盲替换后，诚实叫法仍要在——防止以后有人把「N 条 ERROR 级日志行
    （输出扫描启发式）」改回「错误占比 X%」（OASIS 2026-08-31 质量审的教训）。"""
    filled = _filled()
    assert "输出扫描启发式" in filled
    assert "含框架内部重试，非失败请求数" in filled
    assert "启发式指标，非失败率" in filled
    assert "complete=no" in filled
    assert "run_summary.json" in filled and "meta_sidecar.json" in filled
    assert "model_errors" in filled and "interview_miss" in filled
    assert "advice" in filled


# ── 钱类信号词表


def test_钱类词表在_含403quota变体(tmp_path):
    src = _src()
    # 词表逐条字面在（含 403+quota 复合判定原文），宁漏勿滥的取舍注释也在
    for token in ('"402"', '"Insufficient"', '"余额"', '"欠费"',
                  '"PermissionDenied"', '"token quota is not enough"'):
        assert token in src, f"钱类词表缺 {token}"
    assert '"403" in line and "quota" in line' in src
    assert "宁漏勿滥" in src
    # 见钱不重试、全停——银行家规矩与 /social-sim 说明书同一口径
    assert "全停" in src and "不自动重试" in src

    hit = _exec_ns(_filled(), tmp_path)["_money_hit"]
    assert hit("Error code: 402 - Insufficient Balance")
    assert hit("Error code: 403 - token quota is not enough for model X")
    assert hit("403 ... quota exceeded")
    assert hit("账户余额不足，请充值")
    assert hit("已欠费停机")
    assert hit("openai.PermissionDeniedError: ...")
    assert not hit("HTTP 403 Forbidden")          # 裸 403 是权限类，不配 quota 不算钱
    assert not hit("[10:00:00] INFO    step 7 done")


# ── 环境变量、CLI 形态与差异声明


def test_环境变量与并发缺省与CLI形态():
    src = _src()
    # BASE/KEY 进 AGENTSOCIETY_*（与 OASIS 跑单同一套操作习惯）
    assert 'os.environ.get("BASE")' in src and 'os.environ.get("KEY")' in src
    for var in ("AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE",
                "AGENTSOCIETY_LLM_MODEL", "AGENTSOCIETY_LLM_RAY_CONCURRENCY"):
        assert var in src
    # 并发缺省 8 的理由要写明：上游 16 是 AIMD 自适应初值，本仓中转站史 32 压垮过
    assert "16" in src and "32" in src
    # CLI 子进程正门（不 import agentsociety2 的内部类）
    assert "agentsociety2.society.cli" in src
    assert "--run-dir" in src and "--config" in src and "--steps" in src
    assert "\nimport agentsociety2" not in src
    assert "\nfrom agentsociety2" not in src


def test_docstring差异声明与布局约定():
    src = _src()
    doc = ast.get_docstring(ast.parse(src)) or ""
    # 与 OASIS 模板的差异及理由（无内置重试网——它自有 AIMD 并发自适应）
    assert "AIMD" in doc and "OASIS" in doc
    assert "重试" in doc
    # 填好的脚本放 raw/<条件>_<第几次>.py，与跑目录并排——check_box 的证据要写明
    assert "check_box" in doc and "raw/" in doc
    # steps.yaml 写 JSON 文本的理由（JSON 是 YAML 子集，不多背 pyyaml）
    assert "YAML" in doc and "JSON" in doc
    # 成本预警：未实测，量级说明
    assert "未实测" in src and "3-10" in src


# ── profile.json（停车场 2：doctor 裸抛 FileNotFoundError 的洞在这里关）


def test_profile档案齐且doctor不再裸抛():
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["name"] == "agentsociety"
    assert data["version_locked"] == "2.8.4"
    assert data["pip_package"] == "agentsociety2"
    assert data["defaults"]["concurrency"] == 8
    assert data["condition_knob"]["kwarg"] == "feed_source"
    assert "sample_data" in data
    notes = "\n".join(data["notes"])
    assert "未实测" in notes and "3-10" in notes     # 成本预警
    assert "8" in notes and "16" in notes            # 并发缺省的理由链
    # doctor 走通（检查项可以不过——只报事实；但不许 FileNotFoundError 裸炸）
    from oss.api import sim as sim_api
    out = sim_api.doctor("agentsociety")["data"]
    assert out["simulator"] == "agentsociety"
    assert out["version_locked"] == "2.8.4"
    assert len(out["checks"]) >= 3
