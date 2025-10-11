"""FinancialJuice SignalR 관련 상수 모음"""

BASE_WS_HOST = "https://ws1.financialjuice.com"

CONNECT_WS_TEMPLATE = (
    "wss://ws1.financialjuice.com/signalr/connect?transport=webSockets"
    "&clientProtocol=2.1&ftoken={ftoken}&connectionToken={connectionToken}"
    "&connectionData={connectionData}&tid=2"
)

START_URL_TEMPLATE = (
    "https://ws1.financialjuice.com/signalr/start?transport=webSockets"
    "&clientProtocol=2.1&ftoken={ftoken}&connectionToken={connectionToken}"
    "&connectionData={connectionData}&callback={callback}&_={ts}"
)

# default: [{"name":"newshub"}]
DEFAULT_CONNECTION_DATA_JSON = "[{\"name\":\"newshub\"}]"
DEFAULT_CALLBACK = "jQuery1124018846644728821516_1759907136799"


