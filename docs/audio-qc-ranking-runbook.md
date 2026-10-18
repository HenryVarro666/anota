# LibriSpeech QC 排序评测：运行与续接

本轮用已有 ASR 预测和官方转录评估 10%/20% 音频时长预算的错误覆盖率。完整规则见 [冻结协议](audio-qc-ranking-protocol-v1.md)。本程序不启动 ASR、不下载语料或模型、不改写原参考和活跃 ASR 状态库。

## 依赖与输入

在 Anota 根目录使用本机 `.venv`；如在新环境复现，安装 `requirements-audioqc-eval.txt`。本次实际运行环境在 `protocol.json` 中固定，未声称已完成跨平台验证。需要已准备的 LibriSpeech manifest、references 和兼容 `speechprep` 产生的 ASR 运行目录。

源输入不包含在 Git 仓库中。原始 LibriSpeech 音频、参考和下载校验位于 `runtime/datasets/librispeech/official-test/`；已有 ASR 位于 `runtime/librispeech-base-2026-10-17/`。中期与全量使用同一份模型/解码身份，但各自冻结输入副本和结果。

## 本轮命令

先验证代码，再创建不可覆盖的研究目录。`freeze` 记录参考文件字节哈希，但不解析或评分标签。不要对已经存在的研究目录重复 freeze。

```powershell
.venv\Scripts\python.exe scripts/evaluate_qc_ranking.py freeze --study-dir evals/runs/2026-10-17-librispeech-qc-ranking-v1 --manifest runtime/datasets/librispeech/official-test/prepared/manifest.jsonl --references runtime/datasets/librispeech/official-test/prepared/references.jsonl --asr-run runtime/librispeech-base-2026-10-17

.venv\Scripts\python.exe scripts/evaluate_qc_ranking.py run --study-dir evals/runs/2026-10-17-librispeech-qc-ranking-v1 --cohort interim --work-dir runtime/audio-qc-ranking-v1 --cache-dir runtime/audio-qc-ranking-v1/cache

.venv\Scripts\python.exe scripts/evaluate_qc_ranking.py watch-full --study-dir evals/runs/2026-10-17-librispeech-qc-ranking-v1 --work-dir runtime/audio-qc-ranking-v1 --cache-dir runtime/audio-qc-ranking-v1/cache
```

最后一个命令等待既有 ASR 自身标记完成，并核对全量输出的哈希和精确 ID 集合，再运行全量 QC/评分。它每 30 秒只检查依赖状态；不会将 pending 算作失败，也不会重启或复制 ASR 推理。Windows 后台启动使用隐藏窗口，日志和 PID 留在 `runtime/audio-qc-ranking-v1/`；不要同时启动第二个 watcher。

若已有全量结果且未启动 watcher，也可用同一 `run` 命令将 `--cohort interim` 改为 `--cohort full`。同一 cohort 的结果不可覆盖；修复或重新测量需要新研究目录，旧失败记录保留。

## 阶段与证据

1. `protocol.json`、`config.json`、`source-snapshot/`、`frozen-hashes.json` 固定实现、模型、环境、种子和规则。
2. 中期用每个 worker 的独立只读 SQLite 事务捕获已完成的终态记录，成功/真正失败均保留。快照纳入规则不读取 gold，未完成的数量与时长单列。
3. QC 完整报告保存在无损压缩的 `qc-reports.jsonl.gz`；特征和所有排序/预算计划在 gold 评分前封存。
4. 使用原评分器连接参考，逐条保存 S/D/I、参考词数和规范化文本。随机排序使用固定 1,000 个种子，所有预算共享各自同一排列。
5. 校验选择结果与封存计划一致、所有输入和源码及模型未变，再发布 `status=completed` 和产物哈希。

研究目录下 `cohorts/interim/` 与 `cohorts/full/` 分别保存报告、PNG/SVG、汇总、逐条评分和随机试验明细；完整输入副本位于 `runtime/audio-qc-ranking-v1/<cohort>/`，精确哈希记录于相应 `inputs.json`。`full-followup-status.json` 位于运行目录，说明依赖等待、全量评测、完成或失败状态。

若原 ASR 失败、冻结源码被修改、依赖/模型发生变化或完整性检查不通过，续接会明确失败并保留证据；不会悄悄换算法继续。后续开发应保留冻结版本，必要时新建研究。

## 如何解释结果

主表比较 QC 与随机排序在同一目标音频时长预算下覆盖的错误比例，并给出实际用量、欠用预算和剩余 WER。随机区间表示固定队列上随机顺序的波动，不是泛化置信区间。中期完成队列可能只有部分说话人或 split，不能替代全量结论。

DSP-only、coverage-only、最短/最长优先等附加条件全部保留，不从中挑选最好方法冒充预注册主结果。错误覆盖是所选录音包含的错误；它不是已经修正的错误，也不是实际人工节时。音质提示与转录错误可以不一致。
