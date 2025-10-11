import json
import os
import re
from typing import Any, Dict, List, Optional


# JSON 추출용 정규식 (객체/배열 조각을 모두 포착)
JSON_RE = re.compile(r"(\{.*?\}|\[.*?\])", re.DOTALL)


def extract_json_from_jsonp(text: str) -> Optional[str]:
    """JSONP 텍스트에서 JSON 본문을 추출. plain JSON이면 그대로 반환."""
    text = text.strip()
    if "(" in text and text.endswith(")"):
        try:
            body = text[text.index("(") + 1 : text.rindex(")")]
            return body
        except Exception:
            pass
    m = JSON_RE.search(text)
    if m:
        return m.group(1)
    return None


def parse_signalr_frame(raw: str) -> List[Any]:
    """SignalR 프레임 문자열에서 모든 JSON 객체/배열 조각을 찾아 파싱."""
    items = JSON_RE.findall(raw)
    parsed: List[Any] = []
    for it in items:
        try:
            parsed.append(json.loads(it))
        except Exception:
            parsed.append({"raw": it})
    return parsed


def build_headers(origin: str, user_agent: str) -> Dict[str, str]:
    return {
        "Origin": origin,
        "User-Agent": user_agent,
    }


def load_cookies(cli_cookies: Optional[str]) -> Dict[str, str]:
    if cli_cookies:
        try:
            return json.loads(cli_cookies)
        except Exception:
            raise SystemExit("--cookies 값은 JSON 문자열이어야 합니다.")
    env_json = os.getenv("FINANCIALJUICE_COOKIES_JSON")
    if env_json:
        try:
            return json.loads(env_json)
        except Exception:
            raise SystemExit("FINANCIALJUICE_COOKIES_JSON 환경변수는 JSON 문자열이어야 합니다.")
    return {}


