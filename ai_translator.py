"""
OpenRouter 기반 AI 번역 모듈

특징
- OpenRouter Chat Completions API 사용
- 환경변수 OPENROUTER_API_KEY 또는 --api-key 인자로 인증
- 기본 타겟 언어: 한국어 (옵션으로 변경 가능)
- CLI 사용 가능: 표준입력/인자 텍스트 번역

의존성: requests
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b:free"


class OpenRouterTranslator:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
        http_referer: Optional[str] = None,
        x_title: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_referer = http_referer
        self.x_title = x_title

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 선택 헤더 (랭킹/통계용)
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.x_title:
            headers["X-Title"] = self.x_title
        return headers

    def translate(self, text: str, target_lang: str = "ko") -> Dict[str, str]:
        """
        Structured Outputs 사용 번역.
        반환: {"original": str, "translation": str, "explanation": str, "advice": str}
        """
        # Structured Outputs 스키마
        response_format: Dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_result",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "original": {
                            "type": "string",
                            "description": "Original input text as-is",
                        },
                        "translation": {
                            "type": "string",
                            "description": f"Accurate, fluent translation of the original into {target_lang}",
                        },
                        "explanation": {
                            "type": "string",
                            "description": "Plain-language explanation of the news in the target language for non-experts (1-3 sentences)",
                        },
                        "advice": {
                            "type": "string",
                            "description": "Expert economic viewpoint on potential market/macro impact in the target language (1-2 sentences); empty string if no meaningful insight",
                        },
                    },
                    "required": ["original", "translation", "explanation", "advice"],
                    "additionalProperties": False,
                },
            },
        }

        system_hint = (
            "You are a professional financial translator and analyst. Preserve proper nouns, "
            "institution names, economic indicators, and numeric values exactly. Keep signs and "
            "directions (e.g., up/down) accurate. Use concise, natural language suitable for a "
            "real-time markets feed. All fields must be in the target language."
        )
        user_prompt = (
            "Translate the following real-time economic news into the target language and respond "
            "STRICTLY using the provided JSON schema fields. Requirements by field:\n"
            "- translation: precise and fluent translation.\n"
            "- explanation: plain-language summary for non-experts in the target language (1-3 sentences).\n"
            "- advice: expert economic viewpoint on potential market or macro impact in the target language (1-2 sentences), "
            "clearly stating uncertainty; if no meaningful insight, return an empty string.\n\n"
            f"Target language: {target_lang}\n"
            f"Original text:\n{text}"
        )

        body: Dict[str, Any] = {
            "model": self.model,
            "extra_body": {
                "models": ["tngtech/deepseek-r1t2-chimera:free", "qwen/qwen3-235b-a22b:free"],
            },
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": system_hint}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
            ],
            "response_format": response_format,
        }

        print("thinking...")
        url = f"{self.base_url}/chat/completions"
        resp = requests.post(url, headers=self._headers(), data=json.dumps(body), timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenRouter error: {resp.status_code} {resp.text[:500]}"
            )
        data = resp.json()
        print(json.dumps(data, ensure_ascii=False))
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            # 기본 키 존재 확인
            for k in ("original", "translation", "explanation"):
                if k not in parsed or not isinstance(parsed[k], str):
                    raise RuntimeError("Structured output missing required fields")
            # optional advice -> ensure string
            if "advice" not in parsed or not isinstance(parsed["advice"], str):
                parsed["advice"] = ""
            return parsed
        except Exception as e:
            raise RuntimeError(f"Unexpected structured output: {e}")


def _read_stdin() -> str:
    chunks: List[str] = []
    for line in sys.stdin:
        chunks.append(line)
    return "".join(chunks).strip()


def main() -> None:
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="AI 번역 (OpenRouter)")
    parser.add_argument("--text", type=str, default=None, help="번역할 원문 텍스트")
    parser.add_argument("--target", type=str, default="ko", help="타겟 언어 코드 (기본: ko)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="모델 ID")
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("OPENROUTER_API_KEY"),
        help="OpenRouter API Key (또는 환경변수 OPENROUTER_API_KEY)",
    )
    parser.add_argument("--http-referer", type=str, default=None, help="HTTP-Referer 헤더")
    parser.add_argument("--x-title", type=str, default=None, help="X-Title 헤더")
    parser.add_argument("--json", action="store_true", help="JSON 형태로 결과 출력")

    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("API 키가 없습니다. --api-key 또는 OPENROUTER_API_KEY 환경변수를 지정하세요.")

    text = args.text if args.text is not None else _read_stdin()
    if not text:
        raise SystemExit("번역할 텍스트가 없습니다. --text 또는 표준입력을 통해 제공하세요.")

    translator = OpenRouterTranslator(
        api_key=args.api_key,
        model=args.model,
        http_referer=args.http_referer,
        x_title=args.x_title,
    )
    result = translator.translate(text=text, target_lang=args.target)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("원문:")
        print(result["original"]) 
        print()
        print("번역문:")
        print(result["translation"]) 
        print()
        print("설명:")
        print(result["explanation"]) 
        print()
        print("조언:")
        print(result["advice"]) 


if __name__ == "__main__":
    main()


