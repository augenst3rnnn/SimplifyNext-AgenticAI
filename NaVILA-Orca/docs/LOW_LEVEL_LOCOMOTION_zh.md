<p align="right"><sub><a href="LOW_LEVEL_LOCOMOTION.md">English</a> · <strong>中文</strong></sub></p>

# 低层运动控制：训练平台自由，接入必须严谨

随包 Go2 checkpoint 是可运行的基线，低层执行也是竞赛指标。该模型有意保持为通用平地策略：它不针对工厂导航、离散 NaVILA 动作片段或任务物体附近的精确停止做专门优化。参与者可在 [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion)（默认参考）、IsaacLab 或其他仿真/训练栈中训练低层策略。

Orca_VLN 仅使用 MJLab 运行提供的基线，并为 Go2 模型输出具体的对齐报告；不要求团队在 MJLab 中重新训练。

## 环境边界

OrcaLocomotion 或其他训练栈必须使用独立环境。Orca_VLN/OrcaLab 运行环境
固定到经过验证的 CUDA 12.8 PyTorch；训练仓库的 requirements 可能覆盖该
版本，使运行环境重新要求更高版本的主机驱动。

两个环境之间只传递兼容的 actor `state_dict` 或推理 checkpoint，不复制
训练环境。如果完整训练 checkpoint 还包含 optimizer 或 scheduler 状态，
请先单独导出 actor 权重再接入；断点续训和 optimizer 状态验证仍在原训练
环境完成。

## 稳定运行时接口

VLN 层只发送机体坐标系速度目标：

```text
VelocityCommand(vx, vy, wz, duration_s) → 低层策略 → RobotState + qpos
```

你的策略可使用任意观测向量、奖励、地形表达、动作参数化或网络结构。接入必须保留命令语义，并以已知 `control_dt` 产生同步机器人状态。

## 两条接入路径

### 1. 直接替换 checkpoint

仅当 checkpoint 与提供的 `Unitree-Go2-Flat` 策略使用相同运行时 ABI 时，`--checkpoint` 才能直接加载：runner 格式、actor 架构、观测顺序、动作顺序和 Go2 关节约定都必须一致。该路径适合在兼容 Go2 task 上重训的策略。

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --checkpoint /absolute/path/to/compatible_go2_policy.pt
```

### 2. Adapter 接入

对于 checkpoint 或 runner 格式不同的 IsaacLab、OrcaLocomotion 或其他策略，请勿强行通过 `--checkpoint` 加载。应实现满足 `VelocityPhysicsBackend` 的小型 backend adapter：

```text
reset(episode)                 -> RobotState
set_velocity_command(command)  -> 保存 vx、vy、wz 目标
step()                         -> 推进一个控制 tick 并返回同步状态
control_dt                     -> 策略 tick 持续时间
qpos_batch                     -> OrcaLab 渲染所需的当前 Go2 广义位置
```

这样可将平台特定的加载、观测构建、动作缩放和物理步进隔离在 VLN 边界之下。

## 对齐清单

在将自定义策略接入实时 NaVILA 运行前，请验证：

1. **机器人资源：** Go2 link 名、关节名、关节限制和中立姿态与渲染机器人一致。
2. **关节动作 ABI：** 12 个关节的顺序和符号符合预期；先进行低幅度逐关节扫描，再跑策略。
3. **观测 ABI：** 本体感觉、命令缩放、历史、地形特征和归一化与训练时一致。
4. **时序：** 策略控制周期、动作保持/降频与 `VelocityCommand` 片段持续时间一致。
5. **状态桥：** local qpos 中根位姿为 `(x, y, z, w, x, y, z)`，公共接口边界为 `(w, x, y, z)`；渲染器持续同步更新。
6. **运动检查：** 在接入任何 VLM 前先通过站立、前进、转向、停止和恢复测试。

基线 runner 会将对齐报告写入 `measurements.json`。比较机器人 XML、动作顺序、qpos 映射和控制周期时，以它作为参考。

## 建议工作流

1. 原样复现提供的工厂基线；
2. 创建独立环境，在 OrcaLocomotion、IsaacLab 或选定平台训练并验证策略；
3. 仅当 checkpoint ABI 兼容时直接替换，否则构建 adapter；
4. 在进入 VLN 闭环前运行固定速度测试；
5. 重复未改动的 `VLN_Presentation` 工厂回合，比较漂移、动作片段和最终轨迹。

进阶赛道请提交训练来源、对齐说明和基线/自定义策略运行目录。这能让低层研究与高层 VLN 改动保持可比较性。
