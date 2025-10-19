import argparse
import os
import urllib.parse
from typing import Optional

from dotenv import load_dotenv

from .logger import setup_logging, get_logger
from . import (
    DEFAULT_CONNECTION_DATA_JSON as FJ_DEFAULT_CONNECTION_DATA_JSON,
    DEFAULT_CALLBACK as FJ_DEFAULT_CALLBACK,
    SignalRClient as FJSignalRClient,
    NewsHubFirestoreHandler as FJFirestoreHandler,
)
from .utils import build_headers, load_cookies


DEFAULT_CONNECTION_DATA_JSON = FJ_DEFAULT_CONNECTION_DATA_JSON
DEFAULT_CALLBACK = FJ_DEFAULT_CALLBACK


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="FinancialJuice -> Firestore 적재 클라이언트")
    parser.add_argument("--ftoken", type=str, default=os.getenv("FINANCIALJUICE_FTOKEN"))
    parser.add_argument("--connection-data", type=str, default=DEFAULT_CONNECTION_DATA_JSON)
    parser.add_argument("--callback", type=str, default=DEFAULT_CALLBACK)
    parser.add_argument("--origin", type=str, default="https://www.financialjuice.com")
    parser.add_argument("--user-agent", type=str, default="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    parser.add_argument("--cookies", type=str, default=None, help="JSON 문자열 or env FINANCIALJUICE_COOKIES_JSON")

    # Firestore
    parser.add_argument(
        "--firebase-credentials",
        type=str,
        default="secret/firebase-credentials.json",
        help="Firebase 서비스 계정 JSON 경로(기본: secret/firebase-credentials.json)",
    )
    parser.add_argument("--dev", action="store_true", help="개발 모드: news-dev 컬렉션 사용(기본: news)")

    # Logging
    # parser.add_argument("--log-level", type=str, default=os.getenv("LOG_LEVEL", "INFO"))
    parser.add_argument("--log-level", type=str, default="DEBUG")
    parser.add_argument("--json-logs", action="store_true")
    parser.add_argument("--log-file", type=str, default=os.getenv("LOG_FILE"))
    parser.add_argument("--slack-webhook-url", type=str, default=os.getenv("SLACK_WEBHOOK_URL"))
    parser.add_argument("--slack-log-level", type=str, default=os.getenv("SLACK_LOG_LEVEL", "ERROR"))

    args = parser.parse_args()
    if not args.ftoken:
        raise SystemExit("ftoken이 없습니다. --ftoken 또는 환경변수 FINANCIALJUICE_FTOKEN 를 지정하세요.")

    connection_data_encoded = urllib.parse.quote(args.connection_data, safe="")
    headers = build_headers(origin=args.origin, user_agent=args.user_agent)
    cookies = load_cookies(args.cookies)

    setup_logging(
        level=args.log_level,
        json_logs=args.json_logs,
        log_file=args.log_file,
        slack_webhook_url=args.slack_webhook_url,
        slack_min_level=args.slack_log_level,
    )
    log = get_logger("cli.ingest")

    collection = "news-dev" if args.dev else "news"
    handler = FJFirestoreHandler(collection=collection, credentials_path=args.firebase_credentials)
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
        log.info("Interrupted by user, stopping.")
        client.stop()


if __name__ == "__main__":
    main()


