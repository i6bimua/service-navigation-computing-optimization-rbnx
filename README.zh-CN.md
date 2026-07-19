<div align="center">

# RoboNix Compute Optimization Skill

**面向双系统视觉语言导航的自适应计算优化 Skill**

[![Tests](https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill/actions/workflows/ci.yml/badge.svg)](https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-MulanPSL--2.0-red.svg)](LICENSE)
[![Model](https://img.shields.io/badge/Validated-InternVLA--N1_DualVLN-16a34a.svg)](docs/supported-models.md)

[English](README.md) | [简体中文](README.zh-CN.md)

</div>

RoboNix Compute Optimization Skill 将慢速语义推理放在云端 GPU，将低时延动作生成放在端侧设备，并通过异步执行、关键 latent 感知同步、缓存上下文复用和自适应超时降低双系统 VLN 的执行开销。

本项目提供独立计算运行时和外部 HTTP Skill 边界，不修改 RoboNix 核心，也不宣称已经实现 RoboNix 原生 Driver。

## 效果前置

![R2R-CE benchmark overview](docs/assets/benchmark_overview.svg)

在完整 R2R-CE `val-unseen`、InternVLA-N1 DualVLN 设置下：

- Orin+A100 相比 Edge Only 实现 **2.22× 延迟加速**
  （`497.8 ms → 224.4 ms`）；
- 相比 Naive ECC，**SR 提升 6.1 个百分点，SPL 提升 12.7 个百分点**
  （`SR 56.7→62.8`，`SPL 45.1→57.8`）；
- 端侧模型内存为 **0.60 GB**，Edge Only 为 16.63 GB；
- pending context 和控制状态带来的额外开销 **小于 8 KB**。

该 benchmark 覆盖 1,839 个 episode，使用原始 DualVLN 权重、NVIDIA A100 云端和 `MAX_N` 模式下的 Orin/Thor 端侧设备。完整基线、Thor 数据、实验条件和复现入口见 [R2R-CE Benchmark](benchmarks/r2r_ce/README.md)。

## 支持模型与平台

| 组件 | 状态 | 已验证范围 |
| --- | --- | --- |
| InternVLA-N1 DualVLN | 端到端验证 | 云端 S2 语义推理 + 端侧 S1 动作生成 |
| Mock S1/S2 backend | CI 验证 | 协议、同步、超时和遥测 |
| 通用双系统 runner contract | Adapter API | 需要独立适配与 benchmark 证据 |
| 云端设备 | 已验证 | NVIDIA A100 |
| 端侧设备 | 已验证 | NVIDIA AGX Jetson Orin、Thor |
| 仿真平台 | 已验证 | Habitat / VLN-CE、R2R-CE |

OpenVLA、π0 等模型目前不列为已支持模型。新增模型只有同时具备 adapter、smoke test、真实运行说明和 benchmark metadata 后，才进入支持列表。详见 [支持模型](docs/supported-models.md)。

## 核心计算优化

1. **异步执行**：S2 在后台更新语义 latent，S1 保持本地控制闭环。
2. **关键 latent 同步**：在高价值步骤请求新 latent，不使用固定同步周期。
3. **自适应缺失处理**：通过超时预测、latent 复用和迟到响应吸收限制网络阻塞。
4. **完整遥测**：记录 S1/S2 延迟、同步、超时、复用、载荷和导航指标。

## 系统架构

![Compute optimization architecture](docs/assets/compute_optimization_architecture.svg)

当前后端由云端 S2、端侧 S1、active/pending context buffer 和遥测链路组成。“cloud-edge”只用于描述当前计算放置方式，不再作为项目名称。

## RoboNix 理想工作流

![Compute optimization workflow](docs/assets/compute_optimization_workflow.svg)

流程图描述目标 RoboNix 集成路径。当前仓库提供计算运行时、评测工具和可供 RoboNix 编排系统调用的 HTTP Skill 边界。

## 三分钟 Quick Start

Mock 路径不需要权重、仿真数据或 GPU：

```bash
git clone https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill.git
cd RoboNix-Compute-Optimization-Skill

conda create -n robonix-compute python=3.10 -y
conda activate robonix-compute
python -m pip install -U pip
python -m pip install -e ".[dev,websocket]"

bash scripts/run_mock_compute.sh --steps 5
```

输出位于：

```text
outputs/mock_compute/telemetry.json
```

分进程运行：

```bash
# 终端 1
robonix-compute-cloud --mode mock --host 0.0.0.0 --port 8765

# 终端 2
robonix-compute-edge \
  --mode mock \
  --cloud-host 127.0.0.1 \
  --cloud-port 8765 \
  --steps 5
```

## 真实模型运行

下载完整模型并导出端侧 S1 权重：

```bash
robonix-compute-download --output checkpoints

export INTERNNAV_ROOT=<path-to-InternNav>
export PYTHONPATH="$INTERNNAV_ROOT:$INTERNNAV_ROOT/third_party/diffusion-policy:${PYTHONPATH:-}"

robonix-compute-export-s1 \
  --source checkpoints/InternVLA-N1 \
  --output checkpoints/InternVLA-N1-S1
```

配置路径：

```bash
export ROBONIX_COMPUTE_MODEL_DIR="$(pwd)/checkpoints/InternVLA-N1"
export ROBONIX_COMPUTE_S1_MODEL_DIR="$(pwd)/checkpoints/InternVLA-N1-S1"
export ROBONIX_COMPUTE_DATA_ROOT=<path-to-vln-data>
```

正式推理或评测前执行严格检查：

```bash
robonix-compute-preflight \
  --internnav-root "$INTERNNAV_ROOT" \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --checkpoint-path "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-path "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --strict
```

完整说明：

- [环境与依赖](docs/installation.md)
- [端云运行与评测](docs/quickstart.md)
- [Benchmark 复现](docs/benchmarks.md)
- [常见问题](docs/troubleshooting.md)

## HTTP Skill 边界

```bash
robonix-compute-skill \
  --host 0.0.0.0 \
  --port 8090 \
  --config-json examples/robonix_compute_config.json
```

```bash
curl http://127.0.0.1:8090/health

curl -X POST http://127.0.0.1:8090/step \
  -H 'Content-Type: application/json' \
  -d '{"observation":{"rgb":[1.0,0.0],"depth":[0.0]}}'
```

服务提供 `health`、`setup`、`reset`、`step`、`telemetry` 和 `close` 操作。边界与当前状态见 [架构与集成](docs/architecture.md)。

## 仓库结构

```text
RoboNix-Compute-Optimization-Skill/
├── robonix_compute/            # 运行时、协议、适配器、CLI 和 Skill API
├── benchmarks/r2r_ce/          # 结构化结果与复现 metadata
├── configs/                    # 默认、云端、端侧和 Habitat 配置
├── docs/                       # 安装、模型、架构和 benchmark 文档
├── examples/                   # Mock 和 InternNav 示例
├── scripts/                    # 启动、汇总和发布审计脚本
└── tests/                      # 单元与集成测试
```

仓库不分发权重、数据集、原始实验日志和私有路径。

## 验证

```bash
python -m pytest -q
python scripts/release_audit.py
python -m build
```

轻量测试由 GitHub Actions 执行；GPU、InternNav、Habitat 和授权数据集测试作为本地或自托管验证步骤。

## 文档

- [安装](docs/installation.md)
- [Quick Start 与部署](docs/quickstart.md)
- [支持模型](docs/supported-models.md)
- [架构与 RoboNix 边界](docs/architecture.md)
- [Benchmarks](docs/benchmarks.md)
- [故障排查](docs/troubleshooting.md)
- [贡献指南](CONTRIBUTING.md)
- [版本记录](CHANGELOG.md)

## 引用与许可证

引用信息见 [`CITATION.cff`](CITATION.cff)。本项目采用[木兰宽松许可证第 2 版](LICENSE)。InternNav、InternVLA-N1、Habitat、Matterport3D 等第三方组件保留各自许可证，详见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
