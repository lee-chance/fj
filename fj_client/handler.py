from typing import Any, Dict, List, Optional
import json
from .slack import send_slack_message
from .logger import get_logger
import firebase_admin  # type: ignore
from firebase_admin import firestore  # type: ignore
import hashlib, time


class NewsHubTranslatorHandler:
    """
    NewsHub/sendUpdates 프레임을 찾아 번역기를 통해 번역 결과를 출력하는 핸들러.
    translator는 ai_translator.OpenRouterTranslator 호환 객체로 가정한다.
    """

    def __init__(self, translator: Optional[Any], target_lang: str = "ko", slack_webhook_url: Optional[str] = None) -> None:
        self.translator = translator
        self.target_lang = target_lang
        self.slack_webhook_url = slack_webhook_url
        self.log = get_logger("handler.newshub")

    def handle(self, frame_obj: Dict[str, Any]) -> None:
        try:
            msgs = frame_obj.get("M")
            if not isinstance(msgs, list):
                return
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                hub = m.get("H") or m.get("h")
                method = m.get("M") or m.get("m")
                if str(hub).lower() != "newshub" or str(method) != "sendUpdates":
                    continue
                args = m.get("A") or m.get("a")
                if not isinstance(args, list) or not args:
                    continue
                payload = args[0]
                news_list: List[Dict[str, Any]] = []
                if isinstance(payload, str):
                    try:
                        news_list = json.loads(payload)
                    except Exception:
                        continue
                elif isinstance(payload, list):
                    news_list = payload
                else:
                    continue

                for news in news_list:
                    if not isinstance(news, dict):
                        continue
                    title = str(news.get("Title") or "").strip()
                    description = str(news.get("Description") or "").strip()
                    if not title and not description:
                        continue
                    # 비즈니스 알림은 명시적으로 Slack 전송 유지
                    if self.slack_webhook_url:
                        send_slack_message(f"새 뉴스: {title}", webhook_url=self.slack_webhook_url)
                    text = title if not description else f"{title}\n\n{description}"
                    if not self.translator:
                        self.log.info("[translate skipped] translator not configured")
                        continue
                    try:
                        result = self.translator.translate(text=text, target_lang=self.target_lang)
                        self.log.info("=== 번역 결과 ===")
                        if title:
                            self.log.info("원문 제목: %s", title)
                        if description:
                            preview = description[:500]
                            self.log.info("원문 본문: %s", preview + ("..." if len(description) > 500 else ""))
                        self.log.info("번역문: %s", result.get("translation", ""))
                        self.log.info("설명: %s", result.get("explanation", ""))
                        advice = result.get("advice", "")
                        if advice:
                            self.log.info("조언: %s", advice)
                        self.log.info("=================")

                        # Slack 알림 전송
                        slack_lines: List[str] = []
                        if title:
                            slack_lines.append(f"*{title}*")
                        translation_text = str(result.get("translation", "")).strip()
                        if translation_text:
                            slack_lines.append(translation_text)
                        if advice:
                            slack_lines.append(f"(조언) {advice}")
                        if slack_lines:
                            slack_text = "\n\n".join(slack_lines)
                            send_res = send_slack_message(slack_text, webhook_url=self.slack_webhook_url)
                            if not send_res.get("ok"):
                                self.log.error("[slack error] %s", send_res.get("error"))
                    except Exception as e:
                        self.log.exception("[translate error] %s", e)
        except Exception as e:
            self.log.exception("[translate handler error] %s", e)



class NewsHubFirestoreHandler:
    """
    NewsHub/sendUpdates 프레임에서 뉴스 항목을 추출하여 Firestore 컬렉션에 적재.
    """

    def __init__(self, *, collection: str = "news", credentials_path: str = "secret/firebase-credentials.json") -> None:
        # firebase_admin 초기화 (이미 초기화된 경우 건너뜀)
        if not firebase_admin._apps:  # type: ignore[attr-defined]
            from firebase_admin import credentials  # type: ignore
            cred = credentials.Certificate(credentials_path)
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.collection = collection
        self.log = get_logger("handler.firestore")

    def handle(self, frame_obj: Dict[str, Any]) -> None:
        try:
            msgs = frame_obj.get("M")
            if not isinstance(msgs, list):
                return
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                hub = m.get("H") or m.get("h")
                method = m.get("M") or m.get("m")
                if str(hub).lower() != "newshub" or str(method) != "sendUpdates":
                    continue
                args = m.get("A") or m.get("a")
                if not isinstance(args, list) or not args:
                    continue
                payload = args[0]
                news_list: List[Dict[str, Any]] = []
                if isinstance(payload, str):
                    try:
                        news_list = json.loads(payload)
                    except Exception:
                        continue
                elif isinstance(payload, list):
                    news_list = payload
                else:
                    continue

                for news in news_list:
                    if not isinstance(news, dict):
                        continue
                    # 외부 스키마는 그대로 보존하며 타임스탬프 필드만 추가/갱신
                    doc = dict(news)
                    if "createdAt" not in doc:
                        doc["createdAt"] = firestore.SERVER_TIMESTAMP
                    doc["updatedAt"] = firestore.SERVER_TIMESTAMP
                    try:
                        ts_ms = int(time.time()*1000)
                        news_id = str(news.get("NewsID") or "").strip()
                        # published_at = str(news.get("DatePublished") or "").strip()
                        # title = str(news.get("Title") or "").strip()
                        # content_key = f"{news_id}-{published_at}-{title}".encode("utf-8")
                        # suffix = hashlib.sha1(content_key).hexdigest()[:12]
                        doc_id = f"{ts_ms}-{news_id}"
                        self.db.collection(self.collection).document(doc_id).set(doc)
                    except Exception as e:
                        self.log.exception("[firestore write error] %s", e)
        except Exception as e:
            self.log.exception("[firestore handler error] %s", e)

