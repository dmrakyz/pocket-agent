"""Just enough of GitHub's releases API to test the pack cache offline."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI()
REL = {"id": None, "assets": []}
BLOBS: dict[int, bytes] = {}
AUTH_SEEN = []


@app.get("/repos/{o}/{r}/releases/tags/{tag}")
def tag(o: str, r: str, tag: str, request: Request):
    AUTH_SEEN.append(request.headers.get("authorization"))
    if REL["id"] is None:
        raise HTTPException(404, "none")
    return {"id": REL["id"], "assets": REL["assets"]}


@app.post("/repos/{o}/{r}/releases")
def create(o: str, r: str, body: dict):
    REL["id"] = 7
    return {"id": 7, "assets": []}


@app.post("/repos/{o}/{r}/releases/{rid}/assets")
async def upload(o: str, r: str, rid: int, name: str, request: Request):
    data = await request.body()
    aid = len(BLOBS) + 100
    BLOBS[aid] = data
    REL["assets"].append({"id": aid, "name": name, "size": len(data)})
    return {"id": aid}


@app.delete("/repos/{o}/{r}/releases/assets/{aid}")
def delete(o: str, r: str, aid: int):
    REL["assets"] = [a for a in REL["assets"] if a["id"] != aid]
    BLOBS.pop(aid, None)
    return Response(status_code=204)


@app.get("/repos/{o}/{r}/releases/assets/{aid}")
def get(o: str, r: str, aid: int):
    if aid not in BLOBS:
        raise HTTPException(404)
    return Response(BLOBS[aid], media_type="application/octet-stream")


@app.get("/_state")
def state():
    return {"assets": REL["assets"], "auth": AUTH_SEEN[-1] if AUTH_SEEN else None}
