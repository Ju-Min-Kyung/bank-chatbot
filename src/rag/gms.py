"""SSAFY GMS Responses API. Never log secrets, prompts, or remote error bodies."""
import json
import socket
import urllib.error
import urllib.request

from jsonschema import Draft202012Validator, ValidationError


class ModelError(RuntimeError):
    pass


class SourcePermissionError(ModelError):
    pass


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the Authorization header to a redirect destination.
        return None


class GMSClient:
    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or urllib.request.build_opener(NoRedirects())
        self.calls = 0
        self.usage = []

    def structured(self, instructions, payload, schema, name, *, source_data=True):
        if not self.config.api_key.strip():
            raise ModelError("GMS 키가 없습니다. .env의 GMS_API_KEY를 입력하세요.")
        if source_data and not self.config.source_transmission_allowed:
            raise SourcePermissionError("자료의 GMS 전송 허용 설정이 필요합니다.")
        body = {"model": self.config.model, "instructions": instructions,
                "input": json.dumps(payload, ensure_ascii=False), "store": False,
                "max_output_tokens": self.config.max_output_tokens,
                "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
        req = urllib.request.Request(self.config.endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.config.api_key}, method="POST")
        self.calls += 1
        try:
            with self.opener.open(req, timeout=self.config.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ModelError("GMS 응답이 허용 크기를 넘었습니다.")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            messages = {401: "GMS 인증에 실패했습니다. 키를 확인하세요.", 403: "GMS 접근 권한이 없습니다.",
                        429: "GMS 사용 한도에 도달했습니다. 잠시 후 다시 시도하세요."}
            raise ModelError(messages.get(exc.code, f"GMS 요청에 실패했습니다. HTTP {exc.code}.")) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ModelError("GMS 네트워크 연결 또는 응답 대기에 실패했습니다.") from None
        except (ValueError, UnicodeError):
            raise ModelError("GMS가 올바른 JSON 응답을 반환하지 않았습니다.") from None
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise ModelError("GMS 응답이 완료되지 않았습니다. 출력 한도 또는 모델 상태를 확인하세요.")
        parts = []
        for item in result.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise ModelError("모델이 요청 처리를 거절했습니다.")
                if part.get("type") == "output_text":
                    parts.append(part.get("text", ""))
        try:
            value = json.loads("".join(parts))
            Draft202012Validator(schema).validate(value)
        except (ValueError, TypeError, ValidationError):
            raise ModelError("모델 출력 형식 검증에 실패했습니다.") from None
        usage = result.get("usage") or {}
        self.usage.append({key: usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")})
        return value
