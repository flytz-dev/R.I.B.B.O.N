"""Session-cookie authentication for the multi-user prototype.

Passwords are stored as salted PBKDF2-SHA256 hashes. Sessions are random tokens
kept in an HttpOnly cookie; the database stores only their SHA-256 digest."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core import storage

SESSION_COOKIE = "ribbon_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
PBKDF2_ITERATIONS = 310_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8

# Reserved for accounts created by the temporary multi-user simulation.
SIMULATED_PREFIX = "sim-"


class Credentials(BaseModel):
    username: str
    password: str


class SessionInfo(BaseModel):
    username: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return algorithm == "pbkdf2_sha256" and hmac.compare_digest(candidate.hex(), digest)


# Compared against when the username does not exist, so both failures take equal time.
_UNKNOWN_USER_HASH = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_account(username: str, password: str, simulated: bool = False) -> None:
    """Validate and create an account; raises ValueError with a user-facing message."""
    if not USERNAME_PATTERN.match(username):
        raise ValueError("Usernames have 3 to 32 letters, digits, dots, hyphens, or underscores.")
    if username.lower().startswith(SIMULATED_PREFIX) != simulated:
        raise ValueError(f"Usernames starting with '{SIMULATED_PREFIX}' are reserved for the simulation.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    try:
        storage.create_user(
            username, hash_password(password), datetime.now().isoformat(timespec="seconds"), simulated
        )
    except sqlite3.IntegrityError:
        raise ValueError("This username is already taken.") from None


def authenticate(username: str, password: str) -> bool:
    stored = storage.password_hash(username)
    valid = verify_password(password, stored or _UNKNOWN_USER_HASH)
    return valid and stored is not None


def start_session(response: Response, username: str) -> None:
    token = secrets.token_urlsafe(32)
    now = time.time()
    storage.create_session(_token_hash(token), username, now + SESSION_TTL_SECONDS, now)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax", path="/"
    )


def current_user(request: Request) -> str:
    """FastAPI dependency returning the signed-in username, or responding 401."""
    token = request.cookies.get(SESSION_COOKIE)
    username = storage.session_user(_token_hash(token), time.time()) if token else None
    if not username:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return username


router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/register", response_model=SessionInfo)
def register(credentials: Credentials, response: Response):
    """Create an account and sign in."""
    try:
        create_account(credentials.username, credentials.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    start_session(response, credentials.username)
    return SessionInfo(username=credentials.username)


@router.post("/login", response_model=SessionInfo)
def login(credentials: Credentials, response: Response):
    if not authenticate(credentials.username, credentials.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    start_session(response, credentials.username)
    return SessionInfo(username=credentials.username)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        storage.delete_session(_token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=SessionInfo)
def me(username: str = Depends(current_user)):
    return SessionInfo(username=username)
