from typing import Any, Dict, List, Optional
import json


class NewsHubTranslatorHandler:
    """
    NewsHub/sendUpdates 프레임을 찾아 번역기를 통해 번역 결과를 출력하는 핸들러.
    translator는 ai_translator.OpenRouterTranslator 호환 객체로 가정한다.
    """

    def __init__(self, translator: Optional[Any], target_lang: str = "ko") -> None:
        self.translator = translator
        self.target_lang = target_lang

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
                    text = title if not description else f"{title}\n\n{description}"
                    if not self.translator:
                        print("[translate skipped] translator not configured")
                        continue
                    try:
                        result = self.translator.translate(text=text, target_lang=self.target_lang)
                        print("\n=== 번역 결과 ===")
                        if title:
                            print("원문 제목:", title)
                        if description:
                            preview = description[:500]
                            print("원문 본문:", preview + ("..." if len(description) > 500 else ""))
                        print("번역문:", result.get("translation", ""))
                        print("설명:", result.get("explanation", ""))
                        advice = result.get("advice", "")
                        if advice:
                            print("조언:", advice)
                        print("=================\n")
                    except Exception as e:
                        print("[translate error]", e)
        except Exception as e:
            print("[translate handler error]", e)


