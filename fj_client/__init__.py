"""
fj_client: FinancialJuice SignalR 클라이언트 패키지

모듈 구성
- constants: 상수/템플릿/정규식
- utils: 공용 유틸리티 (JSONP 추출, 프레임 파싱, 헤더/쿠키 로딩)
- handler: 수신 프레임 처리기(NewsHub 번역 등)
- client: SignalR 클라이언트 본체
"""

from .constants import (
    BASE_WS_HOST,
    CONNECT_WS_TEMPLATE,
    START_URL_TEMPLATE,
    DEFAULT_CONNECTION_DATA_JSON,
    DEFAULT_CALLBACK,
)
from .client import SignalRClient
from .handler import NewsHubTranslatorHandler, NewsHubFirestoreHandler
from . import utils as utils
from .slack import send_slack_message

__all__ = [
    "BASE_WS_HOST",
    "CONNECT_WS_TEMPLATE",
    "START_URL_TEMPLATE",
    "DEFAULT_CONNECTION_DATA_JSON",
    "DEFAULT_CALLBACK",
    "SignalRClient",
    "NewsHubTranslatorHandler",
    "NewsHubFirestoreHandler",
    "utils",
    "send_slack_message",
]


