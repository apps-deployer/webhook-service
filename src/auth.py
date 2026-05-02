from datetime import UTC, datetime, timedelta

import jwt


def generate_service_token(secret: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "service:webhook-service",
            "typ": "service",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        secret,
        algorithm="HS256",
    )
