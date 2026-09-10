import json
import base64
import logging
from typing import Dict, Any, List, Tuple
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

async def extract_message_content(
    msg_payload: Dict[str, Any],
    graph_access_token: str,
    http_client: httpx.AsyncClient
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    MS Teams 메시지 Payload에서 텍스트(본문+공지배너)와 이미지(Base64)를 파싱하여
    OpenAI Vision API 규격(user content list)으로 변환합니다.
    """
    raw_content = msg_payload.get("body", {}).get("content", "")
    content_type = msg_payload.get("body", {}).get("contentType", "text")

    image_base64_list: List[str] = []
    clean_body = raw_content

    # 1. HTML 본문 파싱 및 hostedContents 이미지 다운로드
    if content_type.lower() == "html" and raw_content:
        soup = BeautifulSoup(raw_content, "html.parser")
        try:
            if graph_access_token:
                img_tags = soup.find_all("img")
                headers = {"Authorization": f"Bearer {graph_access_token}"}
                
                for img in img_tags:
                    img_url = img.get("src")
                    # Teams 내부에 인라인 저장된 이미지 추출
                    if img_url and "hostedContents" in img_url:
                        try:
                            res = await http_client.get(img_url, headers=headers, follow_redirects=True)
                            if res.status_code == 200:
                                b64_img = base64.b64encode(res.content).decode("utf-8")
                                image_base64_list.append(b64_img)
                        except Exception as e:
                            logger.warning(f"이미지 다운로드 실패 ({img_url}): {e}")

            clean_body = soup.get_text(separator=" ", strip=True)
        finally:
            soup.decompose()

    # 2. 첨부파일(공지 배너 등) 텍스트 추출
    attachment_texts: List[str] = []
    for att in msg_payload.get("attachments", []):
        att_content = att.get("content")
        if not att_content:
            continue

        if isinstance(att_content, str):
            try:
                parsed_att = json.loads(att_content)
                if isinstance(parsed_att, dict) and "title" in parsed_att:
                    attachment_texts.append(f"[공지 배너]: {parsed_att['title']}")
                else:
                    attachment_texts.append(att_content)
            except json.JSONDecodeError:
                attachment_texts.append(att_content)
        elif isinstance(att_content, dict) and "title" in att_content:
            attachment_texts.append(f"[공지 배너]: {att_content['title']}")

    # 3. 본문 + 첨부 텍스트 병합
    full_text_parts = []
    if clean_body:
        full_text_parts.append(clean_body)
    if attachment_texts:
        full_text_parts.append("\n".join(attachment_texts))

    final_text_prompt = "\n\n".join(full_text_parts)

    # 텍스트 및 이미지가 모두 없는 빈 메시지 체크
    if not final_text_prompt.strip() and not image_base64_list:
        return [], False

    # 4. OpenAI Vision API 규격으로 user content 구성
    user_content: List[Dict[str, Any]] = [
        {"type": "text", "text": f"새로 수신된 공지글 내용:\n{final_text_prompt}"}
    ]

    for b64_img in image_base64_list:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64_img}",
                "detail": "auto"
            }
        })

    has_images = len(image_base64_list) > 0
    return user_content, has_images

def build_teams_message_link(team_id: str, channel_id: str, message_id: str) -> str:
    """Teams 특정 메시지로 이동하는 Deep Link URL 생성"""
    # message_id에 포함된 타임스탬프 처리
    msg_id_clean = message_id.split(";")[0] if ";" in message_id else message_id
    
    return (
        f"https://teams.microsoft.com/l/message/{channel_id}/{msg_id_clean}"
        f"?groupId={team_id}&tenantId=&createdContext=channel"
    )