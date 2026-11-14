"""模型调用封装 —— 全项目唯一允许发网络请求的模块。

职责：
1. 从环境变量读取配置（密钥绝不写进代码或仓库）
2. 提供 chat() / vision() 两个入口，支持强制 JSON 输出
3. JSON 非法时带原因重试，最终失败抛 LLMError 交上层降级
4. 每次调用记录 token 用量到 llm_log 表（答辩讲成本用这张表）

思考型模型注意：qwen3.5-9b 这类模型会把大量 token 花在内部推理上。
若返回内容为空且 finish_reason == 'length'，说明 max_tokens 不够它"想完再答"，
调大 LLM_MAX_TOKENS（建议 4000）或换非思考模型（如 gemma-3-12b）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from openai import OpenAI

# ---------------------------------------------------------------- 异常与结果


class LLMError(RuntimeError):
    """模型不可用 / 多次重试后仍拿不到合法输出。上层负责降级处理。"""


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    retried: int = 0
    success: bool = True


# ---------------------------------------------------------------- 配置


def _config() -> dict:
    """集中读取环境变量。未设置时给出可跑通的默认值。"""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", ""),      # 🔴 必填：留空会报错
        "api_key": os.environ.get("LLM_API_KEY", ""),        # 🔴 必填：留空会报错
        "model": os.environ.get("LLM_MODEL", ""),            # 🔴 必填：填供应商的模型名
        "timeout": int(os.environ.get("LLM_TIMEOUT", "120")),
        "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "4000")),
        "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.0")),
    }


def is_configured() -> bool:
    """界面用它决定是正常问答还是提示用户去配环境。"""
    c = _config()
    return bool(c["base_url"] and c["api_key"] and c["model"])


def missing_config_keys() -> list[str]:
    """返回尚未配置的变量名，供界面精确提示缺哪几项。"""
    names = {"LLM_BASE_URL": "base_url", "LLM_API_KEY": "api_key", "LLM_MODEL": "model"}
    c = _config()
    return [env for env, key in names.items() if not c[key]]


def _client() -> OpenAI:
    c = _config()
    if not (c["base_url"] and c["api_key"] and c["model"]):
        raise LLMError(
            "模型服务未配置。请设置环境变量 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL"
            "（本地 LM Studio 则填 http://127.0.0.1:1234/v1、任意非空值、实际模型名）。"
        )
    return OpenAI(
        api_key=c["api_key"],
        base_url=c["base_url"],
        timeout=c["timeout"],
        max_retries=0,  # 重试由本模块显式控制，避免双重重试放大耗时
    )


# ---------------------------------------------------------------- 核心调用


def chat(
    messages: list[dict],
    *,
    purpose: str = "general",
    json_mode: bool = False,
    schema_hint: str | None = None,
    validator=None,
    max_retry: int = 2,
) -> LLMResult:
    """发送一次对话请求。

    参数
    ----
    messages   : [{"role": "system|user|assistant", "content": str}, ...]
                 必须以 user 结尾；LM Studio 的 jinja 模板不接受以 assistant 开头的序列。
    json_mode  : True 时在 prompt 里追加 JSON 要求，并对返回值做解析校验。
    validator  : callable(dict) -> str | None。返回错误信息则视为校验失败并重试。
                 例：检查 knowledge_ids 是否都在候选集内。
    返回 LLMResult；全部重试失败抛 LLMError。
    """
    cfg = _config()
    payload = list(messages)
    if json_mode:
        hint = schema_hint or '{"key": "value"}'
        payload.append(
            {
                "role": "system",
                "content": f"只输出一个合法 JSON 对象，不要包含解释文字、不要用 markdown 代码块包裹。结构示例：{hint}",
            }
        )

    client = _client()
    last_err = ""
    for attempt in range(max_retry + 1):
        try:
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=payload,
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
            )
        except Exception as exc:  # noqa: BLE001 - 需要把各类 SDK 异常统一成 LLMError
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < max_retry:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(f"模型调用失败：{last_err}") from exc

        usage = getattr(resp, "usage", None)
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        result = LLMResult(
            text=(resp.choices[0].message.content or "").strip(),
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0,
            retried=attempt,
        )
        log_call(purpose, result)

        # 思考型模型的典型症状：token 全花在 reasoning 上，可见内容为空
        if not result.text:
            last_err = (
                f"模型返回空内容（finish_reason={resp.choices[0].finish_reason}，"
                f"reasoning_tokens={result.reasoning_tokens}）。"
                "若是思考型模型，请调大 LLM_MAX_TOKENS 或改用非思考模型。"
            )
            if attempt < max_retry:
                continue
            raise LLMError(last_err)

        if not json_mode:
            return result

        parsed = _extract_json(result.text)
        if parsed is None:
            last_err = "返回内容不是合法 JSON"
        elif validator is not None:
            problem = validator(parsed)
            last_err = "" if problem is None else str(problem)
        else:
            last_err = ""

        if not last_err:
            return result

        # 把失败原因回喂给模型，比盲目重试成功率高得多
        payload = messages + [
            {"role": "assistant", "content": result.text[:800]},
            {
                "role": "system",
                "content": f"上一次输出不符合要求：{last_err}。请修正后重新只输出 JSON。",
            },
        ]
        if attempt < max_retry:
            continue

    raise LLMError(f"多次重试仍未获得合法输出：{last_err}")


def vision(
    prompt_text: str,
    images: list[bytes],
    *,
    purpose: str = "vision",
    system: str | None = None,
    json_mode: bool = False,
    schema_hint: str | None = None,
    validator=None,
) -> LLMResult:
    """多模态调用：图片 + 文字。用于 M2 错题识别。

    images 传原始字节即可，内部转 base64 data URL（OpenAI 兼容格式）。
    ⚠️ 并非所有供应商/模型都支持图片输入，接入新模型前务必实测一次。
    """
    import base64

    content: list[dict] = [{"type": "text", "text": prompt_text}]
    for raw in images:
        b64 = base64.b64encode(raw).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": content})

    # 部分兼容服务不支持 content 数组形式，失败时退化为纯文字请求
    try:
        return chat(
            msgs,
            purpose=purpose,
            json_mode=json_mode,
            schema_hint=schema_hint,
            validator=validator,
        )
    except LLMError as exc:
        if "content" in str(exc).lower() or "image" in str(exc).lower():
            plain = chat(
                ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt_text}],
                purpose=purpose + "_fallback",
                json_mode=json_mode,
                schema_hint=schema_hint,
                validator=validator,
            )
            plain.text = "[视觉输入不受支持，已降级为纯文本]\n" + plain.text
            return plain
        raise


# ---------------------------------------------------------------- JSON 工具


def _extract_json(text: str) -> dict | None:
    """从模型输出里抠出 JSON 对象。容忍 ```json 包裹和前后废话。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[3:] else t[3:]
        t = t.removeprefix("json").strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- 调用日志
# 延迟导入 db，避免 llm_client 被独立测试时产生依赖环

_LOG_CACHE: list = []


def log_call(purpose: str, result: LLMResult) -> None:
    """记录一次调用。db 不可用时先缓存在内存，不阻塞主流程。"""
    row = (
        purpose,
        result.prompt_tokens,
        result.completion_tokens,
        bool(result.retried),
        result.success,
    )
    try:
        from app.db import insert_llm_log

        insert_llm_log(*row)
    except Exception:  # noqa: BLE001 - 日志失败绝不能影响业务
        _LOG_CACHE.append(row)
