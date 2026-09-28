# 赛事证据采集清单

2026-09-26 已在赛事分配的 Spark 云节点执行一次实测：节点返回 NVIDIA GB10、CUDA 13.0；回归测试、隔离 HTTP 闭环、CUDA 向量运算及结果校验均通过。原始机器记录保存在 `evidence/dgx-spark-cloud-run-20260926.json`。脚本的 `dgx_spark_verified=false` 只表示没有通过软件查询独立认证整机产品型号；实测事实与节点来源应分别表述。

## 可复现的本地闭环

1. 启动 `start.cmd` 并登录。
2. 在左侧“一键完成本地 Skill 诊断”选择 `rag-blueprint`，点击“诊断并生成报告”。
3. 记录两节点 `skill-doctor → skill-report` 的运行状态、五项检查、报告和审计 `run_id`。
4. 改选 `sample-skill-broken` 再运行；应显示 `UNHEALTHY/HIGH`，证明不健康
   目标可以被诊断，但仍不能被下载或作为可执行节点运行。

## 设备事实（必须在真正的 DGX Spark 上复跑）

在目标设备的项目根目录执行：

```powershell
python scripts/collect_platform_evidence.py --target rag-blueprint --output evidence/dgx-spark-run-01.json
```

追加 `--gpu-smoke` 可执行一次小规模 GPU 计算：优先使用现有 PyTorch 做
1024×1024 矩阵乘法；未安装 PyTorch 时，若节点已装 CUDA Toolkit，使用 `nvcc`
编译 100 万元素的向量加法。两种路径都校验结果并记录设备与耗时，均不安装依赖、
不下载模型。CUDA 路径可称为真实 NVIDIA CUDA SDK 冒烟测试，但**不是模型推理**。
若环境不支持，证据会如实标记未执行，不要改写结果。

不要覆盖旧证据。附上 `nvidia-smi`、设备型号、启动服务与浏览器运行的同次录屏，
核对 JSON 中的设备信息和报告摘要。脚本的 `dgx_spark_verified` 故意保持 `false`：
单靠软件查询无法独立认证设备身份，需人工审核实物/系统资料。

**当前 SkillPulse 安检是 CPU 工作负载。可选 GPU 冒烟不改变这一事实。**
要申报“DGX Spark 上的模型推理/加速”，需另外接入一个真实模型或 NVIDIA SDK
工作负载，测量输入、输出、耗时和 GPU 占用，再附上可复现的依赖与命令。

## 提交前

- 公开代码仓库链接；README 说明作品亮点、架构、部署、使用的真实技术栈与限制。
- 作品演示视频：正常检查、不健康阻断、报告下载、审计记录，以及真实设备证据。
- 团队资料按赛事表单提交。没有做过的 NVIDIA SDK/StepFun 模型调用不得写作已完成。
