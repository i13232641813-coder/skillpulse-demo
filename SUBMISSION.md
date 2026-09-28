# SkillPulse 赛事提交说明

## 可用于表单的项目介绍

SkillPulse 是面向企业 AI 平台与安全团队的 Agent Skill 资产治理原型。Agent 的能力越来越多地以可安装的 Skill 文件分发；企业真正担心的不是“能否点击安装”，而是安装前能否说清来源、版本、风险、样例有效性和责任边界。SkillPulse 将治理门槛放到下载与运行之前：用户输入自然语言需求，可选择一次真实 StepFun 解析得到任务、约束、能力和推荐理由；本地目录检索给出匹配依据，公开 GitHub 的指定范围发现则显示仓库、账号、固定提交和原始路径。线上候选只进入隔离初审，不会因为有 SKILL.md 就自动被信任或执行。

平台对本地适配的 Skill 分别展示 Catalog 元数据、Scanned 有限静态规则、Evaluated 真实样例、Signed 本地完整性清单和 Documented 文档五项检查。任何一项失败时，后端同时阻止下载和运行；界面摘要只显示结论、风险与下一步，完整证据需点进详情。通过准入后，画布不是静态连线：`skill-doctor` 实际检查本地目录并输出五项 JSON，`skill-report` 接收该文本生成可读 Markdown；运行记录保存节点顺序、版本、输入输出脱敏摘要、错误和 run_id。故意损坏的 `sample-skill-broken` 用于展示反例阻断。导入的第三方 ZIP 另有内容摘要、双人初审与撤销记录，但初审不等于发布。

赛事分配的 NVIDIA GB10 节点上，项目已经分别完成 CUDA 小规模运算和固定版本 `intfloat/multilingual-e5-small` 的真实 CUDA 前向推理；后者只对已登记本地 Skill 简介做语义排序，排序分数不能替代安全安检。另有一个受控外部动作样板：从 NVIDIA/skills 固定提交取得 `skill-card-generator` 的单一标记校验脚本，核对内容哈希，在无网络、无凭据、只读文件系统、非 root、限时限资源的容器中运行。DGX 上已通过 `skill-doctor → skill-report → nvidia-skill-card-validator` 三节点正例与带待审标记的反例；该动作仅检查 Skill Card 中未处理的 VERIFY/SELECT 标记，不是完整上游 Skill、NVIDIA 背书或合规认证。

因此本作品的定位是可重复演示的治理 MVP，而不是全互联网搜索引擎、生产级恶意代码防护或通用第三方 Skill 商店。本地 SHA-256 清单并非发布者公钥签名；部分视觉/RAG 样例仍是 mock 输出。项目公开这些限制，让评委区分真实模型推理、确定性治理与尚未完成的能力。

## 评审演示顺序

1. 说明痛点：Skill 进入 Agent 前缺少统一准入证据。
2. 搜索 `rag`，展示 GitHub 上实际存在的 `SKILL.md`、仓库来源、固定提交和“未验证”状态；如网络可用，专家可导入隔离区，证明外部代码不会直接运行。
3. 在主页面选择本地 `rag-blueprint`，点击“生成检查报告”，展示 `skill-doctor → skill-report` 的真实五项检查、节点间 JSON 传递与审计 `run_id`。在已准备的 DGX 节点可再点“再用固定外部动作验证报告”，形成三节点并展示固定上游来源。
4. 改选 `sample-skill-broken`，展示不健康结论及下载返回 403。
5. 展示完整性与签名的区别：本地 SHA-256 清单一致，`signature_verified=false`、`origin_verified=false`，健康样本仍标 `LOCAL/MED`。
6. 展示云节点证据：NVIDIA GB10 上的 CUDA 运算与固定版本本地向量模型的真实检索，分别说明；不要把 GPU 冒烟冒充模型推理。队友跨设备登录同一实例仍须现场验收。

## 当前已验证的技术栈

- 前端：原有静态 HTML/CSS/JavaScript 页面，含画布与简明安检结果。
- 后端：FastAPI、Pydantic、本地文件目录、SQLite 演示账号与隔离运行产物。
- Skill 治理：Schema/文档检查、有限规则静态扫描、实际 smoke case、本地 SHA-256 完整性比对。
- 云节点：Linux aarch64、NVIDIA GB10、CUDA 13.0；CUDA 冒烟及 `intfloat/multilingual-e5-small` 本地模型前向推理均已实测，后者仅用于本地 Skill 简介的语义排序。

StepFun 需求解析接口和结构化校验已接入，离线自动化测试通过；2026-09-28 一次获授权实时调用成功返回结构化需求、两项本地 Skill 推荐及匹配依据，另一次早先调用因校验失败未采信。只可称“一个真实样例通过”，不得宣称推荐效果已充分评测。普通搜索是有明确覆盖范围的本地词项匹配，不是全网搜索。DGX CUDA 语义检索已在 GB10 上完成模型前向推理和隔离 HTTP 接口验证，证据见 `evidence/dgx-model-20260928.json`；该模型只给已登记本地 Skill 排序，不能证明 Skill 安全，也不能代替 StepFun 云端需求解析。已有一个**固定上游脚本动作**的受控适配与三节点实测，但没有任意线上候选的通用发布路径。不得将示例 `rag-blueprint` 描述成 NVIDIA 官方原件，也不得把 `Signed=PASS` 解读为公钥验签。

## 提交表单仍需补充

- 公开代码仓库 URL：待补充；上传前再次检查 `.env`、账号表、运行数据库和私人资料未进入仓库。
- 演示视频 URL：待补充；按上面的六步录屏，确保能看清节点和结果。
- 十日谈/开发记录 URL：待补充；写真实开发过程，不编造模型与 SDK 成果。
- 团队合影及团队信息：待补充，由团队本人提供。
- 若表单要求 NVIDIA/StepFun 模型清单：可列出 NVIDIA GB10/CUDA 作为运行硬件与计算环境；本地推理模型实际是第三方 `intfloat/multilingual-e5-small`，**不是 NVIDIA 发布的模型**。StepFun `step-3.7-flash` 已在 Windows 页面真实解析过一条需求；未在 DGX 实例配置 StepFun。不得声称已做 10 条模型效果评测或完整外部 Skill 发布；仅有一个固定上游脚本动作的受控运行样板。

项目运行说明见 `README.md`、`README-DGX.md`；设备与模型实测证据分别见 `evidence/dgx-spark-cloud-run-20260926.json`、`evidence/dgx-model-20260928.json`。
