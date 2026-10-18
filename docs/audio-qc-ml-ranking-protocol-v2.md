# 音频 QC 轻量学习排序 v2：独立开发集协议

日期：2026-10-17。协议 ID：`audio-qc-ml-ranking-v2`。本文规定真实标签评分前确定的方案；文件存在本身不表示冻结、训练、选型或最终评估已经完成。正式执行前须冻结本文、完整配置、特征 schema、源码和环境，并以运行目录中的哈希与阶段记录为准。任何未在冻结配置中落实的实现细节都不能在看到 holdout 或 test 结果后补定。

本轮保留 [v1 排序协议](audio-qc-ranking-protocol-v1.md)及全部历史记录。v1 已查看的 LibriSpeech test 结果属于已暴露 benchmark；本轮主结论来自新开发数据中按说话人锁定的 holdout，不能将旧 test 重新称为从未查看过的独立验证集。

## 1. 问题、两个特征分支与边界

目标是学习每秒音频可能包含多少转录编辑错误，使 10%／20% 音频时长的整录音复核预算优先覆盖更多错误。比较对象至少包括随机排序、最短优先和冻结的 v1 QC 排序；不以训练拟合度代替排序效果。

| 分支 | 输入 | 新 dev holdout | 已暴露 test |
| --- | --- | --- | --- |
| `legacy` | 已保存 ASR 的文本形态、时间戳异常，以及 DSP/VAD/覆盖特征 | 评估 | 复用全部 5,559 条已有 ASR 评估 |
| `confidence` | `legacy` 加上新 dev 首次转录时保存的真实 decoder 置信相关输出 | 与 `legacy` 在同一 holdout 配对评估 | 不评估：旧输出没有相应字段 |

旧 `speechprep/vendor/asr_whisper.py` 只保存文字、词/片段时间戳和语言识别概率，没有保存 word probability、segment average log probability、no-speech probability 等。不得从旧预测时间戳或 gold 反推这些字段，不得以零或其他占位数伪装成真实置信度，也不得为满足本轮 test 置信分支而重复转录 test。

新 dev 的一次 ASR 推理可以同时保存两分支所需字段；两个分支共享相同录音和预测文本。新增字段的保存不得改变模型 revision、解码参数或生成逻辑。新输出与旧输出使用明确不同的 schema/adapter provenance，避免含附加字段的结果与旧缓存混淆。语言识别概率不代表转录正确率，强制英语时也可能没有区分度；本轮将其作为诊断信息，不作为学习特征。

“confidence”表示使用 decoder 提供的置信相关信号，不表示输出已被校准为人工错误概率。此次实验不训练 ASR、不生成纠正文本、不加入第二路 ASR 或 LLM。

## 2. 数据来源、采样与说话人隔离

新数据来自官方 LibriSpeech `dev-clean` 和 `dev-other`。官方分别提供开发集与测试集，并提供归档校验信息；下载地址、归档校验和、解压文件身份与音频 SHA-256 均须记录。[LibriSpeech 官方来源](https://www.openslr.org/12)

截至本轮准备前的本机检查，仅发现 `official-test` 的两个归档、已准备数据及 ASR；未发现可直接复用的独立 dev ASR。因此只为 dev 的固定样本执行新的同配置 ASR，test 完全复用。

采样与分组只使用元数据和 ID，不读取转录错误标签决定选择：

1. 在每个 official dev split 内，把说话人按 `sha256('audio-qc-ml-v2:speaker:' + speaker_id)` 的十六进制值升序排列。设说话人数为 `n`，前 `floor(0.6*n)` 分给 `train`，随后 `floor(0.2*n)` 分给 `tune`，其余分给 `holdout`。
2. 每个说话人的录音按 `sha256('audio-qc-ml-v2:item:' + item_id)` 升序，取最多 30 条；不足 30 条时全部保留。采样不按 ASR 成功、QC 分数、文本长度或实际错误筛选。
3. 保存全部与选中清单、逐说话人分配、选中和未选中 ID、排序哈希、数量及时长。清单与 `assignment.json` 在 ASR 评分前封存。
4. 验证 `train`、`tune`、`holdout` 的 speaker ID 两两互斥；同时验证新 dev 与旧 test 的 speaker ID、item ID、audio SHA-256 不重叠。如同一说话人异常地跨两个 dev split 出现，应停止并记录，不能独立分到两个角色。

这是“每说话人最多 30 条”的固定 pilot，不是全部官方 dev 的随机录音样本。最终数量、说话人数和时长以已封存的准备结果为准；不得看到模型效果后扩缩样本或换 seed。采样使说话人贡献趋于均衡，不能将结果自动解释为完整 dev 或任意真实业务分布的表现。

准备程序可复制官方参考到相互分离的 role reference 文件，但不能输出用于选型的 holdout 错误统计。分配器不得利用参考文本内容或错误标签。先进行说话人划分，再建立训练表；禁止逐录音随机拆分。

## 3. 特征边界与缺失值

特征提取为独立、确定性的纯数据步骤。输入只包括原始音频的 QC 报告、ASR 预测和解码器实际输出；精确列名、列顺序、类型、缺失约定、聚合公式和特征版本须写入冻结的 feature schema。当前实现 `speechrank.features.FEATURE_NAMES` 定义 legacy 67 个原始特征、confidence 98 个原始特征；预处理追加缺失指示器后列数另行记录。不能在读取 holdout/test 效果后增删列。

`legacy` 允许的特征族为：

- 音频时长的固定变换，ASR 假设字符数/词数、每秒速率和重复程度等聚合统计；不使用具体词身份、词表编码或参考文本。
- 词与片段的持续时间、零时长比例、越界/重叠/间隙及缺失时间戳等异常聚合。
- DSP 指标、VAD 语音占比、转录时间覆盖指标、QC 状态、issue 数量或区间聚合。所有比例的分母和空值行为固定在 schema 中。

`confidence` 仅增加新 ASR 中实际存在的 word probability 统计，以及 segment `avg_logprob`、`no_speech_prob`、`compression_ratio`、`temperature` 等可观测统计。数值范围检查、mean/min/max/std、低概率比例阈值和可用比例由源码及 schema 固定；不存在的原始值为缺失而非真实零。空转录或无词结果也不能伪造置信度。

禁止进入模型矩阵的内容包括：speaker/reader ID、split、章节、item ID、文件路径、reference ID、官方参考文本、参考词数、S/D/I、WER、人工注释、v1 人审或评分结果。它们可用于分组审计、训练目标与离线评分，不能成为推理特征。ASR 假设词数可以使用；gold 参考词数不可以使用。

固定预处理为 `SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)` 后接 `StandardScaler()`。候选训练阶段，这些步骤只拟合 `train`；`tune`、`holdout` 和 test 只能调用 transform。最终 refit 阶段重新只拟合 `train+tune`。不得先对所有录音标准化再分组。每列的缺失计数、全缺失处理及训练后列顺序都保存，防止旧 test 和新 dev 的 schema 差异被隐式掩盖。

`legacy` 特征必须在新 dev 和旧 test 上具有同一语义；新附加 confidence 字段不会进入 legacy 矩阵。旧 test 不执行 confidence arm，即使 imputer 在技术上可以填出全缺失矩阵，也不产生该 arm 的虚假 test 指标。

## 4. 训练目标与固定候选

对训练录音 `i`，令 `E_i=S_i+D_i+I_i`，正有限音频时长为 `d_i` 秒，固定：

```text
target_i = E_i / d_i
sample_weight_i = d_i
ranking_score_i = model.predict(features_i)  # predicted errors per second
```

该目标直接对应时长预算下希望捕获的编辑错误量；不训练“是否至少有一个错误”的分类器来代替主问题。对 Poisson loss 使用密度目标和时长权重，可把录音曝光时长计入损失；这是训练设计，不代表错误过程确实服从 Poisson 分布，也不保证预测分数已校准。[PoissonRegressor 官方 API](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.PoissonRegressor.html)

每个 arm 都使用相同 7 个候选，共 14 个拟合候选：

| 模型族 | 网格 | 固定设置 |
| --- | --- | --- |
| PoissonRegressor | `alpha ∈ {0.1, 1, 10}` | `fit_intercept=True`、`solver='lbfgs'`、`max_iter=1000`、`tol=1e-4`、`warm_start=False` |
| HistGradientBoostingRegressor | `max_leaf_nodes ∈ {3, 7}` × `l2_regularization ∈ {1, 10}` | `loss='poisson'`、`max_iter=100`、`learning_rate=0.05`、`min_samples_leaf=20`、`early_stopping=False`、`random_state=20261017` |

所有其他默认参数以实际 `get_params()` 或等价完整配置固化，不能仅记录网格。fit/predict 使用 `threadpool_limits(limits=4)`；保存随机种子、scikit-learn/NumPy/SciPy 版本及计算环境。禁止自动 early stopping 从训练中另行抽取未经记录的验证集，禁止隐式交叉验证或额外扩大搜索空间。

所有候选记录拟合耗时、警告、实际迭代数、参数、训练数据/特征哈希及 tune 预测。拟合完成且预测有限时，即使产生收敛警告也保留候选并明确记录警告；这不等同于声称已收敛。非有限预测或拟合错误保存候选身份、既有结果、警告和原因后停止整轮，不读取最终评估标签、不跳过候选或临时扩大网格。最终 refit 使用相同规则。

## 5. 只用 tune 选择与最终 refit

每个 arm 独立在 pooled `tune` 上选择一个候选，唯一选择目标为：

```text
selection_objective = (error_capture_at_10_percent + error_capture_at_20_percent) / 2
```

两项使用同一固定 tune 总错误分母，可以等价地最大化整数 `captured_errors_10 + captured_errors_20`；并列按预先定义的 candidate ID 字典序升序决定。不得用 holdout/test、某个有利 split、其他预算、最低 WER 或人工观察来打破并列。若 tune 总错误为零，主选择目标不可定义，应明确记录并停止该轮选型，而不是悄悄改目标。

保存 14 个候选的完整 tune 表和两个 arm 的赢家。随后只用 `train+tune`，按已选参数重新拟合两个最终 pipeline；此时允许 imputer/scaler 在 `train+tune` 重拟合。refit 不增加候选，不再选择超参数，不依据 refit 的训练拟合度更换赢家。

refit 完成后封存两个模型、全部预处理状态、特征 schema、输入 ID 与哈希及选型理由。holdout 上必须报告两个 arm，不能看到结果后只保留赢家。confidence 对 legacy 的比较使用同一 holdout 的相同音频与 ASR；test 只评估已选的 legacy。

## 6. 排序、预算与基线

整录音选择器沿用 v1：`duration_us=round(seconds*1_000_000)`，预算为 `floor(total_us*pct/100)`。按完整排序取不超预算的最长前缀，首条会超预算时停止，不跳过填充，也不截断边界录音。固定预算点为 `5/10/20/30/50/100%`，主预算为 `10/20%`。模型分数表示每秒错误密度，不再额外除一次时长。

排序固定保留 v1 的失败层级：ASR 失败为 2，QC 非 complete 或覆盖检查不可用为 1，其余为 0；先按层级降序，再按模型预测密度降序，最后按 v1 的 salted item ID SHA 升序。层级本身的数量、时长和错误贡献单独统计，不按最终效果切换规则。

每个最终评估队列分别计算 pooled 和各 official split 内独立排序/独立时长预算。至少报告：`ml_legacy`、可评估队列中的 `ml_confidence`、v1 `qc`、`shortest`、`random`。其他时长或 v1 消融基线只有在冻结配置中列明才加入主结果表，不按结果选择展示条件。

随机基线沿用 1,000 个固定种子 `20261017..20262016`；每个组先按 item ID 排序，再 shuffle。所有预算复用同一个 seed 的完整顺序，所有方法共用选择器。保存随机顺序哈希和每个预算的选择计数/实际时长。2.5%–97.5% 随机分位仅描述固定数据上的随机顺序波动，不能称为人群泛化或模型效果置信区间。本轮不做数据 bootstrap 或显著性筛选。

## 7. 标签访问与封存阶段

1. **预先确定设计与数据分组**：本协议中的抽样/分组算法、14 候选和选择规则在真实标签评分前确定；先保存 assignment、全部 role 清单和互斥检查，参考文件独立存储。准备过程只按固定算法搬运标签，不计算 holdout/test 效果。
2. **ASR 与完整实验冻结**：新 dev 首次转录前单独保存 ASR 源码、输入、解码与本地模型哈希；旧 test 复用。dev ASR 完成后，将完整模型训练协议、特征 schema、候选全部参数、预算/基线/种子、评分规范化、源码、环境和输入字节哈希冻结到新研究目录；尚未读取 train/tune 或最终队列标签做任何评分。参考文件字节哈希不算解析或评分标签。
3. **特征冻结**：使用固定输入提取特征，不接触 gold。保存全部原始预测/QC、feature rows、缺失统计和 schema 哈希。此阶段不改变已固定的数据分组或 ASR。
4. **训练与选择**：仅打开 train/tune reference 计算标签；候选只拟合 train，只在 tune 上选型。保存所有候选 tune 预测、指标及确定性选择结果。
5. **refit 与模型冻结**：在 train+tune 重拟合选定的两个 pipeline，保存模型/预处理/特征与训练 ID 哈希。模型和预测运行不允许传入 holdout/test reference。
6. **最终排序冻结**：对 holdout 生成两 arm 分数，对已暴露 test 生成 legacy 分数；冻结所有组、方法和随机种子的顺序、预算选择及哈希。此时不读取本轮 holdout/test 逐条 gold 或评分文件。
7. **一次性最终评分**：只有模型和最终排序均已 seal，才读取 holdout reference 与已暴露 test reference。报告所有固定终点，不据结果重训、改特征或换赢家。
8. **独立审计**：复核输入/源码/环境/模型/预测/计划哈希，重算 10/20% 前缀及错误覆盖，核对分组隔离和全部模型最终计划。审计记录单独写入，不能修改已封存研究产物。

两个最终评估队列可分别完成，但必须都使用同一组已封存模型。即使先完成其中一个队列，也不能据其效果改动另一个队列的模型或流程。旧 test 曾在 v1 被查看这一事实需要持续披露，当前程序不重新读取其 gold 来选型并不能撤销历史暴露。

## 8. 指标、失败与结果解释

评分复用已冻结的英语文本规范化和确定性编辑距离。每组每预算报告选中条数、目标/实际/欠用时长、捕获 S/D/I、错误覆盖率、错误录音召回、选中 WER 和剩余 WER，采用累计编辑数/累计参考词数，禁止平均逐录音 WER。零分母输出 `null` 并标明原因。

真正失败的 ASR 结果以空假设参加评分，仍在预先指定队列中；未完成预测不能当作失败。正式评估前须确认全部预定样本已产生终态结果。QC 或置信字段局部不可用由固定缺失策略处理，并记录可用比例；不能依据处理是否成功删除难例。身份错配、重复 ID、无效时长或缺失 reference 是完整性错误，应停止而非缩小分母。

主证据来自 fresh dev holdout 上已预先选定的两个 arm。报告 legacy 相对随机/最短/v1 QC，以及 confidence 相对 legacy 的配对差值；不把“两个 arm 中结果更好的一个”事后指定为唯一主模型。已暴露 test 是保留全量和历史可比性的次要 benchmark，需明确标注；confidence test 指标为“不适用：旧 ASR 未保存字段”，不能填 0、复制 legacy 或填补出假结果。

训练与 tune 分数只用于诊断和选型，不是最终泛化结果。pilot holdout 说话人数量有限，主结论限定于该固定样本；“fresh”指本轮未用其 gold 训练/调参，不保证上游预训练模型从未见过公开语料。speaker disjoint 也不自动保证所有书籍或语言内容互斥。

错误覆盖表示选中录音包含多少错误，不等于审核者实际找出/修正多少，也不能换算为节省多少人工工时。若学习排序没有优于基线，保留该负结果；本轮结束后提出的新想法属于新协议和新开发循环，不能继续利用本轮 holdout 优化后仍称独立验证。

## 9. 必须保留的复现记录

保存下载/音频/ASR 身份、assignment 和各角色完整 ID、数量及时长；独立参考文件路径/哈希；两 arm 的 feature schema/原始缺失统计；所有候选参数、训练告警与 tune 结果；最终两个模型及预处理状态、选择/refit记录；最终预测和全部排序计划；逐条 S/D/I、全部组/预算/基线结果和随机原始试验；源码、环境、模型和输入前后哈希；各阶段开始结束时间与 seal；独立审计及其可复验脚本。

成本分开记录新 dev 下载与 ASR 推理、QC/特征计算、14 候选拟合、最终 refit、预测/评分以及缓存读取。不得把复用 test ASR 的低增量成本写成完整系统推理零成本。模型可在普通 CPU 上运行是实施目标，具体耗时仍以实际测量为准。
