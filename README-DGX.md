# 在赛事 Spark 云节点运行 SkillPulse

本说明适用于组织方分配、硬件报告为 NVIDIA GB10 的 Linux 节点。项目只在个人目录安装依赖，不修改系统 Python、共享模型或节点配置。不要把登记表、密码、`backend/.env` 上传到节点或公开仓库。

## 部署与启动

将提交包解压到个人目录后，在项目根目录执行：

```bash
bash setup-dgx.sh
bash start-dgx.sh
```

服务只监听节点的 `127.0.0.1:8000`，不会直接暴露到公网。在个人电脑另开终端建立 SSH 隧道（用登记表中的真实连接参数替换占位符）：

```bash
ssh -L 8000:127.0.0.1:8000 -p <SSH端口> <用户名>@<节点地址>
```

随后在个人电脑打开 `http://127.0.0.1:8000/frontend/lego/index.html`，首次创建本地专家账号。若本机 8000 端口已被占用，可将隧道左侧端口改为 8001，并在浏览器访问 8001；远端服务端口保持 8000。建议在节点的 `tmux` 会话中运行服务，退出时停止自己的服务，不重启或关闭共享节点。

## 可重复验收

```bash
(cd backend && ../.venv/bin/python -m unittest discover -s tests -q)
.venv/bin/python check_frontend.py
.venv/bin/python scripts/http_smoke.py
.venv/bin/python scripts/collect_platform_evidence.py --target rag-blueprint --gpu-smoke --output evidence/another-run.json
```

最后一条命令要求输出文件尚不存在，避免覆盖旧证据。`http_smoke.py` 使用临时目录、随机测试账号和本机随机端口，不触碰实际账号或历史审计。项目的主要安检逻辑仍运行在 CPU；可选 GPU 冒烟通过 PyTorch 或现有 CUDA Toolkit 执行小规模计算，**不是模型推理**。

本次已在组织方提供的云节点上观察到 NVIDIA GB10、CUDA 13.0，并完成回归、HTTP 闭环及 CUDA 结果校验；具体机器输出见 `evidence/dgx-spark-cloud-run-20260926.json`。`dgx_spark_verified=false` 表示脚本不能单凭软件输出独立认证整机型号，不否认该节点来自组织方分配。

2026-09-28 已在分配节点的个人目录部署固定版本的 `intfloat/multilingual-e5-small`，使用同一虚拟环境的 CUDA PyTorch 完成模型前向推理，并通过隔离账号的 HTTP 接口验证“DGX GPU 语义检索”。本机 8000 端口被旧服务占用时，已用私有 SSH 隧道将节点 8000 映射到本机 8001；访问 `http://127.0.0.1:8001/frontend/lego/index.html`。该节点使用全新账号目录，首次访问需创建账号，原 Windows 实例账号不会自动迁移。模型与复核步骤见 [README-STEPFUN-DGX.md](README-STEPFUN-DGX.md)，脱敏记录见 `evidence/dgx-model-20260928.json`。先前的 CUDA 冒烟仍与本次真实模型推理分开表述。

## 固定外部动作的受控准备

仅在有权限使用 Docker 的已授权 DGX 节点、个人项目目录操作。运行
`.venv/bin/python scripts/prepare_curated_action.py` 会按固定 Git 提交从 GitHub API
取得一个 NVIDIA Skill Card 校验脚本，校对 Git blob 与 SHA-256 后写入个人
`workspace/outbox/curated/`；不会下载依赖、执行代码或覆盖已有不同内容。当前赛事节点
已单独准备了经实测的空容器镜像
`skillpulse/empty-python-host:20260928`，ID
`sha256:45c9fe0b487403c02a757ba69fbcb70476e3cf6d0d6e6943049b240cfe4ce5f2`。
后端要求**精确匹配**该镜像 ID，因此源码包在其他主机上不会自动启用外部动作；
不能为迁就环境而关闭此门禁。不要把 Docker socket、密钥、模型目录或整个项目
挂进容器。此运行方式使用只读挂载的宿主 Python，仍是受限原型而非生产级沙箱。
