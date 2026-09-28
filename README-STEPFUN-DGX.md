# StepFun 与 DGX 实测路径

当前代码提供两个**可选**能力，均不会在无配置时冒充成功：StepFun 3.7 Flash 解析需求；DGX 上的本地 CUDA 向量模型给已登记 Skill 排序。模型建议始终不能决定 Skill 是否可信，准入仍由五项本地检查控制。

## StepFun：先轮换密钥，再本地配置

若密钥曾出现在聊天或截图中，先到 StepFun 控制台撤销并创建新密钥。不要把密钥写入仓库、命令行参数、`backend/.env`、视频或审计日志。启动服务的进程需要环境变量 `STEPFUN_API_KEY`；中国区默认 `STEPFUN_REGION=china`，全球区用 `global`。两个区域的密钥不可混用。

Windows：关闭先前的 SkillPulse Backend 窗口后，双击 `start-stepfun.cmd`，在不回显的提示中输入**新密钥**；启动器只把它传给本轮服务进程，不写入文件或命令行参数。普通 `start.cmd` 仍不会自动读取 `.env`；如 8000 端口已被占用，它会明确报错而不是悄悄启动 8001 的另一版页面。Linux：在运行 `start-dgx.sh` 的进程环境中设置变量。页面登录后会显示“已配置”或“未配置”；只有用户点击“StepFun 理解需求”，才会向模型发送任务描述、所选场景、运行环境和本地目录的名称/简介，不发送 Skill 源码或 API Key。模型固定为 `step-3.7-flash`，返回值必须是结构化 JSON；页面分别展示意图、约束、所需能力、候选匹配证据、缺失能力与下一步，推荐名称须落在本地目录中，随后重新读取实时安检。每个本地账号最多每小时 8 次演示调用；未配置、网络失败或格式错误时，本地搜索仍可用。

自动化测试只使用假响应，**不产生收费请求**。2026-09-28 经授权的 Windows 页面单次实时调用已返回结构化需求及两项本地 Skill 的推荐理由；另一次较早调用因结构化校验失败未采信。这只是单条成功案例，不等于模型效果评测，也不表示 DGX 节点已配置 StepFun 密钥。DGX 私有实例当前仅验证本地 CUDA 语义检索；不要在演示中把它说成 StepFun 云端解析。

## DGX：让 GPU 服务于真实产品功能

先按 [README-DGX.md](README-DGX.md) 启动赛事节点与 SSH 隧道。`nvidia-smi`/CUDA 冒烟仅证明设备可用，不能证明模型推理已接入。新增的“DGX GPU 语义检索”必须同时满足：

1. 节点上的**同一个 Python 环境**安装与该设备 CUDA 匹配的 PyTorch，以及 `sentence-transformers`；用 `torch.cuda.is_available()` 在该环境确认 GPU。遵循 NVIDIA/PyTorch 的设备适配说明，不要盲目用 CPU wheel 覆盖已有 CUDA 安装。
2. 从模型发布者获取并固定 `intfloat/multilingual-e5-small` 的版本快照，存入个人目录；记录具体 revision/文件哈希。设置 `SKILLPULSE_EMBEDDING_MODEL_DIR` 指向该**本地目录**。服务设置 `local_files_only=True`、`trust_remote_code=False`，运行时不自动下载模型。
3. 重启 SkillPulse 服务；访问 `/api/search/semantic/status`。只有返回 `available=true` 且设备是实际 CUDA GPU，才在页面选择“CUDA 语义检索（DGX 可用）”。实际请求返回模型目录名、设备、耗时及排序得分；若缺依赖/模型/GPU，返回 503，不会悄悄改用 CPU 并声称 GPU 成功。界面显示 CUDA 不构成 DGX 整机身份认证。
4. 与普通本地词项匹配使用同一组需求，记录推荐排序、输入输出、耗时及 `nvidia-smi`/进程证据。小目录不保证 GPU 比 CPU 更快，不能未经测量宣称加速。语义相似度不等于安检通过；健康状态仍由治理器实时判断。

节点实测可使用 `python scripts/collect_platform_evidence.py --target rag-blueprint --gpu-smoke --semantic-query "检查技能并生成报告" --output evidence/dgx-semantic-<新编号>.json`。脚本拒绝覆盖已有输出；只有本地模型在 CUDA 上实际推理成功才会写入 `semantic_inference_executed=true`，并记录设备、模型目录名、相似度、耗时、需求哈希及模型配置哈希。保存实际使用的模型 revision 与完整安装步骤，避免把目录名当成模型身份认证。

建议按模型发布页的 `query:`/`passage:` 前缀准备输入。当前实现只对已登记的本地 Skill 名称与简介排序，不加载外部未审查代码。正式视频应在同一次录制中展示节点、模型状态、语义检索、安检阻断、两节点报告和审计；不得将 StepFun 云端调用称为 DGX 本地推理。

**2026-09-28 实机验收**：在分配的 Linux aarch64/GB10 节点个人目录安装 `torch 2.11.0+cu130`、`sentence-transformers 5.1.1`、`transformers 4.57.1`。模型为 `intfloat/multilingual-e5-small`，通过 ModelScope 获取固定提交 `c86aae44dd61ece026b7d9880f0ebce1f805585d` 的运行文件；`model.safetensors` SHA-256 为 `1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`。模型保存在个人目录，运行时仅从本地加载，不启用远端模型代码。`torch.cuda.is_available()` 为真，设备为 NVIDIA GB10；模型在 Skill 目录上实际推理的脱敏结果、耗时与配置摘要见 `evidence/dgx-model-20260928.json`。另以临时账号/临时 outbox 调用真实 HTTP `/api/search/semantic/status` 和 `/api/search/semantic`，返回 6 个本地候选、设备 NVIDIA GB10，本次接口耗时约 311 ms。此耗时是单次演示测量，不是性能基准或加速证明。排序中包含故意损坏的候选，说明语义得分**绝不等于**准入；下载和运行仍由实时安检控制。节点回归 60 项测试、前端静态检查与隔离 HTTP 冒烟均通过。Windows 上的 StepFun 成功截图是另外一项验证，不能混写为节点本地模型。
