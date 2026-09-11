#app/core/dependencies.py
from openai import AsyncOpenAI
from app.core.config import settings
from app.services.graph_service import GraphService
from app.services.llm_service import LLMService
from app.services.bot_service import BotService
from app.services.sync_service import SyncService
from app.services.ocr_service import EasyOCRService
import httpx

# Async Client 초기화
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

global_httpx_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=100, max_connections=300),
    timeout=httpx.Timeout(20.0, connect=10.0)
)

bot_service = BotService(
    client_id=settings.AZURE_CLIENT_ID,
    client_secret=settings.AZURE_CLIENT_SECRET,
    tenant_id=settings.AZURE_TENANT_ID,
    client=global_httpx_client,
)

graph_service = GraphService(
    tenant_id=settings.AZURE_TENANT_ID,
    client_id=settings.AZURE_CLIENT_ID,
    client_secret=settings.AZURE_CLIENT_SECRET,
    client=global_httpx_client
)

ocr_service = EasyOCRService()

sync_service = SyncService(graph_service, ocr_service)

llm_service = LLMService(openai_client=openai_client)