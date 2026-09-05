<p align="right"><sub><a href="ARCHITECTURE.md">English</a> · <strong>中文</strong></sub></p>

# 架构：VLN 与运动控制之间的稳定边界

Orca_VLN 有意将高层导航与低层运动拆分。团队可以改进其中任一层，而无需重训或重写另一层。

```text
RGB 历史帧 + 指令
          │
          ▼
    高层 VLN 策略
          │ 文本动作
          ▼
  动作解析器 / 安全门
          │ VelocityCommand(vx, vy, wz, duration_s)
          ▼
   低层运动控制 backend
          │ RobotState + qpos
          ▼
 OrcaLab 渲染器与常驻第一视角相机
          └─────────────────────────────── 反馈 RGB
```

## 部署方式

部署选择只改变 NaVILA server 的运行位置，不改变下文的 VLM 或运动控制接口：

| 部署方式 | 进程位置 | 对接口的影响 |
| --- | --- | --- |
| **方案 A（默认）— 单机部署** | OrcaLab、导航进程与 NaVILA 位于同一台机器 | TCP client 通过本机回环地址访问 NaVILA |
| **方案 B — 远程推理** | OrcaLab 与导航进程位于客户端，NaVILA 位于独立 GPU 服务器 | 客户端仍使用回环地址，并将连接安全转发到推理服务器；`VLMClient` 接口不变 |
| **方案 C — 托管远程推理（AWS SSM）** | OrcaLab 与导航进程位于参与者本机，NaVILA 位于主办方托管的 AWS 实例 | AWS SSM 端口转发通过相同的本机回环地址提供托管服务；`VLMClient` 接口不变 |

一次部署只选择一种方案。方案 B 的安装、服务启动、隧道和端到端验证见
[远程推理指南](REMOTE_INFERENCE_zh.md)。方案 C 的临时 SSO 凭据、AWS SSM
端口转发、健康检查和客户端启动流程见[托管访问指南](ACCESS_GUIDE_zh.md)。

## 唯一的控制接口

层间边界是 [`VelocityCommand`](../src/navila_orca/contracts.py)。它包含机体坐标系下的 `vx`、`vy`、`wz` 和精确仿真持续时间。默认解析器一次只接受一个规范高层动作：

| 文本动作 | 运动接口 |
| --- | --- |
| `move forward 25/50/75 cm` | `vx=0.5 m/s`，持续 `0.5/1.0/1.5 s` |
| `turn left/right 15/30/45 degrees` | 固定带符号 `wz`，持续 `0.5/1.0/1.5 s` |
| `stop` | 零速度、零持续时间 |

解析器会拒绝空、歧义或不受支持的模型输出。这是有意的：竞赛团队应看到无效高层响应，而不是静默发送非预期运动命令。

## 高层 VLN

高层实现 `VLMClient`：接收恰好八帧 RGB 和当前指令，返回一个文本动作。随包 TCP client 连接到单独运行的 NaVILA server；它不依赖运动网络、关节顺序、地形表达或 OrcaLab actor 名称。

团队可以在此层修改：

- Prompt、分阶段航点逻辑、任务状态和停止条件；
- NaVILA SFT 或 LoRA adapter；
- 风险检测调用、图像采集规则和报告逻辑；
- 其他 VLN server，只要输出规范动作词表。

## 低层运动控制

低层负责平衡、接触、步态生成和关节动作。默认 backend 是随包 Go2 平地策略，以 50 Hz 控制周期运行。它接收 `VelocityCommand`，绝不接收语言文本、RGB 图像或 NaVILA prompt。

团队可以在此层修改：

- 在 [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion)、IsaacLab 或其他平台训练策略；
- 导出与基线 runner 兼容的 checkpoint，或为不同策略 ABI 实现 adapter；
- 增加地形特征或不同机器人 adapter，同时保留速度命令语义。

替换实现必须保留 `VelocityPhysicsBackend` 行为：`reset`、`set_velocity_command`、`step`、`control_dt` 和同步状态输出。详见[低层运动控制](LOW_LEVEL_LOCOMOTION_zh.md)。

## 为什么这对竞赛重要

无需训练任何模型即可复现基线。改进巡检行为的团队通常在边界上方工作；研究步态、不平地面或恢复能力的团队可在边界下方工作。两种路径共享场景、相机、回合和评测产物，因此 demo 更易比较。
