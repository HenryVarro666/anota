# 中英文真实音频闭环：运行与验收

2026-10-17 已完成首个工作包：20 条 FLEURS 真人录音 → 本地真实 ASR → Anota 播放和修正 → 追加式历史与内容哈希导出。人工工作台运行在 <http://127.0.0.1:8421/?batch=1>，批次为 `fleurs-smoke-20`，当前标注员为 `local-reviewer`。人工队列有 20 条任务、0 条人工提交；自动验证使用独立数据库，不能作为人工质量标签。

## 本次实测

| 检查 | 结果 |
| --- | --- |
| 真实数据 | 英语、普通话各 10 条，共 200.36 秒；原始 WAV 共 12,824,200 字节 |
| 真实模型 | `Systran/faster-whisper-base`，固定 revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`，CPU int8、4 线程、beam 5 |
| 推理覆盖 | 20/20 成功；0 失败、0 缺失 |
| 部分执行与强制中断 | 验证开始时有 4 条成功缓存；再完成 2 条后，硬终止下一个持久化为 running 的工作进程；恢复时 `recovered=1`，复用 6 条、完成其余 14 条 |
| 完整缓存重跑 | `cache_hits=20`、`new_inference=0`；流水线内部计时 88.85 ms，未包含 Python 进程启动 |
| HTTP 闭环 | 20 条逐条领取、校验音频字节、自动测试修订、提交；其中 1 条撤销重提；重启数据库仍保持完成状态 |
| 幂等导入 | 同一批次再次导入新增 0 条、跳过 20 条 |
| 追溯导出 | 原始 ASR 保持不变，21 条自动测试修订历史可追溯；连续导出内容哈希一致且独立重算通过 |
| 浏览器操作 | 实际验证英文播放、倍速选择、中英文编辑、中文草稿刷新恢复、Ctrl+Enter 保存、末条撤销和界面导出 |
| 自动回归 | 全套 166 项测试通过；新增语音代码的 Ruff、Git diff 空白检查和依赖一致性检查通过 |
| 初始识别指标 | 英文 WER 23/186 = 12.37%；中文 CER 117/365 = 32.05% |
| 成功推理计时 | 20 条累计原始逐项处理 40.10 秒；P50 1.38 秒、P95 3.55 秒；处理 RTF 0.200。包含各进程首条模型加载等开销，不是纯解码基准 |

这 20 条来自固定训练压缩包前缀，是工程冒烟集。WER/CER 仅描述这些条目；完整分母、规范化和逐项错误见离线评测 JSON。[数据卡](speech-smoke-data-card.md)记录来源、许可、采样和局限。

完整证据保存在本机 `runtime/speech-smoke/`，该目录已被 Git 忽略：

- `manifest.jsonl`、`references.jsonl`：音频来源清单与独立参考库。
- `asr.jsonl`、`asr-state.db`：真实结果、成功缓存和逐项处理状态。
- `workflow-verification.json`：实际硬终止、恢复、缓存、HTTP 闭环和哈希验证摘要，包含原始日志位置。
- `asr-evaluation.json`：完整 20 条分母、语言质量与原始推理/缓存耗时。
- `anota.db`：供人工继续听音的持久数据库。
- `verification/*/workflow-test.db`：自动集成验证数据库，修订明确标为 `AUTOMATED TEST EDIT`。
- `ui-test.db`、`ui-test-exports/`：浏览器操作的隔离测试记录。
- `listening-ready.png`、`ui-test-proof.png`：浏览器验证截图。

## 重现命令

在 Anota 根目录运行，使用现有虚拟环境。离线语音依赖与 Web 依赖分开：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-speech.txt
.venv\Scripts\python.exe -m speechprep.prepare_fleurs --output runtime/speech-smoke
.venv\Scripts\python.exe -m speechprep transcribe --manifest runtime/speech-smoke/manifest.jsonl --output runtime/speech-smoke/asr.jsonl --state runtime/speech-smoke/asr-state.db --model base --revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 --device cpu --compute-type int8
.venv\Scripts\python.exe run.py --db runtime/speech-smoke/anota.db --speech-manifest runtime/speech-smoke/asr.jsonl --batch fleurs-smoke-20 --export-dir runtime/speech-smoke/exports --port 8421
```

工作台已运行时，直接打开网址；重启前先结束此前的 8421 服务。原有 8420 服务保留。`--speech-manifest` 对同一批次幂等，重启不会复制任务。

新数据首次运行可在 ASR 命令末尾加 `--limit 2`，再移除此参数续跑。`--limit` 限制新的处理尝试，已有成功缓存仍输出；未处理数量通过 `remaining` 明示。成功缓存不加载模型。输入音频、语言、模型 revision、推理参数、预处理版本或关键运行库版本变化会改变缓存身份；音频原始哈希每次重用仍会校验。失败行不进入成功缓存，允许恢复后重试。

使用真正的 ASR 子进程重新验证强制终止与恢复：

```powershell
.venv\Scripts\python.exe scripts/verify_speech_workflow.py
.venv\Scripts\python.exe scripts/evaluate_speech_smoke.py --results runtime/speech-smoke/asr.jsonl --references runtime/speech-smoke/references.jsonl --output runtime/speech-smoke/asr-evaluation.json
.venv\Scripts\python.exe -m pytest -q
```

验证器只终止自己启动的 ASR 子进程，保留原始日志，并在独立测试数据库提交自动修订。已经完整缓存时，它采用新的验证状态库以实际触发缺失推理；会重新发布 `asr.jsonl`。人工队列里的已导入 ASR 与修订保持原记录，后续导入新结果须明确使用相应批次。评测 JSON 保存输入文件哈希；ASR 结果更新后重新生成评测。

## 使用听音修正

1. 打开工作台，使用 `local-reviewer`，选择 `fleurs-smoke-20`。
2. 点击播放器或时间戳片段听音；可选择 0.75×、1×、1.25×。`Alt+P` 播放/暂停，编辑框内同样有效。
3. 修改 `Corrected transcript`；需要时按 `start,end,reason` 记录不确定片段。浏览器本地草稿在刷新后恢复。
4. 点击 `Save & Next` 或按 `Ctrl+Enter`。原始 ASR 不被覆盖；空转录允许显式提交。
5. 使用 `Undo` 撤销并重新提交。最后一条完成后仍有撤销入口，旧修订保留。
6. 点击 `Export` 下载版本化 JSON，包含音频哈希、数据/模型来源、原始识别、修订历史和当前有效结果。撤销中的旧修订不作为当前最终结果。

`active_ms` 是本次页面会话中可见且获得焦点的近似交互时间；`elapsed_ms` 是本次页面墙钟时间。跨刷新或多次重开的累计人效尚未建立，不能据此声称节省人工时间。播放器只接受已注册且哈希一致的音频 ID，HTTP 请求不接受任意本地文件路径。

撤销后即使任务租约到期、领取记录被替换，同一标注员仍能找回上次的转录修订；其他标注员的修订不会作为自己的编辑初值。

## 复用与实现边界

复用了原 `data_preprocess/utils/asr_whisper.py` 的模型缓存和推理包装，以及 `audio_io.wav_info()`；来源哈希与必要改动记录在 `speechprep/vendor/SOURCES.json`。适配 IEEE float WAV 时使用 soundfile。没有运行时依赖原 Documents 目录。

Anota 的任务领取、租约、跳过、撤销、审计和内容哈希导出继续复用。数据库通过版本化追加迁移增加语音任务、注册音频和独立转录修订表；语音修正无需填写翻译评分。语音与翻译提交入口互相校验类型，翻译 judge/probe/routing 不处理语音任务。claim 响应采用字段白名单，参考转录、本地路径和任意 metadata 不进入听音页面。

ASR 独立运行，Web 进程不加载语音模型，也不依赖 LLM 可用性。`requirements-speech.txt` 固定了本次验证可用的 `av==16.1.0`；faster-whisper 1.2.1 与新解析到的 PyAV 19 实跑出现 `metadata_errors` API 不兼容。适配器在下载和加载模型前实际解码一个内存 WAV，版本不兼容会给出明确修复信息。

后续工作按一周计划继续：冻结更大的开发/测试集，比较 base/small 与中文适配配置，再引入有对照和新错误统计的 LLM 建议实验。
