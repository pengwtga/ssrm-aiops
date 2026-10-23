"""LLM 客户端 — 多 Provider 容灾链（OpenAI 兼容接口通用）

设计要点（面试重点）：
1. Provider 链式容灾：primary(deepseek) → zhipu(glm-4-flash 免费) → siliconflow(免费 Qwen)，
   逐个尝试，任何一个成功即返回；全挂才抛 LLMError，由分类 Agent 降级到规则基线
2. response_format 兼容性处理：glm-4-flash 等模型对 json_object 支持不一，
   遇 400 自动去掉该参数重试，并用宽松 JSON 提取兜底（剥 markdown 代码围栏）
3. 纯 httpx 实现，不依赖 SDK
4. 未配置任何 Key 抛 LLMNotConfiguredError —— 服务永不因 LLM 挂掉而不可用
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMNotConfiguredError(LLMError):
    pass


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str


class LLMClient:
    def __init__(self):
        self._chain: Optional[list[Provider]] = None

    @property
    def chain(self) -> list[Provider]:
        """按配置构建 Provider 容灾链（惰性构建，便于测试时热替换）"""
        if self._chain is None:
            chain: list[Provider] = []
            if settings.llm_api_key:
                chain.append(Provider("primary", settings.llm_base_url, settings.llm_model, settings.llm_api_key))
            if settings.zhipu_api_key:
                chain.append(Provider("zhipu", settings.zhipu_base_url, settings.zhipu_model, settings.zhipu_api_key))
            if settings.siliconflow_api_key:
                chain.append(Provider("siliconflow", settings.siliconflow_base_url, settings.siliconflow_model, settings.siliconflow_api_key))
            self._chain = chain
        return self._chain

    def invalidate_chain(self):
        """配置热更新后调用，强制重建容灾链"""
        self._chain = None

    @property
    def is_configured(self) -> bool:
        return len(self.chain) > 0

    @property
    def configured_providers(self) -> list[str]:
        return [p.name for p in self.chain]

    async def chat_json(self, system_prompt: str, user_payload: str) -> dict[str, Any]:
        """遍历容灾链调用 LLM，返回 (解析后的 JSON, provider 名)。

        全链失败才抛 LLMError。
        """
        if not self.is_configured:
            raise LLMNotConfiguredError("未配置任何 LLM Provider 的 API Key")

        errors: list[str] = []
        for provider in self.chain:
            try:
                result = await self._call_with_retry(provider, system_prompt, user_payload)
                result["_provider"] = provider.name
                return result
            except Exception as e:
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"[LLMClient] provider={provider.name} 失败，切换下一个: {e}")
        raise LLMError(f"所有 LLM Provider 均失败: {'; '.join(errors)}")

    async def _call_with_retry(self, provider: Provider, system_prompt: str, user_payload: str) -> dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                return await self._call(provider, system_prompt, user_payload, use_json_mode=True)
            except _JSONModeUnsupported:
                continue  # json_object 不支持 → 立即换宽松模式，不消耗重试
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, LLMError) as e:
                last_err = e
                logger.warning(f"[LLMClient] {provider.name} attempt {attempt + 1} failed: {e}")
        raise LLMError(str(last_err))

    async def _call(
        self, provider: Provider, system_prompt: str, user_payload: str,
        use_json_mode: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": provider.model,
            "temperature": settings.llm_temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
        }
        if use_json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code == 400 and use_json_mode:
                # 部分模型不支持 response_format=json_object → 宽松模式重试
                logger.info(f"[LLMClient] {provider.name} 不支持 json_object，改用宽松 JSON 提取")
                raise _JSONModeUnsupported()
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json(content)


class _JSONModeUnsupported(Exception):
    pass


def _extract_json(content: str) -> dict[str, Any]:
    """宽松 JSON 提取：直接解析 → 剥代码围栏 → 截取首个平衡花括号块"""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    brace = re.search(r"\{.*\}", content, re.DOTALL)
    if brace:
        return json.loads(brace.group(0))
    raise json.JSONDecodeError("no json found", content, 0)


# 模块级单例
llm_client = LLMClient()
