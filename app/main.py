"""SSRM Ops Agent — FastAPI 服务（端口 8001，与主仓库短链 demo 的 8000 完全隔离）

端点：
- GET  /health          健康检查（含 LLM 配置状态）
- POST /classify        单条事件根因分类
- POST /eval            数据集评测（LLM vs 规则基线）
- GET  /stats           数据集统计
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from app.classifier import ROOT_CAUSE_CLASSES, RootCauseClassifier
from app.dataset import load_dataset, stats
from app.llm_client import llm_client
from app.rag_assistant import RagAssistant

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

classifier = RootCauseClassifier(llm_client)
rag = RagAssistant(llm_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    providers = ",".join(llm_client.configured_providers) if llm_client.is_configured else "未配置(规则基线降级)"
    logger.info("SSRM Ops Agent started | LLM 容灾链: %s", providers)
    await rag.warmup()
    logger.info("RAG 值班助手就绪 | 模式: %s", rag.mode)
    yield


app = FastAPI(title="SSRM Ops Agent", version="0.1.0", lifespan=lifespan)


class ClassifyRequest(BaseModel):
    incident: dict[str, Any]


class EvalRequest(BaseModel):
    limit: Optional[int] = 30   # 每次评测条数（控制 LLM 成本）


@app.get("/")
async def root():
    return {
        "service": "SSRM Ops Agent",
        "docs": "/docs",
        "playground": "/playground",
        "endpoints": {
            "POST /classify": "单条事件根因分类（body: {\"incident\": {...}}）",
            "POST /eval": "数据集评测",
            "GET /stats": "数据集统计",
        },
    }


@app.get("/playground")
async def playground():
    """浏览器可用的分类测试页：选一个预设案例或粘贴自己的 JSON"""
    html = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>SSRM 根因分类 Playground</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 860px; margin: 40px auto; padding: 0 16px; background:#f7f8fa; color:#222; }
  h1 { font-size: 22px; }
  textarea { width: 100%; height: 180px; font-family: Menlo, monospace; font-size: 13px; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
  button { margin: 12px 8px 12px 0; padding: 8px 18px; border: none; border-radius: 6px; background: #1a73e8; color: #fff; font-size: 14px; cursor: pointer; }
  button.sec { background: #5f6368; }
  pre { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 14px; white-space: pre-wrap; }
  .label { color:#888; font-size:13px; margin-top:18px; }
</style>
</head>
<body>
<h1>🔍 SSRM 根因分类 Playground</h1>
<p>点击预设案例或粘贴自己的 incident JSON，然后点「分类」。当前通道见返回的 <code>source</code>/<code>llm_provider</code> 字段。</p>
<div>
  <button class="sec" onclick="fill('callback')">案例: 回调丢失</button>
  <button class="sec" onclick="fill('ea')">案例: EA 超时</button>
  <button class="sec" onclick="fill('dup')">案例: 重复提交</button>
  <button class="sec" onclick="fill('hard')">案例: 困难(口语化)</button>
</div>
<textarea id="payload"></textarea>
<div><button onclick="run()">🔍 分类</button></div>
<div class="label">结果：</div>
<pre id="result">（等待请求）</pre>
<script>
const CASES = {
  callback: {"stage":"register","source_system":"xflow","error_code":null,"error_message":"审批单 FT20261025088 状态为 APPROVED 但 SSRM 无对应注册任务","wait_minutes":55,"retry_count":0,"downstream":"ssrm","http_status":null},
  ea: {"stage":"change","source_system":"ssrm","error_code":"EA_TIMEOUT","error_message":"调用 EA updateSupplier 超时 5s","wait_minutes":3,"retry_count":4,"downstream":"ea","http_status":504},
  dup: {"stage":"register","source_system":"dora","error_code":null,"error_message":"同一供应商 10 秒内提交了两次注册，第二次提示申请已存在","wait_minutes":0,"retry_count":0,"downstream":"ssrm","http_status":409},
  hard: {"stage":"register","source_system":"portal","error_code":"JOB_STUCK","error_message":"用户反馈提交后页面一直显示审批中，但后台查审批记录其实早已通过","wait_minutes":95,"retry_count":0,"downstream":"ssrm","http_status":null}
};
function fill(k){ document.getElementById('payload').value = JSON.stringify({"incident": CASES[k]}, null, 2); }
async function run(){
  const r = document.getElementById('result');
  r.textContent = '分类中...（LLM 通道约需 5~10 秒）';
  try {
    const resp = await fetch('/classify', {method:'POST', headers:{'Content-Type':'application/json'}, body: document.getElementById('payload').value});
    const d = await resp.json();
    r.textContent = JSON.stringify(d, null, 2);
  } catch(e){ r.textContent = '请求失败: ' + e; }
}
</script>
</body>
</html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "llm_configured": llm_client.is_configured,
        "llm_providers": llm_client.configured_providers,
        "classes": list(ROOT_CAUSE_CLASSES.keys()),
    }


@app.post("/classify")
async def classify(req: ClassifyRequest):
    result = await classifier.classify(req.incident)
    return result


@app.post("/eval")
async def eval_endpoint(req: EvalRequest):
    """数据集评测：逐条分类并与标注对比，输出总体准确率、分类别准确率、混淆对。"""
    rows = load_dataset(limit=req.limit, shuffle=True)
    correct = 0
    per_class: dict[str, dict[str, int]] = {}
    confusions: list[dict[str, str]] = []
    latencies: list[float] = []
    sources: dict[str, int] = {}

    for row in rows:
        import time
        t0 = time.monotonic()
        result = await classifier.classify(row["incident"])
        latencies.append((time.monotonic() - t0) * 1000)
        sources[result.get("source", "?")] = sources.get(result.get("source", "?"), 0) + 1

        pred = result.get("root_cause_class")
        label = row["label"]
        per_class.setdefault(label, {"total": 0, "correct": 0})
        per_class[label]["total"] += 1
        if pred == label:
            correct += 1
            per_class[label]["correct"] += 1
        else:
            confusions.append({"label": label, "predicted": pred, "incident_id": row["incident"]["incident_id"]})

    total = len(rows)
    return {
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "source_channels": sources,
        "per_class": {
            k: {"accuracy": round(v["correct"] / v["total"], 3), **v}
            for k, v in per_class.items()
        },
        "confusions": confusions[:10],
    }


@app.get("/stats")
async def dataset_stats():
    return stats()


class AssistRequest(BaseModel):
    question: str


@app.post("/assist")
async def assist(req: AssistRequest):
    """值班助手：描述你遇到的问题，返回相似历史工单和处置建议"""
    return await rag.assist(req.question)


@app.get("/kb/stats")
async def kb_stats():
    from collections import Counter
    c = Counter(d["root_cause_class"] for d in rag.docs)
    return {"tickets": len(rag.docs), "by_class": dict(c), "mode": rag.mode, "ready": rag.ready}
