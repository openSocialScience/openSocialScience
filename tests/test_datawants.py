

# ═══════════ 数据包匹配：CGSS 不是 GSS（三次自由跑同时撞上）

def test_cgss不该被匹配成gss() -> None:
    """**`"gss" in "cgss2021"` 是真的。**

    三次互不相通的真跑同时撞上：候选说「CGSS2021 独有的主观加班阈值」，
    工具回一句「数据包 gss——`oss data get gss` 直接拿」，
    而 GSS 是美国的综合社会调查，补不了中国 CGSS 的题。

    三个窗口全都自己识破了（「我不会拿它顶替」），所以没造成错误分析；
    但一条明显错的建议摆在那儿，代价是使用者学会忽略整张缺数据清单。
    """
    from oss.core.datawants import _pack_in

    assert not _pack_in("gss", "cgss2021 独有的主观加班阈值 l5")
    assert _pack_in("cgss", "cgss2021 独有的主观加班阈值 l5"), "右边界只挡字母，年份要认"
    assert _pack_in("gss", "gss 1972–2010 累积截面")
    assert _pack_in("gss", "gss_like.csv")
    assert _pack_in("cfps", "cfps2018 的家庭问卷")


def test_被当成不够用的那套不算解法() -> None:
    """用户常先点名手上有的那套、再说它缺什么：

        没有中国的时间日志数据可对照——**CGSS 只有**自报工时

    照名字匹配会回「`oss data get cgss` 直接拿」——**他缺的正好不是它**。
    这比 CGSS→GSS 更隐蔽：名字对上了，对象反了，建议读起来完全通顺。
    """
    from oss.core.datawants import _named_as_lacking

    assert _named_as_lacking("cgss", "没有中国的时间日志数据可对照——cgss 只有自报工时")
    assert _named_as_lacking("cgss", "cgss 2006 与 2008 两轮没有可比的单位所有制变量")
    assert _named_as_lacking("gss", "gss 没有出生州")
    assert not _named_as_lacking("gss", "需要 gss 1972–2010 累积截面")


def test_手上已经有的数据包不再推荐() -> None:
    """**结构判据，不靠措辞。**这一轮用的就是 CGSS，那「去下载 cgss」永远是废话。"""
    from oss.core.datawants import collect_wants

    ideas = {"ideas": [{"idea_id": "i1",
                        "needs_extra_data": "CGSS 缺少行业编码，需要更细的分类"}]}
    got = collect_wants(ideas, {}, ["cgss", "gss"])
    assert got[0].tier == 3, "没声明手上有什么时，照旧按措辞判"

    held = collect_wants(ideas, {}, ["cgss", "gss"], in_hand={"cgss"})
    assert held[0].tier != 1, "手上就是 cgss，不该再推荐它"
