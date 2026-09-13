# app/services/bot_service.py
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BotService:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        client: httpx.AsyncClient,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self._client = client

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def _get_access_token(self) -> str:
        """Bot Framework REST API용 OAuth2 Token 발급 및 메모리 캐싱"""
        now = datetime.now(timezone.utc)

        # 토큰이 유효한 경우 재사용 (만료 5분 전까지)
        if (
            self._access_token
            and self._token_expires_at
            and now < (self._token_expires_at - timedelta(minutes=5))
        ):
            return self._access_token

        # Bot Framework 전용 Token Endpoint
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://api.botframework.com/.default",  # Bot Connector 전용 Scope
        }

        res = await self._client.post(token_url, data=payload)
        res.raise_for_status()
        data = res.json()

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = now + timedelta(seconds=expires_in)
        '''
        logger.info("[BotService] Bot Framework 액세스 토큰 발급완료")
        '''
        return self._access_token

    async def send_teams_reply(
        self, service_url: str, conversation_id: str, message: str
    ):
        """[POST] 기존 봇 대화창(conversation_id)으로 메시지 발송"""
        if not service_url or not conversation_id:
            logger.warning("[BotService] service_url 또는 conversation_id가 누락되었습니다.")
            return

        token = await self._get_access_token()
        base_url = service_url.rstrip("/")
        endpoint = f"{base_url}/v3/conversations/{conversation_id}/activities"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        payload = {
            "type": "message",
            "text": message,
        }

        res = await self._client.post(endpoint, headers=headers, json=payload)

        # 401 Unauthorized 발생 시 토큰 강제 재발급 후 1회 재시도
        if res.status_code == 401:
            logger.info("[BotService] 토큰 만료 감지, 재발급 후 재시도합니다.")
            self._access_token = None
            token = await self._get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            res = await self._client.post(endpoint, headers=headers, json=payload)

        if res.status_code not in (200, 201, 202):
            logger.error(
                f"[BotService] 메시지 발송 실패 ({res.status_code}): {res.text}"
            )
        else:
            '''
            logger.info(
                f"[BotService] 메시지 발송 성공 (Conversation: {conversation_id})"
            )
            '''
    async def create_or_get_conversation(
        self, service_url: str, user_id: str, tenant_id: str
    ) -> Optional[str]:
        """user_id를 기반으로 새 봇 기준 1:1 대화방 생성 및 conversation_id 반환"""
        try:
            token = await self._get_access_token()
            base_url = service_url.rstrip("/")
            endpoint = f"{base_url}/v3/conversations"

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            payload = {
                "bot": {"id": self.client_id},
                "members": [{"id": user_id}],
                "channelData": {"tenant": {"id": tenant_id}},
            }

            res = await self._client.post(endpoint, headers=headers, json=payload)
            res.raise_for_status()

            # 새로 발급된 conversation_id
            return res.json().get("id")

        except Exception as e:
            logger.error(f"[BotService] 대화방 생성 실패 (User: {user_id}): {e}")
            return None