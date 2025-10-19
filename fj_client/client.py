import json
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional

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

    def open_ws(self, connection_token: str) -> None:
        ws_url = CONNECT_WS_TEMPLATE.format(
            ftoken=urllib.parse.quote(self.ftoken or "", safe=""),
            connectionToken=urllib.parse.quote(connection_token, safe=""),
            connectionData=self.connection_data_encoded,
        )
        header_list = [f"{k}: {v}" for k, v in self.headers.items()]
        self.log.info("[ws] connect to %s", ws_url)
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

    def on_open(self, ws) -> None:  # type: ignore[no-untyped-def]
        self.log.info("[ws] opened")
        with self.lock:
            self.connected = True

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
        # on_error 발생 시 단 1회 자동 재시도
        if not self.retry_attempted and not self._stop:
            self.retry_attempted = True
            self.log.warning("[ws retry] scheduling single retry after error")

            def _retry_once() -> None:
                # 현재 연결을 정리한 뒤 짧게 대기하고 재시작 시도
                try:
                    if self.ws:
                        try:
                            self.ws.close()
                        except Exception:
                            pass
                finally:
                    time.sleep(1.5)
                try:
                    self.start()
                except Exception as e:  # 방어적 로깅
                    self.log.exception("[ws retry failed] %s", e)

            t = threading.Thread(target=_retry_once)
            t.daemon = True
            t.start()

    def on_close(self, ws, close_status_code, close_msg) -> None:  # type: ignore[no-untyped-def]
        self.log.info("[ws close] %s %s", close_status_code, close_msg)
        with self.lock:
            self.connected = False

    def start(self) -> None:
        # 동시에 하나의 start만 실행되도록 보장
        with self._start_mutex:
            try:
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

                for _ in range(10):
                    with self.lock:
                        if self.connected:
                            break
                    time.sleep(0.2)
                if not self.connected:
                    self.log.warning("WebSocket did not open; exiting.")
                    return

                ts = str(int(time.time() * 1000))
                # HTTP start 호출은 params로 안전하게 구성 (WS용과 달리 raw connectionData 사용)
                connection_data_raw = urllib.parse.unquote(self.connection_data_encoded)
                start_base = BASE_WS_HOST + "/signalr/start"
                start_params = {
                    "transport": "webSockets",
                    "clientProtocol": "2.1",
                    "ftoken": self.ftoken or "",
                    "connectionToken": self.connection_token or "",
                    "connectionData": connection_data_raw,
                    "callback": self.callback,
                    "_": ts,
                }
                self.log.debug("[start] GET %s", start_base)
                r = (self.session or requests).get(  # type: ignore[attr-defined]
                    start_base,
                    params=start_params,
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=10,
                )
                self.log.info("[start] status %s %s", r.status_code, r.text)

                while True:
                    with self.lock:
                        if not self.connected:
                            self.log.info("connection closed; exiting.")
                            return
                    time.sleep(0.5)

            except Exception as e:
                self.log.exception("Error in start: %s", e)
                return

    def stop(self) -> None:
        self._stop = True
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


