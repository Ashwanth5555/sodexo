from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token
from app.core.users import authenticate, get_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

# auto_error=False: we raise our own 401 with a WWW-Authenticate header so a
# missing token and a bad token look identical to the caller.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str
    full_name: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> UserOut:
    """FastAPI dependency: resolve the bearer token to one of the app users."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    payload = decode_access_token(credentials.credentials, secret=get_settings().auth_secret_key)
    if payload is None:
        raise _UNAUTHORIZED
    user = get_user(str(payload.get("sub", "")))
    if user is None:
        raise _UNAUTHORIZED
    return UserOut(username=user.username, full_name=user.full_name, role=user.role)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    token = create_access_token(
        subject=user.username,
        role=user.role,
        full_name=user.full_name,
        secret=settings.auth_secret_key,
        expires_minutes=settings.auth_token_expire_minutes,
    )
    return LoginResponse(
        access_token=token,
        expires_in=settings.auth_token_expire_minutes * 60,
        user=UserOut(username=user.username, full_name=user.full_name, role=user.role),
    )


@router.get("/me", response_model=UserOut)
def me(user: UserOut = Depends(get_current_user)):
    """Used by the UI on load to confirm a stored token is still valid."""
    return user


async def require_api_auth(request: Request, call_next):
    """Optional middleware (settings.auth_protect_api) that closes the API to
    unauthenticated callers, so the login gate is not UI-only."""
    path = request.url.path
    exempt = (
        request.method == "OPTIONS"
        or not path.startswith("/api/")
        or path.startswith("/api/auth/login")
    )
    if exempt:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or decode_access_token(token, secret=get_settings().auth_secret_key) is None:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Not authenticated"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)
