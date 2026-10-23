# 在别的 agent 宿主上用 OSS

OSS 的说明书只有一份，在仓库的 `.claude/skills/`。装到别的宿主时不是复制，是**投影**：
`oss install-skills --host <宿主>` 按那个宿主的规矩改写之后再铺过去。

---

## 一句话装完

```bash
oss install-skills --host dsh          # 或 claude-code / codex / openclaw / workbuddy
oss doctor --host dsh                  # 自检也按宿主来
```

不给 `--host` 会自动探测，并把依据打出来：

```
宿主：DeepSeek Harness（dsh）｜依据：环境变量 DSH_SHELL
```

探测三级：`OSS_HOST` 环境变量 → 宿主注入的环境变量（谁在跑你，最可信）→ 目录标记
（`.dsh/` → `.agents/` → `.claude/`，**罕见的标记优先**，因为 `.claude/` 几乎每个仓库都有）。
探错了就加 `--host`。

第二级只认宿主**每次起会话才注入**的那些变量（dsh 是 `DSH_SHELL` / `DSH_SESSION_ID`，
Claude Code 是 `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT`）。像 `DSH_HOME` 这种你自己在
`.zshrc` 里 export 一次就一直在的配置变量**不算依据**：它只说明这台机器装过 dsh，
不说明现在是 dsh 在跑你。第三级的目录标记同理，只用有文档出处的那几个——
Codex 只认 `.agents/`（`.codex/` 未见文档记载，不作依据）。

---

## 五个宿主

| 宿主 | `--host` | 装到 | 怎么叫 | 限制 |
|---|---|---|---|---|
| Claude Code | `claude-code` | `~/.claude/skills` | `/social-research 方向 --- data: x.csv` | 无，说明书原样复制 |
| DeepSeek Harness | `dsh` | `${DSH_HOME:-~/.dsh}/skills` | 消息里任意位置写 `/social-research`，整条消息都是参数 | 不替换 $ARGUMENTS；忽略 allowed-tools；bash 里拿不到名字带 KEY/TOKEN/SECRET/PASSWORD 的环境变量 |
| Codex / OpenCode | `codex` | `~/.agents/skills` | `/social-research …` | 没实测过是否替换 $ARGUMENTS、是否认 allowed-tools；两处都按保守方向取值 |
| OpenClaw | `openclaw` | `~/.openclaw/workspace/skills` | `/social-research …` | 只有全局 workspace 技能目录，没有项目级；未实测 |
| WorkBuddy（腾讯） | `workbuddy` | `~/.workbuddy/skills` | `/social-research …`（输入框提示写的是 `@` 提及，两种都到得了同一个技能） | 技能认得出，bash 能跑本机命令行；**是否替换 $ARGUMENTS 仍未测到**，按不替换准备；不写项目指令文件；它跑的是腾讯混元（Hy3），`.env` 的 EXECUTOR_MODEL 要照实填，否则留痕里记的执行者不对 |

设了 `DSH_HOME` 就装到它下面的 `skills/`，跟 dsh 自己找技能的位置一致（`~/.dsh` 只是
`DSH_HOME` 的缺省值，不是写死的路径）——装错地方的话技能列表里什么都不会出现，
而 `install-skills` 会显示全绿。不确定装到了哪儿就跑 `oss doctor --host dsh`，它打的是解析后的路径。

只给当前项目装（不装全局）：`oss install-skills --host dsh --project`，铺到 `.dsh/skills`
（Codex 是 `.agents/skills`，WorkBuddy 是 `.workbuddy/skills`，OpenClaw 没有项目级位置，会明确拒绝）。

---

## 三处宿主差异，OSS 是怎么处理的

**`$ARGUMENTS`。**Claude Code 会把它换成 `/social-research` 之后的内容，dsh 不会。
五份说明书里都写着这句：「如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；
原始输入就是用户消息里 `/social-research` 之后的全部内容。」
不替换的宿主要是缺了这句，`install-skills` 会**拒绝安装**——不自动补，
因为那句话要写在上下文里才对模型有用。

**`allowed-tools`。**Claude Code 认，dsh 明确忽略（它只读 frontmatter 的 name、description
和几个调用开关）。所以铺到 dsh 时那一行降级成注释，原值留着：

```yaml
# allowed-tools: Bash(oss *), Read, Write, Edit, Glob, Grep    # dsh 不认工具白名单，这行只作存档
```

Codex 那边没实测过，保留原行——宿主不认就是忽略，宿主认了则限制仍在；删掉才是危险的方向。

**指令文件。**装完会往项目的 `CLAUDE.md`（Claude Code）或 `AGENTS.md`（dsh / Codex）里写一段
托管块，告诉模型这个项目装了哪些技能、装在哪儿、怎么重铺：

```markdown
<!-- OSS:BEGIN -->
## OSS 技能范围
...
<!-- OSS:END -->
```

块外的正文一个字不动；文件不存在就跳过，**不会替你新建**。不想要就加 `--no-doc`，
想删掉已有的块用 `--clear-doc`。dsh 的 `AGENTS.md` 与 `CLAUDE.md` 两份都会被读，
所以托管块只写 `AGENTS.md` 一份——两份都写会被当成两段不同的指令都加载。

---

## 审稿模型与执行者

审稿算「跨模型」还是「自审」，看执行者和审稿人是不是一家，所以 OSS 得知道**宿主正在跑什么模型**。
按三处依次问，不猜；都问不到就按「自审」记，开了 `REQUIRE_CROSS_MODEL=1` 时才拒绝执行：

1. `EXECUTOR_MODEL` —— 写进项目根 `.env`（或 `~/.config/oss/.env`）
2. 宿主自己暴露的模型名变量 —— 多数宿主没有（dsh 一个都没有；Claude Code 只有你显式设过 `ANTHROPIC_MODEL` 时才有）
3. 同一运行目录里上一次审稿记进 `run_state.json` 的 `models.executor`

**dsh 用户注意**：它的 bash 会剥掉名字里带 `KEY` / `TOKEN` / `SECRET` / `PASSWORD` 的环境变量，
所以 `export REVIEWER_API_KEY=...` 传不进来，报错还会长得像「审稿后端未配置」。
把 key 写进项目根的 `.env`（不在仓库目录里跑就写 `~/.config/oss/.env`），`oss` 自己会读：

```bash
REVIEWER_BASE_URL=https://api.deepseek.com/v1
REVIEWER_MODEL=deepseek-chat
REVIEWER_API_KEY=sk-...
EXECUTOR_MODEL=deepseek-v4-flash    # 宿主实际跑的模型。跟上面的审稿模型是一家，这一跑按「自审」记
```

`oss doctor --host dsh` 会专门查这一条。

---

## 常见问题

| 症状 | 原因 / 怎么办 |
|---|---|
| `oss doctor` 说 skill 跟源头不一致 | 换过宿主形状了。`oss install-skills --host <宿主> --force` |
| 装到 dsh 之后技能列表里没有 | dsh 只认一层：`<技能根>/<名字>/SKILL.md`。别把整个 `skills` 目录再套一层 |
| 模型看到字面的 `$ARGUMENTS` | 正常，那个宿主不替换。说明书里已写明该怎么取参数 |
| 想看某个宿主投影出来长什么样 | `python scripts/build_hosts.py --host dsh --out /tmp/x` |
