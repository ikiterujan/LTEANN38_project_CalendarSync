#app/services/graph_service.py
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
        client: httpx.AsyncClient,  # 전역 httpx.AsyncClient 주입
        max_concurrent_requests: int = 7
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = client  # 전역 HTTP 클라이언트 재사용
        self._access_token: Optional[str] = None
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

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
        json_payload: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> httpx.Response:
        """401 토큰 만료 자동 재시도를 포함한 공통 HTTP 요청 Wrapper"""
        async with self._semaphore:
            for attempt in range(max_retries):
                headers = await self._get_headers()
                
                try:
                    res = await self._client.request(method, url, headers=headers, json=json_payload)

                    # 1. 429 Too Many Requests 처리 (지수 백오프 및 Retry-After 적용)
                    if res.status_code == 429:
                        retry_after = int(res.headers.get("Retry-After", 2 ** attempt))
                        logger.warning(f"[Graph API 429 Throttled] {retry_after}초 후 재시도합니다... ({attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_after)
                        continue

                    # 2. 401 Unauthorized 처리 (토큰 재발급 후 재시도)
                    if res.status_code == 401 and attempt == 0:
                        logger.info("[Graph API] 토큰 만료 감지, 재발급 후 재시도합니다.")
                        await self._get_access_token()
                        continue

                    return res

                except httpx.RequestError as e:
                    logger.error(f"[Graph API 네트워크 에러] {e} (재시도 중... {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)

            return res

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        [GET] 특정 단일 사용자 정보 조회 (mail, userPrincipalName, displayName 등)
        webhook.py 등에서 user_id(aadObjectId) 기반으로 이메일을 즉시 추출할 때 사용합니다.
        """
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}?$select=id,mail,userPrincipalName,displayName"

        try:
            res = await self._request_with_retry("GET", url)
            if res.status_code == 404:
                '''
                logger.warning(f"[Graph API] 존재하지 않는 사용자 ID: {user_id}")
                '''
                logger.warning(f"[Graph API] 존재하지 않는 사용자 ID")
                return None
            
            res.raise_for_status()
            user_data = res.json()
            '''
            logger.info(f"[Graph API] User {user_id} 정보 조회 성공 (UPN: {user_data.get('userPrincipalName')})")
            '''
            return user_data

        except httpx.HTTPStatusError as e:
            '''
            logger.error(f"[Graph API] User {user_id} 정보 조회 실패: {e}")
            '''
            logger.error(f"[Graph API] 정보 조회 실패: {e}")
            return None
        except Exception as e:
            '''
            logger.error(f"[Graph API] User {user_id} 조회 중 알 수 없는 에러: {e}")
            '''
            logger.error(f"[Graph API] 조회 중 알 수 없는 에러: {e}")
            return None
        
    async def _get_all_pages(self, url: str) -> List[Dict[str, Any]]:
        """@odata.nextLink 페이지네이션을 모두 따라가며 value 배열을 누적 반환"""
        items: List[Dict[str, Any]] = []
        headers = await self._get_headers()

        while url:
            res = await self._client.get(url, headers=headers)
            if res.status_code == 401:
                await self._get_access_token()
                headers = await self._get_headers()
                res = await self._client.get(url, headers=headers)

            res.raise_for_status()
            data = res.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        return items

    # ------------------------------------------------------------------
    # MS Graph Teams Channel Discovery
    # ------------------------------------------------------------------

    async def get_user_joined_channels(self, user_id: str) -> List[Dict[str, Any]]:
        """[GET] 사용자가 속한 모든 팀의 채널 목록을 (channel_id, team_id, displayName) 형태로 반환"""
        teams_url = f"https://graph.microsoft.com/v1.0/users/{user_id}/joinedTeams"

        try:
            joined_teams = await self._get_all_pages(teams_url)
        except httpx.HTTPStatusError as e:
            '''
            logger.error(f"[Graph API] User {user_id} 소속 팀 조회 실패: {e}")
            '''
            logger.error(f"[Graph API] 소속 팀 조회 실패: {e}")
            return []

        channels: List[Dict[str, Any]] = []
        for team in joined_teams:
            team_id = team["id"]
            channels_url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels"
            try:
                team_channels = await self._get_all_pages(channels_url)
            except httpx.HTTPStatusError as e:
                '''
                logger.error(f"[Graph API] Team {team_id} 채널 조회 실패: {e}")
                '''
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
        """[GET] 채널의 최근 메시지 목록 조회"""
        url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages/delta"

        try:
            messages = await self._get_all_pages(url)
        except httpx.HTTPStatusError as e:
            '''
            logger.error(f"[Graph API] Channel {channel_id} 메시지 조회 실패: {e}")
            '''
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

    # ------------------------------------------------------------------
    # MS Graph Calendar CRUD Operations (EncryptedString 평문 수신 호환)
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
            "subject": title,  # EncryptedString을 통해 복호화된 평문 전달
            "body": {
                "contentType": "HTML",
                "content": description or ""
            },
            "start": {
                "dateTime": start_dt.isoformat() if isinstance(start_dt, datetime) else start_dt,
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_dt.isoformat() if isinstance(end_dt, datetime) else end_dt,
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            },
            #calendarsync 식별자
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
        '''
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 생성 성공 (Event ID: {event_data['id']})")
        '''
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
                "dateTime": start_dt.isoformat() if isinstance(start_dt, datetime) else start_dt,
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_dt.isoformat() if isinstance(end_dt, datetime) else end_dt,
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or ""
            }
        }

        res = await self._request_with_retry("PATCH", url, json_payload=payload)
        res.raise_for_status()
        '''
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 수정 성공 (Event ID: {event_id})")
        '''

    async def delete_user_calendar_event(
        self,
        user_id: str,
        event_id: str
    ):
        """[DELETE] 사용자 캘린더 이벤트 삭제"""
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/calendar/events/{event_id}"

        res = await self._request_with_retry("DELETE", url)

        if res.status_code == 404:
            '''
            logger.warning(f"[Graph API] User {user_id} 삭제 대상 이벤트가 존재하지 않음 (Event ID: {event_id})")
            '''
            logger.warning(f"[Graph API] 삭제 대상 이벤트가 존재하지 않음")
            return

        res.raise_for_status()
        '''
        logger.info(f"[Graph API] User {user_id} 캘린더 일정 삭제 성공 (Event ID: {event_id})")
        '''
        
    
    async def delete_user_synced_events(
        self, 
        user_id: str, 
        property_name: str = "CalendarSyncApp", 
        property_value: str = "CalendarSync_2026"
    ) -> int:
        """
        사용자의 캘린더에서 Extended Property(식별자)가 일치하는 모든 일정을 병렬(gather)로 일괄 삭제합니다.
        """
        property_guid = "fbd4ec16-7f0f-46d2-a9db-f32258a48607"
        prop_id = f"String {{{property_guid}}} Name {property_name}"

        # 1. Extended Property 식별자가 있는 일정만 OData $filter로 조회
        filter_query = f"singleValueExtendedProperties/any(ep: ep/id eq '{prop_id}' and ep/value eq '{property_value}')"
        url = (
            f"https://graph.microsoft.com/v1.0/users/{user_id}/events"
            f"?$select=id,subject"
            f"&$filter={filter_query}"
            f"&$top=50"
        )

        try:
            events_to_delete = await self._get_all_pages(url)
            
            if not events_to_delete:
                logger.info(f"[Graph API] 삭제할 동기화 일정이 없습니다.")
                return 0

            total_count = len(events_to_delete)
            '''
            logger.info(f"[Graph API] User({user_id})의 동기화 일정 {total_count}건 발견. 병렬 삭제 시작...")
            '''

            # 2. asyncio.gather로 삭제 태스크들을 병렬로 생성 및 실행
            # return_exceptions=True를 설정하면 특정 일정 1개가 실패하더라도 나머지는 계속 삭제를 진행합니다.
            delete_tasks = [
                self.delete_user_calendar_event(user_id=user_id, event_id=event.get("id"))
                for event in events_to_delete
                if event.get("id")
            ]

            results = await asyncio.gather(*delete_tasks, return_exceptions=True)

            # 3. 삭제 성공/실패 카운트 집계
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            failed_count = total_count - success_count

            if failed_count > 0:
                '''
                logger.warning(f"[Graph API] User({user_id}) 일정 삭제 완료 (성공: {success_count}건, 실패: {failed_count}건)")
                '''
                logger.warning(f"[Graph API] 일정 삭제 완료 (성공: {success_count}건, 실패: {failed_count}건)")
            else:
                '''
                logger.info(f"[Graph API] User({user_id}) 모든 동기화 일정 삭제 성공 (총 {success_count}건)")
                '''
                logger.info(f"[Graph API] 모든 동기화 일정 삭제 성공 (총 {success_count}건)")

            return success_count

        except Exception as e:
            '''
            logger.error(f"[Graph API] 일정 일괄 삭제 처리 중 에러 (User: {user_id}): {e}", exc_info=True)
            '''
            logger.error(f"[Graph API] 일정 일괄 삭제 처리 중 에러: {e}", exc_info=True)
            return 0
    async def delete_events_by_ids(self, user_id: str, event_ids: List[str]) -> int:
        """Event ID 리스트를 전달받아 병렬 삭제 처리"""
        valid_ids = [eid for eid in event_ids if eid]
        if not valid_ids:
            return 0

        delete_tasks = [
            self.delete_user_calendar_event(user_id=user_id, event_id=eid)
            for eid in valid_ids
        ]

        results = await asyncio.gather(*delete_tasks, return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        return success_count
