"""Principals. Identity comes from a verified JWT; roles and tenancy come ONLY from server-side
municipal_memberships. Claims such as role/tenant/city in a token are ignored on purpose."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID

import jwt

from . import db
from .config import get_settings

DEV_ISSUER = "lanekeeper-dev"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Membership:
    jurisdiction_id: str
    role: str
    department_id: str | None
    district_ids: tuple[str, ...]


@dataclass(frozen=True)
class Principal:
    user_id: str | None                      # None = anonymous public rider
    memberships: tuple[Membership, ...] = field(default_factory=tuple)

    @property
    def anonymous(self) -> bool:
        return self.user_id is None

    def roles_in(self, jurisdiction_id: str) -> set[str]:
        return {m.role for m in self.memberships if m.jurisdiction_id == str(jurisdiction_id)}

    def jurisdictions(self) -> set[str]:
        return {m.jurisdiction_id for m in self.memberships}


ANONYMOUS = Principal(user_id=None)


def _decode(token: str) -> dict:
    s = get_settings()
    errors = []
    if s.supabase_jwt_secret:
        try:
            return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError as e:
            errors.append(type(e).__name__)
    try:
        return jwt.decode(token, s.signing_secret, algorithms=["HS256"], issuer=DEV_ISSUER, audience="authenticated")
    except jwt.PyJWTError as e:
        errors.append(type(e).__name__)
    raise AuthError("invalid token: " + ", ".join(errors))


def load_memberships(user_id: str) -> tuple[Membership, ...]:
    rows = db.all_rows(
        """select jurisdiction_id::text, role, department_id::text, district_ids
             from municipal_memberships
            where user_id = %s and status = 'active' and valid_from <= now()
              and (valid_until is null or valid_until > now())""",
        (user_id,),
    )
    return tuple(Membership(r["jurisdiction_id"], r["role"], r["department_id"], tuple(r["district_ids"] or ()))
                 for r in rows)


def principal_from_token(token: str | None) -> Principal:
    if not token:
        return ANONYMOUS
    claims = _decode(token)
    sub = claims.get("sub")
    try:
        UUID(str(sub))
    except (ValueError, TypeError) as e:
        raise AuthError("token subject is not a user id") from e
    return Principal(user_id=str(sub), memberships=load_memberships(str(sub)))


def reload(principal: Principal) -> Principal:
    """Fresh membership read: used on graph resume and at privileged tool boundaries."""
    if principal.anonymous:
        return principal
    return Principal(user_id=principal.user_id, memberships=load_memberships(principal.user_id))


def mint_dev_token(user_id: str, ttl_s: int = 8 * 3600) -> str:
    """Local development only: a token signed with LK_SIGNING_SECRET. Carries identity, not roles."""
    s = get_settings()
    from .config import demo_identities_allowed
    if s.supabase_jwt_secret and not demo_identities_allowed():
        raise AuthError("refusing to mint dev tokens when a real Supabase JWT secret is configured")
    now = int(time.time())
    return jwt.encode({"sub": user_id, "aud": "authenticated", "iss": DEV_ISSUER, "iat": now, "exp": now + ttl_s,
                       "role": "authenticated"}, s.signing_secret, algorithm="HS256")
