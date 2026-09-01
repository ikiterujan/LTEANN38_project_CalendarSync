import logging
from typing import Optional
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)


class GraphService:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
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

        async with httpx.AsyncClient() as client:
            res = await client.post(token_url, data=payload)
            res.raise_for_status()
            data = res.json()
            return data["access_token"]

    async def _get_headers(self) -> dict:
        """Graph API 요청 헤더 생성"""
        if not self._access_token:
            self._access_token = await self._get_access_token()
        
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json"
        }

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
        headers = await self._get_headers()

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

        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json=payload)
            
            # 토큰 만료 401 재시도 로직
            if res.status_code == 401:
                self._access_token = await self._get_access_token()
                headers = await self._get_headers()
                res = await client.post(url, headers=headers, json=payload)

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
        headers = await self._get_headers()

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

        async with httpx.AsyncClient() as client:
            res = await client.patch(url, headers=headers, json=payload)
            
            if res.status_code == 401:
                self._access_token = await self._get_access_token()
                headers = await self._get_headers()
                res = await client.patch(url, headers=headers, json=payload)

            res.raise_for_status()
            logger.info(f"[Graph API] User {user_id} 캘린더 일정 수정 성공 (Event ID: {event_id})")

    async def delete_user_calendar_event(
        self,
        user_id: str,
        event_id: str
    ):
        """[DELETE] 사용자 캘린더 이벤트 삭제"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"
        headers = await self._get_headers()

        async with httpx.AsyncClient() as client:
            res = await client.delete(url, headers=headers)
            
            if res.status_code == 401:
                self._access_token = await self._get_access_token()
                headers = await self._get_headers()
                res = await client.delete(url, headers=headers)

            # 이미 삭제된 이벤트(404)인 경우는 정상 처리
            if res.status_code == 404:
                logger.warning(f"[Graph API] User {user_id} 삭제 대상 이벤트가 존재하지 않음 (Event ID: {event_id})")
                return

            res.raise_for_status()
            logger.info(f"[Graph API] User {user_id} 캘린더 일정 삭제 성공 (Event ID: {event_id})")