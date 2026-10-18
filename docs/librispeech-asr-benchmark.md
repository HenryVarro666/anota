# LibriSpeech 官方测试集：完整 ASR 基准

核对日期：2026 年 10 月 17 日，America/Chicago。

本轮已将 LibriSpeech 的 `test-clean` 和 `test-other` 全量接入现有本地 ASR，完成全部 **5,559 条、约 10.75 小时**音频的真实识别与离线评分。使用固定版本 faster-whisper base，CPU int8、beam 5，三个独立进程各 4 线程；全部成功，0 失败、0 缺失。test-clean WER **5.42%**，test-other **11.83%**，全量加权 WER **8.62%**。并发识别墙钟时间约 **67.16 分钟**。

## 数据来源与校验

数据来自 [OpenSLR SLR12 官方页面](https://www.openslr.org/12/)，是 LibriVox 有声读物中的真人英语朗读，音频为单声道 16 kHz FLAC。许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；保留数据来源、许可及必要的修改说明。它不是合成语音，也不包含医疗口译或临床风险金标。

数据引用：[Panayotov 等，LibriSpeech: an ASR corpus based on public domain audio books，ICASSP 2015](https://www.danielpovey.com/files/2015_icassp_librispeech.pdf)。

| 官方归档 | 官方测试条目数 | 已下载精确字节数 | 官方 MD5 |
| --- | ---: | ---: | --- |
| [test-clean.tar.gz](https://www.openslr.org/resources/12/test-clean.tar.gz) | 2,620 | 346,663,984 | `32fa31d27d2e1cad72775fee3f4849a9` |
| [test-other.tar.gz](https://www.openslr.org/resources/12/test-other.tar.gz) | 2,939 | 328,757,843 | `fb5a50374b501bb3bac4815ee91d3135` |
| 合计 | 5,559 | 675,421,827 | 分文件校验 |

下载量约 675.42 MB，按 1 MB = 1,000,000 字节计算。这是两个压缩归档的总和；准备后原始 FLAC、元数据、模型缓存、SQLite 状态和结果还需另占空间，不能用压缩量代表完整磁盘需求。

下载完成后已对最终文件重新读取，并与[官方 MD5 清单](https://www.openslr.org/resources/12/md5sum.txt)逐项核对。本地 SHA-256 用于固定本次实验实际使用的归档，不将它误称为官方发布的 SHA-256：

| 文件 | 本地完整文件 SHA-256 |
| --- | --- |
| test-clean.tar.gz | `39fde525e59672dc6d1551919b1478f724438a95aa55f874b576be21967e6c23` |
| test-other.tar.gz | `d09c181bba5cf717b3dee7d4d592af11a3ee3a09e08ae025c5506f6ebe961c29` |

下载记录为 `runtime/datasets/librispeech/official-test/librispeech-download-metadata.json`，记录官方链接、实际镜像、精确大小、MD5、SHA-256 和校验状态。同目录保存官方网页快照及 `md5sum.official.txt`。下载记录中的 `extracted: false` 描述下载完成时的状态，准备是否完成应查看随后产生的 `prepared/verification.json`，不能用旧下载字段替代准备验证。

## 完整准备与参考隔离

[准备器](../speechprep/prepare_librispeech.py)只接受这两个固定官方归档，先校验大小和 MD5，再逐成员读取。它拒绝路径越界、绝对路径、链接和特殊成员，保留原始 FLAC 字节；逐条解码确认单声道、16 kHz、非空、有限值且不是全静音，并检查说话人、章节和音频 ID 与转录一致。缺少参考、重复参考或预期条目数不符都使准备失败；两个归档全部通过后才发布清单。

准备输出位于 `runtime/datasets/librispeech/official-test/prepared/`：

- `manifest.jsonl`：全部 5,559 条 ASR 输入，包含音频路径、哈希、语言、split、说话人、章节、来源和 `reference_id`；没有参考正文。
- `references.jsonl`：独立参考库，包含官方转录正文及其来源。
- `test-clean.manifest.jsonl`、`test-other.manifest.jsonl` 及对应 `*.references.jsonl`：按官方 split 保存的同批记录，不是额外样本。
- `audio/<split>/<speaker>/<chapter>/*.flac`：未经转码的原始音频。
- `source_metadata/`：官方许可、说明、校验和及原始章节转录快照。
- `verification.json`：实际条目数、说话人数、时长、FLAC 字节数、清单与参考哈希。

推理命令只读取 manifest，不向 ASR 传入 `references.jsonl`、参考正文、参考 prompt 或词表。评分器在全部推理结束后按 item ID、语言、音频哈希与 reference ID 离线连接参考。结果文件与评分报告分开保存，评分器不调用模型。后续若导入 Anota 听音修正，官方参考仍应与人工编辑界面隔离。

## 单进程完整复现

在 Anota 根目录的 PowerShell 运行，使用现有 `.venv` 和已缓存的固定模型。以下是全量命令，不能添加 `--limit` 后将部分输出当作完整基准。准备器本身不下载数据；先确保上表两个归档及官方校验快照已放到下载目录。

```powershell
.\.venv\Scripts\python.exe -m speechprep.prepare_librispeech `
  --archives runtime/datasets/librispeech/official-test `
  --output runtime/datasets/librispeech/official-test/prepared

$env:HF_HUB_OFFLINE = "1"
New-Item -ItemType Directory -Force runtime/librispeech-base-2026-10-17/single-process | Out-Null

.\.venv\Scripts\python.exe -m speechprep transcribe `
  --manifest runtime/datasets/librispeech/official-test/prepared/manifest.jsonl `
  --output runtime/librispeech-base-2026-10-17/single-process/asr.jsonl `
  --state runtime/librispeech-base-2026-10-17/single-process/asr-state.db `
  --model base `
  --revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 `
  --device cpu --compute-type int8 --cpu-threads 4 --beam-size 5

.\.venv\Scripts\python.exe scripts/evaluate_speech_smoke.py `
  --manifest runtime/datasets/librispeech/official-test/prepared/manifest.jsonl `
  --results runtime/librispeech-base-2026-10-17/single-process/asr.jsonl `
  --references runtime/datasets/librispeech/official-test/prepared/references.jsonl `
  --output runtime/librispeech-base-2026-10-17/single-process/asr-evaluation.json `
  --evaluation-kind benchmark `
  --purpose "Full LibriSpeech test-clean and test-other ASR baseline" `
  --dataset-description "All 5,559 official test recordings; original FLAC; independent reference store"
```

模型为 `Systran/faster-whisper-base`，固定 revision 如上，CPU int8、4 线程、beam 5；现有配置默认开启 VAD 和词时间戳，关闭 `condition_on_previous_text`。`HF_HUB_OFFLINE=1` 要求这个 revision 已在本机缓存；它不会自动补齐缺少的模型文件。依赖与本地环境见[本地运行说明](local-windows.md)和[语音闭环记录](speech-smoke.md)。

每个 state 数据库保留逐项状态和成功缓存；重新执行相同命令可恢复未完成项并重试失败项。重用成功项时仍校验实际音频哈希。完整缓存重跑的耗时属于缓存校验与导出，不代表重新执行模型的速度。`single-process/` 用于单独复现，避免覆盖下面三个 worker 的实际运行证据；它与并行执行的墙钟耗时不能混用。

## 本次三个 worker 的运行记录

本次完整执行使用本机 `runtime/librispeech-base-2026-10-17/run_benchmark.py`，启动 **3 个独立 worker，每个 4 个 CPU 线程**。它将已验证的完整清单按稳定顺序轮转分成三片，每片 1,853 条；每个 worker 内仍调用现有串行 `speechprep transcribe`，各自保存输出和状态库。分片不是重新采样，不改变原始 `test-clean` / `test-other` 标签。

该调度脚本、数据、数据库和全部结果均位于被 Git 忽略的 `runtime/`，不是提交到仓库的通用 CLI。当前机器保留脚本时，可从项目根目录用以下命令执行；另一台机器若没有这个本地脚本，使用上节提交到仓库的单进程入口复现完整集合。

```powershell
.\.venv\Scripts\python.exe runtime/librispeech-base-2026-10-17/run_benchmark.py
```

运行目录的证据约定如下；最终是否成功，以 `run.json`、各 worker summary 和合并评测共同核对，不能仅看进程退出或已产生若干结果行：

- `run.json`：运行状态、起止时间、固定模型与文件哈希、运行库版本、硬件、线程配置、实际命令和 worker 摘要；完成时追加覆盖率及结果哈希。
- `worker-0/` 至 `worker-2/`：分片 `manifest.jsonl`、`asr-state.db`、`asr.jsonl`、`stdout.json` 与 `stderr.log`。
- `source-snapshot/`：本次准备/推理相关源码、评分器及调度脚本的快照；`run.json` 保存其哈希，并检查运行期间源码没有变化。
- `asr.jsonl`：合并后的全部终态结果；合并前检查 worker 条目数、run ID、重复或缺失 item ID。
- `asr-evaluation.json`：离线全量评分，保存输入哈希、逐项错误、整体覆盖率和 `by_source_split` 分组。
- `environment.json`：Python、操作系统及本次使用的语音依赖版本。
- `validation.json`：完成后的独立核验，确认输入/输出哈希、源码快照、唯一完整条目覆盖、参考隔离、分组分母及计数一致。
- `test-clean.completed.evaluation.json`：test-other 仍在推理时生成的完整 test-clean 评分；已核对与最终合并评分一致。

`inference_wall_seconds` 是三个 worker 并发阶段的墙钟时间；评分报告的原始逐项处理时间是逐项累计，可能重叠，且包含各进程首条模型加载、解码、哈希检查等开销。二者分别报告，不把累计处理时间称为并行墙钟时间，也不把它称为纯解码耗时。

## 评分口径与结果

采用现有 `anota-speech-smoke-normalization-v1`：Unicode NFKC 后 casefold，按现有规则处理标点和撇号，保留数字、数字之间的小数/分组分隔符及必要符号，不进行数字词改写、缩写展开或英美拼写统一；英语按空白分词计算 WER。

[Whisper 官方 EnglishTextNormalizer](https://github.com/openai/whisper/blob/main/whisper/normalizers/english.py)另含数字转换、缩约词和称谓展开、英美拼写映射，因此本轮 WER 不能直接视为采用该归一化器的论文同口径结果。必须同时固定模型、解码、VAD、参考文本和归一化规则才能作严格比较。

WER 按全量 `sum(S+D+I) / sum(reference words)` 聚合，不能平均两个 split 的 WER。失败、缺失或无效预测按空假设计入主指标；覆盖率以 manifest 全部有效音频为分母，时长也包含这些条目。报告 `by_source_split` 分别给出 `test-clean` 与 `test-other` 的 WER、覆盖率和实际音频时长；`failed_or_invalid`、`missing` 及空成功预测另列。

| 官方 split | 预期条目 | 成功 / 失败或无效 / 缺失 | WER 分子与分母 | WER | 实际音频时长 |
| --- | ---: | --- | --- | --- | --- |
| test-clean | 2,620 | 2,620 / 0 / 0 | 2,851 / 52,576 | **5.4226%** | 19,452.480625 秒，约 5.40 小时 |
| test-other | 2,939 | 2,939 / 0 / 0 | 6,193 / 52,343 | **11.8316%** | 19,229.570125 秒，约 5.34 小时 |
| 全部 | 5,559 | 5,559 / 0 / 0 | 9,044 / 104,919 | **8.6200%** | 38,682.05075 秒，约 10.75 小时 |

| 运行指标 | 结果 |
| --- | --- |
| 并发推理墙钟时间 | **4,029.590 秒（67.160 分钟）**；不含下载与准备阶段 |
| 编排总时间 | 4,033.318 秒，包含分片、模型本地文件取证、识别、合并与评分；2026-10-17 06:37:22–07:44:34 UTC |
| 原始逐项处理累计、P50 / P95、处理 RTF | 累计 **11,994.581 秒**；P50 **1.737 秒**，P95 **4.118 秒**；累计处理 RTF **0.3101**。三进程处理时间有重叠，不是并行墙钟时间 |
| 缓存命中、新推理、失败与恢复计数 | **0 命中、5,559 次新推理、0 失败、0 中断恢复**；没有用缓存重跑代替首次识别计时 |
| 空成功转录 | 两组均 0 |
| 最终 run、合并结果与评测哈希核对 | `validation.json` 为 `passed: true`；全量唯一 ID、原始顺序、音频身份、分组计数、输入/结果哈希及源码快照均通过 |

词级错误构成：test-clean 为替换 2,239、删除 313、插入 299；test-other 为替换 4,804、删除 586、插入 803。整体 WER 用 9,044 / 104,919 计算，没有平均两个百分数。真实推理数据保存在 `asr.jsonl`（15,035,852 B），逐条评分保存在 `asr-evaluation.json`（5,588,097 B）。

| 已冻结证据 | SHA-256 |
| --- | --- |
| 合并 ASR 结果 | `678fb1d0919c097f41aaa36e55b472cec654f29c7ae79e7559e08f2ee470a82a` |
| 全量评测 JSON | `f2bbf4752098f53438a547839e55837f59fcf4f54fc795209cc6cdcab8d62784` |
| 完成后的 run.json | `5780d84eca0e370aeeaf4b05d2468ee8aec3e9a7afff170fbb687b99b3369d41` |

本机环境为 Windows 11、AMD Ryzen AI 9 HX 370（12 核/24 逻辑处理器）、Python 3.12.10；faster-whisper 1.2.1、CTranslate2 4.8.3、PyAV 16.1.0、soundfile 0.14.0。更完整版本见 `environment.json`。本轮相关自动测试 **49 项通过**，新增准备/评测代码及对应测试的 Ruff 检查通过。实际全量核验另见 `validation.json`；没有将这个数据集批量导入既有人工修订队列。

## 与旧冒烟集的区别及适用边界

旧[20 条 FLEURS 冒烟集](speech-smoke-data-card.md)为英语、普通话各 10 条固定训练包前缀录音，共 200.36 秒，用于验证下载、ASR、恢复、播放、修订与导出工程闭环；它不是完整官方测试集。本轮只有英语，覆盖两个 LibriSpeech 官方 test 的全部 5,559 条，作用是建立更完整的通用朗读 ASR 基线，不将旧冒烟 WER/CER 与本轮全量结果混合汇总。

官方 test 的身份不保证所用预训练模型从未见过这些录音。朗读语音的 WER 不能证明电话、多人会话、医疗术语、药量、单位、否定或临床安全性能；这些应另设任务和人工标签。当前运行不包含 TTS 训练、语音翻译、LLM 纠错或真实人工修订，人效与错误改正收益仍需独立对照实验。后续更换模型或纠错策略时保留本轮原始 ASR，不覆盖原始参考或静默修改归一化规则。
