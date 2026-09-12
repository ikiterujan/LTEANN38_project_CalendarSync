import logging
from typing import Optional, Any, Dict, List
from datetime import datetime, timezone, timedelta
import httpx
import asyncio

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class GraphService:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient,
        max_concurrent_requests: int = 7
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = client
        self._access_token: Optional[str] = None
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._token_lock = asyncio.Lock()  # 토큰 재발급 Race Condition 방지용 락

    async def _get_access_token(self) -> str:
        """Azure AD OAuth2.0 Token 발급 (동시성 Lock 적용)"""
        async with self._token_lock:
            # 다른 코루틴이 이미 토큰을 갱신했다면 즉시 리턴
            if self._access_token:
                return self._access_token

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
        json_payload: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> httpx.Response:
        async with self._semaphore:
            for attempt in range(max_retries):
                headers = await self._get_headers()
                
                try:
                    res = await self._client.request(method, url, headers=headers, json=json_payload)

                    if res.status_code == 429:
                        retry_after = int(res.headers.get("Retry-After", 2 ** attempt))
                        logger.warning(f"[Graph API 429 Throttled] {retry_after}초 후 재시도합니다... ({attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        continue

                    if res.status_code == 401 and attempt == 0:
                        logger.info("[Graph API] 토큰 만료 감지, 재발급 후 재시도합니다.")
                        self._access_token = None  # 토큰 캐시 초기화
                        await self._get_access_token()
                        continue

                    return res

                except httpx.RequestError as e:
                    logger.error(f"[Graph API 네트워크 에러] {e} (재시도 중... {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)

            return res

    def _format_datetime_for_graph(self, dt: Any) -> str:
        """MS Graph API 전용 Pure Local ISO 문자열 포맷팅 (Offset 제거)"""
        if isinstance(dt, str):
            # 문자열로 들어올 경우 ISO 파싱 후 순수 YYYY-MM-DDTHH:MM:SS 형태로 변환
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}?$select=id,mail,userPrincipalName,displayName"

        try:
            res = await self._request_with_retry("GET", url)
            if res.status_code == 404:
                logger.warning(f"[Graph API] 존재하지 않는 사용자 ID")
                return None
            
            res.raise_for_status()
            return res.json()

        except Exception as e:
            logger.error(f"[Graph API] 조회 중 알 수 없는 에러: {e}")
            return None
        
    async def _get_all_pages(self, url: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        headers = await self._get_headers()

        while url:
            res = await self._client.get(url, headers=headers)
            if res.status_code == 401:
                self._access_token = None
                await self._get_access_token()
                headers = await self._get_headers()
                res = await self._client.get(url, headers=headers)

            res.raise_for_status()
            data = res.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        return items

    async def get_user_joined_channels(self, user_id: str) -> List[Dict[str, Any]]:
        teams_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/joinedTeams"

        try:
            joined_teams = await self._get_all_pages(teams_url)
        except httpx.HTTPStatusError as e:
            logger.error(f"[Graph API] 소속 팀 조회 실패: {e}")
            return []

        channels: List[Dict[str, Any]] = []
        for team in joined_teams:
            team_id = team["id"]
            channels_url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels"
            try:
                team_channels = await self._get_all_pages(channels_url)
            except httpx.HTTPStatusError as e:
                logger.error(f"[Graph API] Team 채널 조회 실패: {e}")
                continue

            for ch in team_channels:
                channels.append({
                    "id": ch["id"],
                    "team_id": team_id,
                    "displayName": ch.get("displayName"),
                })

        return channels

    async def get_channel_messages(
        self,
        team_id: str,
        channel_id: str,
        since_minutes: int = 90,
    ) -> List[Dict[str, Any]]:
        url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages/delta"

        try:
            messages = await self._get_all_pages(url)
        except httpx.HTTPStatusError as e:
            logger.error(f"[Graph API] 메시지 조회 실패: {e}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        recent_messages = []
        for msg in messages:
            if msg.get("messageType") != "message" or msg.get("deletedDateTime"):
                continue
            
            last_modified = msg.get("lastModifiedDateTime") or msg.get("createdDateTime")
            if last_modified:
                msg_dt = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
                if msg_dt < cutoff:
                    continue

            recent_messages.append(msg)

        return recent_messages

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
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events"

        payload = {
            "subject": title,
            "body": {
                "contentType": "HTML",
                "content": description or ""
            },
            "start": {
                "dateTime": self._format_datetime_for_graph(start_dt),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": self._format_datetime_for_graph(end_dt),
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            },
            "singleValueExtendedProperties": [
                {
                    "id": "String {fbd4ec16-7f0f-46d2-a9db-f32258a48607} Name CalendarSyncApp",
                    "value": "CalendarSync_2026"
                }
            ]
        }

        res = await self._request_with_retry("POST", url, json_payload=payload)
        res.raise_for_status()
        event_data = res.json()
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
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"

        payload = {
            "subject": title,
            "body": {
                "contentType": "HTML",
                "content": description or ""
            },
            "start": {
                "dateTime": self._format_datetime_for_graph(start_dt),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": self._format_datetime_for_graph(end_dt),
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            }
        }

        res = await self._request_with_retry("PATCH", url, json_payload=payload)
        res.raise_for_status()

    async def delete_user_calendar_event(
        self,
        user_id: str,
        event_id: str
    ):
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"

        res = await self._request_with_retry("DELETE", url)

        if res.status_code == 404:
            logger.warning(f"[Graph API] 삭제 대상 이벤트가 존재하지 않음")
            return

        res.raise_for_status()

    async def delete_user_synced_events(
        self, 
        user_id: str, 
        property_name: str = "CalendarSyncApp", 
        property_value: str = "CalendarSync_2026"
    ) -> int:
        """Extended Property 식별자를 Expand하여 안전하게 일괄 삭제"""
        property_guid = "fbd4ec16-7f0f-46d2-a9db-f32258a48607"
        prop_id = f"String {{{property_guid}}} Name {property_name}"

        # $expand 구문으로 singleValueExtendedProperties를 함께 조회
        url = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/events"
            f"?$select=id,subject"
            f"&\(expand=singleValueExtendedProperties(\)filter=id eq '{prop_id}')"
            f"&$top=100"
        )

        try:
            fetched_events = await self._get_all_pages(url)
            
            # App 식별자 속성값이 일치하는 일정만 필터링
            events_to_delete = []
            for ev in fetched_events:
                ext_props = ev.get("singleValueExtendedProperties", [])
                for prop in ext_props:
                    if prop.get("id") == prop_id and prop.get("value") == property_value:
                        events_to_delete.append(ev)
                        break

            if not events_to_delete:
                logger.info(f"[Graph API] 삭제할 동기화 일정이 없습니다.")
                return 0

            total_count = len(events_to_delete)

            delete_tasks = [
                self.delete_user_calendar_event(user_id=user_id, event_id=event.get("id"))
                for event in events_to_delete
                if event.get("id")
            ]

            results = await asyncio.gather(*delete_tasks, return_exceptions=True)

            success_count = sum(1 for r in results if not isinstance(r, Exception))
            failed_count = total_count - success_count

            if failed_count > 0:
                logger.warning(f"[Graph API] 일정 삭제 완료 (성공: {success_count}건, 실패: {failed_count}건)")
            else:
                logger.info(f"[Graph API] 모든 동기화 일정 삭제 성공 (총 {success_count}건)")

            return success_count

        except Exception as e:
            logger.error(f"[Graph API] 일정 일괄 삭제 처리 중 에러: {e}", exc_info=True)
            return 0

    async def delete_events_by_ids(self, user_id: str, event_ids: List[str]) -> int:
        valid_ids = [eid for eid in event_ids if eid]
        if not valid_ids:
            return 0

        delete_tasks = [
            self.delete_user_calendar_event(user_id=user_id, event_id=eid)
            for eid in valid_ids
        ]

        results = await asyncio.gather(*delete_tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))