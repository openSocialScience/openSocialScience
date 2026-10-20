# 整理模式：输入带 `idea:` 时不出题

**触发条件**：调用参数里有 `--- idea: <想法原文>` 那一段。有就走这份文件，
[lenses.md](lenses.md) 的四步流程（建空白地图 → 过透镜 → 合并去重 → 客观筛选）**整套不跑**。

**为什么要有这个模式。**用户已经有成熟想法时，出题这一步不是多余，是**有害**：
模型会把用户的想法「优化」成另一个题，而锚点冻结的是模型选的那个。
用户拿回来的论文回答的不是他问的问题——**而这件事在产出物上一个字都看不出来**，
`ideas.json` 照样像一份候选清单，`plan.json` 照样跑得通，锚点照样冻着。

**你的角色从「出题人」变成「整理员 + 检视者」。**可以检视、可以补两栏、可以把担心写进局限，
**不能替换**。

---

## 五步：整理 → 经验预检 → 评议 → 确认 → 冻结

### 1. 整理：把原文写成 `ideas.json` 的一条记录

**只有一条**，先跑 `oss schema ideas --example` 看格式，不要猜。逐栏这么填：

| 字段 | 怎么填 |
|---|---|
| `idea_id` | `idea_001`。**写死**——行为指标 `behavior-idea-kept` 认这个编号，漂了就判 FAIL |
| `source` | `user`。**写死**——锚点靠它分得出谁出的题；漏了这一栏，两条自带想法的指标全判 n/a，等于这次没人核 |
| `research_type` | 输入带 `--- type:` 就照填（中文写法换成英文值）；没带就按原文**想下的结论**定——想说 X 影响 Y 就是 `causal`，只描述是 `descriptive`。确认稿里标明「类型是整理时定的」，他可以改 |
| `title` | 从原文提炼一句，**不加原文没有的限定词**（"在制造业中"、"短期内"都是新题） |
| `research_question` | 原文的问句形式；原文没写成问句就改写，**不改变量、不改方向** |
| `hypothesis` | 原文的预期方向。原文说正向就写正向——**你觉得该是负向也照写**，不同意写进收尾说明 |
| `mechanism` | 原文写了就抄；没写就留空，并在确认稿里标「需要用户补」 |
| `identification_strategy` | 同上。原文只说"面板"就写"面板"，不要替他升级成 DID |
| `constructs_needed` | 从原文的 X、Y、机制变量、控制变量里抽**构念名**（不是列名） |
| `theory_hook` | **允许你补**：进入哪个理论对话。用透镜想（见下），补完在确认稿里标明「这一栏是整理时补的」 |
| `value_if_null` | **允许你补**：结果为空时这研究还成立吗。同上标明 |
| `needs_extra_data` | 绑不上的构念（环节 4 `oss bind` 之后回填）。非空就**停下来告诉用户**，不拿相近变量顶上 |
| `source_lens` | `user-idea`。**写死**——这条不是四个起点产出的 |
| `prescreen` | 第 2 步预检回来之后填 |
| `novelty_check` | 定向查重的结论原文，照抄不转述 |
| `lens` | `user-idea`。**这一条不是透镜产出的**，但 `oss eval` 的 `ideas-jury` 规则要求每条都有 `lens`，留空会判 FAIL |
| `jury_verdict` / `jury_reason` | 第 2 步陪审团回来之后填，原文照抄不转述 |
| `transfer_rationale` | 用户的想法本来就不走 `context-transfer` / `temporal-shift` 两个透镜，留 `null` |

顶层 `generated_n` 写 **1**（就这一条，没增没删），`unreachable` 预检之后再填——**不写 `generated_n`，候选数那条判据在整理模式这条路上会一律判不适用，等于这一路没人核。**

`ideas.json` 的顶层 `direction` **写 `idea:` 那一段的原文**：一个字不改、不概括、
不加书名号、不做"更学术"的措辞。**锚点门比对的就是这个字段**——原文不动，锚点就是用户的话。

**十个透镜只做一件事**：帮你把 `theory_hook` 与 `value_if_null` 想出来。
用户的想法里常常没写这两样，而它们正是审稿人最先问的。
**不用透镜出题、不用透镜改题、不用透镜生成第二个候选。**

### 2. 经验预检：给用户的题做一次体检

**这一步是整理模式最该给用户的东西。**他要的不是「你觉得我这题好不好」——
那是他自己的判断；他要的是「这题在这份数据上做不做得了」，而那是机器答得出来的。

照 [prescreen.md](prescreen.md) 对这**一条**跑：构念绑不绑得上、最小可测效应多大、
焦点变量像不像样。证据写进 `prescreen`。

> **只报告不淘汰。**绑不上、功效不够、缺数据——都如实报告，写进确认稿和论文局限，
> **但不许换题、不许改题、不许在旁边再加一条**。做不了的就把编号写进 `unreachable`、
> 在 `needs_extra_data` 写清缺什么，让用户自己决定是去拿数据还是换方向。
>
> 这跟模型出题时的不变量三是同一条：**做不了不扣分**。区别只在于，
> 这里连「排序」都没有——只有一条题，报告就是全部。

### 3. 评议：判词只记录，不淘汰

照跑，一个字不变：

```bash
oss review <运行目录>/ideas.json --kind ideas --level quick --run <运行目录>
```

三种退出情况的含义与 [jury.md](jury.md) 完全一样（0 看判词 / 2 开了 `REQUIRE_CROSS_MODEL=1` 时同家族拒绝执行 /
1 后端不可用，**后两种都不是通过**）。不同的只有一条：

> **判词只能记录，不能淘汰。**理论钩子为空、空结果无价值、缺数据——
> 都原样写进那一条的 `jury_verdict` / `jury_reason`，并进论文局限。
> `ideas.json` 里那一条**不许删、不许改题、不许在旁边再加一条**。

「陪审团说它不好，所以我换一个」是这个模式下最容易犯、也最贵的一个错：
**那不是收窄候选，那是推翻用户交上来的题**。你能做的是把判词原样交给用户（第 3 步）。

### 4. 确认：整理稿回给用户看一眼

主链的 checkpoint `idea`（**单数**，带 `idea:` 时缺省就停这一次；
用户写 `checkpoint: none` 才不停）。**一次性**把四样交出去，不要挤牙膏：

1. 整理稿的九栏——`title`、`research_question`、`hypothesis`、`mechanism`、
   `identification_strategy`、`constructs_needed`、`theory_hook`、`value_if_null`、
   `needs_extra_data`（哪两栏是你补的，标出来：能补的只有 `theory_hook` 与 `value_if_null`）。
   `idea_id: "idea_001"`、`source: "user"`、`lens: "user-idea"` 这三个**不算「栏」**：
   它们是写死的固定值，没有可确认的内容，用户看了也无从点头。
2. 陪审团的三条意见（原文照抄）
3. `needs_extra_data` 里有什么
4. 研究类型（`research_type`）——没带 `--- type:` 时是你定的，说清楚按什么定的，他可以改

**X 或 Y 留空的必须在这一次补齐**才能继续——非程序员写不出规范格式，
让你整理、用户点头，比要求用户按格式填靠谱；但确认这一关不能放空。

用户点头或改字之后，把改动落回 `ideas.json`，然后：

```bash
oss trace save --step ideas --note "自带想法整理稿已确认：idea_001（source=user）" --run <运行目录>
```

### 5. 冻结：交回主链

回环节 4。第一次跑 `oss run` 时锚点自动冻上
`direction`（原文）+ `idea_001` + `source=user`——你不用做任何事。
此后**换题只有一条路**：`oss anchor switch --to <idea_id> --why ... --authorized-by user`。

---

## `design:` 五个键怎么进 `plan.json`

用户可能同时给了 `--- design:`。**那一段不归你办**（它在环节 5 落地），
但你在整理 `identification_strategy` 时要读它，别写出跟它冲突的话。映射规则：

| `design:` 里的写法 | `plan.json` 的字段 | 值怎么变 |
|---|---|---|
| `outcome=Risk` | `outcome` | 原样 |
| `treatment=Misprice` | `treatment` | 原样 |
| `fe=firm+year` | `fixed_effects` | 按 `+` 拆成 `["firm", "year"]` |
| `cluster=firm` | `cluster_level` | 原样 |
| `estimator=ols_fe` | `estimator` | 只认 `ols_fe` / `did` / `did_cs` / `event_study` / `iv` / `iptw` / `rdd` / `describe` / `logit` / `ologit` / `mixed` |

清单外的 `estimator` **停下来告诉用户**，不替换。

---

## 禁止

- 出第二个候选，或者在整理稿旁边"顺便"列几个更好的方向
- 改 `direction`（那是用户的原话，锚点比的就是它）
- 把 `idea_001` 换成别的编号
- 因为 `theory_hook` 空、`value_if_null` 答不上、`needs_extra_data` 非空就淘汰这一条
- 改陪审团的判词（可以不同意，写进收尾说明）
- 拿相近变量顶替绑不上的构念（这条本文件上面已经禁止过；主链 `social-research/stages/3-ideas.md` 的禁止段同样不许把搜到的指标当成答案）
- 把 `--- design:` 的用户写法（`fe` / `cluster`）当成 `plan.json` 里的字段名
- 漏写 `source: "user"` 或 `lens: "user-idea"`——两个都会让事后核账读不出这次发生了什么
