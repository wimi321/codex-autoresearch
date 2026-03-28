# Codex Autoresearch

[English](../README.md) | 简体中文

Codex Autoresearch 是一个面向 OpenAI Codex 的自动研究循环工具。它把 Karpathy loop 做成了真正可执行的 runner：

- 一个机械指标
- 一次只做一个改动
- 用 verify 命令判断成败
- 变好就保留，变差就回滚

## 一键启动

```bash
make setup
```

然后运行：

```bash
. .venv/bin/activate
autore run --iterations 5
```

## 最常用命令

```bash
autore init --preset auto
autore doctor
autore run --iterations 5
autore status
autore watch --follow
```

## 最小可运行 demo

如果你想先看一个几乎零依赖、可直接复现成功的例子，看这里：

- [examples/demo-repo](../examples/demo-repo/README.md)

## 典型使用流程

1. `autore init --preset auto`
2. 修改 `autoresearch.toml`
3. `autore doctor`
4. `autore run --iterations 5`
5. 用 `autore watch --follow` 观察长任务日志

## 当前项目特点

- 基于 `codex exec`
- 支持自动建分支
- 支持 verify / guard
- 支持长任务日志落盘
- 支持超时配置
- 支持结果写入 `.autoresearch/results.tsv`

## 长任务观察

```bash
autore watch --follow
autore watch --stream stdout --follow
autore watch --stream results
```

也可以直接看文件：

```bash
tail -f .autoresearch/runs/iteration-0001/codex.stderr.log
tail -f .autoresearch/runs/iteration-0001/codex.stdout.log
```

## 配置示例

```toml
[research]
goal = "提升 pytest 覆盖率"
metric = "coverage percent"
direction = "higher"
verify = "pytest --cov=src 2>&1 | grep TOTAL"
scope = ["src/**", "tests/**"]
guard = "pytest"
iterations = 10

[runtime]
codex_command = "codex exec"
auto_stage_all = true
codex_timeout_seconds = 1800
verify_timeout_seconds = 300
guard_timeout_seconds = 300
```

## 适合什么项目

- Python 项目
- Node / 前端项目
- 想做自动补测试、减体积、提指标的代码仓库
- 想把 Codex 变成 nightly research worker 的团队

## 相关文档

- [架构说明](architecture.md)
- [研究笔记](research-notes.md)
- [示例配置](../examples/autoresearch.toml)
- [最小 demo](../examples/demo-repo/README.md)
