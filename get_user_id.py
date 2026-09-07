import httpx
import os
from dotenv import load_dotenv

load_dotenv()

# 1. Azure App 정보 입력
TENANT_ID = os.environ.get("AZURE_TENANT_ID")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")

# 2. 찾고자 하는 본인의 계정 이메일 주소 (예: user@school.ac.kr)
MY_EMAIL = os.environ.get("USER_MAIL")

def get_my_user_id():
    # 1. Access Token 발급 (Client Credentials Flow)
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    token_data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default"
    }
    
    token_res = httpx.post(token_url, data=token_data)
    if token_res.status_code != 200:
        print("❌ 토큰 발급 실패:", token_res.text)
        return
        
    access_token = token_res.json().get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # 2. 이메일로 User ID (Object ID) 조회
    user_url = f"https://graph.microsoft.com/v1.0/users/{MY_EMAIL}"
    user_res = httpx.get(user_url, headers=headers)
    
    if user_res.status_code == 200:
        user_info = user_res.json()
        print("=" * 50)
        print("🎉 내 user_id (Azure AD Object ID)를 찾았습니다!")
        print(f"📌 Name    : {user_info.get('displayName')}")
        print(f"📌 Email   : {user_info.get('mail') or user_info.get('userPrincipalName')}")
        print(f"📌 user_id : {user_info.get('id')}")  # 👈 이 값이 user_id (GUID)
        print("=" * 50)
        return user_info.get("id")
    else:
        print("❌ 사용자 조회 실패:", user_res.text)

if __name__ == "__main__":
    get_my_user_id()