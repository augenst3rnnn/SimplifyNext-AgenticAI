<p align="right"><sub><a href="REMOTE_INFERENCE.md">English</a> · <strong>中文</strong></sub></p>

<a id="remote-inference"></a>

# 方案 B：远程推理部署

本章只适用于方案 B：OrcaLab GUI、场景、相机、低层控制和导航循环位于
**OrcaLab 客户端**，NaVILA 位于独立的 **GPU 推理服务器**。如果所有进程
都在同一台机器上，请改用[方案 A：单机部署](GETTING_STARTED_zh.md#option-a-single-host)。

```text
OrcaLab 客户端导航 → 127.0.0.1:54321 → SSH 隧道
                  → 推理服务器 127.0.0.1:54321 → NaVILA
```

完整顺序是：**分别安装 → 启动远端服务 → 建立隧道 → 端到端检查 →
准备场景并运行导航 → 清理**。

## 约定与安全边界

- 两台机器都需要 Git、Conda、可通过 `nvidia-smi` 检查的 NVIDIA 驱动，以及
  同一版本的 Orca_VLN checkout。
- 本章命令均从 `NaVILA-Orca` 目录执行。在完整仓库中先执行
  `cd /path/to/Orca_VLN/NaVILA-Orca`；使用解压后的开发包时进入其根目录。
- 不要在两台机器都执行 `setup_all.sh`，也不要对分离部署运行 `doctor.sh`；
  这两个脚本都按同一台机器拥有两套环境来检查。
- NaVILA 服务必须绑定推理服务器的 `127.0.0.1`。不要对公网开放其 TCP
  端口；服务本身不提供 TLS 或身份认证，只需允许客户端访问 SSH 端口。

## OrcaLab 客户端：安装客户端环境

在客户端的 `NaVILA-Orca` 目录执行：

```bash
./scripts/check_nvidia_driver.sh
./scripts/setup_system_deps.sh
./scripts/setup_orcalab_env.sh
```

## 推理服务器：安装并启动 NaVILA

在推理服务器的 `NaVILA-Orca` 目录安装 NaVILA 环境和模型：

```bash
./scripts/check_nvidia_driver.sh
./scripts/setup_navila_env.sh
./scripts/download_navila_model.sh
```

然后启动服务并保持终端运行：

```bash
REMOTE_VLM_PORT="54321"

NAVVLM_HOST="127.0.0.1" \
NAVVLM_PORT="$REMOTE_VLM_PORT" \
./scripts/start_navvlm_server.sh
```

只有看到服务监听 `127.0.0.1:54321` 后，才继续建立隧道。

## OrcaLab 客户端：建立 SSH 隧道

在客户端终端设置连接参数。下面的 IP、账号和端口都是占位值：

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SSH_HOST="xx.xx.xx.xx"             # 改为推理服务器的实际 IP
SSH_USER="your-ssh-user"          # 改为远端账号
SSH_PORT="22"
LOCAL_VLM_PORT="54321"
REMOTE_VLM_PORT="54321"
SSH_CONTROL_SOCKET="${HOME}/.ssh/orca-vln-%C"
```

### 自动协商认证（默认）

下面的主命令不强制指定密码或密钥。OpenSSH 会根据客户端配置、SSH agent 和
服务端策略自动协商；如果此前认证方式均未成功，且客户端与服务端都允许
交互式密码认证，SSH 会提示输入账号密码。只有认证和端口转发均成功后，SSH
才转入后台。

```bash
ssh -p "$SSH_PORT" \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

### 显式指定 PEM 私钥（仅 PEM 用户）

> **仅当你有需要显式指定的 PEM 私钥时，才执行本节。没有 PEM 私钥请忽略
> 本节，直接使用上面的主命令。**

先设置 PEM 私钥路径和权限：

```bash
SSH_KEY_PATH="/path/to/private-key.pem"
chmod 600 "$SSH_KEY_PATH"
```

仅在使用该 PEM 私钥时，才将标有 `+` 的两行插入主命令的 `ssh -p` 与 `-M`
之间；其余隧道参数保持不变：

```diff
ssh -p "$SSH_PORT" \
+  -i "$SSH_KEY_PATH" \
+  -o IdentitiesOnly=yes \
  -M -S "$SSH_CONTROL_SOCKET" \
```

## OrcaLab 客户端：执行 SSH 隧道与 NaVILA 协议端到端检查

必须在启动导航前，从客户端经本地转发端口发送 NaVILA 协议 health 请求：

```bash
./scripts/check_navvlm_endpoint.py \
  --host 127.0.0.1 \
  --port "$LOCAL_VLM_PORT"
```

检查只有在收到远端 NaVILA 服务的匹配协议响应后才返回成功，实际覆盖：

```text
本地转发端口 → SSH 隧道 → 远端回环端口 → NaVILA 应用层
```

health 请求不会解码图像或执行模型推理。`ssh -O check` 只能确认 SSH master，
`ss` 只能确认本地监听；二者均不能替代端到端检查。NaVILA 服务当前串行处理
请求，因此应在导航开始前执行；导航运行期间超时也可能只是服务正在推理。

## OrcaLab 客户端：准备场景并运行导航

启动 OrcaLab GUI：

```bash
./scripts/start_orcalab_gui.sh
```

按照[快速上手的步骤 1](GETTING_STARTED_zh.md#scene-setup)订阅并选择
`VLN_Presentation`，加载 `factory.json`，再在 GUI 中选择
**运行 → 开始模拟 → 无仿真程序 → 启动**。随后在建立隧道的客户端 shell
中运行：

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --vlm-host 127.0.0.1 \
  --vlm-port "$LOCAL_VLM_PORT"
```

默认的 `LOCAL_VLM_PORT=54321` 也可省略两个 VLM 参数；显式传递能避免修改
本地端口后忘记同步导航命令。

## 清理

导航结束后，在客户端关闭本项目创建的 SSH master：

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O exit \
  "${SSH_USER}@${SSH_HOST}"
```

最后在推理服务器终端按 `Ctrl+C` 停止 NaVILA。

## 远程部署排错

| 现象 | 优先检查 | 说明 |
| --- | --- | --- |
| 认证失败且未提示输入密码 | 客户端 SSH 配置、agent、服务端认证策略 | 默认命令会自动协商；用 `ssh -v` 查看尝试过的方式，不要把密码写入命令；密钥或 agent 已认证成功时不提示密码是正常行为 |
| `Address already in use` | `LOCAL_VLM_PORT` | 换用客户端空闲端口，并将同一个值传给检查脚本和导航命令 |
| 端到端检查出现 connection refused、EOF 或 reset | 推理服务、`REMOTE_VLM_PORT`、SSH 转发目标 | SSH master 或本地 listener 存在，并不代表远端 NaVILA 可达 |
| 端到端检查超时 | 隧道、远端服务状态 | 导航开始前超时通常表示链路异常；导航运行中也可能是单线程服务正在推理 |
| 服务名或协议版本不匹配 | 实际转发目标、两端代码版本 | 确保两台机器使用同一版本 checkout，且远端端口没有其他服务 |
