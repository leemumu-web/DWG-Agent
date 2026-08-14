"""密码哈希与 JWT 签名原语。

令牌 claim 契约：

- 每个签发的令牌都携带 ``sub``（整数用户 id 的字符串形式）、``jti``
  （唯一 id）、``iat``（小数 NumericDate）、``exp`` 与 ``type``
  （``access`` 或 ``refresh``）。
- ``decode_token`` 只校验签名与过期——**不校验** ``type``。调用方
  （identity 依赖/认证/会话）必须自行校验 ``payload["type"]``；吊销通过
  把 ``jti`` 写入 identity 黑名单表并结合 ``password_changed_at`` 时间戳
  检查实现。不要假设 decode 即授权。
- ``sub`` 是整数用户 id；extra_claims 可添加领域特定 claim（如 cookie
  标志），但绝不能覆盖上述核心 claim。
- 密码使用 pwdlib ``PasswordHash.recommended()`` 的 Argon2id；存储的
  ``password_algo`` 列必须与此选择保持一致。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.platform.config.settings import settings
from app.platform.time import business_now

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """哈希明文密码（Argon2id，pwdlib recommended）。"""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """用存储的哈希校验明文密码。"""
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """签发短时 access token（分钟级；见模块 claim 契约）。"""
    now = business_now()
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid.uuid4()),
        # JWT NumericDate permits fractional seconds. Keeping the fraction avoids
        # treating a fresh token issued in the same second as a password change
        # as stale while still accepting older integer-iat tokens.
        "iat": now.timestamp(),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """签发长时 refresh token（天级；见模块 claim 契约）。"""
    now = business_now()
    expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid.uuid4()),
        "iat": now.timestamp(),
        "exp": int(expire.timestamp()),
        "type": "refresh",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """仅解码并校验签名/过期（**不**校验 ``type``）。"""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
