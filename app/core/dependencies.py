# app/core/dependencies.py
from openai import AsyncOpenAI
from app.core.config import settings
from app.services.graph_service import GraphService
from app.services.llm_service import LLMService

import httpx

# Async Client 초기화
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

global_httpx_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=100, max_connections=300),
    timeout=httpx.Timeout(20.0, connect=10.0)
)

graph_service = GraphService(
    tenant_id=settings.AZURE_TENANT_ID,
    client_id=settings.AZURE_CLIENT_ID,
    client_secret=settings.AZURE_CLIENT_SECRET,
    client=global_httpx_client
)

llm_service = LLMService(openai_client=openai_client)