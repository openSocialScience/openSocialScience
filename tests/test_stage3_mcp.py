"""MCP 服务器：握手、列工具、调工具。**离线，两条路径都真跑。**

进程内那一组测协议本身；子进程那一条测真正的入口
（`python -m oss.cli.main mcp serve`，也就是宿主配置里 `oss mcp serve` 那条命令）——
只测进程内的话，typer 那一层接错了没人知道。

还有一组测的是**工具表与 api 函数签名对不对得上**。工具表是手写的 JSON Schema，
而 api 的签名会变；对不上时的表现是「模型递了一个参数，服务器回 TypeError」——
它自己看不出是谁的错，只会换个参数再试一遍。所以这里用 `inspect` 机械地核：
schema 里的每个参数都得是函数真收的，函数每个没有缺省值的参数都得在 required 里。

最后一组把 `docs/user/mcp.md` 与 `docs/user/mcp-snippet.json` 也拴上：文档里的工具表
两个方向都查——**列了但代码里没有，比不列更糟**，模型会去调一个不存在的东西，
然后自己编一套解释。
"""

from __future__ import annotations

import inspect
import io
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from oss.api.result import KEYS as RESULT_KEYS
from oss.mcp import server as srv
from oss.mcp.tools import TOOLS

REPO = Path(__file__).resolve().parents[1]


def _req(mid, method, **params):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}


def _payload(reply: dict) -> dict:
    """从 tools/call 的返回里取出那份统一形状的字典。

    顺手钉住键集合：`structuredContent` 里多冒一个键，宿主那边就多一样没人
    解释得了的东西。判据取 `oss/api/result.py` 的 KEYS——**不在这里手抄一份**，
    抄的那一份改了 result.py 也不会红。
    """
    res = reply["result"]
    assert res["content"][0]["type"] == "text"
    text = json.loads(res["content"][0]["text"])
    assert res["structuredContent"] == text, "双通道必须出自同一次计算"
    assert set(text) <= set(RESULT_KEYS), f"多了 {sorted(set(text) - set(RESULT_KEYS))}"
    return text


# ─────────────────────────── 协议


def test_握手回协议版本与工具能力():
    r = srv.handle(_req(1, "initialize", protocolVersion="2025-06-18",
                        capabilities={}, clientInfo={"name": "t", "version": "0"}))
    assert r["id"] == 1 and r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["capabilities"]["tools"] is not None
    assert r["result"]["serverInfo"]["name"] == "oss"
    assert "run" in r["result"]["instructions"]


def test_客户端要老版本就回老版本():
    r = srv.handle(_req(1, "initialize", protocolVersion="2024-11-05"))
    assert r["result"]["protocolVersion"] == "2024-11-05"


def test_不认识的版本回服务器缺省():
    r = srv.handle(_req(1, "initialize", protocolVersion="1999-01-01"))
    assert r["result"]["protocolVersion"] == srv.DEFAULT_PROTOCOL


def test_通知不回复():
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_不认识的方法回JSONRPC错误():
    r = srv.handle(_req(9, "resources/list"))
    assert r["error"]["code"] == -32601


def test_ping有回复():
    assert srv.handle(_req(2, "ping"))["result"] == {}


def test_没有id的请求一律不回包():
    """JSON-RPC 2.0 §4.1：没有 id 的就是**通知**，一个包都不许回。

    这个判断从前排在方法分发之后，于是无 id 的 `ping` 先被 ping 分支接走，
    回了一个 `"id": null` 的包——协议不允许，而宿主那边表现成一条对不上号的回复。
    """
    assert srv.handle({"jsonrpc": "2.0", "method": "ping"}) is None
    assert srv.handle({"jsonrpc": "2.0", "method": "initialize", "params": {}}) is None
    assert srv.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None
    assert srv.handle({"jsonrpc": "2.0", "method": "resources/list"}) is None


def test_坏JSON与批量请求都回错误不崩():
    """2025-06-18 取消了批量请求。收到列表要明说不支持，不能装作收下了。"""
    fin = io.StringIO('{"jsonrpc": "2.0"\n[{"jsonrpc":"2.0","id":1,"method":"ping"}]\n')
    fout = io.StringIO()
    srv.serve(fin, fout)
    out = [json.loads(x) for x in fout.getvalue().splitlines() if x.strip()]
    assert [o["error"]["code"] for o in out] == [-32700, -32600]


# ─────────────────────────── 工具表


def test_提案第七节点名的工具一个不少():
    want = {"oss_profile", "oss_power", "oss_run", "oss_route", "oss_review",
            "oss_paper_check", "oss_paper_render", "oss_paper_export",
            "oss_loop_status", "oss_schema", "oss_anchor_switch", "oss_eval"}
    assert want <= set(TOOLS)


def test_计划之外补上的四个也在():
    """`lit verify` / `data info` / `data columns` / `oss eval --behavior`
    在计划里被划到「不抽」，但它们都已经落进 api 了——落了就该暴露，
    不然模型只能靠 shell 去调命令行，而 MCP 宿主里往往没有 shell。"""
    assert {"oss_lit_verify", "oss_data_info", "oss_data_columns",
            "oss_eval_behavior"} <= set(TOOLS)


def test_每个工具都有描述和合法的schema():
    r = srv.handle(_req(3, "tools/list"))
    tools = r["result"]["tools"]
    assert len(tools) == len(TOOLS)
    for t in tools:
        assert t["name"].startswith("oss_") and t["description"].strip()
        s = t["inputSchema"]
        assert s["type"] == "object" and isinstance(s["properties"], dict)
        assert s["additionalProperties"] is False
        for req in s.get("required", []):
            assert req in s["properties"], f"{t['name']} 的 required 指到不存在的参数"
        for name, spec in s["properties"].items():
            assert spec.get("description", "").strip(), f"{t['name']}.{name} 没有说明"


def test_工具名和函数一一对应不重不漏():
    assert len(set(id(t.fn) for t in TOOLS.values())) == len(TOOLS)


def test_schema里的参数都是函数真收的():
    """手写 schema 对着会变的签名，只能机械地核。"""
    for tool in TOOLS.values():
        sig = inspect.signature(tool.fn)
        params = sig.parameters
        var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
        for name in tool.properties:
            assert var_kw or name in params, f"{tool.name} 的 {name} 不是 {tool.fn} 的参数"
            if name in params:
                assert params[name].kind is not params[name].POSITIONAL_ONLY


def test_没有缺省值的参数一个不落地在required里():
    for tool in TOOLS.values():
        for name, p in inspect.signature(tool.fn).parameters.items():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if p.default is p.empty:
                assert name in tool.required, f"{tool.name} 少了必填参数 {name}"
        for name in tool.required:
            assert name in tool.properties


def test_审稿的key不是参数():
    """递进来就等于让它出现在对话历史和留痕里。"""
    props = TOOLS["oss_review"].properties
    for bad in ("reviewer", "api_key", "key", "base_url", "model"):
        assert bad not in props


def test_json选项一个都不暴露():
    """MCP 本来就是 JSON；再给一个 --json 开关只会让模型犹豫。"""
    for tool in TOOLS.values():
        assert "json_out" not in tool.properties and "json" not in tool.properties


# ─────────────────────────── 调工具


def test_oss_schema调得通形状对():
    r = srv.handle(_req(4, "tools/call", name="oss_schema", arguments={"artifact": "plan"}))
    assert r["result"]["isError"] is False
    d = _payload(r)
    assert d["ok"] is True and d["data"]["kind"] == "schema"


def test_oss_loop_status在临时目录上跑(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = srv.handle(_req(5, "tools/call", name="oss_loop_status",
                        arguments={"run": str(run)}))
    assert r["result"]["isError"] is False
    d = _payload(r)
    assert d["decision"]["decision"] == "review_first"
    assert d["data"]["exit_code"] == 0
    assert (run / "loop_state.json").exists(), "MCP 调一次，盘上要跟命令行一样"


def test_门拒绝时isError并且退出码在data里(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = srv.handle(_req(6, "tools/call", name="oss_anchor_switch",
                        arguments={"run": str(run), "to": "x", "why": "y"}))
    assert r["result"]["isError"] is True
    d = _payload(r)
    assert d["ok"] is False and d["data"]["exit_code"] == 2
    assert d["data"]["kind"] == "refused" and "用户授权" in d["summary"]


def test_非零退出码但确实跑完了不算isError(tmp_path):
    """`oss eval` must 级没过是**成功地报告了一个坏消息**，不是调用失败。"""
    run = tmp_path / "r"; run.mkdir()
    r = srv.handle(_req(7, "tools/call", name="oss_eval", arguments={"run": str(run)}))
    assert r["result"]["isError"] is False
    d = _payload(r)
    assert d["ok"] is False and d["data"]["exit_code"] == 7


def test_不认识的工具不抛异常():
    r = srv.handle(_req(8, "tools/call", name="oss_nope", arguments={}))
    assert r["result"]["isError"] is True
    assert "没有叫 oss_nope 的工具" in _payload(r)["summary"]


def test_参数不对不抛异常():
    r = srv.handle(_req(8, "tools/call", name="oss_schema", arguments={"wrong": 1}))
    assert r["result"]["isError"] is True
    assert "参数不对" in _payload(r)["summary"]


def test_崩了也回一份能解析的返回(monkeypatch):
    """宿主看见一个断掉的管道，比看见一句错误难查得多。"""
    def boom(**_):
        raise RuntimeError("炸了")
    # Tool 是 frozen 的（工具表在导入时定死，不该有人在运行时改它），
    # 所以换的是表里的那一格，不是那一格里的字段
    monkeypatch.setitem(TOOLS, "oss_data_list",
                        replace(TOOLS["oss_data_list"], fn=boom))
    r = srv.handle(_req(8, "tools/call", name="oss_data_list", arguments={}))
    assert r["result"]["isError"] is True
    d = _payload(r)
    assert "RuntimeError" in d["summary"] and d["data"]["exit_code"] == 1


def test_没给arguments当成空字典():
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "oss_data_list"}})
    assert r["result"]["isError"] is False


def test_oss_route只给run是查看(tmp_path):
    """一个工具三条 api：不给 apply/dismiss/disclose 就是查看，**不是处置**。"""
    run = tmp_path / "r"; run.mkdir()
    r = srv.handle(_req(1, "tools/call", name="oss_route", arguments={"run": str(run)}))
    assert r["result"]["isError"] is False
    d = _payload(r)
    assert d["data"]["action"] == "" and d["data"]["exit_code"] == 0
    assert d["data"]["items"] == []


def test_oss_route同时给两个处置是参数错(tmp_path):
    """判据在 `route_api.dispatch` 里，CLI 与这里共用同一份——
    从前两边各写一遍 `if apply / elif dismiss`，静默只做第一个还报成功。"""
    run = tmp_path / "r"; run.mkdir()
    r = srv.handle(_req(1, "tools/call", name="oss_route",
                        arguments={"run": str(run), "apply": "a", "disclose": "b",
                                   "why": "w"}))
    assert r["result"]["isError"] is True
    d = _payload(r)
    assert "一次只做一种处置" in d["summary"]
    assert d["data"]["kind"] == "bad_input" and d["data"]["exit_code"] == 2
    assert d["problems"] == ["--apply a", "--disclose b"]


# ─────────────────────────── 子进程：真正的入口


def test_子进程管道跑通握手加两次调用(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    lines = [
        json.dumps(_req(1, "initialize", protocolVersion="2025-06-18")),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(_req(2, "tools/list")),
        json.dumps(_req(3, "tools/call", name="oss_loop_status", arguments={"run": str(run)})),
    ]
    p = subprocess.run([sys.executable, "-m", "oss.cli.main", "mcp", "serve"],
                       input="\n".join(lines) + "\n", capture_output=True, text=True,
                       cwd=REPO, timeout=120)
    assert p.returncode == 0, p.stderr
    out = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert len(out) == 3, f"通知不该有回复：{out}"
    assert out[0]["result"]["serverInfo"]["name"] == "oss"
    assert any(t["name"] == "oss_loop_status" for t in out[1]["result"]["tools"])
    assert json.loads(out[2]["result"]["content"][0]["text"])["decision"]["decision"] == "review_first"


def test_stdout上只有协议一句散文都没有(tmp_path):
    """stdout 是协议通道。掺一个字进去就把消息流搅坏了——
    而表现是「客户端偶尔连不上」，最难查的那一类。

    只对每一行 `json.loads` 是**恒真的**：进程崩了、或者一个字都没吐，
    循环体一次都不进，测试照样绿。所以先钉退出码与「确实回了东西」。
    """
    p = subprocess.run([sys.executable, "-m", "oss.cli.main", "mcp", "serve"],
                       input=json.dumps(_req(1, "initialize")) + "\n",
                       capture_output=True, text=True, cwd=REPO, timeout=120)
    assert p.returncode == 0, p.stderr
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    assert lines, f"stdout 上一行都没有——服务器没答话：{p.stderr}"
    for line in lines:
        json.loads(line)      # 每一行都必须是合法 JSON
    assert "result" in json.loads(lines[0]), f"第一行不是一条回复：{lines[0]}"


# ─────────────────────────── 文档跟代码一起红


def test_接入片段是合法JSON且命令对得上():
    d = json.loads((REPO / "docs" / "user" / "mcp-snippet.json").read_text(encoding="utf-8"))
    oss = d["mcpServers"]["oss"]
    assert oss["args"] == ["mcp", "serve"]
    assert oss["command"].endswith("oss")


def test_文档里的工具表跟代码一致():
    """两个方向都查。**文档里列了工具名却没有这个工具，比不列更糟**——
    模型会去调一个不存在的东西，然后自己编一套解释。"""
    t = (REPO / "docs" / "user" / "mcp.md").read_text(encoding="utf-8")
    for name in TOOLS:
        assert f"`{name}`" in t, f"docs/user/mcp.md 没列 {name}"
    listed = set(re.findall(r"`(oss_[a-z_]+)`", t))
    assert listed <= set(TOOLS), f"文档列了不存在的工具：{sorted(listed - set(TOOLS))}"
    assert "@deepseek-ai/dsh-mcp-client" in t
    assert "toolCallTimeoutMs" in t, "oss run / oss review 会超过 dsh 的 60 秒缺省"


def test_README指得到接入说明():
    assert "docs/user/mcp.md" in (REPO / "README.md").read_text(encoding="utf-8")
