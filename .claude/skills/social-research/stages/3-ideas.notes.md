# 环节 3 的散文：确认三件事、三个源的脾气

它会读 `literature.json` 和数据的**形状**（不看变量清单），产出 `ideas.json`。

回来之后你要确认三件事：

- `theory_hook` 为空的 idea 有几个——**那是强负面信号**，进不了理论对话的不是 idea
- `value_if_null` 答不上的有几个——那是 p-hacking 的根源
- `needs_extra_data` 非空的有几个——那些是「更值得做但你这份数据做不了」，
  **要单独交付，不能被能跑的那个顶替**

## 题目需要的数据，去拿

陪审团保留的候选里，`needs_extra_data` 非空的那几个**往往是最好的题**。
以前它们只能「单独交付」然后被能跑的那个顶替——现在先去拿：

```bash
oss data search "internet users share"              # 世界银行 / OECD / OWID 三个目录一起搜（拿一列指标）
oss data fetch owid share-of-individuals-using-the-internet --run <运行目录>
oss data list                                       # 现成的整套数据集（QoG、V-Dem、GSS……拿一整套）
oss merge <主数据> <运行目录>/owid_xxx.csv --left-on country,year --right-on iso3,year --run <运行目录>
```

两条路：**指标**（世界银行这类，只要一列）和**整套**（`oss data get`）。要申请的数据集满足不了就写进
`needs_extra_data`，收尾时告诉用户去申请——不要卡住等，也不要拿别的顶替。

**先搜列，再说没有。**一套数据拿过之后，按构念在它的列名、变量标签、值标签里找：

```bash
oss data columns vdem --search "censor, internet, disinformation"     # 关键词逗号分隔
oss data columns qog  --search "internet"
```

评审第四条致命问题：理论要「未审查的政治信息」，测的是「上网人口比例」，
论文写「需要 V-Dem 数字社会模块」——**那个模块就在 vdem 包里，没人去搜**。
搜到的是候选：口径看码本（`oss data info` 给链接），选定了 `oss data get <名> --columns ...`。

**搜出来的是候选，不是答案。**「这个指标是不是我要的构念」是你的判断：
看名字、看单位、看覆盖年份和国家数，拿错了拼进去回归照跑、量的是别的东西。

三个源的脾气：

| 源 | 特点 | 注意 |
|---|---|---|
| `wb` 世界银行 | 国家-年，自带 ISO3，汇总口径已剔除 | 指标代码像 `NY.GDP.PCAP.KD` |
| `owid` | 国家-年，自带 ISO3 | 按图表名（slug）拉 |
| `oecd` | 维度多（性别、年龄、口径） | **一个国家-年可能多行**，`sample_filter` 里选定一层再分析 |

**按 ISO3 代码拼，不按国家名。**主数据没有代码列的话，先看它有没有能对上的别的列；
实在只有名字，就走 `oss merge` 的对照表流程（见环节 1）。

拿不到（目录里搜不到、源不可用）就如实写进 `needs_extra_data`，题目留在清单里不砍——
**搜不到不等于没有**，目录是按名字匹配的。
