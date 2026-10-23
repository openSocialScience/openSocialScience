# 把 oss 接进宿主（MCP）

`oss` 自带一个 MCP 服务器：`oss mcp serve`，stdio 传输，随 `pip install` 一起装好，
**不需要额外依赖**（没有 `mcp` 那个包，也没有 starlette / uvicorn 那一套 web 栈）。
它把 `oss/api/` 里的函数注册成工具——跟命令行调的是同一批函数，所以「MCP 跑一次」和
「命令行跑一次」，盘上的 `results.json`、`trace.jsonl`、`run_state.json` 一模一样。

先确认它活着（不需要任何宿主）：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
              '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | .venv/bin/oss mcp serve | python3 -c "import sys,json;[print(json.loads(l).get('result',{}).get('serverInfo') or len(json.loads(l)['result']['tools'])) for l in sys.stdin if l.strip()]"
```

应该看到两行：

```
{'name': 'oss', 'version': '0.1.0'}
32
```

第一行是握手回的 `serverInfo`，第二行是工具个数。**stderr 一个字节都不该有。**

## 返回形状

每个工具都返回同一个形状：

```json
{
  "ok": true,
  "summary": "一句人话",
  "data": { "exit_code": 0, "...": "..." },
  "decision": { "decision": "continue", "next_round": 2, "reason": "..." },
  "problems": ["没通过检测时的逐条理由"]
}
```

- **`ok` 就是「退出码为零」**，退出码在 `data.exit_code`。说明书按退出码分支
  （0 可交付 / 3 还要复审 / 4 轮次耗尽 / 5 没审过 / 6 处方没处置 / 7 must 级没过），
  这里不另立一套说法。
- **`decision` 是程序给的下一步，照做，不要从 `summary` 里猜。**
- `problems` 是没通过检测时的逐条理由，照着改，不要绕过去。
- 可选字段为空就不出现——没有 `decision` 这个键，就是这次没有下一步的判定。
- 同一份字典走两个通道：`content[0].text` 是给模型读的那一份文本，
  `structuredContent` 是给程序读的那一份结构。**两边出自同一次计算**，不会漂。
- MCP 的 `isError` 只在**调用没跑完**时为真（工具不存在、参数不对、没通过检测、崩了）。
  `oss_eval` 报「must 级没过」是成功地报告了一个坏消息：`isError` 是 false，
  `ok` 是 false，`data.exit_code` 是 7。

## 运行目录与工作目录

工具的 `run` 参数跟命令行的 `--run` 一样。**不只是 `run`**——所有路径参数
（`run`、`data`、`out`、`files`、`left` / `right`、`map_file`、`explore_plan`）
都相对**服务器进程的工作目录**解析，所以要么全部写成绝对路径，要么在宿主配置里把
`cwd` 指到项目目录。数据包缓存目录 `data/`（`oss_data_get` 下载现成数据集的落盘位置）
也在这个 cwd 下，不是在仓库固定路径，也不是在 `run` 目录下。
**在纯 MCP 宿主里，第一条调用必须是 `oss_profile`。**32 个工具里只有四个会
把运行目录**新建**出来——`oss_profile`、`oss_data_fetch`、`oss_merge`、`oss_reshape`
（都是「往里写第一份东西」的命令）；另外 22 个吃 `run` 的一律**要求目录已存在**，
不存在就退 1 报「运行目录不存在」，绝不顺手建一个（剩下 6 个不吃 `run`）。
命令行上可以先 `mkdir`，而纯 MCP 宿主里没有 shell、也没有建目录的工具，
所以只能靠 `oss_profile` 开这个头——技能的环节 1 本来就是它，照着走就不会撞上。

审稿后端的三个环境变量（`REVIEWER_BASE_URL` / `REVIEWER_MODEL` / `REVIEWER_API_KEY`）
也是从服务器进程的环境、项目目录的 `.env`，或 `~/.config/oss/.env` 读——**key 永远不作为工具参数传递**，
递进来就等于让它出现在对话历史和留痕里。**模型可以自行调用 `oss_review`**，每次
都会花一次审稿 API 的钱，上限由轮次检测控制（默认最多 2 轮，第 3 轮起必须用
`extra_round` 加审、且必须配 `why`，否则直接拒绝，见退出码 4）。

## Claude Code / Cursor / Codex

把 [`mcp-snippet.json`](mcp-snippet.json) 的内容并进宿主的
MCP 配置（Claude Code 是项目根的 `.mcp.json`），把 `<PROJECT_DIR>` 换成仓库绝对路径：

```json
{
  "mcpServers": {
    "oss": {
      "command": "/Users/you/OpenSocialScience/.venv/bin/oss",
      "args": ["mcp", "serve"],
      "cwd": "/Users/you/OpenSocialScience",
      "env": {}
    }
  }
}
```

**用 venv 里的绝对路径。** PATH 上的 `oss` 可能是另一份旧安装，`oss doctor` 的第一条会查这一点。装好之后：

```bash
.venv/bin/oss doctor | head -3
```

**超时。**Claude Code 侧的 MCP 工具调用也有默认超时——约 60 秒。`oss_run` 在大数据上、
`oss_review` 走跨模型调用，都可能跑得比这更久，会被中途切断。调大它：设环境变量
`MCP_TOOL_TIMEOUT`（单位毫秒），比如 `600000` 对应 10 分钟。

## dsh（DeepSeek Harness）

走 dsh 自带的 MCP 桥接插件 `@deepseek-ai/dsh-mcp-client`
（字段表见它自己的 `packages/mcp/mcp-client/README.zh.md`），
在 `cordis.yml` 里加一个插件实例：

```yaml
- id: mcp-oss
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: oss
    transport: stdio
    command: /Users/you/OpenSocialScience/.venv/bin/oss
    args: ['mcp', 'serve']
    cwd: /Users/you/OpenSocialScience
    # 缺省 60 秒。`oss run` 在几十万行数据上、`oss review` 走跨模型调用，都会超。
    toolCallTimeoutMs: 600000
    env:
      REVIEWER_BASE_URL: !!js process.env.REVIEWER_BASE_URL
      REVIEWER_MODEL: !!js process.env.REVIEWER_MODEL
      REVIEWER_API_KEY: !!js process.env.REVIEWER_API_KEY
      EXECUTOR_MODEL: !!js process.env.EXECUTOR_MODEL
```

模型看到的工具名是 `mcp__oss__oss_loop_status` 这种服务器限定形状，跟 Claude Code、
Codex 一致。

## 工具表

32 个工具：

| 工具 | 对应命令 | 一句话 |
|---|---|---|
| `oss_loop_status` | `oss loop status` | 第一行的 `decision` 就是下一步 |
| `oss_schema` | `oss schema` | 某个产出物该长什么样 |
| `oss_profile` | `oss profile` | 数据画像：结构、变量、缺失、层次 |
| `oss_bind` | `oss bind` | 构念绑到真实列（不给 `mappings` 只列候选） |
| `oss_power` | `oss power` | 统计功效，判词三档；描述类题目改按精度判（`mode=precision`，fail 是精度不足、不停） |
| `oss_run` | `oss run` | 按 plan.json 跑分析；`explore=true` 走探索通道 |
| `oss_figure` | `oss figure` | 诊断图，最快的错误检测器 |
| `oss_route` | `oss route` | 诊断 → 处置，一次一处置 |
| `oss_review` | `oss review` | 跨模型审稿（key 只走环境） |
| `oss_paper_check` | `oss paper check` | 校验稿件 |
| `oss_paper_render` | `oss paper render` | 占位符换成真实数字 |
| `oss_paper_export` | `oss paper export` | 导出 Word，要通过七项检测（7 锚点与研究类型、8 正文的因果说法一族——非因果论文下了因果结论，或因果论文弱工具没引 AR 区间，其余同前） |
| `oss_anchor_switch` | `oss anchor switch` | 换题，必须 `authorized_by: user` |
| `oss_eval` | `oss eval` | 说明书的测试：产物合不合说明书 |
| `oss_eval_behavior` | `oss eval --behavior` | 换一把尺子：这次是怎么跑的 |
| `oss_eval_rules` | `oss eval --list` | 只列规则，不评 |
| `oss_merge` | `oss merge` | 拼两份数据并摊开损失 |
| `oss_reshape` | `oss reshape` | 宽表转长表 |
| `oss_lit_search` | `oss lit search` | 检索文献；`zh` 查中文历史层（2018 年前的核心刊白名单） |
| `oss_lit_fetch` | `oss lit fetch` | 拿全文 / 摘要 |
| `oss_lit_cards` | `oss lit cards` | 校验读书卡 |
| `oss_lit_screen` | `oss lit screen` | 盖上模型的逐篇筛选判定 |
| `oss_lit_snowball` | `oss lit snowball` | 从种子滚雪球扩语料 |
| `oss_lit_import` | `oss lit import` | 知网/万方导出文件并进语料；导入不等于筛过，接着 `oss_lit_screen` |
| `oss_lit_verify` | `oss lit verify` | 核对引用的转述对不对 |
| `oss_lit_add` | `oss lit add` | 按 DOI 补一篇进语料 |
| `oss_data_list` | `oss data list` | 现成数据集清单 |
| `oss_data_info` | `oss data info` | 码本、钥匙列、引用格式、许可 |
| `oss_data_columns` | `oss data columns` | 按构念找列（候选，不是答案） |
| `oss_data_search` | `oss data search` | 在公开目录里搜指标 |
| `oss_data_fetch` | `oss data fetch` | 拉成「国家-年」长表 |
| `oss_data_get` | `oss data get` | 拿一套现成数据 |

参数名与命令行一一对应，只差大小写或连字符的照直换（`--cluster-col` → `cluster_col`、
位置参数 `DATA` → `data`）。下面七处改了名：

| 命令行 | MCP 参数 |
|---|---|
| `oss run --for` | `for_id` |
| `oss run --plan` | `explore_plan` |
| `oss review` 的位置参数 `[file]...` | `files` |
| `oss review --round` | `round_` |
| `oss bind --map` | `mappings` |
| `oss merge --map` | `map_file` |
| `oss data columns --search` | `query` |

**能给多次的参数在 MCP 这边是数组。**`oss bind --map` 给多个 → `mappings` 数组；
`oss review` 收多个文件 → `files` 数组；`oss lit search` 的位置参数收多条检索式 →
`query` 数组（名字没变，变的是形状）。`--json` 一律不暴露（MCP 本来就是 JSON）。

`oss_data_get` 会主动联网下载：`qog` / `vdem` / `gss` 是三个固定来源（分别约
16 / 15 / 48 MB），下过一次就有缓存，`refresh: true` 强制重新下载；`cgss` /
`cfps` / `wvs` 这三套需要手动申请、手动放置本地文件，工具不会替你下，会以
退出码 3 拒绝并说明怎么拿。

**没暴露的命令**：`doctor`、`smoke`、`install-skills`、`trace save`、
`style build` / `style template` / `style check`。前两个是边跑边打的进度日志，
一个终态 JSON 说不清；`install-skills` 动的是宿主机全局目录，不该由模型远程触发；
`trace save` 与 `style` 那三条是人在命令行上做的准备工作。
命令行照常用。
