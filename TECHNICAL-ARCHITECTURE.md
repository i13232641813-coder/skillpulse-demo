# SkillPulse 技术架构与部署说明

## 核心链路

自然语言任务 → 可选 StepFun 结构化理解 → 明确范围的本地/线上候选发现 → 五项安检与风险门禁 → 健康 Skill 加入画布 → 按节点顺序传递真实数据 → 运行产物与脱敏审计。线上发现仅是候选，不等于已验证、已批准或可运行。

## 组件与数据边界

- 前端沿用三栏静态 HTML/CSS/JavaScript：需求与安检、节点画布、运行与审计。摘要显示结论与风险，完整证据在详情中展开。
- FastAPI/Pydantic 后端负责 Schema、健康检查、准入状态、运行和审计；SQLite 保存演示账号及业务状态。前端状态不构成授权依据。
- 本地 `skill-doctor` 产生真实检查 JSON，`skill-report` 接收上游输出并生成 Markdown。受控第三节点只适配 NVIDIA/skills 固定提交的 `skill-card-generator` 中单一标记校验脚本；不是完整上游 Skill，也不允许任意第三方代码执行。
- 五项安检分别为 Catalog、Scanned、Evaluated、Signed/完整性、Documented。静态规则不等于恶意代码防护，本地 SHA-256 一致不等于发布者公钥签名；不通过或无法验证的候选默认阻断。

## DGX Spark 本地计算与模型

在获授权的 NVIDIA GB10 云节点上，已实测 CUDA 13.0 环境和 `intfloat/multilingual-e5-small` 的真实 CUDA 前向推理。模型固定为 ModelScope revision `c86aae44dd61ece026b7d9880f0ebce1f805585d`，仅对已登记本地 Skill 简介进行语义排序。该模型并非 NVIDIA 发布的模型；本项目未集成 NIM、NeMo 或 TensorRT，不能将硬件平台名称冒充 SDK 使用。当前优化是固定模型版本、本地加载、进程内模型缓存、归一化向量；未做量化、微调或性能增益基准测试。检索分数不决定安全状态。

StepFun `step-3.7-flash` 用于需求解析与推荐解释，曾在单独配置的 Windows 实例完成一次获授权的真实结构化调用。DGX 演示实例未配置 StepFun；两者不是同一在线服务。模型建议不能更改健康检查、签名或运行权限。

## Agent Skills 设计与执行限制

每个本地 Skill 提供 `SKILL.md`、标准化输入输出 Schema、资源/版本说明和 smoke case。运行按拓扑传递类型化数据，失败和缺输入会停止。普通本地 Python runner 是受限子进程原型，不是生产级沙箱。受控外部动作在预备好的 Linux/DGX 容器中使用固定镜像、无网络/凭据、非 root、只读文件系统、独立目录、超时和资源限制；Windows 无法满足时拒绝执行。

## 可复现部署与证据

Windows 本地启动见 `README-LOCAL.md`；DGX 安装、个人 SSH 隧道和运行见 `README-DGX.md`；StepFun 配置与边界见 `README-STEPFUN-DGX.md`。实测和限制见 `SUBMISSION-EVIDENCE.md`、`CONTEST-READINESS.md`，上游来源见 `PROVENANCE.md`。源码包不包含密钥、账号库、审计原文、模型权重或私有节点登录资料。
