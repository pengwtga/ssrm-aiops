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
            "POST /assist": "值班助手问答（body: {\"question\": \"...\"}）",
            "GET /kb/stats": "知识库统计",
        },
        "pages": {
            "playground": "/playground",
            "assistant": "/assistant",
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


@app.get("/assistant")
async def assistant_page():
    """值班助手可视化页面：聊天式问答 + 相似工单展示"""
    html = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SSRM 值班助手</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", sans-serif; margin:0; background:#f0f2f5; color:#222; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 20px 16px 40px; }
  header { background: linear-gradient(135deg,#1a73e8,#4285f4); color:#fff; border-radius: 12px; padding: 18px 22px; margin-bottom: 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
  header h1 { margin:0; font-size: 20px; }
  header p { margin: 4px 0 0; font-size: 13px; opacity:.9; }
  .badge { background: rgba(255,255,255,.2); border:1px solid rgba(255,255,255,.4); border-radius: 999px; padding: 3px 12px; font-size:12px; }
  .presets { display:flex; flex-wrap:wrap; gap:8px; margin-bottom: 14px; }
  .presets button { background:#fff; border:1px solid #d0d7de; border-radius: 8px; padding: 8px 14px; font-size:13px; cursor:pointer; color:#333; }
  .presets button:hover { border-color:#1a73e8; color:#1a73e8; }
  .chat { display:flex; flex-direction:column; gap:12px; }
  .msg { display:flex; gap:10px; }
  .msg .avatar { width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:17px; flex-shrink:0; }
  .msg.user { flex-direction: row-reverse; }
  .msg.user .avatar { background:#1a73e8; }
  .msg.bot .avatar { background:#dfe3e8; }
  .bubble { max-width: 78%; border-radius: 12px; padding: 12px 16px; font-size: 14px; line-height: 1.65; }
  .msg.user .bubble { background:#1a73e8; color:#fff; border-top-right-radius: 4px; }
  .msg.bot .bubble { background:#fff; border:1px solid #e2e6ea; border-top-left-radius: 4px; }
  .meta { display:flex; flex-wrap:wrap; gap:6px; margin: 8px 0 10px; }
  .tag { font-size:11.5px; border-radius:999px; padding:2px 10px; border:1px solid; }
  .tag.mode { color:#0b7285; border-color:#0b7285; background:#e7f5f7; }
  .tag.provider { color:#5f3dc4; border-color:#5f3dc4; background:#f3f0ff; }
  .tag.conf-high { color:#2b8a3e; border-color:#2b8a3e; background:#ebfbee; }
  .tag.conf-mid { color:#e8590c; border-color:#e8590c; background:#fff4e6; }
  .tag.conf-low { color:#c92a2a; border-color:#c92a2a; background:#fff5f5; }
  .advice { background:#f8f9fa; border-left:3px solid #1a73e8; padding:8px 12px; border-radius:0 6px 6px 0; margin:8px 0; white-space:pre-wrap; }
  .h2 { font-size:13px; color:#666; margin:14px 0 6px; font-weight:600; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th,td { text-align:left; padding:6px 8px; border-bottom:1px solid #eee; }
  th { color:#888; font-weight:500; }
  .score { font-variant-numeric: tabular-nums; color:#1a73e8; font-weight:600; }
  .rawcases { font-size:12.5px; color:#555; }
  .rawcases li { margin: 4px 0; }
  .empty { color:#999; font-size:13px; }
  .inputbar { display:flex; gap:10px; margin-top:16px; position:sticky; bottom:0; }
  .inputbar input { flex:1; border:1px solid #d0d7de; border-radius:10px; padding: 12px 14px; font-size:14px; outline:none; background:#fff; }
  .inputbar input:focus { border-color:#1a73e8; }
  .inputbar button { background:#1a73e8; color:#fff; border:none; border-radius:10px; padding: 0 22px; font-size:14px; cursor:pointer; }
  .inputbar button:disabled { opacity:.6; cursor:not-allowed; }
  .typing { color:#999; font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>🛎️ SSRM 值班助手</h1>
      <p>描述你遇到的问题，我会检索相似的历史工单并给出处置建议</p>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <span class="badge" id="modeBadge">模式加载中…</span>
      <a class="badge" href="/playground" style="text-decoration:none;color:#fff">→ 根因分类</a>
    </div>
  </header>

  <div class="presets">
    <button onclick="askPreset('审批已经通过了，但 SSRM 里一直没生成注册任务，等了一个小时了')">审批通过但没生成任务</button>
    <button onclick="askPreset('调用 EA 的 updateSupplier 接口一直超时，重试了 4 次还是失败')">EA 同步一直失败</button>
    <button onclick="askPreset('供应商说提交注册时报错申请已存在，怀疑是重复提交')">怀疑重复提交</button>
    <button onclick="askPreset('在 Dora 上查不到这个供应商的联系人信息')">Dora 查不到联系人</button>
  </div>

  <div class="chat" id="chat">
    <div class="msg bot">
      <div class="avatar">🤖</div>
      <div class="bubble">你好！我是 SSRM 值班助手。遇到线上问题可以直接描述（例如「审批通过但没生成任务」），我会帮你：<br>1️⃣ 检索最相似的历史工单<br>2️⃣ 结合 LLM 给出处置建议<br><span class="empty">也可以点上方预设问题快速体验。</span></div>
    </div>
  </div>

  <div class="inputbar">
    <input id="q" placeholder="输入你的问题，回车发送…" onkeydown="if(event.key==='Enter')send()">
    <button id="sendBtn" onclick="send()">发送</button>
  </div>
</div>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('q');
const sendBtn = document.getElementById('sendBtn');

fetch('/kb/stats').then(r=>r.json()).then(d=>{
  const b = document.getElementById('modeBadge');
  const modeName = d.mode === 'vector' ? '向量检索' : (d.mode === 'keyword+rerank' ? '关键词+LLM重排' : d.mode);
  b.textContent = `📚 知识库 ${d.tickets} 条 | ${modeName}`;
}).catch(()=>{});

function esc(s){ const d=document.createElement('div'); d.textContent = s==null?'':String(s); return d.innerHTML; }

function addUser(text){
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = `<div class="avatar">🧑‍💻</div><div class="bubble">${esc(text)}</div>`;
  chat.appendChild(div);
}
function addBotHTML(html){
  const div = document.createElement('div');
  div.className = 'msg bot';
  div.innerHTML = `<div class="avatar">🤖</div><div class="bubble">${html}</div>`;
  chat.appendChild(div);
  return div;
}
function confTag(c){
  if (c == null) return '';
  const cls = c >= 0.85 ? 'conf-high' : (c >= 0.7 ? 'conf-mid' : 'conf-low');
  return `<span class="tag ${cls}">置信度 ${(c*100).toFixed(0)}%</span>`;
}
function lowConfHint(c){
  if (c == null || c >= 0.55) return '';
  return '<div class="empty">⚠️ 相似度低于采信阈值(0.55)，以上建议仅供参考，请人工判断。</div>';
}

async function askPreset(q){ input.value = q; send(); }

async function send(){
  const q = input.value.trim();
  if (!q || sendBtn.disabled) return;
  input.value = '';
  sendBtn.disabled = true;
  addUser(q);
  const t = addBotHTML('<span class="typing">正在检索历史工单并生成建议…（约 5~10 秒）</span>');
  try {
    const resp = await fetch('/assist', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question: q})});
    const d = await resp.json();
    render(t, d);
  } catch(e){
    t.innerHTML = `<span style="color:#c92a2a">请求失败：${esc(e.message || e)}</span>`;
  } finally {
    sendBtn.disabled = false;
  }
}

function render(t, d){
  if (d.error){ t.innerHTML = `<span style="color:#c92a2a">${esc(d.error)}</span>`; return; }

  // /assist 返回：answer(字符串) / confidence(顶层) / mode / llm_provider / sources / cases(降级)
  const meta = [
    `<span class="tag mode">模式: ${esc(d.mode || '-')}</span>`,
    d.llm_provider ? `<span class="tag provider">LLM: ${esc(d.llm_provider)}</span>` : '',
    confTag(d.confidence)
  ].join('');

  const answerText = typeof d.answer === 'string' ? d.answer : (d.answer && (d.answer.suggested_action || d.answer.summary)) || '（无建议）';
  const advice = `<div class="h2">💡 处置建议</div><div style="white-space:pre-wrap">${esc(answerText)}</div>` + lowConfHint(d.confidence);

  let cases = '';
  if (d.cases && d.cases.length){
    // LLM 降级模式：返回完整 case（含症状与处置过程）
    cases = `<div class="h2">📋 相似历史工单（降级返回原始 case）</div>` + d.cases.map(c =>
      `<div style="background:#f8f9fa;border-radius:8px;padding:10px 12px;margin:6px 0;font-size:13px">` +
      `<b>${esc(c.ticket_id)}</b> · ${esc(c.title)}<br>` +
      `<span style="color:#666">现象：</span>${esc(c.symptom)}<br>` +
      `<span style="color:#666">处置：</span>${esc(c.resolution)}</div>`).join('');
  } else if (d.sources && d.sources.length){
    const hasDetail = !!d.sources[0].title;
    cases = `<div class="h2">📋 相似历史工单（Top ${d.sources.length}）</div><table><tr><th>工单</th>` +
      (hasDetail ? '<th>标题</th><th>根因类别</th>' : '') + '<th>相似度</th></tr>' +
      d.sources.map(s=>`<tr><td>${esc(s.ticket_id)}</td>` +
        (hasDetail ? `<td>${esc(s.title)}</td><td>${esc(s.root_cause_class)}</td>` : '') +
        `<td class="score">${((s.score||0)*100).toFixed(0)}%</td></tr>`).join('') +
      `</table>`;
  } else {
    cases = '<div class="empty">（未检索到相似工单）</div>';
  }

  t.innerHTML = meta + advice + cases;
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
