# app/core/dependencies.py
from openai import AsyncOpenAI
from app.core.config import settings
from app.services.graph_service import GraphService
from app.services.llm_service import LLMService

# Async Client 초기화
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

graph_service = GraphService(
    tenant_id=settings.AZURE_TENANT_ID,
    client_id=settings.AZURE_CLIENT_ID,
    client_secret=settings.AZURE_CLIENT_SECRET
)

llm_service = LLMService(openai_client=openai_client)