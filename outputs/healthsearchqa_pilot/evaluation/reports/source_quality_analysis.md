# Citation 来源质量初步分析

## 评估范围与方法

按 `PILOT_EVALUATION_METRICS.md` 的 `high`、`moderate`、`low`、`unclassifiable` 四类，对两轮 HealthSearchQA AI Overview 的完整 response-level citation pool 进行来源类型分类。78 个有 AI Overview 的回答共包含 699 次 citation（第一轮 356、第二轮 343）。原始 URL 去重后有 378 个；去除视频时间戳、搜索追踪参数等之后，得到 354 个来源身份。

分类以来源网址和发布机构为主。OpenRouter 模型每次收到 10 个来源的规范化 URL、采集标题；173 个已有本地页面缓存的来源还提供页面标题和最多 850 字符的开头摘录。**未向模型发送整篇网页。**模型生成类别、来源类型、简短理由和复核标记。随后人工核查并修正了 18 个有具体依据的条目；模型原判、修正结果及理由均保存在逐项表中。

这一分析衡量的是**来源类型**。它尚未评价某来源是否适合支持某条具体医学主张，也不代替 citation validity、entailment 或 coverage 评估。不能把 `high` 理解成该 citation 必然支持它所附的句子。

## 主要结果

| 范围（按来源身份去重） | High | Moderate | Low | Unclassifiable | High / 可分类来源 |
|---|---:|---:|---:|---:|---:|
| 两轮合并，354 个 | 143 | 64 | 120 | 27 | 43.7%（143/327） |
| 第一轮，334 个 | 136 | 58 | 116 | 24 | 43.9%（136/310） |
| 第二轮，320 个 | 130 | 58 | 111 | 21 | 43.5%（130/299） |

第一轮 39 个有 AI Overview 的回答，其 response-level `high_quality_source_rate` 均值为 **46.4%**，中位数 50.0%；第二轮 39 个回答的均值为 **46.5%**，中位数 50.0%。每个回答内先按来源身份去重，再按文档公式以可分类来源为分母。这个 response-level 均值与上表将全轮来源合并计算的比率是不同的统计量。

合并后的 27 个 `unclassifiable` 全部是发布者无法从现有标题和缓存中可靠识别的 YouTube 视频。它们占全部 354 个来源的 7.6%。若极端地把它们全部判为低质量，高质量比例为 143/354 = **40.4%**；若全部判为高质量，则为 170/354 = **48.0%**。这只是未分类项造成的敏感性范围，并不覆盖已分类项的模型误判。

## 人工核查发现

- **托管平台不是内容作者。**模型曾把一个 [PubMed 记录](https://pubmed.ncbi.nlm.nih.gov/16765736/)按 `.gov` 托管域名判为 `high`。这应按文章本身判断，现暂列 `moderate` 并保留复核标记。[MotherToBaby fact sheet](https://www.ncbi.nlm.nih.gov/books/NBK582729/) 虽托管在 NCBI Bookshelf，但其作者机构 [OTIS 是专业科学学会](https://mothertobaby.org/about-otis/)，因此保留 `high` 的依据是发布机构而非 `.gov` 域名。
- **不能把所有 PMC 页面视为政府来源或系统综述。**一个标题被采集为“Sickle cell disease”的 [PMC 页面](https://pmc.ncbi.nlm.nih.gov/articles/PMC4771139/) 实际对应 [原始患病率研究](https://pubmed.ncbi.nlm.nih.gov/26977274/)，因此修正为 `moderate`。[另一篇 GDM 文章](https://www.frontiersin.org/journals/clinical-diabetes-and-healthcare/articles/10.3389/fcdhc.2020.546256/full) 被期刊标为 “Specialty Grand Challenge”，不是它正文引用的那篇系统综述，也列为 `moderate`。
- **明确的商业或宣传来源按 rubric 判 `low`。**[Shine a Light on HS](https://www.shinealightonhs.com/) 的页脚注明 Novartis Pharmaceuticals Corporation；[Get Smart About AFib](https://cloud.info.getsmartaboutafib.com/en-us-subscribe) 的订阅说明涉及 Biosense Webster。只看健康教育外观会遗漏其商业背景。
- **域名通常足以作机构层面的初分，但共享平台有例外。**CDC、Mayo Clinic、私人诊所和药品站的 URL 大多可以直接定位来源类型；YouTube 需确认发布频道，PMC/PubMed 需确认实际文章类型。当前 38 个条目仍带复核标记，其中 27 个是上述未分类视频。

## 解读与下一步

现阶段可以报告“来源类型的初步构成”：按两轮合并的去重来源计算，约 43.7% 为 `high`，19.6% 为 `moderate`，36.7% 为 `low`（三者分母均为 327 个可分类来源）。目前尚未对 354 个来源逐一进行完整人工核查，因此这些不是终审标签。

若要落实原文提出的“来源是否适合支持所述医学主张”，应在已有 claim-citation pair 的基础上另加 `claim_fitness` 判断，保留来源类型标签不变。例如医院患者页可能足以支持稳定的定义，却未必足以支持精确死亡率或治疗效果。此项不能仅凭 URL 完成。

逐项可筛选结果见 [source_quality_evaluation.csv](source_quality_evaluation.csv)，完整机器可读记录见 [source_quality_evaluation.json](source_quality_evaluation.json)。
