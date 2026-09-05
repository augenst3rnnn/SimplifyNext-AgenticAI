# NaVILA 远程推理 — 访问与冒烟测试指南

本文档面向**单个测试人员**，帮助你实际连上 NaVILA GPU 推理端点。该端点是一台
位于 GPU 集群前端的 **nginx 负载均衡器**：你向它建立端口转发，请求会被分发到
当前运行的任意 GPU 副本。环境中有 **8 台等效的负载均衡器（“分片”）**，每台
都连接到**同一个**集群；它们分布在两个培训场地——**SMU**（场地 A）有 4 台，
**NTU**（场地 B）有 4 台。你需要连接**所在场地**的一台分片（按学员组轮询
分配）；同一场地内的分片可互换使用。场地划分只是为了分散 SSM 会话负载——
无论位于哪个场地，每台分片都能访问完整的 GPU 池。本文档专用于当前环境
（单账号、东京区域——步骤 4 按场地写死了实例 ID），并假定使用 **Linux**
客户端（以下命令基于 Debian/Ubuntu）。本文档**不是**学生讲义，请配合现场
讲解使用。

你使用 **IAM 用户的访问密钥**（单独提供的访问密钥 ID 和私有访问密钥）进行
身份验证，其权限只允许**做一件事：向 NaVILA 负载均衡器（分片）建立 SSM
端口转发**。没有 shell 权限，也没有任何其他 AWS 访问权限。隧道建立后，
推理端点就表现为 `127.0.0.1:54321` 上的本地服务。

连接前只需安装**两个组件**（步骤 1–2）。认证只需导出分配给你的两个访问密钥
值（步骤 3）。uv 是第三项**可选**安装，仅用于步骤 6 的模拟推理，health 检查
不需要。

## 本环境固定参数

| | |
|---|---|
| AWS 账号 | `sn.devlabs` (`433129444392`) |
| 区域 | `ap-northeast-1`（东京） |
| 负载均衡器（分片） | 共 8 台，步骤 4 按场地写死——**SMU（A）** = 4 台，**NTU（B）** = 4 台 |
| 你的分片 | 所在场地 4 台中的一台（按学员组轮询分配）；同一场地内任意一台均可使用 |
| 凭据 | IAM 用户访问密钥（访问密钥 ID + 私有访问密钥），**单独提供**——参见步骤 3 |
| 本地端口 | `54321` |

> **有一件事不受你控制：** 负载均衡器后端至少必须有一个 **GPU 后端正在运行
> 且状态健康**。你无法自行启动后端——按设计，你的权限只包含端口转发。如果
> 步骤 5 的 health 检查无法连接后端，就需要管理员处理。

> **负载均衡器如何工作：** 上文所说的**实例**是 nginx 主机，而不是 GPU。nginx
> 在 TCP 层进行负载均衡，对 NaVILA 协议完全透明。因此从你的角度看，下文的
> 隧道、health 检查和模拟推理，与直接连接单块 GPU 时的字节流完全一致。请求会
> 被分发到池中的任意 GPU 副本；一个副本宕机后会自动故障转移。_（这些分片主机
> 会长期运行且不会被销毁，因此步骤 4 按场地写死了实例 ID，而不是动态查询。）_

## 最终运行方式

使用两个终端：

- **终端 A** — 你的 AWS 凭据 + SSM 隧道。全程保持打开。
- **终端 B** — health 检查（以及可选的模拟推理）。只访问
  `127.0.0.1:54321`，因此**不需要任何 AWS 凭据**。

---

## 1. 安装 AWS CLI v2

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

（ARM64 机器请把 URL 换成 `awscli-exe-linux-aarch64.zip`。）

## 2. 安装 Session Manager 插件

实际承载端口转发的是该插件，CLI 会调用它。

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
session-manager-plugin
```

最后一条命令应输出一行 “successfully installed”。（RPM 系发行版使用同一
S3 路径下 `linux_64bit/` 目录中的 `.rpm` 包。）

## 3. 设置访问密钥 — 终端 A

你会**另外收到一段访问密钥配置**（不在本指南中——凭据保存在组织者发放的
文件里）。根据你的场地和学员组，配置内容如下所示（你收到的会包含真实值），
并且**没有会话令牌**——这是 IAM 用户密钥，不是临时凭据：

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
```

1. 将分配给**你的**配置粘贴到**终端 A**。
2. 确认已生效——不需要 `--profile`：

   ```bash
   aws sts get-caller-identity
   ```

   输出中应出现类似下面的**用户** ARN：
   `arn:aws:iam::433129444392:user/navila-student/navila-student-...`。

> 这些是**长期有效**的密钥——不会在会话期间过期，而且新开终端后仍可使用
> 同一配置（只需再次粘贴）。请勿向他人泄露；培训结束后这些密钥会被撤销。
> 如果新终端提示找不到凭据，说明你还没有把配置粘贴到**该终端**。

---

## 4. 建立隧道 — 终端 A

在粘贴凭据的同一个终端中，从下表选择**你所在场地**的分片主机，并将
`INSTANCE_ID` 设为对应值。同一场地的 4 台主机可互换使用——按学员组轮询分配
（第 1 组 → 第 1 行，第 2 组 → 第 2 行，以此类推），以便均匀分散会话。

| 场地 | 学员组 | 实例 ID |
|---|---|---|
| **SMU**（A） | 1 | `i-066515f762428ba55` |
| **SMU**（A） | 2 | `i-07c7311b7db2a70b9` |
| **SMU**（A） | 3 | `i-0d399fd6c5430ae74` |
| **SMU**（A） | 4 | `i-025cf537751c328e8` |
| **NTU**（B） | 5 | `i-01a6c1da2a41f1b36` |
| **NTU**（B） | 6 | `i-00e69a7c2bd5ff0ec` |
| **NTU**（B） | 7 | `i-0b304e822e0e09faa` |
| **NTU**（B） | 8 | `i-0aaccf0863578108a` |

然后建立隧道：

```bash
INSTANCE_ID=i-066515f762428ba55    # <-- 改成你所在场地的分片主机（见上表）

aws ssm start-session \
  --target "$INSTANCE_ID" \
  --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

等待出现：

```text
Waiting for connections...
```

**保持这个终端打开。** 关闭它（或按 Ctrl-C）会断开隧道。如果这里提示
`AccessDeniedException`（凭据过期或无效），重做步骤 3 刷新凭据。

---

## 5. Health 检查 — 终端 B

将以下内容保存为 `check_navvlm_endpoint.py`。它向 NaVILA 发送 health 请求
（8 字节长度前缀 + JSON，不带图片、不做推理）并校验响应。脚本只使用标准库，
因此用系统自带的 `python3` 运行即可，且不需要 AWS 凭据。

```python
#!/usr/bin/env python3
"""Health-check the NaVILA endpoint (no inference). Standard library only."""

import json
import socket
import sys

HOST, PORT = "127.0.0.1", 54321


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


req = json.dumps({"type": "health"}).encode()
with socket.create_connection((HOST, PORT), timeout=5) as sock:
    sock.sendall(len(req).to_bytes(8, "big"))
    sock.sendall(req)
    size = int.from_bytes(recv_exact(sock, 8), "big")
    resp = json.loads(recv_exact(sock, size).decode())

if resp.get("service") == "navila-vlm" and resp.get("status") == "ok":
    print(f"NaVILA endpoint healthy at {HOST}:{PORT} "
          f"(protocol_version={resp.get('protocol_version')})")
else:
    print(f"unexpected health response: {resp}", file=sys.stderr)
    sys.exit(2)
```

运行：

```bash
python3 check_navvlm_endpoint.py
```

预期输出：

```text
NaVILA endpoint healthy at 127.0.0.1:54321 (protocol_version=1)
```

如果提示 `connection closed` 或 `connection refused`，说明隧道已建立，但
负载均衡器后端**没有健康的 GPU 后端**（后端池为空时 nginx 会关闭连接）——
这属于管理员操作，你的权限无法修复。请联系管理员启动后端，然后重试。

**这就是核心冒烟测试。** 步骤 6 仅在你需要确认真实推理往返时执行。

---

## 6. （可选）模拟推理 — 终端 B

这一步比 health 检查更进一步：发送 8 帧真实图像并在 GPU 上运行模型。
它需要 `pillow` 库，因此这是唯一需要用到包管理器的步骤。
[uv](https://docs.astral.sh/uv/) 最简单——它会读取 `# /// script` 头并自动
安装 `pillow`，无需手动创建 venv。

安装 uv（已安装可跳过），然后新开一个 shell 使其出现在 `PATH` 上：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

将以下内容保存为 `mock_infer.py`：

```python
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow"]
# ///
"""Send one real mock inference to the NaVILA server over the raw protocol."""

import base64
import io
import json
import os
import socket
import sys
import time

from PIL import Image

HOST, PORT = "127.0.0.1", 54321
NUM_FRAMES = 8
QUERY = "Walk forward down the corridor and stop at the open door."


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


def make_frame():
    # 这是管线/延迟冒烟测试，而不是导航输出正确性测试，因此帧内容并不重要。
    # 切换下面两行的注释，即可在随机噪声与单一纯色之间切换。
    # img = Image.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3))  # 随机噪声
    img = Image.new("RGB", (512, 512), (128, 128, 128))  # 单一纯色
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


images = [make_frame() for _ in range(NUM_FRAMES)]
payload = json.dumps({"images": images, "query": QUERY}).encode()
print(f"query   : {QUERY!r}")
print(f"frames  : {NUM_FRAMES}  payload: {len(payload) / 1024:.1f} KiB")

t0 = time.perf_counter()
with socket.create_connection((HOST, PORT), timeout=120) as sock:
    sock.sendall(len(payload).to_bytes(8, "big"))
    sock.sendall(payload)
    size = int.from_bytes(recv_exact(sock, 8), "big")
    resp = json.loads(recv_exact(sock, size).decode())
elapsed = time.perf_counter() - t0

print(f"latency : {elapsed:.3f} s")
print(f"response: {resp!r}")
```

运行：

```bash
uv run mock_infer.py
```

预期——大约 **2.7 s** 并返回一个动作字符串（具体措辞可能不同）：

```text
query   : 'Walk forward down the corridor and stop at the open door.'
frames  : 8  payload: ~630 KiB
latency : 2.68 s
response: 'The next action is move forward 25 cm.'
```

这次端到端往返——你的受限 IAM 身份 → SSM 隧道 → GPU 推理 → 返回动作——
就完成了完整的测试。

---

## 7. 结束后

在**终端 A** 按 Ctrl-C 关闭隧道。AWS 侧无需任何清理。

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `get-caller-identity` 报 `InvalidClientTokenId` / `SignatureDoesNotMatch` / `AccessDenied` | 密钥粘贴错误，或粘贴到了错误的终端 | 将发放的密钥文件中属于**你的**配置完整、准确地重新粘贴到终端 A（两行都要粘贴，不要带多余空格）。如果仍然失败，你的密钥可能已被撤销——请联系组织者 |
| 建立隧道时报 `TargetNotConnected` / `InvalidInstanceId` | `INSTANCE_ID` 输入错误，或使用了不属于你所在场地的主机 | 从步骤 4 的表格中准确复制 ID，并确认它属于你所在场地的 4 行（SMU 为第 1–4 行，NTU 为第 5–8 行） |
| `Unable to locate credentials` | 当前终端没有凭据 | 你开错了终端，或还没有粘贴步骤 3 的内容 |
| 报 `AccessDeniedException` 且包含 `SSM-SessionManagerRunShell` | 你尝试了普通 shell 会话（未传 `--document-name`） | 这是设计上禁止的——始终像步骤 4 那样传入端口转发文档 |
| Health 检查：`connection refused` / `connection closed` | 负载均衡器后端没有健康的 GPU 后端（后端池为空时 nginx 会关闭连接） | 请管理员启动后端（你无法操作——只有端口转发权限） |
| Health 检查：`connection refused` 且隧道终端已关闭 | 隧道未运行 | 重新打开终端 A（步骤 3–4） |
| 建立隧道时报 `Address already in use` | 本地 54321 端口被旧隧道占用 | 关闭旧隧道，或将隧道的 `localPortNumber` **与**两个脚本中的 `PORT` 常量一起改成空闲端口 |
| `session-manager-plugin: command not found` | 插件未安装 | 重做步骤 2 |
| `uv: command not found` | 新 shell 尚未加载 `uv` | 新开终端或执行 `source ~/.bashrc` |

---

## 附录 — 备选认证：持久命名 profile

如果不想在每个新终端中都导出环境变量（步骤 3），可以将密钥一次性保存到命名
profile，之后使用 `--profile navila`。代价是密钥会保存在磁盘上的
`~/.aws/credentials` 中，而不是只存在于 shell 里——个人电脑上通常没问题，
共享电脑上则不建议这样做。

一次性配置（根据提示粘贴发放配置中属于**你的**访问密钥 ID 和私有访问密钥；
这里不会询问会话令牌，因为并不存在会话令牌）：

```bash
aws configure --profile navila
```

```text
AWS Access Key ID [None]:       AKIA...        （来自你的配置）
AWS Secret Access Key [None]:   ...            （来自你的配置）
Default region name [None]:     ap-northeast-1
Default output format [None]:   json
```

之后每次需要访问时，在 AWS 命令中加上 `--profile navila`：

```bash
aws sts get-caller-identity --profile navila

INSTANCE_ID=i-066515f762428ba55    # <-- 改成你所在场地的分片主机（步骤 4 表格）

aws ssm start-session \
  --target "$INSTANCE_ID" \
  --profile navila --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

health 检查和模拟推理不变——它们从不使用 AWS 凭据。
