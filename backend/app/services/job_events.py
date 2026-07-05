"""Redis pub/sub 进度通道 — 任务执行进度实时回传前端（spec §13.1 SSE 推送）。

Key schema:  ``job:events:{job_id}`` 频道，worker publish 进度事件，SSE 端点 subscribe。

事件结构（spec §13.4 每步写 job_steps + 进度推送）:
    {"type": "status|progress|step|done|error",
     "job_id": int, "status": str, "progress": int,
     "step_name": str?, "message": str?, "error_code": str?}

全部 fail-safe：Redis 不可用时 publish 静默跳过、stream 立即结束，不抛异常。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "job:events:"

# SSE 订阅单次轮询超时秒数。超过则 yield keepalive（None），让 SSE 端点发心跳。
_STREAM_POLL_TIMEOUT = 5
# SSE 整体最长订阅秒数（防止长连接泄漏，前端可重连）。
_STREAM_MAX_DURATION = 600


def _channel(job_id: int) -> str:
    return f"{CHANNEL_PREFIX}{job_id}"


def publish_job_event(job_id: int, event: dict[str, Any]) -> None:
    """向 ``job:events:{job_id}`` 频道发布进度事件。

    Redis 不可用时静默跳过 — 前端退化为 DB 轮询。
    """
    client = get_redis()
    if client is None:
        return
    payload = {"job_id": job_id, **event}
    try:
        client.publish(_channel(job_id), json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        logger.exception("Failed to publish job event for job_id=%s", job_id)


def make_event(
    *,
    type_: str,
    status: str | None = None,
    progress: int | None = None,
    step_name: str | None = None,
    message: str | None = None,
    error_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """构造标准化事件 dict（worker 调用）。"""
    event: dict[str, Any] = {"type": type_}
    if status is not None:
        event["status"] = status
    if progress is not None:
        event["progress"] = progress
    if step_name is not None:
        event["step_name"] = step_name
    if message is not None:
        event["message"] = message
    if error_code is not None:
        event["error_code"] = error_code
    event.update(extra)
    return event


def job_event_stream(job_id: int) -> Iterator[dict[str, Any] | None]:
    """订阅 ``job:events:{job_id}``，yield 事件 dict；yield None 表示该轮无消息（发心跳）。

    用于 SSE 端点。Redis 不可用时直接返回（SSE 端点降级为只发初始快照 + keepalive）。
    订阅最多持续 _STREAM_MAX_DURATION 秒，超时后自动结束（前端可重连）。
    """
    client = get_redis()
    if client is None:
        return
    try:
        pubsub = client.pubsub()
        pubsub.subscribe(_channel(job_id))
    except Exception:
        logger.exception("Failed to subscribe job events for job_id=%s", job_id)
        return

    deadline = time.monotonic() + _STREAM_MAX_DURATION
    try:
        # 丢弃订阅确认消息（第一条是 {"type":"subscribe",...}）。
        while time.monotonic() < deadline:
            try:
                msg = pubsub.get_message(timeout=_STREAM_POLL_TIMEOUT)
            except Exception:
                logger.exception("job_event_stream get_message failed for job_id=%s", job_id)
                return
            if msg is None:
                yield None  # 该轮无消息，让 SSE 发 keepalive
                continue
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if not isinstance(data, str):
                continue
            try:
                event = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed job event for job_id=%s: %r", job_id, data[:200])
                continue
            yield event
            # 终态事件后结束订阅。
            if event.get("type") in {"done", "error"}:
                return
    finally:
        try:
            pubsub.close()
        except Exception:
            pass
