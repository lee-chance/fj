"""
FinancialJuice SignalR WebSocket 클라이언트 (Python 스크립트)

주요 기능
- /signalr/negotiate 로 connectionToken 획득
- WebSocket 연결 후 /signalr/start 호출
- 수신 프레임에서 JSON 조각 추출/출력

사용 예시
  python financial_juice_client.py \
    --ftoken "$FINANCIALJUICE_FTOKEN" \
    --origin https://www.financialjuice.com \
    --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" \
    --cookies '{"ASP.NET_SessionId":"..."}'

환경변수
- FINANCIALJUICE_FTOKEN: --ftoken 미지정 시 사용
- FINANCIALJUICE_COOKIES_JSON: --cookies 미지정 시 사용 (JSON 문자열)

필수 패키지
- requests
- websocket-client
"""

import argparse
import json
import os
import urllib.parse
from typing import Optional, Dict

from dotenv import load_dotenv
from ai_translator import OpenRouterTranslator as _ORT, DEFAULT_MODEL as _OR_DEFAULT_MODEL
from fj_client import (
    DEFAULT_CONNECTION_DATA_JSON as FJ_DEFAULT_CONNECTION_DATA_JSON,
    DEFAULT_CALLBACK as FJ_DEFAULT_CALLBACK,
    SignalRClient as FJSignalRClient,
    NewsHubTranslatorHandler as FJNewsHandler,
)

# -----------------------------
# 상수/기본값
# -----------------------------
DEFAULT_CONNECTION_DATA_JSON = FJ_DEFAULT_CONNECTION_DATA_JSON
DEFAULT_CALLBACK = FJ_DEFAULT_CALLBACK


def _build_headers(origin: str, user_agent: str) -> Dict[str, str]:
    return {
        "Origin": origin,
        "User-Agent": user_agent,
    }


def _load_cookies(cli_cookies: Optional[str]) -> Dict[str, str]:
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


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="FinancialJuice SignalR 클라이언트")
    parser.add_argument(
        "--ftoken",
        type=str,
        default=os.getenv("FINANCIALJUICE_FTOKEN"),
        help="브라우저에서 복사한 ftoken (또는 환경변수 FINANCIALJUICE_FTOKEN 사용)",
    )
    parser.add_argument(
        "--connection-data",
        type=str,
        default=DEFAULT_CONNECTION_DATA_JSON,
        help='connectionData JSON 문자열 (기본: [{"name":"newshub"}])',
    )
    parser.add_argument(
        "--callback",
        type=str,
        default=DEFAULT_CALLBACK,
        help="JSONP 콜백 이름",
    )
    parser.add_argument(
        "--origin",
        type=str,
        default="https://www.financialjuice.com",
        help="요청 Origin 헤더",
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        help="요청 User-Agent 헤더",
    )
    parser.add_argument(
        "--cookies",
        type=str,
        default=None,
        help="요청 쿠키(JSON 문자열). 미지정 시 FINANCIALJUICE_COOKIES_JSON 사용",
    )
    # 번역 옵션
    parser.add_argument(
        "--translate",
        action="store_true",
        help="웹소켓 수신 뉴스 자동 번역 출력",
    )
    parser.add_argument(
        "--target-lang",
        type=str,
        default="ko",
        help="번역 타겟 언어 코드 (기본: ko)",
    )
    parser.add_argument(
        "--or-model",
        type=str,
        default=_OR_DEFAULT_MODEL,
        help="OpenRouter 모델 ID (ai_translator.DEFAULT_MODEL 기본값)",
    )
    parser.add_argument(
        "--or-api-key",
        type=str,
        default=os.getenv("OPENROUTER_API_KEY"),
        help="OpenRouter API Key (또는 환경변수 OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--or-http-referer",
        type=str,
        default=None,
        help="OpenRouter HTTP-Referer 헤더",
    )
    parser.add_argument(
        "--or-x-title",
        type=str,
        default=None,
        help="OpenRouter X-Title 헤더",
    )
    # Slack 옵션
    parser.add_argument(
        "--slack-webhook-url",
        type=str,
        default=os.getenv("SLACK_WEBHOOK_URL"),
        help="Slack Incoming Webhook URL (환경변수 SLACK_WEBHOOK_URL 기본)",
    )

    args = parser.parse_args()

    if not args.ftoken:
        raise SystemExit(
            "ftoken이 없습니다. --ftoken 또는 환경변수 FINANCIALJUICE_FTOKEN 를 지정하세요."
        )

    connection_data_encoded = urllib.parse.quote(args.connection_data, safe="")
    headers = _build_headers(origin=args.origin, user_agent=args.user_agent)
    cookies = _load_cookies(args.cookies)

    translator: Optional[_ORT] = None
    if args.translate:
        if not args.or_api_key:
            raise SystemExit("번역이 활성화되었으나 OpenRouter API 키가 없습니다. --or-api-key 또는 OPENROUTER_API_KEY 지정")
        translator = _ORT(
            api_key=args.or_api_key,
            model=args.or_model,
            http_referer=args.or_http_referer,
            x_title=args.or_x_title,
        )

    handler = None
    if args.translate:
        handler = FJNewsHandler(
            translator=translator,
            target_lang=args.target_lang,
            slack_webhook_url=args.slack_webhook_url,
        )

    client = FJSignalRClient(
        ftoken=args.ftoken,
        connection_data_encoded=connection_data_encoded,
        callback=args.callback,
        headers=headers,
        cookies=cookies,
        handler=handler,
        slack_webhook_url=args.slack_webhook_url,
    )

    try:
        client.start()
    except KeyboardInterrupt:
        print("Interrupted by user, stopping.")
        client.stop()


if __name__ == "__main__":
    main()