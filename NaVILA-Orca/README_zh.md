<p align="right"><sub><a href="README.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN 开发包

此目录是可分发的 OrcaLab 运行时。GitHub 项目主页位于上一级：[Orca_VLN](../README_zh.md)。

部署方式三选一：

| 部署方式 | 结构 | 操作指南 |
| --- | --- | --- |
| **💻 方案 A（默认）— 单机部署** | OrcaLab、NaVILA 与导航进程位于同一台机器 | [快速上手](docs/GETTING_STARTED_zh.md#option-a-single-host) |
| **🖥️ 方案 B — 远程推理** | OrcaLab 与导航进程位于客户端，NaVILA 位于独立 GPU 服务器 | [远程推理部署](docs/REMOTE_INFERENCE_zh.md) |
| **☁️ 方案 C — 托管远程推理** | OrcaLab 与导航进程位于参与者本机，NaVILA 位于主办方托管的实例 | [托管访问指南](docs/ACCESS_GUIDE_zh.md) |

## 💻 方案 A（默认）— 单机开发流程

全新 checkout 先执行一次 `./scripts/setup_all.sh`，并确认
`./scripts/doctor.sh` 全部通过。此后无需激活 Conda 环境或设置仓库根目录
变量。经过验证的运行时固定为 OrcaLab 26.7.1。OrcaLocomotion 等低层训练
工具请放在独立环境中，只传递兼容的策略 checkpoint。

以下三条命令覆盖正常的单机开发流程：

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

完整的首次运行流程见快速上手指南中的
[方案 A](docs/GETTING_STARTED_zh.md#option-a-single-host)。

## 🖥️ 方案 B — 远程推理

方案 B 将 OrcaLab GUI 和导航进程留在客户端，只在独立 GPU 服务器上运行
NaVILA 服务。不要把上面的三条单机命令当作一组客户端命令执行。请按照
[远程推理指南](docs/REMOTE_INFERENCE_zh.md)，分别完成两台机器的安装、服务
启动、SSH 隧道和 NaVILA 协议端到端检查。

## ☁️ 方案 C — 托管远程推理（AWS SSM）

方案 C 适用于由主办方托管运维的 NaVILA 服务器：你无法 SSH 登录实例。请
改用[托管访问指南](docs/ACCESS_GUIDE_zh.md)：使用临时 SSO 凭据建立 AWS SSM
端口转发，即可让 NaVILA 表现为 `127.0.0.1:54321` 上的本地服务，且隧道无法
在实例上打开 shell。

## 基线场景与随包资源

两种部署方案都需要先在 OrcaLab 中订阅 `VLN_Presentation` 和
`unitree_robots`，等待两个订阅完成后选择 `VLN_Presentation` 场景，再执行
**文件 → 打开布局 → `factory.json`**。场景继续使用提供的 `factory.json`
布局。默认路线为：红色垃圾桶 → 蓝色油桶 → 沿白色安全围栏右转 → 红色灭火器 →
灰色底座上的白色工业机械臂。

两种方案中的 NaVILA 都使用独立运行环境。TCP 服务适配器由本项目提供，模型
下载到实际执行推理的机器上的项目默认模型目录。

```bash
./scripts/build_kit.sh
```

该开发包包含 OrcaLab adapter、本地 Go2 task/MJCF/mesh 资源、默认 Go2 运动 checkpoint、`factory.json` 布局和一个可复现的 `VLN_Presentation` 回合。它不包含 NaVILA/LLaVA 源码、模型权重、已订阅的 OrcaLab 资源或 IsaacLab。
