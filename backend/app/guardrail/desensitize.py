"""不可逆的文本与结构化脱敏；不因字段名像业务 ID 就放行。"""

import json
import re


class SensitiveDataError(RuntimeError):
    def __init__(self):
        super().__init__("内容安全处理失败")


# 凭证完全替换，避免短密钥的首尾片段合起来暴露完整值。
# 字母/数字/下划线边界避免把 req_<hex> 等业务 ID 的片段当完整签名。
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(r"(?i)(?<![A-Za-z0-9])(?:ak|sk)[-_][A-Za-z0-9_-]+")),
    ("secret", re.compile(
        r'''(?i)((?:secret(?:[_-]?key)?|api[_-]?key|access[_-]?token|token|password)'''
        r'''\s*["']?\s*[=:]\s*["']?)([A-Za-z0-9._~+/=-]+)'''
    )),
    ("token", re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)")),
    ("idcard", re.compile(r"(?<![A-Za-z0-9_])\d{17}[\dXx](?![A-Za-z0-9_])")),
    ("bankcard", re.compile(r"(?<![A-Za-z0-9_])\d{16,19}(?![A-Za-z0-9_])")),
    ("phone", re.compile(r"(?<![A-Za-z0-9_])1[3-9]\d{9}(?![A-Za-z0-9_])")),
    ("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("signature", re.compile(r"(?<![A-Za-z0-9_])[a-fA-F0-9]{32,}(?![A-Za-z0-9_])")),
]

_SENSITIVE_FIELDS = {
    "api_key": "api_key", "secret": "secret", "secret_key": "secret", "password": "secret",
    "token": "token", "access_token": "token", "authorization": "token",
    "phone": "phone", "phone_number": "phone", "mobile": "phone", "email": "email",
    "idcard": "idcard", "id_card": "idcard", "bankcard": "bankcard", "bank_card": "bankcard",
    "signature": "signature",
}


def detect(text: str) -> list[str]:
    return [kind for kind, pattern in _PATTERNS if pattern.search(text)]


def desensitize_text(text: str) -> str:
    try:
        if not isinstance(text, str):
            raise SensitiveDataError()
        # JSON arguments 先解码再处理，防止 Unicode 转义改变匹配边界。
        if text.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                return json.dumps(_clean(parsed), ensure_ascii=False)
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text)
        for kind, pattern in _PATTERNS:
            marker = f"[已脱敏:{kind}]"
            if kind in {"secret", "token"}:
                text = pattern.sub(lambda match, replacement=marker: match.group(1) + replacement, text)
            else:
                text = pattern.sub(lambda _match, replacement=marker: replacement, text)
        return text
    except Exception:  # noqa: BLE001 -- 安全边界失败时统一拒绝，不透传原始异常。
        raise SensitiveDataError() from None


def _clean(obj, *, mask_fields=True):
    if isinstance(obj, str):
        return desensitize_text(obj)
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            field = key.casefold().replace("-", "_") if isinstance(key, str) else ""
            kind = _SENSITIVE_FIELDS.get(field) if mask_fields else None
            clean_key = desensitize_text(key) if isinstance(key, str) else key
            clean[clean_key] = (f"[已脱敏:{kind}]" if kind and value not in (None, "")
                                else _clean(value, mask_fields=mask_fields))
        return clean
    if isinstance(obj, (list, tuple)):
        return [_clean(value, mask_fields=mask_fields) for value in obj]
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        raw = str(obj)
        clean = desensitize_text(raw)
        return clean if clean != raw else obj
    return desensitize_text(str(obj))


def desensitize_obj(obj, *, mask_fields=True):
    try:
        return _clean(obj, mask_fields=mask_fields)
    except Exception:  # noqa: BLE001 -- 递归、类型转换或规则异常均不得降级为原文。
        raise SensitiveDataError() from None
