import json
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional

import requests
import websocket

from .constants import (
    BASE_WS_HOST,
    CONNECT_WS_TEMPLATE,
    START_URL_TEMPLATE,
)
from .utils import extract_json_from_jsonp, parse_signalr_frame


def do_negotiate(
    ftoken: Optional[str],
    connection_data_encoded: str,
    callback: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
) -> Dict[str, Any]:
    ts = str(int(time.time() * 1000))
    url = BASE_WS_HOST + "/signalr/negotiate?clientProtocol=2.1"
    if ftoken:
        url += "&ftoken=" + urllib.parse.quote(ftoken, safe="")
    url += "&connectionData=" + connection_data_encoded
    url += "&callback=" + urllib.parse.quote(callback, safe="")
    url += "&_=" + ts

    print("[negotiate] GET", url)
    resp = requests.get(url, headers=headers, cookies=cookies, timeout=10)
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
    ) -> None:
        self.ftoken = ftoken
        self.connection_data_encoded = connection_data_encoded
        self.callback = callback
        self.headers = headers
        self.cookies = cookies
        self.handler = handler

        self.ws: Optional[websocket.WebSocketApp] = None
        self.connected = False
        self._stop = False
        self.lock = threading.Lock()
        self.last_negotiate: Optional[Dict[str, Any]] = None
        self.connection_token: Optional[str] = None

    def open_ws(self, connection_token: str) -> None:
        ws_url = CONNECT_WS_TEMPLATE.format(
            ftoken=urllib.parse.quote(self.ftoken or "", safe=""),
            connectionToken=urllib.parse.quote(connection_token, safe=""),
            connectionData=self.connection_data_encoded,
        )
        header_list = [f"{k}: {v}" for k, v in self.headers.items()]
        print("[ws] connect to", ws_url)
        self.ws = websocket.WebSocketApp(
            ws_url,
            header=header_list,
            on_open=self.on_open,
            on_message=self.on_message,
            on_close=self.on_close,
            on_error=self.on_error,
        )
        t = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=20, ping_timeout=10)
        )
        t.daemon = True
        t.start()

    def on_open(self, ws) -> None:  # type: ignore[no-untyped-def]
        print("[ws] opened")
        with self.lock:
            self.connected = True

    def on_message(self, ws, message: str) -> None:  # type: ignore[no-untyped-def]
        parsed = parse_signalr_frame(message)
        for item in parsed:
            print("[ws message]", json.dumps(item, ensure_ascii=False)[:1000])
            if self.handler and isinstance(item, dict):
                try:
                    self.handler.handle(item)
                except Exception as e:
                    print("[handler error]", e)

    def on_error(self, ws, error) -> None:  # type: ignore[no-untyped-def]
        print("[ws error]", error)

    def on_close(self, ws, close_status_code, close_msg) -> None:  # type: ignore[no-untyped-def]
        print("[ws close]", close_status_code, close_msg)
        with self.lock:
            self.connected = False

    def start(self) -> None:
        try:
            n = do_negotiate(
                ftoken=self.ftoken,
                connection_data_encoded=self.connection_data_encoded,
                callback=self.callback,
                headers=self.headers,
                cookies=self.cookies,
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
                print("WebSocket did not open; exiting.")
                return

            ts = str(int(time.time() * 1000))
            start_url = START_URL_TEMPLATE.format(
                ftoken=urllib.parse.quote(self.ftoken or "", safe=""),
                connectionToken=urllib.parse.quote(self.connection_token, safe=""),
                connectionData=self.connection_data_encoded,
                callback=urllib.parse.quote(self.callback, safe=""),
                ts=ts,
            )
            print("[start] GET", start_url)
            r = requests.get(
                start_url, headers=self.headers, cookies=self.cookies, timeout=10
            )
            print("[start] status", r.status_code, "len", len(r.text))

            while True:
                with self.lock:
                    if not self.connected:
                        print("connection closed; exiting.")
                        return
                time.sleep(0.5)

        except Exception as e:
            print("Error in start:", e)
            return

    def stop(self) -> None:
        self._stop = True
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


