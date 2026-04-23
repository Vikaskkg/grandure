"""
Grandure Hotel Concierge — Production Version
Architecture:
- FastAPI
- Explicit session state
- Selective RAG
- MCP (only when required)
- LLM = response generator only
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict
import os, httpx, re
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Grandure Concierge", version="5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MCP_BASE = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
BASE_DIR = os.path.dirname(__file__)

# ─────────────────────────────
# RAG LOADING (lazy usage)
# ─────────────────────────────
def load_rag(name):
    try:
        with open(os.path.join(BASE_DIR, "rag", name), encoding="utf-8") as f:
            return f.read()
    except:
        return ""

RAG = {
    "room": load_rag("room_types.md"),
    "spa": load_rag("spa_menu.md"),
    "dining": load_rag("fnb_menu.md"),
}

# ─────────────────────────────
# SESSION STATE
# ─────────────────────────────
class SessionState(BaseModel):
    intent: Optional[str] = None       # room | spa | dining
    stage: str = "explore"             # explore | collecting | confirm

    date: Optional[str] = None
    nights: Optional[int] = None
    guests: Optional[int] = None

    spa_treatment: Optional[str] = None


class ChatRequest(BaseModel):
    messages: List[Dict]
    state: SessionState
    provider: str = "openrouter"
    model: Optional[str] = None


# ─────────────────────────────
# SIMPLE INTENT DETECTION
# ─────────────────────────────
def detect_intent(text: str):
    t = text.lower()
    if any(w in t for w in ["spa", "massage", "facial"]):
        return "spa"
    if any(w in t for w in ["room", "stay", "night", "hotel"]):
        return "room"
    if any(w in t for w in ["dinner", "restaurant", "food", "menu"]):
        return "dining"
    return None


# ─────────────────────────────
# STATE EXTRACTION
# ─────────────────────────────
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NIGHT_RE = re.compile(r"(\d+)\s*night")
GUEST_RE = re.compile(r"(\d+)\s*(guest|people|person)")

def update_state(state: SessionState, text: str):
    if not state.date:
        m = DATE_RE.search(text)
        if m:
            state.date = m.group(0)

    if not state.nights:
        m = NIGHT_RE.search(text)
        if m:
            state.nights = int(m.group(1))

    if not state.guests:
        m = GUEST_RE.search(text)
        if m:
            state.guests = int(m.group(1))

    return state


# ─────────────────────────────
# FLOW CONTROL
# ─────────────────────────────
def next_question(state: SessionState):
    if state.intent == "room":
        if not state.date:
            return "What date would you like to check in?"
        if not state.nights:
            return "How many nights will you stay?"
        if not state.guests:
            return "How many guests?"

    if state.intent == "spa":
        if not state.date:
            return "What date would you like your treatment?"

    return None


def ready_for_mcp(state: SessionState):
    if state.intent == "room":
        return state.date and state.nights and state.guests
    if state.intent == "spa":
        return state.date
    return False


# ─────────────────────────────
# MCP CALL
# ─────────────────────────────
async def call_mcp(state: SessionState):
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            if state.intent == "room":
                r = await c.get(f"{MCP_BASE}/tools/check_room_availability", params={
                    "date": state.date,
                    "nights": state.nights,
                    "guests": state.guests
                })
                return r.text

            if state.intent == "spa":
                r = await c.get(f"{MCP_BASE}/tools/check_spa_availability", params={
                    "date": state.date
                })
                return r.text

    except:
        return ""

    return ""


# ─────────────────────────────
# RAG SELECTION
# ─────────────────────────────
def select_rag(state: SessionState, message: str):
    if state.intent:
        return RAG[state.intent][:1500]
    return ""


# ─────────────────────────────
# PROMPT (SHORT & STRONG)
# ─────────────────────────────
BASE_SYSTEM = """
You are Lisa, concierge at Grandure Hotel.

Rules:
- Max 2 sentences
- Always include price when recommending
- Ask only ONE question if needed
- Never repeat information

Flow:
- If exploring → recommend + price
- If booking → collect missing info
- If data provided → confirm clearly

Tone: warm, elegant, concise
"""


# ─────────────────────────────
# LLM CALL (OpenRouter example)
# ─────────────────────────────
async def call_llm(messages, system, model):
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise HTTPException(400, "Missing OPENROUTER_API_KEY")

    payload = {
        "model": model or "google/gemma-3-27b-it",
        "messages": [{"role": "system", "content": system}] + messages[-4:],
        "max_tokens": 200,
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload
        )

    if not r.is_success:
        raise HTTPException(500, r.text)

    return r.json()["choices"][0]["message"]["content"]


# ─────────────────────────────
# QUICK REPLIES (backend)
# ─────────────────────────────
def quick_replies(state: SessionState):
    if state.intent == "spa":
        return ["Swedish massage", "Deep tissue", "Facial"]
    if state.intent == "room":
        return ["2 nights", "3 nights", "Suite"]
    if state.intent == "dining":
        return ["Dinner reservation", "View menu", "Wine"]
    return []


# ─────────────────────────────
# MAIN CHAT ENDPOINT
# ─────────────────────────────
@app.post("/api/chat")
async def chat(req: ChatRequest):

    state = req.state
    last_msg = req.messages[-1]["content"]

    # 1. Detect intent
    if not state.intent:
        state.intent = detect_intent(last_msg)

    # 2. Update state from user input
    state = update_state(state, last_msg)

    # 3. Detect booking trigger
    if any(w in last_msg.lower() for w in ["book", "reserve"]):
        state.stage = "collecting"

    # 4. Ask next question if needed
    if state.stage == "collecting":
        q = next_question(state)
        if q:
            return {
                "reply": q,
                "state": state,
                "quick_replies": quick_replies(state)
            }

    # 5. Get RAG context
    rag = select_rag(state, last_msg)

    # 6. Get MCP only if ready
    live = ""
    if ready_for_mcp(state):
        live = await call_mcp(state)

    # 7. Build system prompt
    system = BASE_SYSTEM + "\n\n" + rag + "\n\n" + live

    # 8. Call LLM
    reply = await call_llm(req.messages, system, req.model)

    return {
        "reply": reply.strip(),
        "state": state,
        "quick_replies": quick_replies(state)
    }


# ─────────────────────────────
# HEALTH CHECK
# ─────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "version": "5.0"}