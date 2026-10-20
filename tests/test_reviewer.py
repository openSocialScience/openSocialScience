"""跨模型审稿的测试。

**这里守的是整套架构的核心契约**：executor 不能给自己判无罪。
所以家族检查的每一条拒绝路径都必须有测试——它一旦松了，
契约就变成一句写在文档里的空话，而且不会有任何东西报错。

真调 API 的标了 `@pytest.mark.llm`（会花钱），默认跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oss.host.reviewer import (
    LEVELS,
    Reviewer,
    ReviewerUnavailable,
    SameFamilyRefused,
    _parse,
    family_of,
)


# ------------------------------------------------------------- 家族识别


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-4", "anthropic"),
        ("claude-3-5-sonnet-20241022", "anthropic"),
        ("gpt-5.6-sol", "openai"),
        ("o3-mini", "openai"),
        ("deepseek-v4-flash", "deepseek"),
        ("deepseek-chat", "deepseek"),
        ("gemini-2.5-pro", "google"),
        ("qwen-max", "qwen"),
        ("glm-4", "zhipu"),
        # 腾讯混元：WorkBuddy 的缺省模型就是它（界面上写 Hy3），
        # 认不出来就是 unknown，而 unknown 一律拒审——整条链会停在审稿这一步
        ("hy3", "tencent"),
        ("tencent/hy3", "tencent"),
        ("hy3-free", "tencent"),
        ("hunyuan-turbos-latest", "tencent"),
        ("", "unknown"),
        ("some-random-model", "unknown"),
    ],
)
def test_家族识别(model: str, expected: str) -> None:
    assert family_of(model) == expected


def test_混元与deepseek是跨家族_与自己是同家族() -> None:
    """在 WorkBuddy 里跑时执行者是混元、审稿人是 DeepSeek——这一对必须放行。

    反过来也要认得出：混元审混元是同家族，照旧拒。
    """
    assert family_of("hy3") != family_of("deepseek-v4-flash")
    assert family_of("hunyuan-turbos-latest") == family_of("tencent/hy3")


# --------------------------------------------------- 家族检查：缺省只标注，严格档才拒绝
#
# 2026-09-07 改：原先同家族（或判不出家族）一律拒绝执行。那条规矩对「验证的可信度」
# 是对的，但它把门槛压在了任何人跑起来之前——新用户得先凑齐两家模型的钥匙才能看见
# 这套东西好在哪。项目要开源，这个成本换不来相应的收益。
# 缺省改成「照审，但把档次记进判词」；`REQUIRE_CROSS_MODEL=1` 恢复老行为。


@pytest.fixture
def strict(monkeypatch):
    """严格档：正式验收跑用的那一档，同家族仍然拒绝。"""
    monkeypatch.setenv("REQUIRE_CROSS_MODEL", "1")


def test_同家族被拒绝(strict) -> None:
    """同一家的模型互审，ARIS 直接称为「非功能」。**严格档下仍然拒绝。**"""
    rev = Reviewer(
        base_url="http://x", model="deepseek-v4-flash",
        api_key="k", executor_model="deepseek-chat",
    )
    with pytest.raises(SameFamilyRefused) as exc:
        rev.check_family()
    assert exc.value.family == "deepseek"
    assert "同家族互审不构成跨模型验证" in str(exc.value)


def test_判不出家族也被拒绝(strict) -> None:
    """**「不知道是不是同一家」和「确定不是同一家」在契约上不等价。**

    判不出就不能声称跨家族——宁可拒绝，不可放行。
    """
    rev = Reviewer(
        base_url="http://x", model="mystery-model-v9",
        api_key="k", executor_model="claude-opus-4",
    )
    with pytest.raises(SameFamilyRefused) as exc:
        rev.check_family()
    assert exc.value.family == "unknown"
    assert "判不出模型家族" in str(exc.value)


def test_没配executor也被拒绝(strict) -> None:
    """不知道宿主跑的是什么，就没法保证跨家族。"""
    rev = Reviewer(base_url="http://x", model="deepseek-chat", api_key="k", executor_model="")
    with pytest.raises(SameFamilyRefused):
        rev.check_family()


def test_显式传空串不会被环境变量顶掉(monkeypatch) -> None:
    """调用方说「我不知道 executor 是谁」，就不该拿到 .env 里的值。

    写成 `x or os.getenv(...)` 会有这个洞——家族检查于是放行一次本该拒绝的调用。
    """
    monkeypatch.setenv("EXECUTOR_MODEL", "claude-opus-4")
    assert Reviewer(executor_model="").executor_model == "", "显式空串被环境变量顶掉了"
    assert Reviewer().executor_model == "claude-opus-4", "没传时应当读环境变量"


def test_跨家族才放行() -> None:
    rev = Reviewer(
        base_url="http://x", model="deepseek-v4-flash",
        api_key="k", executor_model="claude-opus-4",
    )
    assert rev.check_family() == ("anthropic", "deepseek")


def test_家族检查跑在网络调用之前(tmp_path: Path, strict) -> None:
    """同家族时不该先花钱调 API 再发现。base_url 是假的也应当抛家族错误。"""
    f = tmp_path / "x.json"
    f.write_text("{}")
    rev = Reviewer(
        base_url="http://definitely-not-a-real-host.invalid",
        model="deepseek-chat", api_key="k", executor_model="deepseek-v4-flash",
    )
    with pytest.raises(SameFamilyRefused):
        rev.review(f)


# ------------------------------------------------------- 判词解析：不许默认通过


def test_抽不出判词就是ERROR而不是通过() -> None:
    """**这是最危险的失败模式。**模型回了一堆散文没给判词，
    绝不能当成通过。"""
    v = _parse("这份清单看起来还不错，我觉得没什么大问题。")
    assert v.verdict == "ERROR"
    assert "不能当作通过" in v.reason


def test_非法判词也是ERROR() -> None:
    assert _parse("VERDICT: LOOKS_GOOD\nREASON: 挺好").verdict == "ERROR"


def test_六态判词都能解析() -> None:
    for v in ("PASS", "WARN", "FAIL", "BLOCKED", "ERROR", "NOT_APPLICABLE"):
        assert _parse(f"VERDICT: {v}\nREASON: 理由").verdict == v


def test_逐条意见被抽出来() -> None:
    v = _parse("VERDICT: WARN\nREASON: 总评\nFINDINGS:\n- idea_001: 砍掉\n- idea_002: 保留")
    assert v.findings == ["idea_001: 砍掉", "idea_002: 保留"]


# ------------------------------------------------------------- 不可用 ≠ 通过


def test_没配置时抛不可用而不是返回通过() -> None:
    rev = Reviewer(base_url="", model="", api_key="", executor_model="claude-opus-4")
    with pytest.raises(ReviewerUnavailable):
        rev.review(Path("whatever.json"))


def test_文件不存在时抛不可用(tmp_path: Path) -> None:
    rev = Reviewer(
        base_url="http://x", model="deepseek-chat",
        api_key="k", executor_model="claude-opus-4",
    )
    with pytest.raises(ReviewerUnavailable, match="不存在"):
        rev.review(tmp_path / "nope.json")


# ------------------------------------------------------------- token 预算


def test_深度越高token预算越大() -> None:
    """推理模型会先花一批 token 想。给太少的话正文是空的——
    实测 max_tokens=50 时 reasoning 吃掉全部 50，content 为空。"""
    assert LEVELS["quick"] < LEVELS["normal"] < LEVELS["deep"]
    assert LEVELS["quick"] >= 1000, "推理模型的 reasoning 就可能吃掉几百"


# --------------------------------------------------------------- 真调 API


@pytest.mark.llm
def test_真审一次(tmp_path: Path) -> None:
    f = tmp_path / "ideas.json"
    f.write_text(
        '{"direction":"测试","ideas":[{"idea_id":"idea_001","title":"用横截面相关性证明因果",'
        '"research_question":"X 和 Y 相关吗？","theory_hook":"","value_if_null":""}]}',
        encoding="utf-8",
    )
    v = Reviewer().review(f, level="quick", kind="ideas")
    assert v.verdict in {"PASS", "WARN", "FAIL", "BLOCKED", "NOT_APPLICABLE"}
    assert v.cross_family is True
    assert v.reviewer_family != v.executor_family
    # 一个没有理论对话、没有 value_if_null、靠横截面相关性的 idea 不该被判通过
    assert v.verdict != "PASS", f"这个 idea 明显有问题，却被判 PASS：{v.reason}"


# ═══════════════════ 审论文：必须跨产出物比对


def test_审稿能一次收多个文件(tmp_path: Path) -> None:
    """**审论文跟审 idea 不一样：它必须跨产出物比对。**

    「稳健性是不是事后补的」这个问题，只看 `paper.md` 回答不了——
    要对着 `plan.json` 里**事前锁定**的清单看。
    「局限写全没有」也一样，要对着 `power.json` 与 `literature.json` 的 notes 看。

    合并是**机械拼接原文**，不是摘要——
    「一总结就带上了你的口径」这条契约不能破。
    """
    a = tmp_path / "paper.md"
    a.write_text("正文", encoding="utf-8")
    b = tmp_path / "plan.json"
    b.write_text('{"robustness": []}', encoding="utf-8")

    sent = {}

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            sent["content"] = content
            sent["system"] = system
            return {"choices": [{"message": {"content": "VERDICT: PASS\nREASON: ok"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    v = r.review([a, b], kind="paper")
    assert v.verdict == "PASS"
    assert "正文" in sent["content"] and "robustness" in sent["content"]
    assert "paper.md" in sent["content"] and "plan.json" in sent["content"], \
        "每一段要标明来自哪个文件，否则审稿人分不清"


def test_有专门的论文审稿提示词() -> None:
    """通用提示词审不了论文——它不知道该看结论有没有超出设计支持的范围。"""
    from oss.host.reviewer import _PROMPTS

    assert "paper" in _PROMPTS
    p = _PROMPTS["paper"]
    for key in ("局限", "稳健性", "因果", "超出"):
        assert key in p, f"论文审稿提示词里没提「{key}」"


def test_单个文件仍然照旧收(tmp_path: Path) -> None:
    """别把老用法弄坏了。"""
    a = tmp_path / "ideas.json"
    a.write_text("{}", encoding="utf-8")

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            return {"choices": [{"message": {"content": "VERDICT: WARN\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    assert r.review(a, kind="ideas").verdict == "WARN"


# ═══════════════════ 互盲多审稿人（借自 nature-reviewer）


def test_侧重简报会进提示词(tmp_path: Path) -> None:
    """多审稿人各自只拿到**同一份材料 + 自己的侧重**，不给别人的意见。

    借自 `skill reference/nature-skills/skills/nature-reviewer`：
    「Give every reviewer only the same immutable manuscript packet,
    the same journal criteria, and that reviewer's preassigned emphasis.
    Never provide another review, a shared concern ledger, a draft synthesis,
    or hints about what another reviewer noticed.」
    """
    f = tmp_path / "paper.md"
    f.write_text("正文", encoding="utf-8")
    seen = {}

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            seen["system"] = system
            return {"choices": [{"message": {"content": "VERDICT: WARN\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    r.review(f, kind="paper", emphasis="只看识别策略与因果表述")
    assert "只看识别策略与因果表述" in seen["system"]
    assert "本轮侧重" in seen["system"], "侧重要标出来，不能混进通用要求里"


def test_不给侧重时提示词跟原来一样(tmp_path: Path) -> None:
    """单审稿人用法不该被这个功能改变。"""
    from oss.host.reviewer import _PROMPTS

    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    seen = {}

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            seen["system"] = system
            return {"choices": [{"message": {"content": "VERDICT: PASS\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    r.review(f, kind="paper")
    assert seen["system"] == _PROMPTS["paper"]


def test_侧重被记进判词(tmp_path: Path) -> None:
    """**报告要能追溯到它是哪个侧重下产出的**，否则综合时分不清谁说的。"""
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            return {"choices": [{"message": {"content": "VERDICT: WARN\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    v = r.review(f, kind="paper", emphasis="测量与数据")
    assert v.emphasis == "测量与数据"
    assert v.to_dict()["emphasis"] == "测量与数据"


def test_包太大要在发出去之前就拒绝(tmp_path: Path) -> None:
    """**实测踩到过：369 KB 的语料原文整个发出去，网关 524 超时。**

    超时的表现是「审稿后端不可用」——**看起来像后端坏了，其实是包太大**。
    这类误导比直接报错更贵：人会去查网络、查配置、重试，
    而真正该做的是把包缩小。所以要在发出去**之前**就拒绝，并列出各文件大小。
    """
    big = tmp_path / "literature.json"
    big.write_text("x" * 200_000, encoding="utf-8")
    small = tmp_path / "paper.md"
    small.write_text("正文", encoding="utf-8")

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):  # 不该走到这里
            raise AssertionError("包超限了还发出去了")

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    with pytest.raises(ReviewerUnavailable) as exc:
        r.review([small, big], kind="paper")
    msg = str(exc.value)
    assert "超过上限" in msg
    assert "literature.json" in msg, "要列出是哪个文件大，否则不知道该缩哪个"


def test_上限之内照常发(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("正文" * 100, encoding="utf-8")

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            return {"choices": [{"message": {"content": "VERDICT: PASS\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k",
             executor_model="claude-opus-4")
    assert r.review(f, kind="paper").verdict == "PASS"


# ─────────────────────────── 流式：网关等不到第一个字节就挂断


def test_流式回复拼回非流式的结构(monkeypatch, tmp_path: Path) -> None:
    """验证跑 127 分钟里审稿占 77 分钟，七次论文审稿调用五次撞 HTTP 524、三次没拿到判词：
    网关约 100 秒等不到源站第一个字节就挂断，而推理模型先想几分钟再开口。
    流式下源站边想边发（reasoning_content 分片），连接不会被判死。"""
    from contextlib import contextmanager

    import httpx

    lines = [
        'data: {"model":"deepseek-v4-flash","choices":[{"delta":{"reasoning_content":"想…"}}]}',
        'data: {"choices":[{"delta":{"content":"VERDICT: PASS\\n"}}]}',
        'data: {"choices":[{"delta":{"content":"REASON: ok"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
        "data: [DONE]",
    ]

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        def iter_lines(self):
            yield from lines

    sent = {}

    @contextmanager
    def fake_stream(method, url, headers=None, json=None, timeout=None):
        sent["json"] = json
        yield FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    r = Reviewer(base_url="http://x", model="deepseek-v4-flash", api_key="k",
                 executor_model="claude-opus-4")
    data = r._post("sys", tmp_path / "f.md", "body", 100)
    assert data["choices"][0]["message"]["content"] == "VERDICT: PASS\nREASON: ok"
    assert data["usage"]["completion_tokens"] == 5 and data["model"] == "deepseek-v4-flash"
    assert sent["json"]["stream"] is True


def test_网关不认流式就照旧解析整包(monkeypatch, tmp_path: Path) -> None:
    from contextlib import contextmanager

    import httpx

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def read(self):
            return b'{"choices":[{"message":{"content":"VERDICT: PASS"}}],"usage":{"total_tokens":3}}'

    @contextmanager
    def fake_stream(method, url, headers=None, json=None, timeout=None):
        yield FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    r = Reviewer(base_url="http://x", model="deepseek-v4-flash", api_key="k",
                 executor_model="claude-opus-4")
    data = r._post("sys", tmp_path / "f.md", "body", 100)
    assert data["choices"][0]["message"]["content"] == "VERDICT: PASS"
    assert data["usage"]["total_tokens"] == 3


def test_流式下非200仍报后端不可用(monkeypatch, tmp_path: Path) -> None:
    from contextlib import contextmanager

    import httpx

    class FakeResp:
        status_code = 524
        headers = {"content-type": "text/html"}

        def read(self):
            return b"<!DOCTYPE html>"

    @contextmanager
    def fake_stream(method, url, headers=None, json=None, timeout=None):
        yield FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    r = Reviewer(base_url="http://x", model="deepseek-v4-flash", api_key="k",
                 executor_model="claude-opus-4")
    with pytest.raises(ReviewerUnavailable, match="HTTP 524"):
        r._post("sys", tmp_path / "f.md", "body", 100)


# --------------------------------------------------- 缺省档：照审，但档次照实记


def test_缺省不再拦同家族(monkeypatch) -> None:
    """开源要紧的是先能跑起来。同家族照审——但**不许假装它等于跨模型验证**。"""
    monkeypatch.delenv("REQUIRE_CROSS_MODEL", raising=False)
    rev = Reviewer(base_url="http://x", model="deepseek-v4-flash",
                   api_key="k", executor_model="deepseek-chat")
    assert rev.check_family() == ("deepseek", "deepseek"), "缺省不该抛"


def test_缺省也不拦判不出家族(monkeypatch) -> None:
    monkeypatch.delenv("REQUIRE_CROSS_MODEL", raising=False)
    rev = Reviewer(base_url="http://x", model="mystery-v9", api_key="k", executor_model="")
    assert rev.check_family() == ("unknown", "unknown")


def test_三档判词各说各的话() -> None:
    from oss.host.reviewer import validation_label

    cross = validation_label("anthropic", "deepseek")
    same = validation_label("deepseek", "deepseek")
    unknown = validation_label("unknown", "deepseek")

    assert "跨模型" in cross and "不构成" not in cross
    assert "自审" in same and "不构成独立验证" in same and "deepseek" in same
    assert "自审" in unknown and "判不出" in unknown and "不构成独立验证" in unknown
    assert len({cross, same, unknown}) == 3, "三档不能长得一样"


def test_严格档的开关认得出常见写法(monkeypatch) -> None:
    from oss.host.reviewer import require_cross_model

    for on in ("1", "true", "TRUE", "yes", "on"):
        assert require_cross_model({"REQUIRE_CROSS_MODEL": on}), on
    for off in ("", "0", "false", "no", "随便"):
        assert not require_cross_model({"REQUIRE_CROSS_MODEL": off}), off
    assert not require_cross_model({}), "没设就是不要求"
