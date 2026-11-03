import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException

PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
PASSWORD_SALT_BYTES = 16
PASSWORD_KEY_BYTES = 64


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
        dklen=PASSWORD_KEY_BYTES,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${PASSWORD_SCRYPT_N}${PASSWORD_SCRYPT_R}${PASSWORD_SCRYPT_P}${salt.hex()}${password_hash.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algorithm, n, r, p, salt_hex, hash_hex = stored_hash.split("$")
        if (
            algorithm != "scrypt"
            or (int(n), int(r), int(p)) != (PASSWORD_SCRYPT_N, PASSWORD_SCRYPT_R, PASSWORD_SCRYPT_P)
        ):
            return False
        expected = bytes.fromhex(hash_hex)
        salt = bytes.fromhex(salt_hex)
        if len(expected) != PASSWORD_KEY_BYTES or len(salt) != PASSWORD_SALT_BYTES:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=PASSWORD_SCRYPT_N,
            r=PASSWORD_SCRYPT_R,
            p=PASSWORD_SCRYPT_P,
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_invitation_code() -> str:
    return secrets.token_urlsafe(32)


def hash_invitation_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_session_token(user_id: str) -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(503, "Server session signing key is not configured")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + timedelta(hours=1), "auth": "password"},
        secret,
        algorithm="HS256",
    )


def decode_session_token(token: str) -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(503, "Server session signing key is not configured")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "auth"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "Session expired; sign in again") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Session is invalid; sign in again") from exc
    if claims["auth"] != "password":
        raise HTTPException(401, "Session is invalid; sign in again")
    return claims["sub"]
