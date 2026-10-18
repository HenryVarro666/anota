# Propio 相关业务公开数据集与论文实验记录

核对日期：2026 年 10 月 17 日，America/Chicago。

这份记录用于 Anota 后续下载语料、建立翻译质检基准、扩展医疗语音实验。范围围绕 Propio 公开介绍的口译、笔译、医疗语言服务及人工复核业务；论文与数据的关联来自各论文实验部分，不代表 Propio 内部使用了这些数据或方案。[Propio 业务介绍](https://propio.com/)

首轮建议下载 WMT22/23 MQM、TICO-19 和 ClinSpEn；在许可允许的研究中加入 ACES 压力测试。医疗音频先用 PriMock57，之后再扩展 MultiMed-ST。现有 FLEURS 小样本继续用于工程回归。

## 数据大小与统计口径

下表 MB = 1,000,000 字节，GB = 1,000,000,000 字节。精确字节数来自官方文件元信息；标注“官网约值”的大小保留网站原有单位。文件总和不含 Git 历史、下载缓存、临时文件、数据库、模型权重和推理输出。

“源片段”“系统译文”“错误标注行”“对比组”是不同统计单位。同一个源句可以对应多个系统译文、多位标注者和多个错误。源音频翻译成多个目标语，也不会增加独立录音数。

| 数据集与版本 | 数据规模 | 下载或文件体积 | 体积包含什么 |
| --- | --- | --- | --- |
| WMT22 MQM 核心三方向 | 有 MQM 金标的源片段：英德 1,315；英俄 1,315；中英 1,875 | **45.47 MB**，45,467,209 B | 三个未压缩原始 TSV；不含完整年度目录的其他版本 |
| WMT23 MQM 核心三方向 | 英德 460 个段落；希伯来英 820 句；中英 1,177 句有金标 | **143.63 MB**，143,633,624 B | 三个未压缩原始 TSV；不将重标注版本重复相加为新样本 |
| WMT24 QE Task 2 | 论文称每方向标注 1,511 个片段；当前公开输入行数另见下文 | **1.83 MB**，1,831,451 B | 六个源文与译文文件；**不含已核实的 gold 错误标签** |
| ACES | 36,476 组对比样本，146 方向，68 类现象 | **23.04 MB**，23,039,543 B | 未压缩 challenge_set.jsonl |
| Span-ACES | 同一批 36,476 组增加错误位置字段 | **31.07 MB**，31,067,872 B | 未压缩 span_aces.jsonl；与 ACES 合存共 54.11 MB |
| PhysicianQE | 28 句出院指示金标；另有 125 句阈值校准材料与 65 人实验结果 | **0.78 MB**，776,415 B | 四个数据附件合计；其中主标注 XLSX 仅 28,414 B；没有口译音频 |
| TICO-19 | 3,071 个英语源句；971 dev、2,100 test；含英西、英中 | **15.17 MB**，15,168,404 B | 官方多语言测试集 ZIP；解压体积未核实 |
| ClinSpEn 统一 gold 包 | 病例 202 份；术语 19,128 条；概念 2,189 条 | **19.43 MB**，19,429,899 B | clinspen_corpora_complete.zip，另含背景材料；解压体积未核实 |
| SpeechQE-CoVoST2 | 西英、英德测试材料；每方向约 500 个源音频片段，由多个系统产生译文 | **1.12 MB**，1,116,259 B | 全部 16 个 Parquet 的总和，含重复覆盖的分系统版本；**不含音频** |
| IWSLT da2023 | 12,480 行人工评分记录 | **4.62 MB**，4,620,472 B | 原始未压缩 CSV；另有 1.55 MB 的自动转换 Parquet；均不含音频 |
| PriMock57 | 57 场模拟问诊，8 小时 38 分会话；两个角色音轨合计 17 小时 16 分 | **1.99 GB**，1,989,491,736 B | 114 个 WAV 的 Git LFS 实体；不是 Git 指针大小 |
| MultiMed-ST | 48,369 个源条目，约 150 小时；跨方向展开约 29 万条 | **4.63 GB**，4,629,831,449 B | 原发布 28 个 Parquet，**内含音频字节**；解码后体积未核实 |
| AfriDOC-MT Health | 10,000 个平行句，334 个文档 | **11.00 MB**，10,999,465 B | Health 部分三个原始 CSV，不是整个 AfriDOC-MT 仓库 |
| DischargeMe 1.3 | 109,168 次急诊就诊记录，分为 train/validation/test Phase 1/test Phase 2 | 协议限制，体积待核 | 需认证后核对实际文件清单；不能按样本数推算下载量 |
| FLEURS 本项目固定中英文版本 | 英语 3,643 段约 10.31 小时；普通话 4,600 段约 14.07 小时 | **4.36 GB**，4,356,231,440 B | 两语全部 train/dev/test 的六个原始音频压缩包，不含小型 TSV |

WMT22/23 的源片段量按各年度 Metrics 论文的金标覆盖范围统计，并非跨语言去重后的全局独立样本量。[WMT22 表 3](https://aclanthology.org/2022.wmt-1.2.pdf)、[WMT23 表 3](https://aclanthology.org/2023.wmt-1.51.pdf)

## 论文与实际实验数据

| 论文与会议 | 实际训练或评测数据 | 可复用的实验设计 |
| --- | --- | --- |
| [GEMBA-MQM，WMT 2023](https://aclanthology.org/2023.wmt-1.64/) | 固定 3 个提示示例、无微调；评测 MQM22 的英德、英俄、中英，另报告 MQM23 盲测 | 原文和译文输入 LLM，输出错误位置、类别及严重度，与专业人工审校比较 |
| [xCOMET，TACL 2024](https://aclanthology.org/2024.tacl-1.54/) | 训练用 WMT17–20 DA、MLQE-PE，以及 WMT20–22 MQM、IndicMT、DEMETR 和幻觉增广；明确保留 WMT22 news 测试，另测 WMT23 | 同时评估句分与错误片段；其 span 输出主要是严重度，不能当作完整 MQM 类别预测 |
| [WMT 2024 Quality Estimation](https://aclanthology.org/2024.wmt-1.3/) | Task 2 英德、英西、英印地语专业 MQM 标注；训练主要复用往年 QE 数据 | 参考无关的字符位置与 minor/major 检测；该任务将 critical 并入 major |
| [ACES，WMT 2022](https://aclanthology.org/2022.wmt-1.44/) | FLORES、PAWS-X、XNLI 等来源加工成正确译文与错误译文对；Span-ACES 为同批数据加位置标签 | 数字、实体、增译、漏译等专项压力测试；比较评分器是否更偏好正确译文 |
| [Early-Exit and Instant Confidence QE，EACL 2026](https://aclanthology.org/2026.eacl-long.4/) | WMT19–22 人工片段评分训练，WMT23 的约 116k 系统译文片段测试 | 固定人工复核预算，比较随机选择、低分选择、低置信度选择；该 116k 也不是独立源句数 |
| [Physician Detection of Clinical Harm，EMNLP 2023](https://aclanthology.org/2023.emnlp-main.712/) | UCSF 去标识出院指示，6 份文书 28 句英→简中 Google 译文；3 名双语医生建立准确性和五级临床风险金标 | 65 名不懂中文的医生比较无反馈、回译或 QE 提示下是否采用译文；适合研究人工依赖与风险识别 |
| [Examining Large Pre-Trained Language Models for MT，WMT 2022](https://aclanthology.org/2022.wmt-1.84/) | MeSpEn/IBECS 整理的 250k 对齐句微调；ClinSpEn 病例、术语、本体概念分别测试 | 医疗领域适配、术语保真；临床病例应按文档分组，不能当作无关短句随意重分割 |
| [MultiMed-ST，EMNLP 2025](https://aclanthology.org/2025.emnlp-main.599/) | 英、越、德、法、中文医疗源语音与多语译文；比较直接语音翻译和 ASR→MT，以及双语/多语训练 | 分开统计 ASR 错误、翻译错误和跨方向差异；同源音频必须保持同一 split |
| [Viability of MT for Healthcare in Low-Resourced Languages，EMNLP 2025](https://aclanthology.org/2025.emnlp-main.535/) | AfriDOC-MT Health 与 DischargeME 抽取 1,000 个英→阿姆哈拉语/提格雷语句对，每方向健康信息与医患沟通各 250 句 | 长尾语言中的准确性、药名、否定、单位及临床风险；新标注整包公开性尚未核实 |
| [SpeechQE，EMNLP 2024](https://aclanthology.org/2024.emnlp-main.1218/) | CoVoST2/Common Voice 4 西英、英德音频，每方向约 80k 训练源片段；多个 ST 系统生成译文，以 xCOMET 等自动指标赋分；另用 IWSLT23-ACL 人工 DA 验证 | 源音频加机器译文的无参考质量估计；自动质量标签和人工 DA 必须分开报告 |
| [PriMock57，ACL 2022 数据集](https://github.com/babylonhealth/primock57) | 真人扮演医生和患者的 57 场英语问诊，人工逐话轮转录与医生笔记 | 医疗 ASR、回听修正、审核时长和追加审计；没有跨语译文及翻译错误金标 |
| [Haitian Creole Medical Translation，LAW 2026](https://aclanthology.org/2026.law-main.1/) | 152 个英→海地克里奥尔语医学句对，三轮标注与裁决，五折交叉验证 | 将翻译可接受性、临床风险和语言变体分开；尚未核实可直接下载的金标入口 |

## 翻译质检数据下载入口

### WMT22 和 WMT23 MQM

- 官方数据：[google/wmt-mqm-human-evaluation](https://github.com/google/wmt-mqm-human-evaluation)。本次文件清单版本为 `29acd6999aaea3586378c9dcb58d8cc4820cede3`。
- [WMT22 目录](https://github.com/google/wmt-mqm-human-evaluation/tree/29acd6999aaea3586378c9dcb58d8cc4820cede3/generalMT2022)、[WMT23 目录](https://github.com/google/wmt-mqm-human-evaluation/tree/29acd6999aaea3586378c9dcb58d8cc4820cede3/generalMT2023)。可以按语言和文件下载，避免为一个实验下载整个仓库历史。
- 原始 TSV 含系统、文档/片段、标注者、原文、译文、错误及严重度信息。实际导入前读取该文件表头；不同年度和重标注版本不应直接拼接去重。
- 仓库附 Apache 2.0；来源文本仍应保留所选年度 WMT 的出处及使用条款。
- [MT Metrics Eval](https://github.com/google-research/mt-metrics-eval) 可统一获取原文、参考、系统输出和人工分。它宣传的约 2G 是跨年度全套聚合包，不能计作单个 MQM 集体积。

| 核心文件 | 字节数 |
| --- | ---: |
| generalMT2022/ende/mqm_generalMT2022_ende.tsv | 9,018,125 |
| generalMT2022/enru/mqm_generalMT2022_enru.tsv | 15,121,407 |
| generalMT2022/zhen/mqm_generalMT2022_zhen.tsv | 21,327,677 |
| generalMT2023/ende/mqm_generalMT2023_ende.tsv | 58,272,212 |
| generalMT2023/heen/mqm_generalMT2023_heen.tsv | 4,850,970 |
| generalMT2023/zhen/mqm_generalMT2023_zhen.tsv | 80,510,442 |

完整 `generalMT2022/` 目录 172,123,132 B，完整 `generalMT2023/` 目录 387,119,885 B；两者含附加评分、重复标注或其他版本，不等于核心数据的独立样本总量。[文件大小元信息](https://api.github.com/repos/google/wmt-mqm-human-evaluation/git/trees/29acd6999aaea3586378c9dcb58d8cc4820cede3?recursive=1)

### WMT24 QE Task 2

[公开输入](https://github.com/WMT-QE-Task/wmt-qe-2024-data/tree/fbf4d71738e38c7f236b70afc3392e6b514a3387/test/Task2/MQM) 本次核对版本为 `fbf4d71738e38c7f236b70afc3392e6b514a3387`，仅确认有六个 `.src/.tgt` 文件。该目录没有 gold span/severity 文件；[Codabench 比赛页](https://www.codabench.org/competitions/3518/) 的同一金标下载尚未确认。仓库也未核实到统一的数据 LICENSE。

| 方向 | 论文 MQM 标注片段数 | 公开源文与译文成对物理行数 | 输入文件合计 |
| --- | ---: | ---: | ---: |
| 英→德 | 1,511 | 2,393 | 600,548 B |
| 英→西 | 1,511 | 2,382 | 582,919 B |
| 英→印地语 | 1,511 | 2,039 | 647,984 B |

物理行数不能当作金标覆盖数；没有片段身份与 gold 映射前，不计算官方 span F1。HF 上同名近似的 `meta-metrics/wmt24-mqm-qe` 是另一集合，不能凭名字替代 Task 2。**当前主结果优先使用 WMT22/23 MQM；QE24 英西保留为待补 gold 与许可的扩展项。**[论文数据说明](https://aclanthology.org/2024.wmt-1.3.pdf)、[文件元信息](https://api.github.com/repos/WMT-QE-Task/wmt-qe-2024-data/git/trees/fbf4d71738e38c7f236b70afc3392e6b514a3387?recursive=1)

### ACES 和 Span-ACES

- 官方数据：[nikitam/ACES](https://huggingface.co/datasets/nikitam/ACES)，固定版本 `b497a6456957a5660ac20b8cac5b5222eb9b669c`。
- 直接文件：[ACES JSONL](https://huggingface.co/datasets/nikitam/ACES/resolve/b497a6456957a5660ac20b8cac5b5222eb9b669c/challenge_set.jsonl)、[Span-ACES JSONL](https://huggingface.co/datasets/nikitam/ACES/resolve/b497a6456957a5660ac20b8cac5b5222eb9b669c/span_aces.jsonl)。有错误定位实验时可先取后一份。
- 许可为 CC BY-NC-SA 4.0。HF split 名称虽然是 `train`，这里应按论文用途登记为 `challenge_test`，不要默认用于训练后再在同批样本上报告测试效果。
- 两个文件覆盖同批 36,476 组；增加字段不会产生新独立样本。[文件元信息](https://huggingface.co/api/datasets/nikitam/ACES/tree/b497a6456957a5660ac20b8cac5b5222eb9b669c?recursive=true)

## 医疗文本数据下载入口

### PhysicianQE

[官方仓库](https://github.com/n-mehandru/PhysicianQE) 提供带 adequacy/clinical risk 标注的出院指示、问卷和 65 名医生实验结果。28 句来自 6 份文书；另有 125 句模拟对话用于校准 QE 阈值，不能合并称为 153 句同质测试金标。仓库当前未见明确 LICENSE。

[数据目录](https://github.com/n-mehandru/PhysicianQE/tree/main/data) 的主标注 XLSX 为 28,414 B，实验结果 CSV 为 28,573 B，模拟对话 CSV 为 41,175 B，问卷 PDF 为 678,253 B，四个附件共 776,415 B。全仓库文件总和 1,717,744 B，包含代码等，不是 Git ZIP 压缩量。[文件元信息](https://api.github.com/repos/n-mehandru/PhysicianQE/git/trees/main?recursive=1)

这组数据适合小规模风险案例和人机协作实验，不适合独立估计自然业务中的总体错误率。论文的研究场景是书面出院指示复核，不能将其当作口译员真实通话质量数据。[论文正文与伦理说明](https://aclanthology.org/2023.emnlp-main.712.pdf)

### ClinSpEn

优先使用[统一且含 gold 的发布包](https://zenodo.org/records/7711516)，`clinspen_corpora_complete.zip` 为 19,429,899 B，开放访问，CC BY 4.0。[官方项目页](https://temu.bsc.es/clinspen/)、[Zenodo 文件元信息](https://zenodo.org/api/records/7711516)

| 子任务 | 方向 | dev | test | 统计单位 |
| --- | --- | ---: | ---: | --- |
| CC 临床病例 | 英→西 | 50 | 152 | 病例文档，总计 202 份 |
| CT 临床术语 | 西→英 | 7,000 | 12,128 | 术语，总计 19,128 条 |
| OC 本体概念 | 英→西 | 400 | 1,789 | 概念，总计 2,189 条 |

旧系列入口：[CC](https://zenodo.org/records/6497350)、[CT](https://zenodo.org/records/6497372)、[OC](https://zenodo.org/records/6497388)。已核实的旧 sample+test+background ZIP 为 [CC 6957351](https://zenodo.org/records/6957351) 的 4,565,681 B、[CT 6957317](https://zenodo.org/records/6957317) 的 3,263,379 B、[OC 6957405](https://zenodo.org/records/6957405) 的 2,429,092 B；不能将其当作最新 gold 包。统一包另含 9,804 份背景文档、201,890 条背景术语、299,408 个背景概念，这些不计入上表的 dev/test 数量。背景文档混合病例与其他 PubMed 内容，不全部视为临床病例。微调语料 [MeSpEn](https://zenodo.org/records/3562536) 与测试包分开管理；论文使用的 250k 对齐句不是测试集规模。

### TICO-19

- 项目与下载：[官方 benchmark](https://tico-19.github.io/testset.html)、[完整测试 ZIP](https://tico-19.github.io/data/tico19-testset.zip)。本次仓库版本 `d97615f9cffa28ec7de571e00fbd513e5a1c7fc7`，ZIP 15,168,404 B。[文件元信息](https://api.github.com/repos/tico-19/tico-19.github.io/git/trees/d97615f9cffa28ec7de571e00fbd513e5a1c7fc7?recursive=1)
- 3,071 个英语源句，来自 30 个文档；dev 971 句、test 2,100 句。当前官网列 37 个目标语言。多个语对共享英语源句，不能跨语对重复算独立样本。
- 内容混合医疗口语、科研、公卫、新闻与百科，不能将全体视为临床医患对话。[论文](https://aclanthology.org/2020.nlpcovid19-2.5/)
- 发布译文为 CC0；源文本按来源保留出处和条款。[仓库许可证](https://github.com/tico-19/tico-19.github.io/blob/master/LICENSE.md)
- 数据提供参考译文，没有机器译文错误或临床风险金标。质量审核实验需要先固定待测译文，再独立标注。
- 如只需要翻译记忆，英西 TMX ZIP 为 381,511 B，英中为 385,761 B；主实验优先完整测试包以保留官方 dev/test。

### AfriDOC-MT 和 DischargeMe

[AfriDOC-MT](https://github.com/masakhane-io/afridoc-mt) 的 Health 部分来自 WHO 健康文档。句级 train/dev/test 为 7,041/977/1,982，文档级为 240/33/61，共 10,000 句、334 文档，覆盖英语与阿姆哈拉、豪萨、斯瓦希里、约鲁巴、祖鲁语。三个句级 Health CSV 合计 10,999,465 B；文档级三个 CSV 共 10,944,007 B。两者是同一材料的两种粒度，不要相加算样本。该部分许可 CC BY-NC-SA 3.0。[HF 文件入口](https://huggingface.co/datasets/masakhane/AfriDocMT)、[原始文件大小 API](https://huggingface.co/api/datasets/masakhane/AfriDocMT?blobs=true)

[DischargeMe 1.3](https://physionet.org/content/discharge-me/1.3/) 有 109,168 次急诊就诊记录，train/validation/test Phase 1/test Phase 2 分别为 68,785/14,719/14,702/10,962。需 PhysioNet 认证、指定 CITI 培训及 DUA，实际下载体积应在获准后读取文件清单。其 [Credentialed Health Data License 1.5.0](https://physionet.org/content/discharge-me/view-license/1.3/) 和 [DUA](https://physionet.org/content/discharge-me/view-dua/1.3/) 限定使用与再分发，不能沿用公开 demo 的第三方 API 数据流。仅扩展公开小型翻译实验时，无需先下载整个 MIMIC 系列数据库。

[Viability 论文](https://aclanthology.org/2025.emnlp-main.535/) 从二者采样后生成并标注的 1,000 个句对，是另一实验子集；本次未确认其完整标注包已经公开，不能把母数据的可访问性写成子集金标已可下载。

## 语音数据下载入口

### PriMock57

[官方仓库](https://github.com/babylonhealth/primock57) 使用 Git LFS 保存音频，许可 [CC BY 4.0](https://github.com/babylonhealth/primock57/blob/main/LICENSE.md)。只克隆 Git 指针不会得到可播放录音。114 个 WAV 实体合计 1,989,491,736 B；转录和笔记另占少量空间。

本次版本为 `cd2ac707ad03cb4d2531f4ec6b90c659bf4357c5`；医生轨合计 994,764,108 B，患者轨合计 994,727,628 B。读取 [GitHub 文件树](https://api.github.com/repos/babylonhealth/primock57/git/trees/cd2ac707ad03cb4d2531f4ec6b90c659bf4357c5?recursive=1) 后，按各 WAV 的 LFS pointer 内 `size` 字段求和；GitHub API 中 pointer 自身的 size 不是音频实体大小。

57 场会话时长 8 小时 38 分；医生与患者分别存轨，两类音轨累计 17 小时 16 分，不是 17 小时独立会话。数据为临床医生与员工角色扮演的真人录音，非真实患者诊疗，也非 TTS 合成。[音频说明](https://github.com/babylonhealth/primock57/blob/main/audio/README.md)

### MultiMed-ST

[官方数据](https://huggingface.co/datasets/leduckhai/MultiMed-ST) 的 28 个原始 Parquet 合计 4,629,831,449 B，包含音频实体及文本。HF viewer 自动转换缓存约 4,628,028,302 B，属于另一套存储产物，不能与原文件相加为新语料。

本次 HF 版本为 `d8c3b300b0f2e2ec8fa2113df3bdbc25f5e64668`，可以按源语和 split 取文件。[原始文件元信息](https://huggingface.co/api/datasets/leduckhai/MultiMed-ST?blobs=true)、[HF 转换缓存信息](https://datasets-server.huggingface.co/size?dataset=leduckhai/MultiMed-ST)

| 源语 | train 条目 | eval 条目 | corrected.test 条目 | 全 split 原始 Parquet | 仅 corrected.test |
| --- | ---: | ---: | ---: | ---: | ---: |
| 中文 | 1,242 | 91 | 225 | 228,368,102 B | 33,078,580 B |
| 英语 | 25,512 | 2,816 | 4,751 | 3,643,498,843 B | 554,739,766 B |
| 法语 | 1,403 | 42 | 344 | 217,026,996 B | 42,889,406 B |
| 德语 | 1,443 | 287 | 1,091 | 356,006,389 B | 138,323,867 B |
| 越南语 | 4,548 | 1,137 | 3,437 | 184,931,119 B | 69,992,341 B |

合计 train/eval/corrected.test 为 34,148/4,373/9,848 条。仅取中英文 corrected.test 共 4,976 个源条目、587,818,346 B，约 0.588 GB；全语言 corrected.test 合计 839,023,960 B，约 0.839 GB。语言分布不均衡，比较时同时报告各语言分组。

真实来源录音约 150 小时、48,369 个条目，跨语言方向展开约 29 万条。源内容包含医疗纪录片、访谈、讲座等，不能等同真实医院三方口译。**译文先由 Gemini 生成，论文明确对 test 做人工校改与交叉核验；不能把全部训练译文称为专业人工翻译。**[论文数据构建与实验](https://aclanthology.org/2025.emnlp-main.599.pdf)

当前 HF 与[数据许可文件](https://github.com/leduckhai/MultiMed-ST/blob/main/DATASET_LICENSE.md) 标 MIT 并写可商业使用，但论文数据来源与伦理说明包含研究/非商业措辞。商业使用前需要澄清这组说明的适用范围；代码 MIT 不能自动解决底层音频的来源限制。

### SpeechQE 和 IWSLT

[SpeechQE-CoVoST2](https://huggingface.co/datasets/h-j-han/SpeechQE-CoVoST2) 全部 16 个 Parquet 共 1,116,259 B，其中两个合并测试文件共 508,094 B。分系统文件与合并文件重复覆盖同一批材料，不应相加算独立样本。HF 数据卡标 MIT，但它只包含测试表格、译文、自动分数和音频路径，**没有音频实体**；完整训练代码和语料仍需核对官方发布状态。[官方代码](https://github.com/h-j-han/SpeechQE)

本次版本 `0833a0167640af58281045e1a94d97e1da104d86`。两个方向的合并 test 各 3,500 行，即每方向 500 源段乘 7 个系统；分系统文件再次保存相同内容，仓库累计 14,000 行，独立“源段×系统”样本为 7,000。HF 的约 4.54 MB dataset_size 是表格加载表示，不是音频或解压大小。[文件元信息](https://huggingface.co/api/datasets/h-j-han/SpeechQE-CoVoST2/tree/0833a0167640af58281045e1a94d97e1da104d86?recursive=true)、[split 元信息](https://datasets-server.huggingface.co/info?dataset=h-j-han/SpeechQE-CoVoST2)

论文依赖 Common Voice 4。旧版音频当前需要按 [Mozilla 官方说明](https://community.mozilladatacollective.com/covost-2-datasets-now-available-through-mozilla-data-collective/) 联系获取并说明为何需要该版本，不能直接换成新版 Common Voice 后宣称复现实验。原始 [CoVoST 发布](https://github.com/facebookresearch/covost) 的翻译数据标 CC0；Mozilla Data Collective 重打包可能有不同的访问与使用条件，应以实际获取的包分别登记。

[IWSLT/da2023](https://huggingface.co/datasets/IWSLT/da2023) 公开 12,480 行文本和人工 DA 评分，CC BY-NC-ND 4.0；HF 名称为 train split，实验用途不由该名字决定。本次版本 `b76ed52ece5b1834e8e4712f2a1f7d2828e853b0`，原始 `iwslt2023da.csv` 为 4,620,472 B，自动转换 Parquet 为 1,553,033 B，二选一即可。该包不含音频；CoVoST2 上的自动质量标签与 IWSLT 的人工分不可混称人工语音审核真值。[原始文件元信息](https://huggingface.co/api/datasets/IWSLT/da2023/tree/b76ed52ece5b1834e8e4712f2a1f7d2828e853b0?recursive=true)

当前 Mozilla Data Collective 的 CoVoST2 重打包包含音频与文本，但需注册、逐方向 Request Access，并受该包 CC BY-NC 4.0 及额外使用条款约束；不能将原始 Meta CoVoST 的 CC0 标签套用到重打包文件。以下为页面 JSON-LD 的实际 contentSize；页面显示单位可能接近 GiB，故统一换算十进制 GB。

| 配套来源 | 已核实下载量 | 包内容与限制 |
| --- | ---: | --- |
| [原 Meta 英德 TSV](https://dl.fbaipublicfiles.com/covost/covost_v2.en_de.tsv.tar.gz) | 25,779,505 B | 仅翻译文本，不含 Common Voice 音频 |
| [原 Meta 西英 TSV](https://dl.fbaipublicfiles.com/covost/covost_v2.es_en.tsv.tar.gz) | 3,202,080 B | 同上 |
| [MDC 英德](https://mozilladatacollective.com/datasets/cmpq9bjzy00f7nv07qff3brpb) | 13,482,337,226 B，约 13.48 GB | 音频加文本压缩包；页面约 436 小时 |
| [MDC 西英](https://mozilladatacollective.com/datasets/cmp704ive02qymp075orb8ok4) | 4,423,111,371 B，约 4.42 GB | 音频加文本压缩包；页面约 143 小时 |
| [MDC 英中](https://mozilladatacollective.com/datasets/cmpqxcoy400zinv07bmrdx59x) | 13,484,616,759 B，约 13.48 GB | 音频加文本压缩包 |
| [MDC 中英](https://mozilladatacollective.com/datasets/cmp5kv8j101hvo007hjamlqet) | 748,540,442 B，约 0.75 GB | 音频加文本压缩包 |

这些重打包的解压体积及与原论文 CV4 文件的逐项对应仍待实际获取后验证；英德和英中可能重复包含同一英语音频，不能当作两批独立来源。旧版完整 CV4 的 [历史元信息](https://github.com/common-voice/cv-dataset/blob/main/datasets/scripted-speech/cv-corpus-4-2019-12-10.json) 中 size 未核实是压缩还是解压口径，本表不将其登记为当前可下载归档量。

### FLEURS 中英文

沿用本项目版本 `70bb2e84b976b7e960aa89f1c648e09c59f894dd`，许可 CC BY 4.0。[官方数据卡](https://huggingface.co/datasets/google/fleurs)

| 语言 | split | 录音数 | 音频时长 | 原始音频压缩包 |
| --- | --- | ---: | ---: | ---: |
| en_us | train | 2,602 | 7.49 小时 | 1,380,572,241 B |
| en_us | dev | 394 | 1.05 小时 | 171,250,900 B |
| en_us | test | 647 | 1.77 小时 | 289,851,356 B |
| cmn_hans_cn | train | 3,246 | 9.73 小时 | 1,771,862,730 B |
| cmn_hans_cn | dev | 409 | 1.27 小时 | 217,347,747 B |
| cmn_hans_cn | test | 945 | 3.07 小时 | 525,346,466 B |

条数与时长由该固定版本各 TSV 的行数及 num_samples/16,000 计算；大小来自 [英文文件元信息](https://huggingface.co/api/datasets/google/fleurs/tree/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/en_us/audio?recursive=true)、[普通话文件元信息](https://huggingface.co/api/datasets/google/fleurs/tree/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/cmn_hans_cn/audio?recursive=true)。

两语仅 test 共 815.20 MB；dev+test 共 1.204 GB；全部六包共 4.356 GB。多个录音可能朗读同一句子，抽样和拆分保留 source_id。现有 20 条冒烟音频共 200.36 秒、12,824,200 B，已准备在本地，详见[样本数据卡](speech-smoke-data-card.md)。当前准备器只做每语言训练包前缀取样；完整 dev/test 下载与固定抽样仍需新增入口，不能把冒烟命令当成正式评测下载器。

## 后续 TTS 实验候选

这些是语音合成训练候选，不作为医疗翻译风险或口译质检金标。

| 数据集 | 规模 | 官方下载体积 | 许可与用途 |
| --- | --- | --- | --- |
| [LJSpeech 1.1](https://keithito.com/LJ-Speech-Dataset/) | 13,100 段，单女声，23 小时 55 分 | 官网约 2.6 GB | 美国公有领域；英文单说话人起步 |
| [VCTK 0.92](https://datashare.ed.ac.uk/items/30e7453c-9ea8-48b4-8e18-f96d0dc62928) | 110 位说话人，109 人有转录 | 官网 ZIP 10.94 GB | CC BY 4.0；英文多说话人和口音 |
| [AISHELL-3](https://www.openslr.org/93/) | 85 小时，218 人，88,035 段 | 官网约 19G | 数据页 Apache 2.0；中文多说话人 |
| [LibriTTS-R](https://www.openslr.org/141/) | 约 585 小时，24 kHz | train-clean-100 约 8.1G；另两训练包约 28G、46G | CC BY 4.0；可先取一个子集，下载时排除修复失败名单 |

上述官网约值不是本次精确字节测量，解压体积未核实。Baker、WenetSpeech、WenetSpeech4TTS、GigaSpeech 的数据正文含非商业或额外访问条件；本轮不把仓库顶部的 MIT/Apache/CC 标签直接当作底层音频统一商业授权。

## 从其他 ChatGPT 聊天补充的可下载候选

本节来自对相关历史聊天的定向检索和阅读，再回到论文、原作者仓库及文件元信息核验；不是对账户全部聊天的逐条审计。重点线索来自「[ASR 入门与发展](https://chatgpt.com/c/6a252525-8d7c-83ea-8470-6aa01a720323)」「[Seed-TTS 语音生成模型](https://chatgpt.com/c/6a379db0-bb08-83ea-a714-c8fae618ec6a)」「[语音生成方法比较](https://chatgpt.com/c/6a36202c-a324-83ea-a6e1-304258f6c255)」「[IndexTTS2 简介](https://chatgpt.com/c/6a3a836a-b048-83ea-866d-d52423e200db)」「[全文翻译请求](https://chatgpt.com/c/6a40d812-7088-83ea-8322-bc9bd8c56365)」及「[优化简历建议](https://chatgpt.com/c/6aa08c94-3298-83e9-91fc-502e55c3994a)」。没有把旧回答中的数据规模或“公开”判断直接当成事实，也没有复制个人履历、邮件或完整聊天正文。

### 新候选与下载优先级

| 建议顺序 | 数据集 | 规模 | 下载体积 | 获取条件和能做的实验 |
| --- | --- | --- | --- | --- |
| 1，直接补 ASR | [LibriSpeech test-clean / test-other](https://www.openslr.org/12/) | 2,620 / 2,939 段；本地逐条解码约 **5.40 / 5.34 小时** | **346.66 / 328.76 MB**，两包 **675.42 MB** | CC BY 4.0，直接下载；英语清晰朗读与困难朗读 ASR 对照，保留官方测试划分 |
| 2，补 TTS 评测 | [Seed-TTS-eval](https://github.com/BytedanceSpeech/seed-tts-eval) | 论文约 1,000 英文、2,000 中文；另有 400 中文 hard 文本 | 官方未压缩 tar **1.240 GB** | 有公开下载入口，统一数据许可待澄清；适合中英零样本 TTS、漏词/重复/截断和中文绕口令测试 |
| 3，研究情绪语音 | [ESD](https://github.com/HLTSingapore/Emotional-Speech-Data) | 中英各 10 人，5 种情绪，合计 35,000 段 | 官方 ZIP **2.446 GB** | 需填写许可交作者，限研究；中英情绪 TTS、情绪影响下的 ASR 和人工听审 |
| 3，研究英语情绪 | [EmoV-DB](https://www.openslr.org/115/) | 4 人，5 种风格，官方分项计数合 7,000 段 | 18 个压缩包合 **1.422 GB** | 非商业研究/教学/个人实验；情绪、困倦及非语言发声难例 |
| 4，中文 ASR 扩展 | [AISHELL-1](https://www.openslr.org/33/) | 全集约 178 小时；test 7,176 段，发布音频时长本轮未实测 | 官方主包 **15.58 GB** | 数据页 Apache 2.0 并写有学术免费说明；未找到官方独立 test 下载包，不能把全包体积写成 test 大小 |
| 后续规模扩展 | [原始 Emilia](https://huggingface.co/datasets/amphion/Emilia-Dataset) | 101.7k 小时；英语 46.8k、中文 49.9k | 2,360 个 tar 合 **2.611 TB** | 需接受访问条款，CC BY-NC 4.0；适合抽样纠错/TTS 训练，自动转录不是人工金标 |
| 后续规模扩展 | 同仓库 **Emilia-YODAS** | 113.9k 小时；英语 92.2k、中文约 0.3k | 1,983 个 tar 合 **2.136 TB** | 单独标为 CC BY 4.0，仍需接受访问条款并保存来源；不能把许可套给原始 Emilia |
| 先确认源数据协议 | [RadQNLI](https://github.com/wuzihaoo/RadQNLI) | 过滤版 9,838 问题–句子对；原始版 39,895 对 | 过滤版 CSV 合 **1.24 MB**；原始版 **5.53 MB** | GitHub 文件可访问；上游临床数据有协议，派生集授权需确认；英语医疗事实蕴含对照，没有音频或双语翻译金标 |

TB 使用十进制。上述压缩包、tar 和 CSV 文件体积均不包含解包缓存、模型及生成音频。研究许可与非商业用途也不能自动等同于企业业务实验许可。

### LibriSpeech 与 AISHELL-1：直接入口

- [test-clean.tar.gz](https://www.openslr.org/resources/12/test-clean.tar.gz)：**346,663,984 B**，2,620 段，40 位说话人。
- [test-other.tar.gz](https://www.openslr.org/resources/12/test-other.tar.gz)：**328,757,843 B**，2,939 段，33 位说话人。
- 两包合计 **675,421,827 B**、5,559 段，含音频与转录；大小由官方文件元信息及下载后实际文件核验。2026-10-17 完整解码得到原始音频 **38,682.05075 秒（约 10.75 小时）**、FLAC 合 **719,037,356 B**；这是本次发布包实测时长，参考规模另见 [LibriSpeech 原论文](https://www.danielpovey.com/files/2015_icassp_librispeech.pdf)。不要将 Hugging Face 同内容的 `all/test.clean` 与 `clean/test` 重复相加。准备和实际 ASR 评测见[完整基准记录](librispeech-asr-benchmark.md)。
- AISHELL-1 [data_aishell.tgz](https://www.openslr.org/resources/33/data_aishell.tgz)：**15,582,913,665 B**，包含全量音频与转录；[补充资源包](https://www.openslr.org/resources/33/resource_aishell.tgz)另 **1,246,920 B**。[官方文件目录](https://www.openslr.org/resources/33/)未提供单独 test tar。AISHELL-1 是识别语料，与上节 AISHELL-3 合成语料不同。

两者都是通用朗读语音，可补工程基线和错误分析，不能据此声称覆盖医疗口译、自然对话或临床风险。

### Seed-TTS-eval：评测包与任务边界

[官方 Drive 测试包](https://drive.google.com/file/d/1GlSjVfSHkW3-leKKBlfrjuuTGqQ_xaLP/view)名为 `seedtts_testset.tar`，公开页面文件元信息为 **1,239,582,720 B**；未压缩 tar。官方仓库核对 revision 为 `752f4297f090c46bb1a55a1f7439e5944ddefe8d`。[原论文](https://arxiv.org/abs/2406.02430)与仓库说明的标准客观集约为英文 1,000、中文 2,000 条；不能将内部主观集或训练数据算入公开包。

为了提前设计导入结构，另外读取了[逐文件社区镜像](https://huggingface.co/datasets/zhaochenyang20/seed-tts-eval/tree/8f5e1aa2a35d42f42e940074c1983358b9491f89)的小型任务清单和文件树。它不是新的独立语料，也尚未与原 tar 做逐文件哈希比对。

| 清单 | 该镜像实测行数 | 应保留的内容 |
| --- | --- | --- |
| `en/meta.lst` | 1,088 | 英文 prompt 文本/音频与 target 文本；镜像另有真人 target 音频 |
| `zh/meta.lst` | 2,020 | 中文标准 TTS，同上 |
| `zh/hardcase.lst` | 400 | 中文困难文本和已有 prompt；**没有配套真人 target 音频** |
| `en/non_para_reconstruct_meta.lst` | 1,086 | 英文 VC，另有输入音频字段 |
| `zh/non_para_reconstruct_meta.lst` | 2,018 | 中文 VC，另有输入音频字段 |

标准 TTS/hard 清单为四字段 `utt_id | prompt_text | prompt_wav_relative_path | target_text`；VC 为五字段，末尾增加 `input_wav_relative_path`。这些任务复用音频，不能把五组全包大小相加。镜像全部文件共 **1,235,342,453 B**；仅英文标准 TTS 引用的音频去重后 **378,744,228 B**，中文标准 TTS **779,144,296 B**。镜像行数与论文约数并列记录，不反推原论文实际样本数。

官方根目录和 README 未确认统一数据许可，原 tar 内部许可本轮未检查；镜像的 CC BY 4.0 / Apache 2.0 标签不一致，不能代替原始 Common Voice、DiDiSpeech 音频的授权证明。可先设计 manifest，授权明确后导入；保留 prompt、真人 target、模型生成音频和人工修正的独立字段，hard 的真人 target 留空。它是公开常用评测材料，不能用于训练后再称作未见测试。

### 情绪语音与大规模训练语料

**ESD** 的[原作者说明](https://github.com/HLTSingapore/Emotional-Speech-Data)要求填写许可并发送给作者，仅研究使用；仓库 MIT 标记不能覆盖该数据条款。[官方 ZIP](https://drive.google.com/file/d/1scuFwqh8s7KIYAfZW1Eu6088ZAK2SI-v/view)名为 `Emotional Speech Dataset (ESD).zip`，公开页面文件元信息为 **2,446,419,486 B**，解压大小未知。五种情绪为 neutral/happy/angry/sad/surprise；中英两组不应自动视作逐句双语翻译金标。

**EmoV-DB** 用[OpenSLR 115](https://www.openslr.org/115/)的 18 个 `.tar.gz`，HTTP HEAD 大小合 **1,421,505,568 B**；解包实际条数与时长本轮未实测。原作者[数据许可](https://github.com/numediart/EmoV-DB/blob/master/LICENSE.md)限制非商业研究、教学和个人实验，商业用途需联系作者。按文本 ID 对齐转录；对笑声、哈欠等非语言发声单独标记。

**Emilia** 的文件树已跟随分页完整统计，revision `d7f2f7340a6385696f3766c8049fa920a4707c07`：原始 `Emilia/` **2,610,507,489,280 B**；`Emilia-YODAS/` **2,135,750,082,560 B**；合计 **4,746,257,571,840 B**。[官方卡片的访问条款](https://huggingface.co/datasets/amphion/Emilia-Dataset)明确区分原始 Emilia 的 CC BY-NC 与 YODAS 的 CC BY；顶部单一 CC BY 标签不能概括两部分。当前只适合挑语言和 shard 抽样，避免直接全量下载。

历史聊天中的论文实验应这样对应：[F5-TTS](https://arxiv.org/abs/2410.06885)用约 **95k 小时过滤后中英 Emilia**，另以 WenetSpeech4TTS Premium 945h 做消融；[IndexTTS2](https://arxiv.org/abs/2506.21619)用约 **55k 小时**中英语音，大部分来自 Emilia，但没有据此确认其同一筛选清单公开。IndexTTS2 的 135h 情绪训练中只有 29h 来自 ESD，其余为购买数据；自建 12 人、7 情绪、每情绪 3 句的约 252 条测试材料，本轮未确认完整公开包。论文训练规模不能当作全部可下载规模，Emilia-YODAS 也不是 F5-TTS 原论文所用版本。

### RadQNLI 与旧 TTS 偏好实验：先分清可获取性

历史聊天提过 **RadQNLI**。[原论文](https://arxiv.org/abs/2304.09138)的过滤版是 **9,838 总样本**，含 2,614 个不同问题、5,355 个不同句子；不是 9,838 条 test。[作者仓库](https://github.com/wuzihaoo/RadQNLI)的 `data/rouge_02/` 三个 CSV 共 **1,240,479 B**，`data/full/` 三个 CSV 共 **5,534,161 B**；本次核对的 Git 文件树 SHA 为 `d5477d8bc50931c7c82695bb74dd3007b2fe16b6`。仅读取文件元信息，没有下载临床正文。

该集源于 RadQA；[RadQA 官方入口](https://physionet.org/content/radqa/1.0.0/)要求认证、培训和数据使用协议，采用 PhysioNet Credentialed Health Data License 1.5.0。本轮未在 RadQNLI 根目录发现独立 LICENSE，因此不能仅因 GitHub CSV 可访问就认定可无条件使用。协议和派生使用范围明确后，可做英语医疗事实一致性或 judge 对照；它没有语音、专业翻译错误标签或临床伤害金标。

「全文翻译请求」记录的连续潜变量 TTS 偏好实验提到 **188 对生成偏好样本**和 **100 条保留的 LJSpeech 文本**。这只证明旧报告描述过该实验；本轮未找到可核验下载入口或当前原始结果文件，不能将它列为已落地的公开数据。若后续找回生成音频、偏好标签、split 与模型版本，可以作为个人实验延续；LJSpeech 本体已在上节列出，不重复计算为新数据集。

本轮建议先增加 **LibriSpeech 两个测试包（675 MB）**；TTS 方向先准备 Seed-TTS-eval 的英文/中文/中文 hard 三组导入方案，数据许可明确后再取包。情绪研究优先 ESD，EmoV-DB 作为英语补充；TB 级 Emilia 留在需要训练或规模化抽样时。此前的 WMT/TICO/ClinSpEn/PriMock57 仍优先服务 Propio 相关翻译质检和医疗语音实验。

## 下载与实验执行顺序

| 阶段 | 首批内容 | 可以完成的实验 | 磁盘规划 |
| --- | --- | --- | --- |
| 1 文本质检 | WMT22/23 六个核心 TSV、TICO-19、ClinSpEn，许可允许时加 Span-ACES | LLM judge、错误定位、术语保真、扰动压力测试 | 文件下载量合计约 255 MB；缓存、解压和输出另留空间 |
| 2 医疗音频 | PriMock57，复用本地 FLEURS 冒烟集；需要正式通用 ASR 对照时再取 FLEURS dev/test | 下载→ASR→回听修正→导出；人工修正效率与覆盖率 | PriMock WAV 约 1.99 GB；FLEURS 两语 dev/test 压缩包另约 1.20 GB |
| 3 医疗语音翻译 | MultiMed-ST 先取中英文 corrected.test | ASR→MT 与直接 ST；医疗术语及级联错误分析 | 两语测试 Parquet 约 0.588 GB；全集约 4.63 GB；解码、模型与缓存另算 |
| 4 依赖确认后的扩展 | QE24 gold、Common Voice 4/SpeechQE、DischargeMe、长尾语言标注 | 官方任务复现、受限临床数据或多语风险研究 | 按实际批准和文件清单重新预算 |

阶段 1 的约 255 MB = 45,467,209 + 143,633,624 + 15,168,404 + 19,429,899 + 31,067,872 字节，包含 Span-ACES 而不再重复下载 ACES，未包含 PhysicianQE 和工具代码。实际磁盘空间还要覆盖压缩包与解压文件共存、HF/Git LFS 缓存、数据库和输出，不能直接把下载总量当作可用空间要求。

下载时将文件放入已被 Git 忽略的 `runtime/datasets/<corpus>/<revision>/`。下面是后续在 Anota 根目录运行的 PowerShell 示例，只取一个中英 MQM 文件；该示例尚未执行。其他文件沿用同样的目录、大小核验和哈希记录方式。

```powershell
$datasetRevision = '29acd6999aaea3586378c9dcb58d8cc4820cede3'
$datasetFolder = Join-Path (Get-Location) "runtime/datasets/wmt-mqm/$datasetRevision"
New-Item -ItemType Directory -Path $datasetFolder -Force | Out-Null
$datasetFile = Join-Path $datasetFolder 'mqm_generalMT2022_zhen.tsv'
$datasetUrl = "https://raw.githubusercontent.com/google/wmt-mqm-human-evaluation/$datasetRevision/generalMT2022/zhen/mqm_generalMT2022_zhen.tsv"
Invoke-WebRequest -Uri $datasetUrl -OutFile $datasetFile
if ((Get-Item -LiteralPath $datasetFile).Length -ne 21327677) {
    throw 'File size differs from the recorded upstream snapshot.'
}
Get-FileHash -LiteralPath $datasetFile -Algorithm SHA256
```

每次下载在该目录保存如下记录；模型、译文与人工修订另存，不改源数据。

```json
{
  "corpus": "source-dataset-name",
  "revision": "upstream-commit-or-release",
  "source_url": "official-download-url",
  "downloaded_at": "ISO-8601-timestamp",
  "license_id": "exact-license-or-unresolved",
  "license_snapshot_path": "source_metadata/LICENSE.txt",
  "split": "official-split",
  "language_pair": "en-es",
  "files": [{"path": "raw/source-file", "bytes": 0, "sha256": "computed-after-download"}],
  "count_unit": "source_segment_or_translation_or_annotation",
  "source_count": null,
  "translation_count": null,
  "annotation_row_count": null,
  "audio_seconds": null,
  "role": "development_or_frozen_test_or_challenge"
}
```

JSON 是记录格式示例，不是已下载清单。实际下载后填入真实数值；Git blob SHA、仓库 revision 和文件 SHA-256 是不同字段，不能互换。

后续导入 Anota 时：

1. 保留官方 split、文档 ID、源片段 ID、系统 ID、标注者及原标签；同源多译文、多方向、多位标注及派生音频始终分组。
2. 用开发集选择提示词和阈值；冻结测试集后再运行。已有模型用过的年度数据不能宣称对该模型完全未见。
3. 区分官方参考、专业风险金标、模型自动分数、机器扰动和本地人工修正；不要把这些列合成一个不注明来源的 golden 字段。
4. 先做原标签到 Anota schema 的显式适配，保留不可映射和缺失项；MQM major 不自动等于临床 high risk，QE24 合并后的 major 也不能反推 critical。
5. 句分报告相关性，位置报告字符级或词级 precision/recall/F1，风险报告严重错误召回与误报；固定复核预算时比较随机、低分与不确定性路由。

现有实施约束见[语音一周计划](plans/2026-10-17-speech-annotation-roadmap.md)和[本地运行说明](local-windows.md)。

## 需要补齐的获取事项

- QE24 Task 2：同一版本的 gold span/severity、输入映射及数据许可。
- PhysicianQE：明确数据许可；下载前确认样本和实验结果文件的实际清单。
- SpeechQE：原始 Common Voice 4 获取途径、完整训练材料发布状态及配套音频大小。
- MultiMed-ST：商业使用时澄清当前 MIT 发布与论文非商业说明的关系；按源音频去重统计各方向。
- DischargeMe：完成数据协议后读取包大小，避免将原始 MIMIC 数据与论文采样子集混淆。
- LAW 2026 的 152 句金标：确认公开下载入口和许可。
- [BLASER，ACL 2023](https://aclanthology.org/2023.acl-long.504/)：可以借鉴源语音与翻译语音评分；尚未确认原论文整套人工评分实验语料公开下载。
- [Simul-MuST-C，EMNLP 2024](https://aclanthology.org/2024.emnlp-main.1238/)：真实 TED 音频加 LLM 改写译文；[官方说明](https://github.com/naist-nlp/SimulST/tree/main/corpus-creation/Simul-MuST-C) 因源许可问题尚未发布语料，暂保留代码与方法参考。

## 大小复核方法

精确文件大小使用官方 GitHub Tree API 的 blob size、HF Tree API 的文件 size 或 Zenodo API 的 files.size。Git LFS 必须读实体大小，不能读约百字节的 pointer。HF 原始 Parquet、viewer 转换缓存和解码后 Arrow/WAV 可能重复占盘，应分别计数。未知大小保留未知；不按“条数×平均长度”伪装成下载实测。

后续若文件 revision 改变，重新获取清单与许可并更新核对日期；保留旧实验实际使用的下载记录。此次工作仅核对元信息和小型文本材料，未批量下载这些音频语料。
