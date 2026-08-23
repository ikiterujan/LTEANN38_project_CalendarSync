# channel_export.py
import os
import msal
import httpx
from dotenv import load_dotenv
import logging
from datetime import datetime
from temp_http_client import safe_http_request

load_dotenv()

TENANT_ID = os.getenv('TENANT_ID')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
'''
logger = logging.getLogger("ScheduleBot")
'''

async def get_graph_access_token() -> str | None:
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        return None

    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    
    # msal의 동기 네트워크 요청을 별도 스레드로 격리하여 이벤트 루프 마비 방지
    import asyncio
    result = await asyncio.to_thread(
        app.acquire_token_for_client,
        scopes=["https://graph.microsoft.com/.default"]
    )

    token = result.get("access_token")
    if not token:
        '''
        logger.error(f"[토큰 발급 실패] {result.get('error_description')}", exc_info=True)
        '''
    return token


async def channel_export(TEAM_ID: str, CHANNEL_ID: str, last_sync_time, client: httpx.AsyncClient, access_token: str = None):
    """채널 메시지를 가져오되, 마지막 동기화된 시점을 만나면 즉시 중단합니다."""
    if not access_token:
        access_token = await get_graph_access_token()
        
    if not access_token:
        return []
    
    new_messages = []
    
    # 1. $filter 제거하고 기본 최신순($top=20 또는 50)으로 요청
    url = f"https://graph.microsoft.com/v1.0/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages?$top=50"
    
    stop = False
    
    while url and not stop:
        try:
            response = await safe_http_request(
                client,
                "GET",
                url, 
                headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
            data = response.json()
            messages = data.get("value", [])
            
            for msg in messages:
                # 메시지 작성 시간 파싱 (Graph API 시간 형식 대응)
                created_dt = datetime.fromisoformat(msg['createdDateTime'].replace("Z", "+00:00"))
                
                # 2. 핵심: 이미 동기화된 시간보다 과거 메시지를 만나면 탐색 즉시 중단 (Early Exit)
                if created_dt <= last_sync_time:
                    stop = True
                    break
                    
                new_messages.append(msg)
                
            url = data.get("@odata.nextLink")
        except Exception as e:
            '''
            logger.error(f"[Graph API Error] 메시지 조회 실패 ({response.status_code}): {response.text}")
            '''
            break
    return new_messages