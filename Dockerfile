# SSRM Ops Agent — 后端容器镜像
# 构建：docker build -t ssrm-aiops-api:latest .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# curl 只用于 HEALTHCHECK；装完即清 apt 缓存，避免镜像变大
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# 关键：先只拷依赖清单再装依赖。
# 这样改业务代码不会触发重装，构建从 60s 降到 3s（层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 业务代码与数据最后拷
COPY app ./app
COPY data ./data
COPY scripts ./scripts

# 非 root 运行：容器逃逸风险从 root 降为普通用户
# data 目录需要可写——RAG 启动时会把向量缓存落到 data/kb_vectors.json
RUN useradd -m -u 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# 用 /health 而不是 TCP 探活：能真正反映「LLM 通道是否就绪 + 应用是否启动完成」
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
