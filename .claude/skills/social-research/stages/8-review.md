# 环节 8 · 审稿—改—复审

## 输入
- `<运行目录>/paper_rendered.md`、`plan.json`、`results.json`、`power.json`、`literature.json`、`review_plan.json`
- `run_state.stage == "8-review"`；`run_state.review.round` 是**真实轮数，以它为准，不凭记忆**
- 迭代设计对着 `run_state.json` 的 `anchor` 看，**不看 `loop_state.json` 里那个**——后者是按当前 plan/ideas 重算的，对着它看不出漂移

## 产出
- `review_desk.json`、三份（测量类四份）`review_paper_rendered_*.json`、`review_editor.json`（每轮一套，`_r2` 不覆盖 `_r1`）
- 改过的 `paper.md` 与重新渲染的 `paper_rendered.md`
- `loop_state.json`（由 `oss loop status` 落盘）

## 完成条件（满足其一才进环节 9）
- `oss loop status` 第一行的决定是 `stop` → 导出
- 或决定是 `export_with_open_items` → 导出，并把遗留的设计级意见写进论文 Open questions
- 决定是 `continue` 的，改稿、重渲染、用它给的 `--round N` 复审；**本轮少回来的那一位可以补齐**，重跑那一位的同一轮**不算加轮**
- 每条意见都有交代：改了 / 写进局限了 / 不同意（说理由）
- `run_state.stage` 推进为 `9-wrapup`

## 禁止
- 自己判断"还要不要再审一轮"——照 `oss loop status` 第一行的决定做（`../rules/stop-conditions.md`）
- 审第三轮（`oss review --round 3` 会被拒绝；耗尽后**再开一轮**也会被拒绝）
- 轮次耗尽时问用户"哪里改不动"
- 把后端不可用当成一轮，或当成通过
- 拿上一轮的判词充数
- 只改渲染稿
- 第二轮拿新看到的问题判 FAIL（新问题写进「新发现（不作为 FAIL 依据）」）
- 想试一试新设计就自己写 python——走 `oss run --explore --for review-rN`（`../rules/explore.md`）

## 命令
```
/social-review <运行目录> --- round: <N>
```
```bash
oss review --desk --run <运行目录>
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "识别策略与因果表述：结论有没有超出设计支持的范围、话跟数对不对"
oss review --alternatives --level deep --run <运行目录> --round <N>
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "局限与稳健性：该写的写全没有（含因果审计 review_plan.json 的每一条）、稳健性是不是事后补的"
# research_type 为 measurement、或 also 含 measurement 时加第四位，之后每轮都带上
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "测量与数据：构念、代理变量、缺失机制、样本构成"
oss review --editor --round <N> --run <运行目录>
oss loop status --run <运行目录>
```

**这五条一律前台跑，一条一条来，不要放后台。**长档审稿几分钟到十几分钟没有输出是正常的。
验收跑的教训：放后台之后分不清死活，12 分钟里同一份审稿起了 4 次，**每次都真花钱**。
真起重了工具会以**退出码 3** 拒绝并告诉你那个的 PID——**看到 3 就是等，不是重试**。

## 数据来自仿真货箱时

三位互盲审稿人的**成见**在这里换一套问法——不再是「识别 / 替代解释 / 局限」
原版三顶帽子对应的老问法，桌审也多看一条。具体怎么换、`--emphasis` 怎么写、
桌审多看哪一条，见 `.claude/skills/social-review/reference/simulation.md`，
这里不重复。

## 卡住时读
[8-review.notes.md](8-review.notes.md)——四个标签怎么处理、迭代时对着哪个锚点看、改稿改哪一份。
循环规则、设计级意见为什么不要求清零、后端不可用怎么算，都在 [../rules/stop-conditions.md](../rules/stop-conditions.md)。
