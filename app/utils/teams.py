def build_teams_message_link(team_id: str, channel_id: str, message_id: str) -> str:
    """Teams 특정 메시지로 이동하는 Deep Link URL 생성"""
    # message_id에 포함된 타임스탬프 처리
    msg_id_clean = message_id.split(";")[0] if ";" in message_id else message_id
    
    return (
        f"https://teams.microsoft.com/l/message/{channel_id}/{msg_id_clean}"
        f"?groupId={team_id}&tenantId=&createdContext=channel"
    )