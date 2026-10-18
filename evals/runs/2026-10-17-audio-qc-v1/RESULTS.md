# Audio QC v1 冻结工程运行

运行于 2026-10-17 01:35（America/Chicago），协议冻结时间为 `2026-10-17T06:35:04.304927+00:00`。在取得结果前冻结配置和源快照；没有按本轮输出调阈值。分析器 `speechqc 0.1.0`，实际 Silero v6 VAD；Windows、Python 3.12.10，AMD64 Family 26 Model 36 Stepping 0、24 逻辑处理器。完整环境见 [protocol.json](protocol.json)。

| 条件 | 完整报告 / 计划 | 预定检查 | 说明 |
| --- | --- | --- | --- |
| 原始真实音频 | 20 / 20 | 仅观察，无准确率结论 | 200.36 秒，英语/普通话各 10 条 |
| 删除全部转录 | 20 / 20 | 20 / 20 可检出变体提示 `speech_without_transcript` | 原音频保留，实际 VAD 区间 |
| 静音音频保留转录 | 20 / 20 | 20 / 20 可检出变体提示 `transcript_without_speech` | 同形状零音频，保留原时间轴 |
| 确定性 DSP 信号 | 6 / 6 | 6 / 6 预定断言通过 | 正常、静音、低音量、近满幅、DC、反相双声道 |

总计 66/66 条完整报告、66/66 次实际 VAD 调用成功。20 条父录音的变体互相关联，不是 60 个独立样本。合成信号不是自然语音，VAD 成功返回不代表对其语义作了正确判断。本轮未使用参考文本，不评估自然错误召回、真实误报率或人类效率。

原始音频有 16/20 条产生提示：低能量 13 条/22 处、整体低音量 10 条/10 处、语音缺少转录 1 条/1 处、文字在语音外 3 条/5 处。类别重叠。全部提示待人工复核；不能将 16/20 称为错误率。覆盖时间轴为词级 19 条、片段回退 1 条。

| 性能观察 | 初始空缓存 | 缓存复跑 |
| --- | ---: | ---: |
| 原始 20 条累计 | 1.709 s | 0.325 s |
| 原始 20 条 P50 | 82.34 ms | 16.19 ms |
| 原始 20 条 P95 | 126.49 ms | 18.61 ms |
| 全部 66 条累计 | 6.581 s | 1.066 s |

首条合成案例 898.42 ms 包含进程内首次模型初始化；真实音频之前已运行合成案例，原始 20 条空缓存时间不是冷进程时间。每条的缓存独立且初始为空；计时包含 API 读文件、哈希、解码、DSP/VAD、序列化等工作，不是纯模型时间。缓存重复读取与模型推理分别报告，没有合并当作推理速度。

66/66 缓存报告一致，缓存条目哈希和修改时间保持不变。输入 manifest、20 个原音频和运行期间代码均未变化。没有失败、部分报告、预定断言失败或缓存不一致。

测量完成后，工作源码做了非功能 Ruff 导入格式和异常边界说明清理；评测脚本把 `datetime.UTC` 换为等价的 `timezone.utc` 以支持 Python 3.10。算法与阈值未变。测量用的快照保持原样，最终工作源码哈希与后续验证见 [post-run-validation.json](post-run-validation.json)。这不把后来文件冒充为实际测量版本。

证据：

- [protocol.json](protocol.json)、[config.json](config.json)、[冻结文档](source-snapshot/docs/audio-qc-v1.md)：先于测量保存的方案和参数。
- [observations.jsonl](observations.jsonl)：66 条逐案例结果、父录音、生成参数、原始报告、预定断言及两次调用耗时。
- [summary.json](summary.json)：完整分母、提示计数、性能、源输入前后哈希和限制。
- [human-audit-template.csv](human-audit-template.csv)：全部 `pending`，没有伪造听音标签。
- [artifact-hashes.json](artifact-hashes.json)：所有已保存实验文件的 SHA-256，自身除外。
- `source-snapshot/`：此次实际运行的分析器、脚本、实验测试和依赖清单。

原音频来自固定版本 Google FLEURS、CC BY 4.0；来源和许可见 [数据卡](../../../docs/speech-smoke-data-card.md)。快照不包含音频、模型文件、机器绝对路径、缓存或数据库。进一步的人工对照设计见 [设计文档](../../../docs/audio-qc-v1.md)；正确性审核与效率试验仍为 `pending`。

重新运行应创建新目录，不能覆盖本次记录：

```powershell
.venv\Scripts\python.exe scripts/evaluate_audio_qc.py --manifest runtime/speech-smoke/asr.jsonl --output-dir evals/runs/NEW-RUN-ID
```
