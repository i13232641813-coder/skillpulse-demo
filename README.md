# SkillPulse Demo

一个可直接运行的前后端演示，展示 **Agent Capability Trust Platform** 的本地原型。默认演示页面是 `frontend/lego/index.html`。

项目原创源码由团队按 [Apache-2.0](LICENSE) 许可开源；第三方模型、上游 Skill
和单独下载的外部脚本遵循各自许可，详见 `PROVENANCE.md`。本源码包不含密钥、
账号数据库、运行审计或外部脚本原件。

## 运行方式

1. 安装依赖
2. 启动后端
3. 打开前端
4. 演示本地 Skill 检索、GitHub 在线候选发现、五项基础安检、画布连线、受限子进程运行与审计报告

## 快速开始

```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/frontend/lego/index.html`。Windows 用户优先使用 `setup.cmd`、`start.cmd`；详见 `README-LOCAL.md`。
赛事 Spark 云节点（Linux/aarch64）请使用 `bash setup-dgx.sh`、`bash start-dgx.sh`，通过 SSH 本地端口转发访问；完整步骤见 `README-DGX.md`。
可选 StepFun 3.7 Flash 需求解析和 DGX 本地 CUDA 语义检索的安全配置、实机验收见 `README-STEPFUN-DGX.md`；没有配置时，两者不会伪报成功。
队友共享同一实例的安全接入方式见 `TEAM-DEMO.md`；视频和赛事材料的逐项核对见 `CONTEST-READINESS.md`。

## 在线 Skill 来源发现

默认搜索会同时返回本地可检查资产和公开 GitHub 仓库中可识别的 `SKILL.md` 文件。在线结果列出仓库、发布账号、固定提交、文件路径与原始链接；当前覆盖 NVIDIA、Anthropic、OpenAI、Vercel 的公开 Skill 目录及 GitHub 仓库搜索候选，**不是全互联网爬取、也不是语义检索**。GitHub API 不可用或限流时，界面显示原因，本地闭环继续可用。[GitHub 未登录 API 额度较低](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)，多人连续查询建议控制频率。

线上候选统一标记 `DISCOVERED/UNKNOWN`，不可直接下载或运行。专家可按固定 Git 提交逐文件核对 Git blob 哈希后导入隔离区，进行基础静态初审；不自动安装到 `workspace/skills`，不执行外来代码。Git 提交和哈希只证明取得了与该仓库版本一致的内容，**不证明发布者身份或数字签名**。旧隔离记录若没有导入时的内容摘要会显示 `UNVERIFIED`，不能事后补成“当时已验证”，须重新导入固定版本。第三方标准 Agent Skill 若只有有效 `SKILL.md` 而无平台 `skill.schema.json`，隔离报告会标为 `PARTIAL` 并指出缺少执行契约，不会伪造可运行端口或自动准入。

## 项目结构
- `backend/app.py`：FastAPI 后端
- `frontend/lego/index.html`：主页面；`frontend/index.html` 是健康检查辅助页面
- `workspace/skills/`：演示用 skill 目录
- `workspace/outbox/reports/`：检查报告输出目录
- `scripts/http_smoke.py`：隔离的 HTTP 端到端验收，不使用真实账号或历史运行数据
- `evidence/dgx-spark-cloud-run-20260926.json`：赛事云节点的实测记录；含 NVIDIA GB10 与 CUDA 运算结果

## 架构、Skill 设计与本地算力

前端保留原有三栏布局：左侧输入需求并查看本地资产和线上候选，中间画布连接节点，右侧查看节点配置、运行结果及审计。FastAPI 后端负责检索、准入检查、账号权限、工作流校验与执行。每个可运行的本地 Skill 有明确的名称、版本、输入输出类型、依赖与资源声明；画布连线和后端运行器共用该 Schema，而不是以页面颜色或静态徽章判断能否执行。运行前重新校验节点、必填输入和类型，按拓扑顺序把上一节点输出映射到下一节点输入；本地运行器使用独立临时工作目录和带超时的子进程。该机制能减少演示任务相互污染，但不能取代操作系统级沙箱，未知第三方代码不会进入运行链。

治理层分别给出 Catalog 元数据、Scanned 基础静态规则、Evaluated 样例运行、Signed 本地完整性以及 Documented 文档检查的结果，并保留失败原因。健康报告采用摘要加按需展开详情的呈现方式；版本快照以内容哈希复核，审计记录只保存必要的脱敏摘要。这里的“Signed”不是公钥或上游 OMS 验签，因此界面同时明确显示发布者身份和数字签名未验证。GitHub 候选按仓库和固定提交展示来源，只能进入隔离初审，不会因为搜到 `SKILL.md` 就自动信任。默认搜索是本地词项匹配加公开仓库范围内的发现，不是全网索引；无匹配时返回空结果，不再填充无关 Skill。可选 CUDA 模型仅在真实可用时提供语义排序。

平台适配方面，项目已在赛事云节点验证 GB10 环境与 CUDA 小型向量运算；主要 Skill 治理流水线仍由 CPU 执行。现提供可选的 StepFun 3.7 Flash 主动需求解析接口，以及仅在本地模型与 CUDA 可用时启用的 DGX 语义排序接口。2026-09-28 的一次获授权真实调用成功返回结构化需求、两项本地 Skill 推荐与匹配依据；此前一次调用因结构化校验失败未采信。单条成功案例不代表推荐质量评测完成。StepFun 成功后自动触发本地匹配，GitHub 在线查询仍需用户明确点击。DGX 已有一次固定模型的真实 CUDA 前向推理与 HTTP 检索证据，但没有性能优化对比或多样本质量评测；不能把示例视觉或 RAG mock 输出称为实时模型生成。自动化测试使用临时目录和临时账号，不改写用户已有报告或账号。

## 演示账号
- 首次打开主页面时，在 localhost 创建首位专家账号（密码至少 12 位），然后登录；没有预置账号或默认密码。
- 专家可创建业务/专家账号。业务角色可检索、下载并运行已通过准入的本地 Skill，但不能访问专家审计、版本或私有上传接口。
- 专家可上传 ZIP 到私有待审区；上传者可在个人列表查看记录，其他专家可在初审队列查看待审内容。上传包始终为 `QUARANTINED`，不得下载、加入画布或运行。
- 另一名专家可查看只读文件清单和纯文本源码预览，并记录一次不可覆盖的初审决定；初审通过也不会自动发布或解除隔离。
- 每个已登录账号可保存不含输入值的工作流拓扑，并查看自己的脱敏运行摘要；其他账号不能通过这些 API 读取。
- 此为单机演示身份系统，不具备企业 SSO、MFA、细粒度租户隔离或生产级安全保证。

## 演示要点
- 左侧“一键完成本地 Skill 诊断”选择目标，点击“诊断并生成报告”：`skill-doctor → skill-report` 会实际检查本地文件，再传递 JSON 结果生成报告。
- 可用 `sample-skill-broken` 展示不健康目标的诊断结果；该目标本身不能下载或运行。
- 执行健康检查；损坏 Skill 会被阻止下载与运行，不提供伪造式自动修复
- 分支控制节点仅选择显式 true/false 路由；循环最多重复调用健康本地 Skill 5 次
- 为健康 Skill 创建可复核哈希的本地源码快照；查看新记录的审计哈希链、导出拓扑及本地审计报告

`skill-doctor` 和 `skill-report` 是真实、确定性的本地检查与格式化，不调用大模型；其他示例 Skill 的任务输出仍可能是 mock。`Signed` 阶段目前只做仓库本地 SHA-256 完整性比对，`signature_verified=false`、`origin_verified=false`；因此本地检查通过的 Skill 仍标为 `LOCAL/MED`，不能当作公钥验签或 NVIDIA 官方身份认证。本项目不是生产级安全沙箱。详见 `README-LOCAL.md`、`PROVENANCE.md` 和 `SUBMISSION-EVIDENCE.md`。

赛事提交文案见 `SUBMISSION.md`；技术架构与部署见 `TECHNICAL-ARCHITECTURE.md`、`README-DGX.md`；录制顺序与发布门槛分别见 `DEMO-SCRIPT.md`、`RELEASE-CHECKLIST.md`。征文草稿和团队待填信息见 `TEN-DAY-STORY-DRAFT.md`、`TEAM-SUBMISSION-TEMPLATE.md`。本项目不会把小型 CUDA 冒烟写成真实大模型推理。运行 `.venv/Scripts/python.exe scripts/evaluate_local_search.py`（Windows）或 `.venv/bin/python scripts/evaluate_local_search.py`（Linux）可打印 10 条只读词项检索评测；该结果不代表 StepFun 或 DGX 模型效果。

## 固定外部动作样板（仅已准备的 DGX 环境）

`nvidia-skill-card-validator` 是 SkillPulse 为 NVIDIA/skills 固定提交中
`skill-card-generator/scripts/validate_submission.py` **单一动作**编写的运行适配，
不是完整 NVIDIA Skill，也不代表 NVIDIA 对本项目背书。来源提交、路径、脚本 SHA-256
和许可证在目录卡片及 `PROVENANCE.md` 中公开。准备好的 DGX 节点上，后端每次运行前
核对脚本 SHA-256 和容器镜像 ID，在无网络、无凭据、只读挂载、非 root、超时及资源限制
条件下执行；五项检查中的样例运行使用真实脚本。它可以接在
`skill-doctor → skill-report` 后，令 `report` 文本进入 `card` 端口，形成三节点数据流。
脚本只判断是否遗留 VERIFY/SELECT 人工审核标记，`PASS` 不是安全或合规认证。
Windows 或未准备镜像的 Linux 环境会显示 `UNHEALTHY` 并阻止载入运行。
普通 GitHub 在线候选仍保持隔离，不能因此自动获得发布权限。
