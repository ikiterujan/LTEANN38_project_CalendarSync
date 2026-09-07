import os
import sys
import asyncio
import httpx
import msal

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv()
TENANT_ID = os.environ.get("AZURE_TENANT_ID")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")

from database import SessionLocal
import models

def get_graph_access_token() -> str | None:
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        return None

    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    
    # msal의 동기 네트워크 요청을 별도 스레드로 격리하여 이벤트 루프 마비 방지
    import asyncio
    
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

    token = result.get("access_token")
    if not token:
        '''
        logger.error(f"[토큰 발급 실패] {result.get('error_description')}", exc_info=True)
        '''
    return token

# 💡 1. Semaphore를 받아 동시 실행 수를 제한하는 삭제 함수
async def delete_single_event(
    client: httpx.AsyncClient, 
    user_id: str, 
    event_id: str, 
    subject: str, 
    headers: dict,
    semaphore: asyncio.Semaphore  # 추가
):
    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/events/{event_id}"
    
    async with semaphore:  # 동시에 설정한 개수만큼만 이 블록에 진입 가능
        try:
            res = await client.delete(url, headers=headers, timeout=10.0)
            
            if res.status_code == 204:
                print(f"✅ [삭제 완료] {subject}")
            elif res.status_code == 429:
                # 429가 발생할 경우 Retry-After 헤더 시간만큼 대기 후 1회 재시도 (안전장치)
                retry_after = int(res.headers.get("Retry-After", 2))
                print(f"⚠️ [429 Throttled] {retry_after}초 후 재시도... : {subject}")
                await asyncio.sleep(retry_after)
                
                # 재시도
                retry_res = await client.delete(url, headers=headers, timeout=10.0)
                if retry_res.status_code == 204:
                    print(f"✅ [재시도 성공] {subject}")
                else:
                    print(f"❌ [재시도 실패 {retry_res.status_code}] {subject}")
            else:
                print(f"❌ [삭제 실패 {res.status_code}] {subject}")
                
        except Exception as e:
            print(f"💥 [에러 발생] {subject}: {e}")
        
        # MS Graph API에 무리가 가지 않도록 아주 짧은 간격 두기
        await asyncio.sleep(0.5)


# 2. 메인 실행 함수
async def cleanup_app_events_fast(user_id: str, access_token: str, dry_run: bool = True):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    EXCLUDE_SUBJECTS = {
        "[발표] 금속의 광택",
        "[2026학년도 1학년 과제연구기초] 콜로퀴엄 참관 소감문 작성",
        "[2026학년도 1학년 과제연구기초]1회차 활동 설문 작성",
        "[서울국제고 연합] 음악나눔연주회 공연자/사회자 모집",
        "[필수] 화학1 마무리"
        # dry_run 때 보셨던 우리 앱 게 아닌 정확한 제목들을 여기에 넣어주세요!
    }

    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/events?$top=100&$select=id,subject"
    all_events = []

    # 💡 커넥션 수 제한 설정
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=5)
    
    async with httpx.AsyncClient(limits=limits, timeout=15.0) as client:
        print("🔄 전체 일정 목록 가져오는 중...")
        
        while url:
            res = await client.get(url, headers=headers)
            data = res.json()
            events = data.get('value', [])
            all_events.extend(events)
            url = data.get('@odata.nextLink')

        target_events = []
        excluded_events = []

        for e in all_events:
            subject = e.get('subject', '').strip()
            #print(subject)
            if subject.startswith("["):
                if subject in EXCLUDE_SUBJECTS:
                    excluded_events.append(e)
                else:
                    target_events.append(e)

        print(f"📌 '[' 시작 일정: {len(target_events) + len(excluded_events)}개 | 🟢 삭제 대상: {len(target_events)}개 | 🛡️ 보존(제외): {len(excluded_events)}개\n")

        if dry_run:
            print("⚠️ [Dry-Run 모드] 실제 삭제되지 않습니다.")
            print("🟢 [최종 삭제될 대상 목록]")
            for e in target_events:
                print(f"   - {e.get('subject')}")
            return

        # 💡 [핵심] 동시에 진행할 작업 수를 3~5개로 제한하는 Semaphore 생성
        # MS Graph는 보통 동시 요청 3~5개 이하가 안전합니다.
        semaphore = asyncio.Semaphore(5)

        # 🔥 Semaphore를 전달하며 병렬 삭제 수행
        tasks = [
            delete_single_event(client, user_id, e['id'], e.get('subject', ''), headers, semaphore)
            for e in target_events
        ]
        
        await asyncio.gather(*tasks)
        print("\n🎉 모든 일정 청소가 완료되었습니다!")


if __name__ == "__main__":
    USER_ID = os.environ.get("USER_ID")
    access_token = get_graph_access_token()
 
    asyncio.run(cleanup_app_events_fast(USER_ID, access_token, dry_run=False))

# if __name__=="__main__":
#     access_token = get_graph_access_token()
#     db = SessionLocal()
#     all_user = db.query(models.User).all() if hasattr(models,"User") else []
#     for user in all_user:
#         asyncio.run(cleanup_app_events_fast(user.user_id, access_token, dry_run=False))