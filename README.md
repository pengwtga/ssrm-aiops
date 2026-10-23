# SSRM Ops Agent — 供应商系统智能运维 Agent（独立项目）

> 与 `multi-agent-aiops` 主仓库（短链场景 demo，端口 8000）**完全独立**：独立目录、独立端口 8001、零代码依赖。
> 项目动机来自真实 oncall 痛点：SSRM 准入/变更链路卡单排查要跨 5 个系统翻日志，同步失败靠人工发现。

## 定位

根因分类 Agent：输入一条卡单/失败事件的结构化上下文，输出**根因类别 + 置信度 + 证据 + 处置建议**。

5 类根因体系（来自真实 oncall 归因）：

| 类别 | 场景 | 标准处置 |
|---|---|---|
| `xflow_callback_lost` | xFlow 审批已通过但回调未达 SSRM | 按工单号主动拉状态补齐 |
| `ea_sync_timeout` | 向 EA 同步超时/5xx | 异步退避重试 + 对账兜底 |
| `duplicate_submit` | 重复提交/幂等冲突 | 幂等去重，无需人工 |
| `data_inconsistency` | 多下游部分成功的数据缺口 | 对账补推 |
| `frontend_param_error` | 前端/参数错误 | 拒绝并反馈字段问题 |

## 架构要点（面试可讲）

1. **多 LLM Provider 容灾链**：`primary(DeepSeek) → zhipu(glm-4-flash 永久免费) → siliconflow(免费 Qwen) → 规则基线`，逐个尝试自动切换；`/health` 暴露当前链路，响应里的 `source` / `llm_provider` 字段可观测实际生效通道
2. **JSON 模式兼容性处理**：部分模型（如 glm-4-flash）不支持 `response_format=json_object`，遇 400 自动降级为宽松 JSON 提取（剥 markdown 围栏 / 平衡花括号截取）
3. **双通道降级**：全链 LLM 失败才回落规则基线，服务可用性不依赖任何外部 LLM
4. **置信度门禁**：低于 0.85 标记 `needs_human_review` —— 沿用真实 SSRM「审批人工门禁」的设计哲学
5. **评测内建**：`/eval` 端点 + 命令行脚本，准确率/分类别指标/平均延迟/混淆样例

## 快速开始

```bash
# 1. 环境（复用 aiops venv）
pip install -r requirements.txt

# 2. 配置 LLM（不配置则自动走规则基线）
cp .env.example .env   # 填入 DeepSeek API Key

# 3. 启动
python -m uvicorn app.main:app --port 8001

# 4. 评测
python -m app.eval 30
```

## 数据集

`data/incidents.jsonl`：100 条合成事件（5 类 × 20），字段模拟真实工单上下文。
生成脚本：`scripts/gen_dataset.py`（固定随机种子，可复现）。

> ⚠️ 已知局限：合成数据与规则基线同源（模板生成），规则基线准确率偏乐观（当前 100%）。
> **下一步：构建困难测试集**——改写表述、加入未见过的错误码组合、边界样本，让规则失效、检验 LLM 的泛化能力，那才是真实差距。

## 目录结构

```
ssrm-aiops/
├── app/
│   ├── config.py      # pydantic-settings 配置（SSRM_AIOPS_ 前缀）
│   ├── llm_client.py  # OpenAI 兼容客户端（超时/重试/JSON 模式）
│   ├── classifier.py  # 根因分类 Agent（LLM + 规则基线双通道）
│   ├── dataset.py     # 数据集加载
│   ├── eval.py        # 命令行评测
│   └── main.py        # FastAPI（8001）
├── data/incidents.jsonl
├── scripts/gen_dataset.py
├── .env.example
└── requirements.txt
```
