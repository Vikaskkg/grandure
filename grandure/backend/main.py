"""
main.py  —  Grandure Hotel Concierge v4
Architecture:
  RAG  → rag/room_types.md, rag/spa_menu.md, rag/fnb_menu.md  (what we offer)
  MCP  → mcp_server.py on port 8001  (live availability and pricing)
  LLM  → Claude / Gemini / OpenRouter / Azure  (conversation)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, httpx, re, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Grandure Hotel Concierge", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MCP_BASE = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
BASE_DIR  = os.path.dirname(__file__)

# ── Load RAG files ────────────────────────────────────────────
def load_rag(name):
    path = os.path.join(BASE_DIR, "rag", name)
    try:
        with open(path, encoding="utf-8") as f: return f.read()
    except FileNotFoundError:
        return f"[RAG file not found: {name}]"

RAG_ROOMS = load_rag("room_types.md")
RAG_SPA   = load_rag("spa_menu.md")
RAG_FNB   = load_rag("fnb_menu.md")
print(f"RAG loaded: rooms={len(RAG_ROOMS):,} spa={len(RAG_SPA):,} fnb={len(RAG_FNB):,} chars")

# ── MCP caller ────────────────────────────────────────────────
async def mcp(tool: str, params: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{MCP_BASE}/tools/{tool}", params=params)
            return r.json() if r.is_success else {"error": f"{r.status_code}: {r.text[:150]}"}
    except Exception as e:
        return {"error": f"MCP offline: {e}. Run: python mcp_server.py"}

def fmt(d: dict) -> str:
    if "error" in d: return f"Error: {d['error']}"
    lines = []
    for k, v in d.items():
        if k == "tool": continue
        if isinstance(v, list):
            lines.append(f"{k} ({len(v)} items):")
            for item in v[:4]:
                lines.append("  " + str(item) if not isinstance(item, dict)
                              else "  " + ", ".join(f"{ki}={vi}" for ki,vi in item.items() if vi is not None))
        elif isinstance(v, dict):
            lines.append(f"{k}: " + str(v)[:120])
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)

# ── QR tag parser ────────────────────────────────────────────
QR_RE = re.compile(r'\[QR:\s*(.*?)\]\s*$', re.DOTALL | re.IGNORECASE)

def extract_qr(raw: str) -> tuple[str, list[str]]:
    m = QR_RE.search(raw)
    if not m:
        return raw.rstrip(), []
    clean = raw[:m.start()].rstrip()
    opts  = [o.strip().strip('"').strip("'") for o in m.group(1).split('|')]
    return clean, [o for o in opts if o]

# ── Intent detection → MCP enrichment ────────────────────────
DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    r"|june|july|january|february|march|april|august|september|october|november|december)(?:\s+2026)?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|june|july)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+2026)?|"
    r"2026-0[6-9]-\d{2})\b", re.IGNORECASE
)

TREATMENT_MAP = {
    "swedish":              "Swedish Massage",
    "deep tissue":          "Deep Tissue Massage",
    "hot stone":            "Hot Stone Massage",
    "aromatherapy":         "Aromatherapy Massage",
    "sports recovery":      "Sports Recovery Massage",
    "dual suite":           "Dual Suite Massage",
    "couples":              "Dual Suite Massage",
    "classic facial":       "Classic Facial",
    "anti-ageing":          "Anti-Ageing Facial",
    "anti ageing":          "Anti-Ageing Facial",
    "hydrating facial":     "Hydrating Facial",
    "brightening":          "Brightening Facial",
    "body scrub":           "Body Scrub and Wrap",
    "hydrotherapy":         "Hydrotherapy Bath",
    "detox wrap":           "Detox Body Wrap",
    "manicure":             "Classic Manicure",
    "pedicure":             "Classic Pedicure",
    "luxury mani":          "Luxury Manicure Pedicure",
    "hammam":               "Hammam Ritual",
    "detox ritual":         "Detox Ritual",
    "signature":            "Signature Grandure Journey",
}

ITEM_MAP = {
    "mojito":               "Classic Mojito",
    "espresso martini":     "Espresso Martini",
    "negroni":              "Negroni",
    "ribeye":               "8oz Ribeye Steak",
    "sea bass":             "Pan-Seared Sea Bass",
    "champagne":            "Moet and Chandon Brut NV Glass",
    "whispering angel":     "Whispering Angel Rose Bottle",
    "rose wine":            "Whispering Angel Rose Bottle",
    "risotto":              "Wild Mushroom Risotto",
    "pavlova":              "Fruit Pavlova",
    "fondant":              "Chocolate Fondant",
    "aperol spritz":        "Aperol Spritz",
    "club sandwich":        "Classic Club Sandwich",
    "caesar salad":         "Caesar Salad",
    "halloumi fries":       "Halloumi Fries",
    "collagen":             "Collagen Beauty Drink",
    "turmeric latte":       "Turmeric Golden Latte",
}

async def enrich(messages: list[dict]) -> str:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    ll   = last.lower()
    ctx  = []

    # Search last message first, then fall back to conversation history
    m = DATE_RE.search(last)
    if not m:
        for msg in reversed(messages):
            m = DATE_RE.search(msg.get("content", ""))
            if m:
                break
    date_str = m.group(0) if m else None

    # Extract nights and pax from full conversation history
    nights_match = None
    pax_match    = None
    for msg in reversed(messages):
        content = msg.get("content","")
        if not nights_match:
            nights_match = re.search(r"(\d+)\s+night", content, re.I)
        if not pax_match:
            pax_match = re.search(r"(\d+)\s*(?:guest|person|people|adult|pax|of\s+us)", content, re.I)
        if nights_match and pax_match:
            break
    nights = int(nights_match.group(1)) if nights_match else 1
    pax    = int(pax_match.group(1))    if pax_match    else 1

    # Room lookup
    if date_str and any(w in ll for w in ["room","suite","available","book","reserve","stay","night","check in","guest"]):
        rt    = next(({"presidential":"Presidential Suite","junior":"Junior Suite",
                       "superior":"Superior","standard":"Standard"}[k]
                      for k in ["presidential","junior","superior","standard"] if k in ll), None)
        p = {"date": date_str, "nights": nights, "guests": pax}
        if rt: p["room_type"] = rt
        ctx.append(f"[LIVE ROOM AVAILABILITY]\n{fmt(await mcp('check_room_availability', p))}")

    # Spa lookup
    if date_str and any(w in ll for w in ["spa","massage","facial","treatment","therapist","ritual","manicure","pedicure"]):
        tx   = next((TREATMENT_MAP[k] for k in TREATMENT_MAP if k in ll), None)
        gend = ("Female" if any(w in ll for w in ["female","lady","woman"]) else
                "Male"   if "male" in ll else None)
        tod  = ("morning" if "morning" in ll else "afternoon" if "afternoon" in ll else None)
        p    = {"date": date_str}
        if tx:   p["treatment"]         = tx
        if gend: p["gender_preference"] = gend
        if tod:  p["time_of_day"]       = tod
        ctx.append(f"[LIVE SPA AVAILABILITY]\n{fmt(await mcp('check_spa_availability', p))}")

    # F&B price lookup
    if date_str and any(w in ll for w in ["price","cost","how much","cocktail","wine","champagne","bottle","glass"]):
        item = next((ITEM_MAP[k] for k in ITEM_MAP if k in ll), None)
        if item:
            ctx.append(f"[LIVE F&B PRICE]\n{fmt(await mcp('check_fnb_price', {'item':item,'date':date_str}))}")

    return "\n\n".join(ctx)

# ── System prompt (instructions + RAG — no live data) ─────────
BASE_SYSTEM = f"""You are Lisa, the personal concierge at Grandure Hotel for guests.
HOTEL NAME: Grandure Hotel. Never say Grand Azure.

OUTPUT FORMAT — ABSOLUTE RULE
Replies are natural language only. Never output JSON, code blocks, function calls, LIVE_DATA_REQUEST, or any structured format.

BREVITY — ABSOLUTE RULE
Each reply is 2 sentences maximum. One optional question at the end. No bullet points or lists ever.
One evocative sentence describing the experience, then the price. Nothing more.
Never summarise what the guest already said.

YOUR DEFAULT MODE IS TO DESCRIBE AND DELIGHT
Recommend immediately from the RAG knowledge below. Do not interrogate the guest before giving a recommendation.
When a guest mentions an occasion, a room type, a treatment, or a venue — describe it beautifully and state the price.
Trust the RAG. Make the recommendation first. Ask for details only when the guest is ready to confirm a reservation.

WHEN TO ASK FOR BOOKING DETAILS
ONLY enter booking-collection mode when the guest explicitly says "book", "reserve", "I'd like to stay", or "how do I book".
In every other situation — exploring rooms, asking about spa, asking about dining, asking about occasions — do NOT ask for dates or numbers. Just describe and recommend.

NEVER ask for check-in date when the guest is only asking about spa or dining.
NEVER ask for two pieces of information in the same reply.
NEVER repeat a question already answered in the conversation history.

BOOKING FLOW (only triggered by explicit booking intent)
Collect one piece of missing information per reply, in this order:
  Step 1 — If no check-in date in history: ask "What date would you like to check in?"
  Step 2 — If no number of nights in history: ask "How many nights will you be staying?"
  Step 3 — If no number of guests in history: ask "How many guests will be joining you?"
  Step 4 — Once date, nights, and guests are all known AND LIVE AVAILABILITY DATA appears above:
            Confirm one specific available room. State type, floor, nightly rate, and total cost.
After room confirmed → suggest one spa treatment matching the occasion (from RAG).
After spa addressed → suggest one dining option (from RAG).
After dining addressed → ask for email and give the summary.

SUMMARY FORMAT (only at the very end)
Room: [type] [floor] [dates] [price/night]
Spa: [treatment] [date] [time] [price]
Dining: [outlet] [date] [time] [item]
End: Is there anything else I can arrange?

Never start a reply with Yes, Absolutely, Of course, Certainly, or Great.
Never use em-dashes. Say Dual Suite Massage not Couples Massage.
Never put therapist name and treatment in the same sentence.
Tone: warm, confident, specific. Name the price always.

QUICK REPLY SUGGESTIONS — MANDATORY
After every reply, on a new final line, write exactly:
[QR: "phrase 1" | "phrase 2" | "phrase 3"]
Rules:
- Short phrases the GUEST would say next — 5 words or fewer, natural speech.
- Spa enquiry → treatment names. Dining → venue or dish. Room exploring → room type or occasion.
- After asking for check-in date → suggest specific dates: "June 10", "June 14", "July 5".
- After asking for nights → suggest durations: "2 nights", "3 nights", "5 nights".
- After asking for guests → suggest counts: "1 guest", "2 guests", "4 guests".
- NEVER suggest system actions ("send email", "confirm booking").
- OMIT entirely when asking for email or presenting the final summary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAG — ROOM TYPES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{RAG_ROOMS}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAG — SPA TREATMENTS AND THERAPISTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{RAG_SPA}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAG — FOOD AND BEVERAGE MENU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{RAG_FNB}"""

print(f"Base system prompt: {len(BASE_SYSTEM):,} chars")

PROVIDER_DEFAULTS = {
    "claude":     "claude-opus-4-5",
    "gemini":     "gemini-2.0-flash",
    "openrouter": "google/gemma-3-27b-it",
    "azure":      os.getenv("AZURE_MODEL_NAME","gpt-4o"),
}

class ChatRequest(BaseModel):
    messages: list[dict]
    provider: str = "claude"
    model: Optional[str] = None

class EmailRequest(BaseModel):
    to: str
    summary: str
    guest_name: Optional[str] = None

# ── Provider calls ────────────────────────────────────────────
async def call_claude(msgs, model, system):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key: raise HTTPException(400,"ANTHROPIC_API_KEY not set")
    h = [m for m in msgs if m["role"] in ("user","assistant")][-6:]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":model,"max_tokens":400,"system":system,"messages":h})
    if not r.is_success: raise HTTPException(r.status_code,f"Claude: {r.text[:300]}")
    return r.json()["content"][0]["text"]

async def call_gemini(msgs, model, system):
    key = os.getenv("GOOGLE_API_KEY")
    if not key: raise HTTPException(400,"GOOGLE_API_KEY not set")
    h = [m for m in msgs if m["role"] in ("user","assistant")][-6:]
    contents = [{"role":"model" if m["role"]=="assistant" else "user","parts":[{"text":m["content"]}]} for m in h]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            json={"system_instruction":{"parts":[{"text":system}]},"contents":contents,
                  "generationConfig":{"maxOutputTokens":400,"temperature":0.7}})
    if not r.is_success: raise HTTPException(r.status_code,f"Gemini: {r.text[:300]}")
    try: return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except: raise HTTPException(500,str(r.json())[:300])

async def call_openrouter(msgs, model, system):
    key = os.getenv("OPENROUTER_API_KEY")
    if not key: raise HTTPException(400,"OPENROUTER_API_KEY not set")
    h    = [m for m in msgs if m["role"] in ("user","assistant")][-6:]
    full = [{"role":"system","content":system}] + h
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization":f"Bearer {key}","Content-Type":"application/json",
                     "HTTP-Referer":"https://grandurehotel.com","X-Title":"Grandure Concierge"},
            json={"model":model,"messages":full,"max_tokens":400,"temperature":0.7})
    if not r.is_success: raise HTTPException(r.status_code,f"OpenRouter: {r.text[:300]}")
    try: return r.json()["choices"][0]["message"]["content"]
    except: raise HTTPException(500,str(r.json())[:300])

async def call_azure(msgs, model, system):
    ep,key = os.getenv("AZURE_AI_ENDPOINT"), os.getenv("AZURE_AI_API_KEY")
    if not ep or not key: raise HTTPException(400,"AZURE credentials not set")
    h    = [m for m in msgs if m["role"] in ("user","assistant")][-6:]
    full = [{"role":"system","content":system}] + h
    url  = f"{ep}/openai/deployments/{model}/chat/completions?api-version=2024-08-01-preview"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url,headers={"api-key":key,"Content-Type":"application/json"},
                         json={"messages":full,"max_tokens":400,"temperature":0.7})
    if not r.is_success: raise HTTPException(r.status_code,f"Azure: {r.text[:300]}")
    try: return r.json()["choices"][0]["message"]["content"]
    except: raise HTTPException(500,str(r.json())[:300])

PROVIDERS = {"claude":call_claude,"gemini":call_gemini,"openrouter":call_openrouter,"azure":call_azure}

# ── Routes ────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"hotel":"Grandure","status":"ok","architecture":"RAG + MCP + LLM","providers":list(PROVIDERS.keys())}

@app.post("/api/chat")
async def chat(req: ChatRequest):
    prov = req.provider.lower()
    if prov not in PROVIDERS:
        raise HTTPException(400,f"Unknown provider. Use: {list(PROVIDERS.keys())}")
    model = req.model or PROVIDER_DEFAULTS[prov]

    # 1 — Enrich with live MCP data if needed
    live_ctx = await enrich(req.messages)

    # 2 — Build final system prompt (base + live data on top)
    system = BASE_SYSTEM
    if live_ctx:
        system = (f"LIVE AVAILABILITY DATA — USE THESE EXACT FIGURES\n"
                  f"{'='*50}\n{live_ctx}\n{'='*50}\n\n") + BASE_SYSTEM

    # 3 — Call LLM
    try:
        raw = await PROVIDERS[prov](req.messages, model, system)
        # Strip any rogue structured output the model may have improvised
        raw = re.sub(r'LIVE_DATA_REQUEST:\s*\{[^}]*\}', '', raw, flags=re.DOTALL)
        raw = re.sub(r'```[a-z]*\n.*?```', '', raw, flags=re.DOTALL)
        raw = raw.strip()
        reply, quick_replies = extract_qr(raw)
        return {"reply":reply,"provider":prov,"model":model,"mcp_enriched":bool(live_ctx),"quick_replies":quick_replies}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500,str(e))

@app.get("/api/mcp/status")
async def mcp_status():
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{MCP_BASE}/tools")
            return {"online":r.is_success,"tools":[t["name"] for t in r.json().get("tools",[])] if r.is_success else []}
    except:
        return {"online":False,"message":"Start mcp_server.py on port 8001"}

@app.post("/api/send-email")
async def send_booking_email(req: EmailRequest):
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    email_from = os.getenv("EMAIL_FROM")
    email_password = os.getenv("EMAIL_PASSWORD")
    if not email_from or not email_password:
        raise HTTPException(400, "Email not configured. Add EMAIL_FROM and EMAIL_PASSWORD to .env")

    greeting = f"Dear {req.guest_name}," if req.guest_name else "Dear Guest,"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#040302;font-family:Georgia,serif;">
<div style="max-width:580px;margin:0 auto;padding:56px 40px;">
  <div style="font-size:26px;letter-spacing:0.22em;color:#fff;font-family:Georgia,serif;">GRANDURE</div>
  <div style="font-size:8px;letter-spacing:0.38em;color:#C9A84C;font-family:Arial,sans-serif;text-transform:uppercase;margin-top:6px;">Knightsbridge · London SW1</div>
  <div style="border-top:1px solid rgba(201,168,76,0.2);margin:36px 0;"></div>
  <p style="font-size:17px;color:rgba(240,235,224,0.85);font-style:italic;line-height:1.7;margin:0 0 28px;">{greeting}</p>
  <p style="font-size:15px;color:rgba(240,235,224,0.55);font-style:italic;line-height:1.7;margin:0 0 32px;">
    Thank you for choosing Grandure. Here is a summary of your arrangements, as discussed with your personal concierge.
  </p>
  <div style="background:rgba(201,168,76,0.06);border:1px solid rgba(201,168,76,0.15);border-left:3px solid #C9A84C;padding:28px 32px;">
    <pre style="font-family:Georgia,serif;font-size:15px;color:rgba(240,235,224,0.82);white-space:pre-wrap;line-height:1.9;margin:0;">{req.summary}</pre>
  </div>
  <div style="border-top:1px solid rgba(201,168,76,0.2);margin:40px 0 28px;"></div>
  <p style="font-family:Arial,sans-serif;font-size:10px;color:rgba(240,235,224,0.25);letter-spacing:0.1em;line-height:1.9;margin:0;">
    Grandure Hotel · Knightsbridge · London SW1<br/>
    concierge@grandure.com · +44 (0) 20 0000 0000<br/><br/>
    Your concierge team looks forward to welcoming you.
  </p>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Grandure Booking Summary"
    msg["From"]    = f"Grandure Hotel <{email_from}>"
    msg["To"]      = req.to
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.login(email_from, email_password)
            server.sendmail(email_from, req.to, msg.as_string())
    except Exception as e:
        raise HTTPException(500, f"Email failed: {e}")

    return {"sent": True, "to": req.to}

@app.get("/api/providers")
def providers_info():
    km = {"claude":"ANTHROPIC_API_KEY","gemini":"GOOGLE_API_KEY","openrouter":"OPENROUTER_API_KEY","azure":"AZURE_AI_API_KEY"}
    return {p:{"model":PROVIDER_DEFAULTS[p],"configured":bool(os.getenv(km[p]))} for p in PROVIDERS}
