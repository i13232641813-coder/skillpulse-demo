# 来源与信任边界

`workspace/skills` 中的 `rag-blueprint` 和 `tao-generate-image-grounding` 是
SkillPulse 的**本地演示复刻样本**，不是从 NVIDIA 官方目录下载、验证签名后原样发布的
Skill。`skill-doctor` 与 `skill-report` 是本项目自研的本地 Skill。目录名、
`SKILL.md` 或 `skill-card.md` 中出现 NVIDIA 技术词，不构成官方来源证明。
`backend/catalog/skills.json` 仍保留部分没有对应本地目录的历史参考条目，
现在统一标为 `REFERENCE_ONLY/UNVERIFIED`；检索接口会过滤这些条目，不把它们
当作可下载、可执行或已经核验的资产。真正的准入状态以当前磁盘检查结果为准。

在线发现结果单独显示 GitHub 仓库、账号、固定提交、`SKILL.md` 路径和原始链接；
它们一律为 `DISCOVERED/UNKNOWN`，不混进本地 `HEALTHY` 列表。专家主动导入时按固定
提交核对 Git blob 哈希，之后仍仅存放于私有隔离区，不自动运行或发布。仓库账号名、
提交哈希或文件存在都不能证明发布者身份；在线结果的 `origin_verified` 和
`signature_verified` 均为 `false`。GitHub 网络中断或额度耗尽会如实显示，不使用
旧的静态数据伪造“刚刚搜到的全网结果”。
若仓库根目录存在常见许可文件，页面只提供指向固定提交的链接；`license`
仍为 `UNKNOWN`。文件名存在不代表已核实许可条款、Skill 适用范围或再分发权，
外部样板发布前须人工阅读并记录结论。

隔离版本现在以内容摘要、双人初审和不可逆撤销管理：初审通过只转为
`APPROVED_FOR_EVALUATION`，表示可在未来满足隔离条件后安排测试，**不是**
`EVALUATED` 或 `RELEASED`，且仍不能下载、加入画布或运行。初审后的文件变化会
自动撤销评估资格；专家撤销写入独立记录，不能覆盖原有初审。普通在线候选没有通用
外部代码执行或正式发布入口，故不能把它们称为已准入的生产能力。
2026-09-28 在分配节点的受限容器中，仅对固定提交 `d8519c57da6db5d9bea274ec1724a4a7a56a3dee` 的 NVIDIA `skill-card-generator/scripts/validate_submission.py` 执行过一次卡片标记校验。脚本 Git blob 与固定提交一致，运行输出为“无未处理标记”；脱敏参数、哈希与限制见 `evidence/external-skill-action-20260928.json`。这只是该 Skill 的**一个验证动作**，未完成整套卡片生成或通用隔离区到发布的状态转换。
此后新增 `nvidia-skill-card-validator` 作为**人工限定的单动作适配积木**：仅在已准备的 DGX 环境、脚本 SHA-256 与固定容器镜像 ID 均匹配且真实 smoke case 通过时，才允许载入画布。2026-09-28 在临时审计与产物目录中复测了 `skill-doctor → skill-report → nvidia-skill-card-validator` 三节点数据流，并用含 VERIFY 标记的反例确认阻断。此动作不代表完整上游 `skill-card-generator` 或任意 GitHub 候选已发布。
DGX 上的本地向量模型只对已登记的 Skill 名称与简介排序；它不会验证 GitHub 发布者身份、许可证、输入输出契约或运行安全，也不会把隔离候选自动变成可执行积木。让一个外部候选真正可用仍须选定固定提交并审核许可/依赖，补齐有作者与版本记录的运行适配，在无凭据、默认断网、有资源限制的隔离环境完成真实样例评估，最后经过单独的发布审批和撤销校验。当前这些步骤尚未完成，`can_download` 与 `can_run` 继续保持 `false`。
若初审或撤销记录已落盘、中央审计链却写入失败，接口会明确返回失败与“状态已保存”
的提示，操作者须检查版本状态，不应反复提交。

官方项目仅作为设计参考：

- NVIDIA Agent Skills 目录：https://github.com/NVIDIA/skills
- NVIDIA RAG Blueprint Agent Skills：https://github.com/NVIDIA-AI-Blueprints/rag/tree/main/skills

本项目的 `Signed` 检查实际是仓库维护的 SHA-256 文件清单比对。
它能发现与当前清单不一致的文件，但**不能**证明发布者身份，也不能防止有仓库写入权限者
同时改动文件和清单。历史 `skill.oms.sig` 文件不被视为有效签名或准入依据。
如果未来接入 NVIDIA 官方包，应保存上游 URL、提交哈希、包摘要、许可证，使用
NVIDIA 发布的信任锚与 OMS 验签工具验证，且与本地演示样本分开展示。

现有 `BENCHMARK.md` 如标注“未测量”，就不能在路演中引用旧百分比。安检通过仅
说明本地五项基础门禁通过，不等于完整恶意代码防护、模型效果或合规认证。
