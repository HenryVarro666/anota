# 中英文语音闭环样本数据卡

这 20 条真实录音用于验证音频下载、ASR、Anota 听音修正、审计导出、缓存和断点恢复。英语、普通话各 10 条，总计 200.36 秒。样本来自训练分割的固定压缩包前缀，属于工程冒烟集；后续模型比较需要另外冻结开发集和测试集。

| 项目 | 内容 |
| --- | --- |
| 官方来源 | [Google FLEURS](https://huggingface.co/datasets/google/fleurs) |
| 固定仓库版本 | `70bb2e84b976b7e960aa89f1c648e09c59f894dd` |
| 配置与分割 | `en_us/train` 10 条；`cmn_hans_cn/train` 10 条 |
| 选择方式 | 各固定版本 `train.tar.gz` 中按原始 tar 顺序出现的前 10 个普通 WAV 文件 |
| 音频处理 | 保留原始 WAV 字节；没有重合成、增噪、重采样或响度变换 |
| 音频格式 | 单声道、16 kHz；源文件为 IEEE float WAV |
| 音频体积 | 12,824,200 字节 |
| 许可证 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；保留 Google FLEURS 来源及许可证信息 |
| 说话人 | 官方 TSV 未提供个人说话人 ID，记录为未知；不推断身份 |

数据卡和字段定义以[固定版本官方说明](https://huggingface.co/datasets/google/fleurs/blob/70bb2e84b976b7e960aa89f1c648e09c59f894dd/README.md)为依据。原始 TSV、官方说明和许可证副本保存在 `runtime/speech-smoke/source_metadata/`。每条录音的源文件名、源记录 ID、语种、版本、许可、SHA-256、样本数及实际时长保存在 `manifest.jsonl`。同一句话可能有多条不同录音，条目 ID 包含文件名以避免混淆。

准备命令在 Anota 根目录运行：

```powershell
.venv\Scripts\python.exe -m speechprep.prepare_fleurs --output runtime/speech-smoke
```

准备器流式读取各语言训练压缩包，只读取前 10 条录音所需的前缀后关闭连接。初次准备的两个压缩流共消费 9,932,800 个 HTTP 响应体字节；这个数是应用层读取量，未包含 TSV、数据卡、许可证和网络协议开销。下载记录保存在 `prepare_state.json`，复验结果保存在 `verification.json`。

已验证缓存复跑在禁止网络请求时成功，20 条音频重新解码并校验 SHA，manifest 内容哈希保持一致。中断恢复使用临时副本模拟：保留一条完成录音、破坏下一条文件并留下 `.part`，准备器复用第一条并恢复第二条的原始 SHA。结果保存在 `preparation_checks.json`。该验证只说明数据准备阶段的恢复；ASR 推理缓存和恢复另外验收。

参考文本保存在独立的 `references.jsonl`，包含源文件的原始和已规范化文本。`manifest.jsonl` 只包含 `reference_id`，不含转录答案。ASR 入口使用音频和语种，不读取参考库。离线评测器读取参考库；Anota 人工修正记录与官方参考答案保持不同来源，不能把官方参考答案标成人工听音成果。

评测命令：

```powershell
.venv\Scripts\python.exe scripts/evaluate_speech_smoke.py --results runtime/speech-smoke/asr.jsonl --references runtime/speech-smoke/references.jsonl --output runtime/speech-smoke/asr-evaluation.json
```

评测器默认以结果文件旁的 `manifest.jsonl` 提供完整分母，也可用 `--manifest` 指定。英文报告语料级 WER，中文报告语料级 CER，均按错误总数除以参考单位总数计算。失败或缺失的有效录音按空假设计入主指标，同时单独报告成功覆盖率。重复 ID、未知结果 ID、缺失参考答案和音频身份不匹配会显式报错。

固定规范化为 Unicode NFKC、casefold 和一般标点删除；英文用空格分词，中文删除空白后按 Unicode 字符计数。保留数字内部的小数或分组符号、百分号及其他单位和数学符号，不做数字词转换、医学纠错或否定词改写。评测 JSON 保存完整规范化规则和逐条替换、删除、插入次数。

性能报告将首次推理的 `inference_processing_ms` 与缓存命中的当前 `processing_ms` 分开。首次推理记录包含该进程首次模型下载或加载、解码和推理等实际开销；累计处理时间除以音频时长的 RTF 不能解释为纯解码器速度，也不能用缓存读取速度代替 ASR 推理速度。

这些样本是通用朗读语音，不能说明医疗术语、电话噪声、临床风险或极低资源语言表现。官方训练数据可能已被基础模型预训练使用；本集不支持“未见数据”或泛化结论。WER、CER 也不能代替数字、单位、否定语义的专项审核。
