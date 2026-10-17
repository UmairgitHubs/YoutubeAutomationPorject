from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.studio_auth import attach_session, credentials_match

router = APIRouter(prefix="/api/studio", tags=["studio"])


class StudioLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def studio_login(body: StudioLogin, response: Response) -> dict:
    if not credentials_match(body.username, body.password):
        raise HTTPException(status_code=401, detail="Username or password is not right.")
    attach_session(response)
    return {"ok": True}
