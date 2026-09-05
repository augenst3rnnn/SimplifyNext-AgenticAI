<p align="right"><sub><a href="HACKATHON_BASELINE.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN Hackathon 基线

Orca_VLN 是主办方提供的仿真基线。团队从可运行的 OrcaLab 视觉语言导航闭环出发，改进任务行为而无需重建整套机器人栈。

## 基线包

主办方提供：

- 通过资产订阅获取的 OrcaLab `VLN_Presentation` 场景，以及随包提供的
  [`factory.json`](../factory.json) 布局；
- Go2、常驻第一视角 RGB 相机和实时导航监视器；
- NaVILA server 接入点和默认导航回合；
- 随包 Go2 运动 checkpoint 与运行脚本；
- 结果产物：RGB 帧、动作轨迹、状态轨迹和 measurements；
- 本指南以及高层和低层扩展路线。

基线以仿真为先。可使用真实 EDU Go2 完成部分展示，但复现或提交核心流程不要求硬件。

经过验证的运行时版本为 OrcaLab 26.7.1。开发包包含 `factory.json`，但不包含
已订阅的 OrcaLab 资源；加载布局前必须等待 `VLN_Presentation` 和
`unitree_robots` 两个订阅完成。

提供的 Go2 checkpoint 有意保持为通用平地策略，并未针对工厂布局、离散 NaVILA 命令词表或精确停车行为调优。团队应将其跟踪误差和恢复行为视为可见基线特征，而不是需要隐藏的缺陷。

## 四个检查点

| 检查点 | 团队产出 | 证据 |
| --- | --- | --- |
| 1. 环境 | 订阅 `VLN_Presentation` 和 `unitree_robots`，打开场景与布局并验证 Go2 相机 | 工厂地图与机器人视图截图 |
| 2. 自动闭环 | 执行 指令 → NaVILA → 动作 → 运动 | `measurements.json` 与终端动作轨迹 |
| 3. 巡检逻辑 | 增加巡逻、风险或图像采集行为 | 保存的图片和结构化巡检记录 |
| 4. 集成 Demo | 打包一个可重复场景 | 短视频、源码、配置说明和运行目录 |

## 竞赛赛道

### 基线复现

每个团队都必须完成。订阅 `VLN_Presentation`、载入提供的 `factory.json` 布局，
并使用 Go2 checkpoint 和 NaVILA server 跑完“红色垃圾桶 → 右转 → 蓝色油桶 →
白色机械臂”的默认路线，验证仿真器、相机、网络和动作接口正确。
部署时在[方案 A：单机部署](GETTING_STARTED_zh.md#option-a-single-host)与
[方案 B：远程推理](REMOTE_INFERENCE_zh.md)中二选一；方案 B 必须先通过文档中的
SSH 隧道与 NaVILA 协议端到端检查。

### 任务智能

主要创新赛道。可改进路径逻辑、prompt、分阶段航点、风险检测、相机采集、巡检报告或任务特定停止条件；无需模型训练。

### 高层 VLN 适配

可选。收集经审核的 rollout，在不修改运动控制的前提下对 NaVILA 进行 SFT 或 LoRA。详见[VLN 微调](VLN_FINE_TUNING_zh.md)。

### 低层运动控制

低层执行与高层 VLN 一样是评分维度。可改进命令跟踪、转向、停止、稳定性、恢复或地形响应，同时保留速度命令接口。默认低层训练参考为 [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion)；也可使用 IsaacLab 或其他平台，并遵循[低层运动控制](LOW_LEVEL_LOCOMOTION_zh.md)中的模型对齐路线。

## 评测重点

| 区域 | 需要审查的证据 |
| --- | --- |
| VLN 行为 | 指令遵循、视觉落地、有效动作、任务结果 |
| 低层模型 | 指令与实测运动、转向/停止精度、稳定性、恢复 |
| 端到端系统 | 第一视角图像、动作轨迹、状态轨迹、measurements、可复现运行路径 |

## 提交清单

1. 包含一条命令复现路径的仓库或压缩包；
2. 清晰记录场景、prompt 或配置改动；
3. 机器人相机录屏或截图；
4. 一次成功运行的 `measurements.json` 和相关日志；
5. 简要说明修改了哪条赛道，以及保留了哪些基线部分。

## 支持边界

主办方支持场景配置、基线运行路径、相机可见性和高/低层接口。团队负责其自定义 prompt、任务逻辑、数据整理、微调和运动控制策略。
