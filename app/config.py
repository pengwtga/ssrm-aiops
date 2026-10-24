"""SSRM AIOps 全局配置

与 multi-agent-aiops 主仓库完全独立：
- 独立端口 8001（主仓库 demo 是短链场景，跑在 8000）
- 多 LLM Provider 容灾链：primary(deepseek/任意兼容) → zhipu(免费 glm-4-flash) → siliconflow(免费 Qwen) → 规则基线
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "SSRM Ops Agent"
    debug: bool = False

    # 主 LLM（OpenAI 兼容接口，DeepSeek 或任意中转站）
    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    # 备用 Provider：智谱（glm-4-flash 永久免费）
    zhipu_api_key: Optional[str] = None
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "glm-4-flash"

    # 备用 Provider：硅基流动（Qwen2.5-7B-Instruct 免费档）
    siliconflow_api_key: Optional[str] = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "Qwen/Qwen2.5-7B-Instruct"

    # LLM 通用
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 1  # 单 Provider 内重试次数（跨 Provider 容灾由链路负责）

    # RAG 值班助手
    embedding_model: str = "embedding-2"  # 智谱向量化模型
    rag_top_k: int = 3
    rag_min_score: float = 0.55  # 相似度低于此值的检索结果不采信

    # 分类 Agent
    min_confidence_for_auto_action: float = 0.85  # 置信度低于此值 → 建议人工确认

    # 服务
    api_port: int = 8001

    model_config = {"env_prefix": "SSRM_AIOPS_", "env_file": ".env"}


settings = Settings()
