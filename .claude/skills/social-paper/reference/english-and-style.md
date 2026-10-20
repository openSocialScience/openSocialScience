# 英文稿的规矩与风格包

**正文用英文写。**这是项目的方向决定（目标是英文期刊）：
节名用英文，校验两种语言的节名都认。跨模型审稿、跟用户的交流仍用中文。
英文行文只有一条铁律先记住：**不确定的从句宁可短**——
拉长句子最容易把因果说过头（"which suggests that X causes"）。
写作风格的精修是后续阶段的事，现在先把话说对。

两条英文稿专有的规矩（验收跑的成品里踩过）：

- **`diag` / `power:sensitivity` 这类会渲染出中文诊断文本的占位符，英文稿里别直接嵌。**
  结构固定的（「N 行（P%）」「聚类到 X（K 个簇）」）渲染时会自动翻成英文；
  自由文本的（共线诊断长句、功效敏感性那一段）会被校验拦下——
  **用英文散文转述它**。转述里照抄的数字不算裸小数：
  只要那个数原样出现在本次运行的产出文件里（结果、功效、画像、留痕），校验就放行。
- **标题控制在 15 个词以内。**验收跑出了一个 24 词的标题——
  一个副标题式的问句不是标题，是摘要的第一句。

---

## 英文表的体例（工具自动排，你不用手排，但要知道它长什么样）

依据是仓库里 `styles/social-forces/exemplars/` 的 Social Forces 范文：

- **标准误在系数下一行的括号里，不设 p 列**，系数带星号。这是社会学与经济学表的共同通例
- **星号档位 `* p < .05, ** p < .01, *** p < .001`，没有 0.10 那一档**——
  中文稿沿用 0.10/0.05/0.01，**同一份结果在两种语言下星号数可能不同**，这是体例差异不是错
- 表注起头是单数 `Note:`，星号图例排在最后
- **稳健性 `label` 里不许有 R1 / R3 这种规格代号**：期刊的表里写这一项**换了什么**
  （`R1 Outcome: under-5 mortality` → `Outcome: under-5 mortality`）。
  正文里代号是硬拦的，表里现在只警告，但照样要改
- 表和图里印的是 `plan.labels` 的标签，不是列名。**英文稿填英文**——表里出现中文会被拒

---

## 英文稿引中文文献：要罗马化 + 英译

`styles/social-forces/exemplars/` 的 6 篇范文里**一个汉字都没有**。非英文文献的写法是
**原题（拉丁字母）[英译]**——范文里那条德文文献就是这样排的：

```
Berufssektoren und Berufssegmente auf Grundlage der KldB 2010
[Occupational sectors and Occupational segments based on Classification of Occupations 2010].
```

中文文献要先罗马化（拼音）再给英译。`oss paper check` 会把还带汉字的条目报出来（警告）。
**在 `literature.json` 里给那几条补四栏**：

| 栏 | 填什么 | 例 |
|---|---|---|
| `title_romanized` | 原题的拼音，**句首式大写**（虚词不大写） | `Dongbei diqu chengshi gongzuo pinkun qunti jiuye zhiliang yanjiu` |
| `title_en` | 题名的英译 | `Employment quality of the urban working poor in Northeast China` |
| `venue_en` | 刊名的英文，**有官方英文名就用官方的** | 中国人口科学 → `Chinese Journal of Population Science` |
| `authors_romanized` | 罗马化作者名，**写成「姓, 名」** | `["Yin, Haijie", "Wang, Yijia"]` |

三条要点：

- **原题留在 `title` 里不要动。**英译是你产出的内容，留着原题，任何人都能对着核——
  跟「数字必须可核」是同一个道理
- **作者名一定写成「姓, 名」**（`Yin, Haijie`）。不写逗号会按西文顺序把末词当姓，
  排出来是「Haijie, Yin」，姓名整个倒过来
- 这四栏同时管**正文里的引用**和**参考文献表**：不填的话正文会出现
  「尹海洁 and 王翌佳 (2015) find…」

---

## 有风格包就按风格包写，没有就按标准格式写

主链输入带了 `--- style: <刊名>` 时，风格包是**模具**，不只是门：结构、行文、语言、排版四层都按它来。
动笔前四件事：

**① 先拿模板起稿。**

```bash
oss style template <刊名> --out <运行目录>/paper.md
```

它按范文的多数派给出节的顺序和每节的目标词数（写在注释里），`## References` 和 `{{references}}` 已放好。
**往里填，不要另起炉灶**——校验按同一个骨架查节：`oss paper check --run <运行目录> --style <刊名>`。

**② 读 `styles/<刊名>/style_notes.md`；没有就先写一份。**
这是「这本刊怎么讲故事」，工具量不出来，你读范文写。`oss style build <刊名>` 会点名两篇：
读它们的引言和讨论（`exemplars/*.txt`，本机文件，不进仓库、不引用、不抄），按这个提纲写：

- 引言的第一个动作：先立理论对话、先抛现象、还是先摆争论
- 理论 / 背景节放什么、多长；假设写不写成 H1、H2
- 结果怎么报：实质含义优先还是显著性优先；表和图怎么引
- 讨论怎么过渡：先复述发现还是先回理论
- 局限放哪、多保守、有没有单列成节
- 人称和语气：用不用 we；结论的措辞留多少余地

写的是**动作和结构**，不是句子。`oss style check` 会查成稿跟范文有没有连续 8 个词相同，有就拒绝。

**③ 语言指标当目标。**`style_profile.json` 的 `ranges`：句长、每千词引用数、每千词第一人称、
`citation_form`（作者-年份还是数字编号）。写的时候朝中位数靠；偏出区间校验只警告。

**④ 硬规矩硬守。**`rules.json`（字数、图表上限、双盲、摘要不引用、引用格式）。
没有的话先读 `guide.md` 填一份（`oss schema rules --example`），`oss style build <刊名>` 核对——每个数字必须在须知原文里。

**⑤ 版式跟着须知走。**`rules.json` 的 `layout` 一块管页面级的东西：
页边距、页眉页脚的文字、页码（位置 / 格式 / 起始页）、单双面、分栏、行号、脚注每页重编。
声明了它就**自动生成** Word 模板，不用手工做 `template.docx`（包里同时有手工那份时以手工的为准）。

| 须知里的说法 | 填哪一格 |
|---|---|
| 「注释请用页下注（每页单独编号）」 | `footnote_restart_each_page: true`，且 `reference_style` 照抄这句——`csl_for` 会给脚注式国标 |
| 「双面印刷」「奇偶页页眉不同」 | `two_sided: true`（它同时开镜像页边距与奇偶页页眉，缺一个偶数页页眉就不显示） |
| 「页码居中排在页脚」 | `page_number: "footer-center"`。**别把页码写进 `footer_odd` 的文字里**——那是死字符，不随页数变 |
| 「5 号宋体双栏」 | `columns: 2` |
| 「页边距上下各 2.5 厘米」 | `margin_top_cm: 2.5` 等 |

**须知没说的一格都留 None / 空。**版式里的数字跟字数上限一样要落回须知原文
（`check_rules_grounded` 会查），**按印象填一个页边距跟编一个系数是同一件事**，
而且更隐蔽：读者打开 Word 看到的就是这个版式，会以为它来自该刊的要求。
「36 字 × 35 行」这类说法确实隐含了一个版心，但那是推算出来的数不是须知给的数——
原句留进 `other_requirements`，别折成页边距。

仓库里已有三个中文包可照抄：`styles/zhongguo-shehui-kexue`（脚注、每页重编）、
`styles/shehuixue-yanjiu`（夹注、摘要 200 字）、`styles/guanli-shijie`（顺序编码、双盲）。

写完：

```bash
oss paper check  --run <运行目录> --style <刊名>   # 节按骨架查、裸数字、占位符
oss style check  --run <运行目录> --style <刊名>   # 硬规矩拒绝、指标偏离警告、查重
oss paper export --run <运行目录> --style <刊名>   # 参考文献按该刊格式排（Chicago / APA / ASA），套模板
```

**没带 `--- style:` 就按标准格式**：`oss style template --out <运行目录>/paper.md` 给默认骨架
（Abstract / Introduction / Background / Data and Methods / Results / Discussion / Conclusion / References），
导出用 Chicago 作者-年份、Times New Roman 12 号、双倍行距。不查风格，规范即可。
