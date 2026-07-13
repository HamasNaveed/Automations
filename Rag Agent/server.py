"""
server.py
---------
FastAPI server to host the Web Chat Interface for the RAG Business Assistant.
Exposes /api/chat and /api/reset endpoints, and serves the frontend client.
"""

import os
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from agent import initialize_agent, load_env

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load configurations
load_env()

# Initialize FastAPI App
app = FastAPI(title="AI Business Consultant Chat API")

# Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Agent
agent = initialize_agent()

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Exposes a conversational route matching the agent's logic."""
    if not request.message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
        
    logger.info(f"Received user message: {request.message}")
    
    if not agent:
        # Fallback Mock logic if Agent could not be loaded due to missing credentials
        response_msg = (
            "Hello! I am currently running in [Mock Sandbox Mode] because no API keys "
            "were configured. To enable full agent capabilities (retrievals & calendar bookings), "
            "please add OPENAI_API_KEY or GEMINI_API_KEY to your .env file."
        )
        return JSONResponse(content={"response": response_msg})

    try:
        # Execute chat response using ReActAgent runner
        reply = agent.chat(request.message)
        logger.info(f"Agent reply: {str(reply)}")
        return JSONResponse(content={"response": str(reply)})
    except Exception as e:
        logger.error(f"Error during agent chat execution: {e}")
        return JSONResponse(
            status_code=500,
            content={"response": f"Internal agent error: {str(e)}"}
        )

@app.post("/api/reset")
async def reset_endpoint():
    """Resets the chat conversation memory."""
    global agent
    if agent:
        try:
            agent.reset()
            logger.info("Agent memory reset successfully.")
            return JSONResponse(content={"status": "success", "message": "Memory reset."})
        except Exception as e:
            logger.error(f"Failed to reset agent memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(content={"status": "mock", "message": "No active agent memory to reset."})

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serves the static web page for the chat UI client."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    index_path = os.path.join(base_dir, "templates", "index.html")
    
    if not os.path.exists(index_path):
        return HTMLResponse(
            content="<h3>Error: HTML template not found. Ensure templates/index.html exists.</h3>",
            status_code=404
        )
        
    with open(index_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting web server on http://localhost:{port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
