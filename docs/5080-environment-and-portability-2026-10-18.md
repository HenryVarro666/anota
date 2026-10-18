# 5080 环境检查与 CPU / GPU / Cluster 扩展约束

检查日期：2026-10-18（America/Chicago）。本文保存 **RTX 5080 部署前检查**：
当时通过 Tailscale SSH 检查环境，未安装或升级软件、部署项目、下载模型或检查 H100。
本文中的“尚未验收”均指这一阶段；当天后续完成的独立环境、真实 CPU/CUDA ASR 与
Web 冒烟验收见[部署与真实音频验收](5080-deployment-2026-10-18.md)。

范围仅为 `anota`。其他 TTS 合成、训练仓库需要分别验证。本轮文档整理只复核本地
保存的部署证据，没有重新连接远端；下列硬件、旧环境和解码检查保留当日检查记录，
不作为当前机器状态的重新测量。示例使用 `<user>`、`<host>` 和 `$HOME`，实际连接信息
保留在本地部署记录中。

## 部署前结论与检查记录

硬件和现有 PyTorch 环境具备 CPU / GPU 执行能力，但完整项目尚不能直接认定为可运行。
语音环境存在已复现的解码兼容错误；本次没有完成真实音频模型推理或 Web 启动验收。

| 项目 | 当日记录 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5080，16303 MiB，compute capability 12.0 |
| 驱动 | 595.84；`nvidia-smi` 显示 CUDA 13.2 |
| 系统 CUDA 编译器 | `/usr/bin/nvcc`，12.4.131 |
| 系统 Python | `/usr/bin/python3`，3.14.4 |
| 可用 Python 3.12 | `$HOME/.local/bin/python3.12`，3.12.14 |
| 内存 / 磁盘 | 检查时约 26 GiB available；home 所在分区约 3.4 TiB 可用 |
| GPU 占用 | 检查时约 1631 MiB，已有 ComfyUI 与桌面进程 |
| 集群与容器命令 | 当前 SSH PATH 未发现 `sbatch`、`srun`、`mpirun`、`docker`；不代表其他机器不可用 |

三个 CUDA 数字属于不同层次：驱动报告、系统编译工具与 PyTorch 自带运行时。
本次 PyTorch CUDA 13.0 实测通过，不能根据系统 `nvcc` 12.4 推断它不可用；
也不能据此认为自定义 CUDA 扩展编译已经通过。

`$HOME/.local/share/local-ai/training-env/bin/python`：

- Python 3.12.14，PyTorch `2.14.1+cu130`，`torch.version.cuda == '13.0'`。
- `torch.cuda.is_available()` 为 True，编译架构列表包含 `sm_120`。
- CPU FP32 矩阵乘法、GPU 256×256 FP32 / FP16 矩阵乘法及同步均通过，结果均为有限值。
- 未安装项目 Web、faster-whisper、soundfile 或 scikit-learn 依赖。

`$HOME/.local/share/local-ai/webui-env/bin/python`：

- Python 3.12.14；PyTorch 导入后为 `2.14.1+cu130`，CUDA 可用。
- faster-whisper 1.2.1、CTranslate2 4.8.3、PyAV 19.0.1、soundfile 0.13.1、
  scikit-learn 1.9.1、NumPy 2.5.4。
- FastAPI、uvicorn、httpx、NumPy、SciPy、scikit-learn、soundfile、joblib、
  threadpoolctl 导入检查通过；这不等于精确依赖约束或应用启动验收通过。
- CTranslate2 能查询 CPU 和 CUDA 的支持精度；CUDA 列表包含 float16 / int8_float16。
  尚未加载 Whisper 权重验证实际 CUDA 算子和动态库。
- 对内存生成的短 WAV 调用 faster-whisper `decode_audio`，复现
  `TypeError: open() got an unexpected keyword argument 'metadata_errors'`。
  项目的 `requirements-speech.txt` / `requirements-audioqc.txt` 已固定
  `av==16.1.0`，现有环境不符合要求。

还发现 ComfyUI、vLLM 和桌面虚拟环境；它们服务于其他应用，不能作为项目依赖锁。
在该用户 home 最大深度 4 的目录检查中未发现 `anota` checkout；没有做全盘搜索。

## 当时提出的配置与验收顺序

使用已有 Python 3.12.14 建立项目独立环境，按仓库的分层 requirements 安装。
CPU Web 与模型推理分别维护环境；不要将 ComfyUI / WebUI / vLLM 的环境改成项目环境。
Web 依赖应用 `constraints-runtime.txt`；语音依赖遵守仓库 PyAV 固定版本。
PyTorch 可用不代表 faster-whisper 可用：后者实际执行使用 CTranslate2。
其 GPU 动态库要求应按安装版本核对
[CTranslate2 官方安装说明](https://opennmt.net/CTranslate2/installation.html)。

配置完成后依次验证：Web 启动与一次真实请求、内存 WAV 解码、同一小批真实音频的
CPU ASR、GPU ASR、QC 与 SpeechRank bundle 加载、断点恢复。分别记录设备、精度、
权重 revision、依赖、耗时和显存。CPU / GPU 输出不要求逐字或浮点逐位一致。

后续[部署记录](5080-deployment-2026-10-18.md)确认了真实 WAV 解码、CPU/CUDA ASR、
CPU QC、缓存续跑、HTTP 音频与队列检查。该次 SpeechRank 检查只完成审核 bundle
字节哈希验证、打包和使用替身模型的 runtime 合约回归，不能替代真实模型加载或预测验收；
其他独立 ML 集成记录见 [ML 队列指南](audio-ml-review-queue.md#verification)。

## 扩展方向：已存在的能力与待实现部分

用户要求后续兼容 CPU、单机 GPU 和 cluster。以下是设计约束，尚未实现集群部署。

| 层次 | 当前能力 | 后续约束 |
| --- | --- | --- |
| Web / 人工审核 | 单进程 CPU FastAPI + SQLite；文本评审模型通过 HTTP 适配器接入 | 保持无 GPU 启动依赖；离线 ASR 由独立 worker 执行 |
| 离线 ASR | `python -m speechprep transcribe` 已支持 `--device cpu/cuda`、`--compute-type`、`--cpu-threads` | 显式选择设备和精度；GPU 作业使用调度器分配的可见设备；申请 GPU 的作业失败时明确报错 |
| QC / SpeechRank | CPU 分析与可选 ML 推理；存在固定 bundle 与模型适用域 | 保留 CPU 路径；不能将其他 ASR / 精度产生的数据自动视为已验证域 |
| 多任务 / 多 GPU | 离线流水线是串行运行，状态库与锁按单次运行管理 | 先按 manifest 分片，每个作业独立输出和状态库，再校验合并；不能让多个节点共同写同一 SQLite |
| 集群批处理 | 尚无已验证的调度适配器 | 优先增加 Slurm job array 等薄封装，调用同一 CLI；数据和模型缓存先准备，再由计算节点运行 |
| 在线水平扩展 | 当前应用信号量仅约束单进程 | 多副本前需共享事务存储、持久队列、幂等与任务租约；不能简单扩大 uvicorn worker 数 |

推荐先采用“一个标注服务 + 独立 CPU/GPU worker”的结构。批量独立音频按样本分片，
通常无需引入分布式训练；仅在具体模型训练或单模型跨卡需求明确后再选择 DDP / FSDP
等训练机制。集群适配不得改变冻结实验的源数据、协议或结果。

ASR 缓存配置包含设备、精度、beam size、CPU 线程数、VAD/预处理，以及
faster-whisper / CTranslate2 / PyAV 版本；不代表完整 OS 或驱动身份都进入缓存键。
CPU 与 GPU 运行应保留各自身份和输出。
后续增加分片时，还需验证样本恰好覆盖一次、失败重试、恢复、合并冲突及完整来源记录。
容器可作为可复现交付方式；具体使用 Docker 还是 Apptainer，应待实际集群策略明确后选择。

- **部署前阶段：** CPU / GPU 基础算子通过，完整项目迁移未验收。
- **后续阶段：** 20 条真实音频的 CPU/CUDA 工程冒烟验收通过，见[部署报告](5080-deployment-2026-10-18.md)。

Cluster、H100、多节点恢复与生产容量仍未验收。
