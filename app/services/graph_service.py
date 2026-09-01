import logging
from typing import Optional, Any, Dict
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)


class GraphService:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient  # 전역 httpx.AsyncClient 주입
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = client  # 전역 HTTP 클라이언트 재사용
        self._access_token: Optional[str] = None

    async def _get_access_token(self) -> str:
        """Azure AD OAuth2.0 Token 발급 (App-only permission)"""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default"
        }

        res = await self._client.post(token_url, data=payload)
        res.raise_for_status()
        data = res.json()
        self._access_token = data["access_token"]
        return self._access_token

    async def _get_headers(self) -> dict:
        """Graph API 요청 헤더 생성"""
        if not self._access_token:
            await self._get_access_token()
        
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json"
        }

    async def _request_with_retry(
        self, 
        method: str, 
        url: str, 
        json_payload: Optional[Dict[str, Any]] = None
    ) -> httpx.Response:
        """401 토큰 만료 자동 재시도를 포함한 공통 HTTP 요청 Wrapper"""
        headers = await self._get_headers()
        
        res = await self._client.request(method, url, headers=headers, json=json_payload)
        
        # 401 Unauthorized 시 토큰 재발급 후 1회 재시도
        if res.status_code == 401:
            logger.info("[Graph API] 토큰 만료 감지, 재발급 후 재시도합니다.")
            await self._get_access_token()
            headers = await self._get_headers()
            res = await self._client.request(method, url, headers=headers, json=json_payload)

        return res

    # ------------------------------------------------------------------
    # MS Graph Calendar CRUD Operations
    # ------------------------------------------------------------------

    async def create_user_calendar_event(
        self,
        user_id: str,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str] = None,
        description: Optional[str] = None,
        time_zone: str = "Asia/Seoul"
    ) -> str:
        """[POST] 특정 사용자 개인 캘린더에 새 일정 생성 후 event_id 반환"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events"

        payload = {
            "subject": title,
            "body": {
                "contentType": "HTML",
                "content": description or ""
            },
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            }
        }

        res = await self._request_with_retry("POST", url, json_payload=payload)
        res.raise_for_status()
        event_data = res.json()
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 생성 성공 (Event ID: {event_data['id']})")
        return event_data["id"]

    async def update_user_calendar_event(
        self,
        user_id: str,
        event_id: str,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str] = None,
        description: Optional[str] = None,
        time_zone: str = "Asia/Seoul"
    ):
        """[PATCH] 기존 사용자 캘린더 이벤트 핀포인트 수정"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"

        payload = {
            "subject": title,
            "body": {
                "contentType": "HTML",
                "content": description or ""
            },
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            }
        }

        res = await self._request_with_retry("PATCH", url, json_payload=payload)
        res.raise_for_status()
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 수정 성공 (Event ID: {event_id})")

    async def delete_user_calendar_event(
        self,
        user_id: str,
        event_id: str
    ):
        """[DELETE] 사용자 캘린더 이벤트 삭제"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"

        res = await self._request_with_retry("DELETE", url)

        if res.status_code == 404:
            logger.warning(f"[Graph API] User {user_id} 삭제 대상 이벤트가 존재하지 않음 (Event ID: {event_id})")
            return

        res.raise_for_status()
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 삭제 성공 (Event ID: {event_id})")

    async def send_teams_chat_message(self, user_id: str, message: str):
        """[POST] 1:1 Teams 채팅 메시지 발송 (일정 알림용)"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/chats"
        payload = {
            "chatType": "oneOnOne",
            "members": [
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user_id}')"
                }
            ]
        }
        res = await self._request_with_retry("POST", url, json_payload=payload)
        if res.status_code in (200, 201):
            chat_id = res.json()["id"]
            msg_url = f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages"
            await self._request_with_retry("POST", msg_url, json_payload={"body": {"content": message}})