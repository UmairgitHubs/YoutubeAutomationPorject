from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.studio_auth import COOKIE_NAME, credentials_match, make_token

router = APIRouter(prefix="/api/studio", tags=["studio"])


class StudioLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def studio_login(body: StudioLogin, response: Response) -> dict:
    if not credentials_match(body.username, body.password):
        raise HTTPException(status_code=401, detail="Username or password is not right.")
    response.set_cookie(
        COOKIE_NAME,
        make_token(),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=14 * 24 * 60 * 60,
    )
    return {"ok": True}
