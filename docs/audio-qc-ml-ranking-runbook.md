# LibriSpeech 轻量 ML 排序 v2：运行、复验与扩展

本轮目标是用自动参考标签训练错误风险排序，比较相同音频时长预算下的错误覆盖率。完整预先确定的设计见 [协议](audio-qc-ml-ranking-protocol-v2.md)。学习模块在独立 `speechrank/`，不依赖 Anota Web 服务，也不向人工标注界面暴露 gold。v1 的代码和研究产物保留。

## 数据与两组特征

旧的 5,559 条 test ASR 没有保存 word probability、segment avg_logprob、no_speech_prob 等；不能由时间戳反推这些值，也不能把固定语言的 language probability 当作转录置信度。本轮不重跑这些录音。

- `legacy`：67 个可从已有输出、预测时间戳、文本形态与 QC 提取的数值特征；在新 dev holdout 和原 test 上均可评估。
- `confidence`：上述特征加实际 ASR 引擎提供的置信相关字段，共 98 个特征；在新 dev 首次转录时保存，因此只在新 holdout 上评估。

特征列表以冻结 `config.json` 为准。特征仅包含聚合统计，不包含参考文字、错误数、speaker ID、item ID 或 split。缺失值由训练数据拟合的 imputer 处理；真实缺失与概率 0 不混淆。`confidence-availability.json` 保存字段缺失、无效和常量检查。

开发数据为官方 dev-clean/dev-other（共 5,567 条）的固定子集：按每个 speaker 的 item ID 哈希取最多 30 条，独立按 speaker 哈希划分角色。实际选中 2,184 条、73 位说话人，音频约 4.37 小时：

| 角色 | 录音 | 说话人 | 音频秒数 |
| --- | ---: | ---: | ---: |
| train | 1,290 | 43 | 9,311.7150625 |
| tune | 414 | 14 | 2,856.23025 |
| holdout | 480 | 16 | 3,581.8700625 |

未选中的 dev 录音仍保存在原始准备目录，不能在当前实验失败或效果不佳时临时加入。holdout 的“fresh”指未参与本轮训练和选型，不保证上游公开预训练从未接触此语料。原 test 在 v1 已看过，始终单列为已暴露的复用基准。

## 可复现命令

在 Anota 根目录运行；可选依赖放在 `requirements-speechrank.txt`。安装不改变服务端基础依赖。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-speechrank.txt

.venv\Scripts\python.exe scripts/prepare_librispeech_dev.py download --archives runtime/datasets/librispeech/official-dev
.venv\Scripts\python.exe scripts/prepare_librispeech_dev.py prepare --archives runtime/datasets/librispeech/official-dev --output runtime/datasets/librispeech/official-dev/prepared
.venv\Scripts\python.exe scripts/prepare_librispeech_dev.py subset --prepared runtime/datasets/librispeech/official-dev/prepared --output runtime/datasets/librispeech/official-dev/sample-ml-v2 --per-speaker 30 --seed audio-qc-ml-v2 --test-manifest runtime/datasets/librispeech/official-test/prepared/manifest.jsonl

.venv\Scripts\python.exe -u scripts/run_speechrank_dev_asr.py run --manifest runtime/datasets/librispeech/official-dev/sample-ml-v2/manifest.jsonl --output-dir runtime/librispeech-dev-ml-v2-r1

.venv\Scripts\python.exe scripts/evaluate_ml_ranking.py freeze --study-dir evals/runs/2026-10-17-librispeech-ml-ranking-v2
.venv\Scripts\python.exe -u scripts/evaluate_ml_ranking.py run --study-dir evals/runs/2026-10-17-librispeech-ml-ranking-v2
```

`subset`、`freeze` 和正式评测输出不覆盖已有目录。ASR 本身支持固定身份下的中断恢复，会校验源码、模型与输入并保留既有缓存；不要同时启动第二份。已经完成的正式实验不能直接重跑覆盖，修改实现需要新研究目录。

新 dev ASR 使用与旧 test 相同的 base/int8、固定 revision、beam=5、CPU=4 threads/worker、3 workers、VAD 与词时间戳设置。新的 adapter 仅增加字段保存，不改变解码参数；独立 adapter_version 防止混用旧缓存。模型使用已有本地权重，不下载另一套模型。

本轮首次离线预检查误请求了完整模型仓库中的 README/.gitattributes，而这些文件未缓存。尚未执行 ASR 即停止；按旧 runner 的推理文件 allowlist 修复后，使用新目录 `librispeech-dev-ml-v2-r1`。原预检查源码和原因保存在 `runtime/librispeech-dev-ml-v2/PREFLIGHT_FAILURE.json`，不冒充一次成功推理。

## 证据与边界

研究目录的 `source-snapshot/`、`protocol.json`、`config.json` 和 `frozen-hashes.json` 固定实际执行实现、依赖版本、候选参数及全部输入身份。`outputs/` 包含所有候选 tune 预测与指标、最终模型与模型说明、逐条特征/评分、全部排序与预算计划、随机试验、图表和产物哈希。标签访问顺序由运行记录和 `evaluation-seal.json` 绑定，不能仅凭目录名称声称隔离。

完整音频、原参考和 ASR 输入位于本机 `runtime/`，通常不进 Git；复现需要这些输入及其哈希，而不是只克隆文档即可得到数据。官方分发文件 MD5 用于下载完整性核对，另外记录 SHA-256 作为本地证据身份。下载失败、慢连接和镜像切换也保存在官方 dev 目录的下载记录中。

训练目标是 `(S+D+I)/audio_seconds`，按音频秒数加权。每个 arm 只在 train 拟合 7 个候选，用 tune 的 10%/20% 错误覆盖选一个，再在 train+tune 重拟合。两个最终模型都在打开 holdout 标签前保存并生成排序。测试集不参与训练、预处理拟合、候选选择或阈值调整。

这轮不新增人工标签，不自动修正任何转录，也不改变产品默认队列排序。错误风险分数不是经过校准的“正确概率”。是否减少实际人工劳动，还取决于录音切换、播放、重复回听和纠错开销，本次不将音频预算换算成人工工时。

## 后续扩展接口

`speechrank.features.extract_features` 只接收 ASR/QC，可独立用于其他应用；`models.fit_model` 的标签通过显式训练参数传入，`predict_density` 只接特征。未来 LLM、多 ASR 分歧或新信号处理方法可以增加新的特征 arm，保留原有 schema 与模型证据。在新开发数据上确定规则后，再选择未用于选型的评测数据；不能继续对已看过的 holdout 调参并将其报告为新的独立收益。

数据与方法参考：[LibriSpeech 官方分发](https://www.openslr.org/12/)、[scikit-learn Pipeline](https://scikit-learn.org/stable/modules/compose)、[避免数据泄漏](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)。实际库版本以本次冻结环境为准。

## 本轮实际运行与 preflight 事件

正式 v2 已完成，内部完整性验证通过；独立审计通过 577/577 项检查（29.73 秒；检查数不等于测试用例数）。[实际结果、全部 split 与随机顺序分位范围](../evals/runs/2026-10-17-librispeech-ml-ranking-v2/README.md)保存选型、成本和证据索引。冻结的协议与 outputs 保持原样。

第一次 freeze 在 study 创建前发现 test manifest 字节身份差异而停止：assignment 绑定 official prepared 的相对音频路径，v1 snapshot 使用已解析的绝对路径。修复新增 `test_source_manifest` 输入，保留两文件各自哈希，并比较所有 5,559 条完整解析后元数据；源字节仍须匹配原 assignment。数据与旧 v1 未改。[preflight-validation.json](../evals/runs/2026-10-17-librispeech-ml-ranking-v2/preflight-validation.json)记录失败及修复证据。

结果局限于固定 pilot，未测量人时；当前 holdout 已用于最终评分，后续特征或选型改动须使用新协议与新 holdout，不自动启动扩展实验。
