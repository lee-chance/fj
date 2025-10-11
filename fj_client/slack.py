import os
from typing import Any, Dict, Optional

import requests


def send_slack_message(
    text: str,
    *,
    webhook_url: Optional[str] = None,
    username: Optional[str] = None,
    icon_emoji: Optional[str] = None,
    timeout_seconds: float = 5.0,
    retries: int = 0,
) -> Dict[str, Any]:
    """
    Slack Incoming Webhook 으로 간단한 텍스트 메시지를 전송합니다.

    Arguments:
        text: 전송할 텍스트
        webhook_url: 지정하지 않으면 환경변수 `SLACK_WEBHOOK_URL` 사용
        username: 메시지 발신자 이름(옵션)
        icon_emoji: 아이콘 이모지(옵션, 예: ":robot_face:")
        timeout_seconds: 요청 타임아웃(초)
        retries: 실패 시 재시도 횟수

    Returns:
        {"ok": bool, "status": int, "error": Optional[str]}
    """
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return {"ok": False, "status": 0, "error": "SLACK_WEBHOOK_URL not set"}

    payload: Dict[str, Any] = {"text": text}
    if username:
        payload["username"] = username
    if icon_emoji:
        payload["icon_emoji"] = icon_emoji

    attempt = 0
    last_error: Optional[str] = None
    while attempt <= max(0, retries):
        try:
            resp = requests.post(url, json=payload, timeout=timeout_seconds)
            if 200 <= resp.status_code < 300:
                return {"ok": True, "status": resp.status_code, "error": None}
            last_error = f"status={resp.status_code} body={resp.text[:300]}"
        except Exception as e:  # pragma: no cover - network/runtime error path
            last_error = str(e)

        attempt += 1

    return {"ok": False, "status": 0, "error": last_error}
