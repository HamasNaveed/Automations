"""
Web UI + API for the RAG agent.

Run:
    python server.py

Then open http://localhost:8000

Session management: the browser generates a random session_id (stored in
localStorage) the first time it loads the page and sends it with every
/api/chat request. The server keeps one conversation history per session_id
(see agent.py's RagAgent), so a visitor's chat stays coherent across
messages, and a "New chat" button lets them start over.
"""

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import RagAgent

app = FastAPI(title="Company RAG Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "templates")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_agent: RagAgent | None = None


def get_agent() -> RagAgent:
    global _agent
    if _agent is None:
        _agent = RagAgent()
    return _agent


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class ResetRequest(BaseModel):
    session_id: str


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")

    session_id = req.session_id or str(uuid.uuid4())
    try:
        reply = await get_agent().chat(session_id, message)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")
    return ChatResponse(reply=reply, session_id=session_id)


@app.post("/api/reset")
def reset(req: ResetRequest):
    get_agent().reset_session(req.session_id)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    # Load the agent (and thus the embedding model + retriever + LLM) at
    # startup rather than on the first request, so startup errors (like a
    # missing API key) surface immediately instead of on a user's first click.
    get_agent()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
