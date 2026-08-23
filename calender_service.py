import copy
import html
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import msal
import requests
import logging
from Channel_Export import get_graph_access_token
from temp_http_client import safe_http_request
import httpx
load_dotenv()

TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

TIME_ZONE = "Korea Standard Time"
logger = logging.getLogger("ScheduleBot")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


# 8일 이상 장기 일정 백엔드 분할 함수
def process_long_term_schedules(notice_data: dict) -> list[dict]:
    """GPT 추출 결과에서 8일 이상 장기 일정을 [#시작], [#종료] 2개 객체로 분할하여 반환합니다."""
    # 만약 입력이 GPT의 전체 wrapper 객체일 경우(schedules 배열 포함) 처리
    raw_schedules = (
        notice_data.get("schedules")
        if isinstance(notice_data, dict) and "schedules" in notice_data
        else (
            [notice_data] if isinstance(notice_data, dict) else notice_data
        )
    )

    processed_list = []

    for item in raw_schedules:
        raw_start = item.get("start_time") or item.get("start_date")
        raw_end = item.get("end_time") or item.get("end_date")
        title = item.get("title", "")

        if not raw_start or not raw_end:
            processed_list.append(item)
            continue

        try:
            # ISO 문자열에서 날짜 파싱 (시간 및 타임존 오프셋 정제)
            clean_start = raw_start.replace("Z", "").split("+")[0].strip()
            clean_end = raw_end.replace("Z", "").split("+")[0].strip()

            start_dt = datetime.fromisoformat(clean_start)
            end_dt = datetime.fromisoformat(clean_end)

            # 날짜 차이 계산 (일 단위)
            day_diff = (end_dt.date() - start_dt.date()).days

            # 8일 이상 지속되는 장기 기간 일정인 경우 2개로 분할
            if day_diff >= 8:
                # 1) 시작일 당일 객체
                start_item = copy.deepcopy(item)
                start_item["title"] = f"{title} [#시작]"
                start_item["start_time"] = (
                    f"{start_dt.strftime('%Y-%m-%d')}T00:00:00"
                )
                start_item["end_time"] = (
                    f"{start_dt.strftime('%Y-%m-%d')}T23:59:00"
                )
                processed_list.append(start_item)

                # 2) 종료일 당일 객체
                end_item = copy.deepcopy(item)
                end_item["title"] = f"{title} [#종료]"
                end_item["start_time"] = (
                    f"{end_dt.strftime('%Y-%m-%d')}T00:00:00"
                )
                end_item["end_time"] = (
                    f"{end_dt.strftime('%Y-%m-%d')}T23:59:00"
                )
                processed_list.append(end_item)

            else:
                # 7일 이하 단기 일정 또는 당일/마감 일정은 그대로 유지
                processed_list.append(item)

        except Exception as e:
            # 날짜 파싱 실패 시 원본 그대로 보존
            processed_list.append(item)

    return processed_list


def make_event_body(notice: dict):
    # 1. 필드 매핑
    title = notice.get("title") or "팀즈 자동 등록 일정"
    summary = notice.get("summary") or notice.get("description") or ""
    source = notice.get("source") or ""
    web_url = notice.get("web_url") or ""

    # 2. Location 방어 처리
    raw_location = notice.get("location")
    if isinstance(raw_location, str):
        location_name = raw_location
    elif isinstance(raw_location, dict):
        location_name = (
            raw_location.get("raw") or raw_location.get("name") or ""
        )
    else:
        location_name = ""

    # 3. HTML 본문 구성
    content = f"<h2>{html.escape(title)}</h2>"
    if summary:
        content += f"<p>{html.escape(summary)}</p>"
    if location_name:
        content += f"<p><b>장소:</b> {html.escape(location_name)}</p>"
    if source:
        content += f"<p><b>출처:</b> {html.escape(source)}</p>"

    # 4. 날짜 및 시간 처리
    raw_start = notice.get("start_time") or notice.get("start_date")
    raw_end = notice.get("end_time") or notice.get("end_date")

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    def parse_datetime_str(dt_str: str, is_end: bool = False) -> str:
        if not dt_str or not isinstance(dt_str, str):
            return ""

        clean_str = dt_str.replace("Z", "").split("+")[0].strip()

        if len(clean_str) == 10 and clean_str.count("-") == 2:
            return (
                f"{clean_str}T23:59:00" if is_end else f"{clean_str}T00:00:00"
            )

        if "T" in clean_str:
            date_part, time_part = clean_str.split("T")
            time_part = time_part[:8]

            if is_end and time_part.startswith("23:59"):
                time_part = "23:59:00"

            return f"{date_part}T{time_part}"

        return clean_str

    start_datetime = (
        parse_datetime_str(raw_start, is_end=False) or f"{today_str}T00:00:00"
    )
    end_datetime = (
        parse_datetime_str(raw_end, is_end=True)
        or f"{start_datetime.split('T')[0]}T23:59:00"
    )

    if end_datetime < start_datetime:
        end_datetime = start_datetime

    # 5. web url 확인
    if web_url:
        '''
        logger.info(f"[Calendar Service] web_url 정상 수신: {web_url}")
        '''
        # 본문 하단에 원본 메시지 링크 HTML 생성
        content += f'<br><a href="{web_url}">일정 펼치고 여길 클릭해서 게시물로 이동</a><br>'
        
    # 6. MS Graph API Payload 생성
    event = {
        "subject": title,
        "body": {
            "contentType": "HTML",
            "content": content,
        },
        "location": {
            "displayName": location_name,
        },
        "isAllDay": False,
        "start": {
            "dateTime": start_datetime,
            "timeZone": TIME_ZONE,
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": TIME_ZONE,
        },
        #calendarsync 식별자
        "singleValueExtendedProperties": [
            {
                "id": "String {fbd4ec16-7f0f-46d2-a9db-f32258a48607} Name CalendarSyncApp",
                "value": "CalendarSync_2026"
            }
        ]
    }

    return event

def is_duplicate_event(new_event: dict, existing_events: list) -> bool:
    """
    새로 등록하려는 일정(new_event)이 기존 일정 리스트(existing_events)에 이미 존재하는지 검증합니다.
    """
    # 1. 새 일정 데이터 정제
    new_title = new_event.get("subject", "").replace(" ", "").lower()
    new_start_info = new_event.get("start", {})
    new_start_str = new_start_info.get("dateTime") if isinstance(new_start_info, dict) else new_start_info
    
    if not new_start_str:
        return False
        
    new_start_dt = datetime.fromisoformat(new_start_str.replace("Z", "+00:00"))

    for exist in existing_events:
        # 기존 일정 데이터 정제
        exist_title = exist.get("subject", "").replace(" ", "").lower()
        exist_start_info = exist.get("start", {}).get("dateTime")
        
        if not exist_start_info:
            continue
            
        exist_start_dt = datetime.fromisoformat(exist_start_info.replace("Z", "+00:00"))

        # 조건 1: 제목 검사 (완전 일치 또는 한쪽이 다른 쪽에 포함)
        title_matched = (new_title in exist_title) or (exist_title in new_title)

        # 조건 2: 시작 시간 차이가 30분 이내인지 검사
        time_diff = abs((new_start_dt - exist_start_dt).total_seconds()) / 60.0  # 분 단위
        time_matched = time_diff <= 30

        # 제목이 유사하고 시간차이가 30분 이내라면 중복으로 판단!
        if title_matched and time_matched:
            return True

    return False

async def get_existing_events_for_day(user_email: str, headers: dict, target_datetime_str: str, http_client: httpx.AsyncClient) -> list:
    """
    등록하려는 날짜 당일의 기존 일정들을 API로 미리 끌어옵니다.
    """
    try:
        date_part = target_datetime_str.split("T")[0]
        start_of_day = f"{date_part}T00:00:00Z"
        end_of_day = f"{date_part}T23:59:59Z"

        url = (
            f"https://graph.microsoft.com/v1.0/users/{user_email}/calendarView"
            f"?startDateTime={start_of_day}&endDateTime={end_of_day}"
        )
        res = await safe_http_request(http_client, "GET", url, headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json().get("value", [])
    except Exception as e:
        logger.error(f"[calendarView 조회 실패] {e}")
    return []

async def add_notice_to_calendar(user_email: str, notice_data: dict, http_client: httpx.AsyncClient) -> bool:
    """
    GPT AI에서 추출한 단일 notice 또는 schedules 포함 dict를 받아 장기 일정 분할 후 지정 유저의 MS 캘린더에 등록합니다.
    """
    access_token = get_graph_access_token()
    if access_token is None:
        '''
        logger.error("[Calendar] access_token 없음으로 등록 취소")
        '''
        return False

    # 1. 후처리 함수를 거쳐 8일 이상 일정을 분할한 일정 리스트 획득
    target_notices = process_long_term_schedules(notice_data)

    success_all = True
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # 2. 각 일정을 캘린더에 등록
    for notice in target_notices:
        try:
            event_body = make_event_body(notice)
        except Exception as e:
            '''
            logger.error(f"[make_event_body 에러] {e}")
            '''
            success_all = False
            continue
        # 1. 해당 날짜의 기존 일정 미리 끌어오기 (중복 검사용)
        start_datetime = event_body.get("start", {}).get("dateTime", "")
        existing_events = await get_existing_events_for_day(user_email, headers, start_datetime, http_client=http_client)

        # 2. 중복 검사 실행
        if is_duplicate_event(event_body, existing_events):
            logger.info(f"[Calendar 스킵 - 중복 일정 감지] {event_body.get('subject')}")
            continue  # 중복이면 생성 요청을 보내지 않고 다음으로 건너뜁니다.
        
        url = f"https://graph.microsoft.com/v1.0/users/{user_email}/events"

        response = await safe_http_request(http_client, "POST", url, headers=headers, json=event_body)

        if response.status_code in (200, 201):
            '''
            logger.info(f"[Calendar API 성공]")
            '''
        else:
            logger.error(
                f"[Calendar API 실패 - HTTP {response.status_code}]"
                f" {response.text}"
            )
            success_all = False

    return success_all



if __name__ == "__main__":
    user_email = os.environ.get("USER_MAIL")
    # 테스트 케이스: 8일 이상 지속되는 장기 공지
    long_notice = {
        "title": "[테스트] 장기 프로젝트",
        "start_time": "2026-07-11T00:00:00Z",
        "end_time": "2026-08-15T23:59:00Z",
        "summary": "장기 프로젝트가 진행됩니다.",
        "location": None,
        "is_for_all_grades": False,
        "target_grades": [1],
        "confidence": 1.0,
    }
    # 실행 시 [#시작], [#종료] 2개의 객체로 자동 split되어 MS 캘린더에 등록됨
    add_notice_to_calendar(user_email, long_notice)