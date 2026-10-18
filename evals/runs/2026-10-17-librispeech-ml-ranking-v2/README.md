# LibriSpeech 轻量学习排序 v2：实际运行结果

2026-10-17：正式运行完成，内部完整性验证通过；独立审计通过 577/577 项检查（29.73 秒）。所有文件已保存本地，未执行 Git commit/push。

主评估是按说话人隔离的 fresh holdout：480 条、16 位说话人。10%/20% 音频时长预算下，legacy 捕获 14.97%/28.77% 编辑错误，confidence 捕获 28.65%/46.19%；同一 holdout 随机均值为 9.78%/19.76%，最短优先为 12.26%/25.94%，冻结 QC 为 5.42%/14.06%。这是固定 pilot 的离线排序结果，不能换算人工工时或纠错收益，预测值也不是校准的正确概率。

开发样本按每说话人最多 30 条固定抽样，共 2,184 条：train 1,290、tune 414、holdout 480。候选仅拟合 train，两个 arm 分别只用 tune 选择，然后用 train+tune 的 1,704 条 refit。旧 test 的 5,559 条在 v1 已暴露，只作次要复用 benchmark；没有保存真实置信字段，因此不评估 confidence test。

## 固定终点与分组结果

下表均为错误覆盖率；随机列是 1,000 个固定随机顺序的均值及 2.5%–97.5% 分位范围，仅描述顺序波动，不是总体置信区间，也不构成显著性检验。每个 split 在自身时长预算内独立排序，不能把分组百分比直接平均为 pooled。实际选择时长、计数、剩余 WER 和全部 5/10/20/30/50/100% 端点见 [完整结果](outputs/RESULTS.md)及 summary。

### fresh_holdout

| 分组（条数 / 说话人） | 预算 | random 均值 [2.5%,97.5%] | QC | shortest | legacy | confidence |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| pooled (480 / 16) | 10% | 9.78% [6.58%, 13.29%] | 5.42% | 12.26% | 14.97% | 28.65% |
| pooled (480 / 16) | 20% | 19.76% [15.35%, 24.39%] | 14.06% | 25.94% | 28.77% | 46.19% |
| split:dev-clean (240 / 8) | 10% | 9.72% [5.88%, 14.06%] | 8.50% | 14.05% | 17.32% | 27.12% |
| split:dev-clean (240 / 8) | 20% | 19.75% [14.38%, 26.14%] | 16.99% | 25.49% | 28.10% | 41.18% |
| split:dev-other (240 / 8) | 10% | 9.87% [5.54%, 14.29%] | 5.97% | 11.73% | 13.65% | 24.73% |
| split:dev-other (240 / 8) | 20% | 19.86% [13.85%, 25.38%] | 11.30% | 23.24% | 24.09% | 46.06% |

![fresh_holdout](outputs/fresh_holdout.png)

### previously_exposed_test

| 分组（条数 / 说话人） | 预算 | random 均值 [2.5%,97.5%] | QC | shortest | legacy | confidence |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| pooled (5559 / 73) | 10% | 9.99% [9.09%, 10.99%] | 10.06% | 14.61% | 17.56% | 不适用 |
| pooled (5559 / 73) | 20% | 20.00% [18.68%, 21.24%] | 19.81% | 27.15% | 29.52% | 不适用 |
| split:test-clean (2620 / 40) | 10% | 10.02% [8.52%, 11.54%] | 9.68% | 13.78% | 17.89% | 不适用 |
| split:test-clean (2620 / 40) | 20% | 19.98% [17.99%, 21.96%] | 18.17% | 25.99% | 30.31% | 不适用 |
| split:test-other (2939 / 33) | 10% | 9.99% [8.90%, 11.13%] | 9.37% | 13.58% | 16.10% | 不适用 |
| split:test-other (2939 / 33) | 20% | 20.00% [18.62%, 21.41%] | 19.97% | 25.50% | 28.29% | 不适用 |

![previously_exposed_test](outputs/previously_exposed_test.png)

## 选型、成本与校验

14 个候选（每 arm 7 个）全部完成，无候选训练/预测警告；两个最终 refit 也无警告。legacy 选择 `hgb-leaves-3-l2-1`，confidence 选择 `hgb-leaves-3-l2-10`，均为 Poisson loss HistGradientBoostingRegressor、每棵树最多 3 个叶节点、100 次迭代。最终特征分别为 67 / 98 列，训练中未产生额外缺失指示列。选择目标是 tune 10%+20% 捕获错误数，并列按 candidate ID；未按最终结果更换赢家。

新 dev 首次 ASR 2,184 次、零失败，用时 886.63 秒；新 test ASR 调用为零。正式评估共 187.13 秒：输入验证/QC/特征 135.23 秒，train/tune 评分 0.71 秒，候选拟合/选型/refit 23.20 秒，最终预测/计划 4.94 秒，最终评分/渲染 18.91 秒。允许 QC 内容缓存，且并发机器负载未隔离；这些是本机观察耗时，不含下载与历史 test ASR 完整成本。

`evaluation-seal.json` 于 08:23:17.532974 UTC 生成，heldout gold 于 08:23:18.268670 UTC 开始评分。内部验证通过：7,743 条原音频哈希检查、源码/环境/输入未变、全部最终预算计划保持封存选择、旧 test 基线精确复现 v1。独立审计：**通过 577/577 项检查，用时 29.73 秒**。这些是审计断言，不是新增测试用例数。

## 冻结前 manifest 事件

第一次 freeze 在创建 study 目录前因 test manifest 字节哈希不一致停止，未评分真实 gold。assignment 绑定官方 prepared manifest（相对 audio_path），v1 snapshot 使用绝对 audio_path；两者全部 5,559 条在统一解析路径后完整元数据相同。修复保留两种独立字节身份，源 manifest 必须匹配原 assignment 哈希，并严格比较完整解析后行；未修改 v1、assignment 或已完成 ASR。修复后 50 项相关测试、Ruff、diff 检查通过；此前全项目 367 项测试通过。详见 [preflight 记录](preflight-validation.json)。

## 证据索引

- [冻结协议](source-snapshot/docs/audio-qc-ml-ranking-protocol-v2.md)、[配置](config.json)、[输入/源码/环境身份](protocol.json)、[冻结哈希](frozen-hashes.json)。
- [全部候选](outputs/candidate-trials.json)、[选择记录](outputs/selected-models.json)、[legacy 模型说明](outputs/model-legacy.json)、[confidence 模型说明](outputs/model-confidence.json)。
- [最终排序 seal](outputs/evaluation-seal.json)、[holdout summary](outputs/fresh_holdout-summary.json)、[旧 test summary](outputs/previously_exposed_test-summary.json)、[置信字段审计](outputs/confidence-availability.json)。
- [耗时](outputs/timings.json)、[内部校验](outputs/validation.json)、[输出哈希](outputs/artifact-hashes.json)。逐条评分、预测、随机原始试验和最终预算计划保存在 outputs，原始输入依 protocol 中本地路径及哈希定位。

## 解释与后续边界

confidence 相对 legacy 在 pooled holdout 10%/20% 的错误覆盖差值为 +13.68/+17.42 个百分点；两个 arm 和两个 split 均保留，不事后指定效果较好者为唯一主模型。旧 test legacy 为 17.56%/29.52%，高于该队列 shortest 的 14.61%/27.15%，但历史暴露不能撤销。v1 QC 未建立有用排序增益的负结果仍完整保留。

holdout 仅 16 位说话人，样本受每人 30 条上限约束，不能自动外推完整 dev 或业务分布。公开语料的 fresh 只表示本轮没有用其 gold 训练/调参，不保证 ASR 预训练未见过。错误覆盖统计选中整录音中包含的编辑错误，不测量人是否找出或修正。

以后增加 LLM、多 ASR 或新特征时，应先建立新协议和开发循环，再使用新的未用于选择的 holdout；当前 holdout 不再用于优化后声称独立验证。此处只记录后续方向，没有自动启动新实验。

## 独立审计记录

[审计结果](../../reviews/librispeech-ml-ranking-v2-audit.json)与[可复验审计源码](../../reviews/librispeech-ml-ranking-v2-audit-source.py)记录 577/577 项检查通过。范围包括全部最终模型参数与预测重放、14 个候选 tune 目标、7,743 条原始音频哈希、全部角色 S/D/I、全部固定预算、每组 1,000 个随机排序计划及随机试验汇总。逐 seed 随机指标独立重算限定为 4 个固定 seed（20261017、20261018、20261517、20262016）；审计没有从头重新训练模型。

首次外部审计脚本误要求旧 test 特征同时具有 confidence，与预注册的 test 仅 legacy 冲突。只修正外部断言，保留[首次脚本失败记录](../../reviews/librispeech-ml-ranking-v2-audit-initial-script-schema-failure.json)和[修正说明及源码哈希](../../reviews/librispeech-ml-ranking-v2-audit-correction.json)；正式实验 outputs 未修改。测试证据仍为此前全量 367 项及 manifest 修复后相关 50 项，不将 577 项审计检查计作测试数量。
