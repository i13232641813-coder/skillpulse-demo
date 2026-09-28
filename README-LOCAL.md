# SkillPulse 本地运行（Windows）

## 首次配置

1. 安装 Python 3.10 或更高版本，并确保 `python` 命令可用。
2. 双击 `setup.cmd`。它会在项目目录创建 `.venv`，并安装 `backend/requirements.txt` 中固定的直接与间接依赖版本。
3. 本地 Skill 检索、安检和演示执行不需要 `backend/.env`、API Key 或付费模型。页面还可从公开 GitHub 仓库发现带 `SKILL.md` 的候选，并显示仓库、提交和来源链接；此功能需要后端能访问 GitHub，属于有范围的在线发现，**不是全网或语义检索**。外部候选只能进入隔离初审，不能直接运行。`skill-doctor → skill-report` 实际检查本地 Skill 文件并生成报告；其他示例 Skill 的任务输出仍可能是 mock，均不代表真实模型或 NVIDIA 硬件推理。
4. 双击 `start.cmd`。浏览器会打开 SkillPulse 主页面；首次使用需自行创建专家账号（密码至少 12 位），没有默认密码。API 文档地址为 `http://127.0.0.1:8000/docs`，若 8000 端口被占用则使用 8001。

## 手动启动

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

随后打开 `http://127.0.0.1:8000/frontend/lego/index.html`。旧的 `frontend/index.html` 只是辅助页，未接入 Phase3 登录流程。

`.env` 和 `.venv` 是本机文件，不应提交到公开仓库。不要使用或分享未经密钥持有人许可的 API Key。

## Phase 3 本地角色与上传待审区

- 账号和会话数据保存在 `workspace/outbox/accounts.sqlite3`；会话使用 HttpOnly、SameSite=Strict Cookie，8 小时后失效。仅首次从 localhost 可以初始化专家账号。请勿把此服务暴露到公网或共享网络；它没有 SSO、MFA、登录限流或成熟的租户隔离。忘记密码时，在**原项目文件夹**（能看到 `.venv` 和 `scripts` 的目录，不是在 ZIP 压缩包预览中）双击 `reset-password.cmd`，按提示输入账号和两次新密码；旧会话会失效，账号、流程与审计文件不会被删除。密码不会出现在命令行参数中。不要把账号库或重置工具的终端访问权交给不受信任的人。
- 专家可以切换专家/业务视图，并创建业务或专家账号。业务账号只能进入简化视图；真正的权限检查由后端执行，隐藏按钮不构成安全边界。新审计事件会关联本地 actor_id，但客户端上报的操作类型仍不能单独证明真实用户行为。业务账号仍可操作公共健康 Skill，暂不支持私有工作流或完整的每用户数据隔离。
- 专家上传 `.zip` 后，文件被保存在 `workspace/outbox/private_skills/<用户ID>/<上传ID>/`；上传者可在个人列表查看记录，其他专家可从初审队列读取待审内容。ZIP 限 5 MiB / 100 文件 / 解压后 20 MiB；拒绝路径穿越、符号链接和常见密钥文件。静态预检只解析元数据、文档与少量代码模式；**不执行上传代码**，也不把上传包加入可运行 Skill 目录。
- 上传包的 Signed 永远标记 `UNVERIFIED`、Evaluated 标记 `NOT_RUN`，整体 `QUARANTINED/HIGH`。本阶段没有自动审批、晋级或发布流程；即使静态检查通过也不能下载、加入画布或运行。

## Phase 4 本地初审与账号数据归档

- 专家可查看全体隔离上传的待审队列、文件名/大小/SHA-256 清单，以及不超过 256 KiB 的文本文件纯文本预览。上传内容不可信；不要把文件里的指令当作系统操作要求。
- 上传者不能复核自己的包。另一名专家可记录一次 `PRELIMINARY_PASS`、`CHANGES_REQUESTED` 或 `REJECTED`，需填写理由；决定不可覆盖，变更须重新上传。只有元数据、基础静态扫描和文档均通过时才允许标记“初审通过”。无论决定如何，包仍为 `QUARANTINED/HIGH`，未完成受信任数字签名和安全执行验证，也没有自动晋级/发布入口。
- 登录后的流程拓扑保存在 `workspace/outbox/user_workflows/<用户ID>/`，输入值不保存；载入时会重新核对 Skill 版本与当前安检状态，必填输入需重新填写。新运行摘要保存在 `workspace/outbox/user_runs/<用户ID>/`，只包含节点状态、字段名和内容哈希等脱敏信息。旧的 `workspace/outbox/catalogs/` 历史文件原样保留，不自动迁移或改写。
- `/api/me/workflows` 和 `/api/me/runs` 按登录账号读取；初审队列及全局审计仅专家可见。该隔离只在本地应用 API 层生效：同一操作系统账号若能直接读取 `outbox` 文件，仍可看到其他账号的本地文件；它不等于租户级或生产级隔离。审计链和初审文件不是跨文件原子事务，也没有外部锚定。
- 回归命令：在 `backend` 目录运行 `..\.venv\Scripts\python.exe -m unittest discover -s tests`；返回项目根目录运行 `.venv\Scripts\python.exe check_frontend.py` 和 `.venv\Scripts\python.exe scripts/http_smoke.py`。HTTP 冒烟测试使用隔离的临时目录，不会写入真实 `workspace/outbox`。

## Phase 2 本地控制节点

- **分支**：先从 `rag-blueprint` 的输出端连接分支输入端；在分支的输入表单填 `condition=true` 或 `false`，再分别拉两条出线并指定路由。运行时只执行被选中的路径，另一条显示 `SKIPPED`。条件是显式布尔值，不执行表达式或用户代码。
- **循环**：在循环节点填写 `value`、`count`（1–5）和 `target_skill`（例如 `skill-doctor`）。目标 Skill 必须通过当前安检且恰有一个输入、一个输出；每轮把上轮输出传给下轮。循环是有界的重复调用，不允许图上形成循环依赖。
- **版本与依赖**：版本显示以磁盘 `skill.schema.json` 为准。只有健康 Skill 的当前版本可创建内容寻址的源码 ZIP 快照；下载前会复核 ZIP 的 SHA-256。旧版本记录保留并标为未验证，不证明对应源码仍存在。快照会拒绝 `.env*`、私钥等明显凭证文件，但不能保证识别所有内嵌秘密；创建前仍应人工检查源码。依赖检查只比较 Schema 声明中的精确 `package==version`，未探测本机安装包、GPU 驱动或模型兼容性。
- **导出与审计**：YAML 是拓扑与版本快照，不含运行输入或密钥；API 描述是本地原型接口说明，不是可独立部署的服务。新运行/部分操作事件写入本地哈希链，运行摘要与链中的内容哈希关联；旧审计文件原样保留，但在 UI 和导出中隐藏旧详情并标记未验证。哈希链可发现内容修改与重排，**不能**防止有文件写权限者删除链尾或重建整链；没有外部时间戳或身份签名。页面的添加/移除/连线记录由客户端上报，没有用户身份保证，不是完整操作审计。审计报告不是第三方合规认证。

运行器使用临时目录中的独立子进程并有超时，但 Windows 下没有可靠的内存限制或 OS 级隔离。请勿把未知恶意 Skill 当作已经安全隔离。`skill-doctor` 的五项结果来自本地检查，`skill-report` 只格式化其结果；其他示例能力仍可能是 mock，且没有真实模型推理。`Signed=PASS` 仅表示本地 SHA-256 清单一致，不表示真实数字签名；API 同时返回 `signature_verified=false` 和 `origin_verified=false`。提交证据要求见 `SUBMISSION-EVIDENCE.md`。
