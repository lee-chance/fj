import json
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional
import random

import requests
from requests import Session
from requests.adapters import HTTPAdapter
try:
    # requests>=2 uses urllib3 Retry
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover - very old environments
    Retry = None  # type: ignore
import websocket
from .logger import get_logger

from .constants import (
    BASE_WS_HOST,
    CONNECT_WS_TEMPLATE,
)
from .utils import extract_json_from_jsonp
from .slack import send_slack_message


def do_negotiate(
    ftoken: Optional[str],
    connection_data_encoded: str,
    callback: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    ts = str(int(time.time() * 1000))
    base_url = BASE_WS_HOST + "/signalr/negotiate"
    # connection_data_encoded는 WS용으로 인코딩됨. HTTP params는 raw로 주고 인코딩을 위임한다.
    connection_data_raw = urllib.parse.unquote(connection_data_encoded)
    params = {
        "clientProtocol": "2.1",
        "connectionData": connection_data_raw,
        "callback": callback,
        "_": ts,
    }
    if ftoken:
        params["ftoken"] = ftoken

    sess = session or requests
    get_logger("client").debug("[negotiate] GET %s", base_url)
    resp = sess.get(base_url, params=params, headers=headers, cookies=cookies, timeout=10)  # type: ignore[attr-defined]
    if resp.status_code != 200:
        raise RuntimeError(
            f"negotiate failed: {resp.status_code} {resp.text[:300]}"
        )
    body = extract_json_from_jsonp(resp.text)
    if not body:
        raise RuntimeError("negotiate: cannot extract JSON from response")
    return json.loads(body)


class SignalRClient:
    def __init__(
        self,
        ftoken: Optional[str],
        connection_data_encoded: str,
        callback: str,
        headers: Dict[str, str],
        cookies: Dict[str, str],
        *,
        handler: Optional[Any] = None,
        slack_webhook_url: Optional[str] = None,
    ) -> None:
        self.ftoken = ftoken
        self.connection_data_encoded = connection_data_encoded
        self.callback = callback
        self.headers = headers
        self.cookies = cookies
        self.handler = handler
        self.slack_webhook_url = slack_webhook_url

        self.ws: Optional[websocket.WebSocketApp] = None
        self.connected = False
        self._stop = False
        self.lock = threading.Lock()
        self.last_negotiate: Optional[Dict[str, Any]] = None
        self.connection_token: Optional[str] = None
        # retry 제어: on_error 발생 시 1회만 자동 재시도
        self.retry_attempted = False
        # start 재진입 방지용 뮤텍스 (동시에 하나의 start만 실행)
        self._start_mutex = threading.Lock()
        self.log = get_logger("client")
        self.session: Optional[Session] = self._create_session()
        # 연결 상태 동기화 및 재연결 설정
        self.connected_evt = threading.Event()
        self.max_retries = 2
        self.backoff_base = 1.0  # seconds
        self.max_backoff = 30.0  # seconds
        self._retries = 0
        self._ws_thread: Optional[threading.Thread] = None
        self._reconnect_lock = threading.Lock()
        self.open_wait_timeout = 5.0

    def _create_session(self) -> Optional[Session]:
        try:
            s = requests.Session()
            if Retry is not None:
                retry = Retry(
                    total=3,
                    backoff_factor=0.5,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=("GET",),
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retry)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
            return s
        except Exception:
            return None

    def _notify_slack(self, text: str, icon_emoji: Optional[str] = None) -> None:
        if not self.slack_webhook_url:
            return
        try:
            send_slack_message(
                text=text,
                webhook_url=self.slack_webhook_url,
                username="FJ Bot",
                icon_emoji=icon_emoji,
            )
        except Exception:
            # 슬랙 전송 실패는 흐름에 영향 주지 않음
            pass

    def open_ws(self, connection_token: str) -> None:
        ws_url = CONNECT_WS_TEMPLATE.format(
            ftoken=urllib.parse.quote(self.ftoken or "", safe=""),
            connectionToken=urllib.parse.quote(connection_token, safe=""),
            connectionData=self.connection_data_encoded,
        )
        header_list = [f"{k}: {v}" for k, v in self.headers.items()]
        self.log.info("[ws] connect to %s", ws_url)
        self.connected_evt.clear()
        self.ws = websocket.WebSocketApp(
            ws_url,
            header=header_list,
            on_open=self.on_open,
            on_message=self.on_message,
            # on_ping=self.on_ping,
            # on_pong=self.on_pong,
            on_close=self.on_close,
            on_error=self.on_error,
        )
        t = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=60, ping_timeout=50)
        )
        t.daemon = True
        t.start()
        self._ws_thread = t

    def on_open(self, ws) -> None:  # type: ignore[no-untyped-def]
        self.log.info("[ws] opened")
        self.connected_evt.set()
        with self.lock:
            self.connected = True
        # 정상 연결 시 재시도 카운터/플래그 초기화
        self.retry_attempted = False
        self._retries = 0
        # Slack: 연결 성공
        self._notify_slack("[FinancialJuice] WebSocket 연결 성공", ":satellite:")

    def on_message(self, ws, message: str) -> None:  # type: ignore[no-untyped-def]
        item = json.loads(message)
        try:
            preview = json.dumps(item, ensure_ascii=False)[:1000]
        except Exception:
            preview = str(item)[:1000]
        self.log.debug("[ws message] %s", preview)
        if self.handler and isinstance(item, dict):
            try:
                self.handler.handle(item)
            except Exception as e:
                self.log.exception("[handler error] %s", e)

    def on_error(self, ws, error) -> None:  # type: ignore[no-untyped-def]
        self.log.error("[ws error] %s", error)
        # 에러 발생 시 정책 기반 재연결 스케줄링
        self._schedule_reconnect(reason=f"error: {error}")

    def on_close(self, ws, close_status_code, close_msg) -> None:  # type: ignore[no-untyped-def]
        self.log.info("[ws close] %s %s", close_status_code, close_msg)
        self.connected_evt.clear()
        with self.lock:
            self.connected = False
        # 정상/비정상 종료 모두 정책에 따라 재연결 시도
        self._schedule_reconnect(reason=f"close {close_status_code} {close_msg}")
        # Slack: 연결 끊김
        self._notify_slack(
            f"[FinancialJuice] WebSocket 연결 종료 code={close_status_code} msg={close_msg}",
            ":warning:",
        )

    def _schedule_reconnect(self, reason: str) -> None:
        if self._stop:
            return
        # 중복 스케줄 방지
        if not self._reconnect_lock.acquire(blocking=False):
            return

        # 최대 재시도 횟수 확인
        if self._retries >= self.max_retries:
            self.log.error("[ws reconnect] give up after %d retries (%s)", self._retries, reason)
            # Slack: 재시도 포기
            self._notify_slack(
                f"[FinancialJuice] WebSocket 재연결 포기 (retries={self._retries}) reason={reason}",
                ":x:",
            )
            try:
                self._reconnect_lock.release()
            except Exception:
                pass
            return

        # 지수 백오프 + 소폭 지터
        base_delay = min(self.max_backoff, self.backoff_base * (2 ** self._retries))
        jitter = random.uniform(0, 0.25 * base_delay)
        delay = min(self.max_backoff, base_delay + jitter)
        self._retries += 1
        self.log.warning("[ws reconnect] in %.1fs (attempt %d/%d): %s", delay, self._retries, self.max_retries, reason)
        # Slack: 재연결 스케줄링
        self._notify_slack(
            f"[FinancialJuice] WebSocket 재연결 시도 예정 in {delay:.1f}s (attempt {self._retries}/{self.max_retries}) reason={reason}",
            ":arrows_counterclockwise:",
        )

        def _do() -> None:
            try:
                # 이전 연결 정리
                try:
                    if self.ws:
                        self.ws.close()
                except Exception:
                    pass
                time.sleep(delay)
                if not self._stop:
                    before = time.time()
                    self.start()
                    # 재연결 성공 판단: start() 이후 연결 상태 체크
                    success = self.connected_evt.is_set()
                    if success:
                        self._notify_slack(
                            f"[FinancialJuice] WebSocket 재연결 성공 (after {time.time()-before:.1f}s)",
                            ":white_check_mark:",
                        )
                    else:
                        self._notify_slack(
                            f"[FinancialJuice] WebSocket 재연결 실패 (attempt {self._retries}/{self.max_retries})",
                            ":x:",
                        )
            finally:
                try:
                    self._reconnect_lock.release()
                except Exception:
                    pass

        t = threading.Thread(target=_do)
        t.daemon = True
        t.start()

    def start(self) -> None:
        # 동시에 하나의 start만 실행되도록 보장
        with self._start_mutex:
            try:
                # 시작 시 재시도 가능 상태로 초기화
                self.retry_attempted = False
                self.connected_evt.clear()
                n = do_negotiate(
                    ftoken=self.ftoken,
                    connection_data_encoded=self.connection_data_encoded,
                    callback=self.callback,
                    headers=self.headers,
                    cookies=self.cookies,
                    session=self.session,
                )
                self.last_negotiate = n
                conn_token = n.get("ConnectionToken") or n.get("ConnectionId")
                if not conn_token:
                    raise RuntimeError("negotiate response missing ConnectionToken")
                self.connection_token = conn_token

                self.open_ws(self.connection_token)

                if not self.connected_evt.wait(timeout=self.open_wait_timeout):
                    self.log.warning("WebSocket did not open in %.1fs; closing and exiting.", self.open_wait_timeout)
                    try:
                        if self.ws:
                            self.ws.close()
                    except Exception:
                        pass
                    finally:
                        self.ws = None
                    return

                ts = str(int(time.time() * 1000))
                # HTTP start 호출은 params로 안전하게 구성 (WS용과 달리 raw connectionData 사용)
                connection_data_raw = urllib.parse.unquote(self.connection_data_encoded)
                start_base = BASE_WS_HOST + "/signalr/start"
                start_params = {
                    "transport": "webSockets",
                    "clientProtocol": "2.1",
                    "connectionToken": self.connection_token or "",
                    "connectionData": connection_data_raw,
                    "callback": self.callback,
                    "_": ts,
                }
                if self.ftoken:
                    start_params["ftoken"] = self.ftoken
                self.log.debug("[start] GET %s", start_base)
                r = (self.session or requests).get(  # type: ignore[attr-defined]
                    start_base,
                    params=start_params,
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=10,
                )
                self.log.info("[start] status %s %s", r.status_code, r.text)

#                 self.handler.handle({
#                     "M": [
#                         {
#                             "H": "newshub",
#                             "M": "sendUpdates",
#                             "A": ["[{\"Tags\":[],\"STID\":0,\"NewsID\":9238334,\"Title\":\"Canada Finance Minster: Tariff remission on Chinese steel is very small.\",\"TypeID\":\"0\",\"Description\":\"\",\"PostedShort\":\"15:06\",\"PostedLong\":\"20 October 2025\",\"DatePublished\":\"2025-10-20T15:06:53.22\",\"TestDatePublished\":\"\",\"Breaking\":false,\"Upd\":\"\",\"Img\":\"\",\"Level\":\"\",\"EURL\":\"https://www.financialjuice.com/News/9238334/Canada-Finance-Minster-Tariff-remission-on-Chinese-steel-is-very-small.aspx\",\"HasE\":false,\"RURL\":\"\",\"EURLImg\":\"<img src=\\\"images/rss_0.gif\\\">\",\"STRID\":0,\"RID\":0,\"FCID\":0,\"FCName\":\"\",\"FCNameURL\":null,\"StreamIDs\":[5,2],\"TickerIDs\":[9354],\"Labels\":[\"CAD\",\"Canada\",\"China\",\"Metal\"],\"IID\":\"6fb3107f-a9ed-4939-86d4-9c41e179ac2f\"}]"
# ]
#                         }
#                     ]
#                 })  # type: ignore[attr-defined]

                while True:
                    if self._stop:
                        return
                    if not self.connected_evt.is_set():
                        self.log.info("connection closed; exiting.")
                        return
                    time.sleep(0.5)

            except Exception as e:
                self.log.exception("Error in start: %s", e)
                return

    def stop(self) -> None:
        self._stop = True
        self.connected_evt.clear()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            finally:
                self.ws = None
        # WS 스레드 종료 대기 (최대 2초)
        try:
            if self._ws_thread and self._ws_thread.is_alive():
                self._ws_thread.join(timeout=2.0)
        except Exception:
            pass
        # HTTP 세션 정리
        try:
            if self.session:
                self.session.close()
        except Exception:
            pass