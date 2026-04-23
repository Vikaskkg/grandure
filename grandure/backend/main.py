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

_KW_ROOM = {"room","suite","floor","stay","night","bed","view","presidential","junior","superior","standard","book","reserve","check in","checkin"}
_KW_SPA  = {"spa","massage","facial","treatment","therapist","manicure","pedicure","ritual","wellness","hammam","scrub","swedish","deep tissue","hot stone","aromatherapy"}
_KW_FNB  = {"dining","food","restaurant","bar","drink","cocktail","wine","champagne","menu","breakfast","lunch","dinner","rooftop","lounge","mojito","espresso"}

def select_rag(messages: list[dict]) -> str:
    recent = " ".join(m.get("content","").lower() for m in messages[-6:])
    parts  = []
    if any(w in recent for w in _KW_ROOM):
        parts.append(f"RAG — ROOM TYPES\n{RAG_ROOMS}")
    if any(w in recent for w in _KW_SPA):
        parts.append(f"RAG — SPA\n{RAG_SPA}")
    if any(w in recent for w in _KW_FNB):
        parts.append(f"RAG — F&B\n{RAG_FNB}")
    return ("\n\n" + "━"*48 + "\n").join(parts) if parts else f"RAG — ROOM TYPES\n{RAG_ROOMS}"

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

# ── System prompt — instructions only; RAG injected per-request ──
BASE_SYSTEM = """You are Lisa, the personal concierge at Grandure Hotel.
HOTEL NAME: Grandure Hotel. Never say Grand Azure or Azure Hotel.

── FORMAT ──────────────────────────────────────────
Plain prose only. No JSON, no lists, no bullet points, no code blocks.
Maximum 2 sentences per reply, plus one question if needed.
Name the price in every reply that mentions a service.
Never start with: Yes / Absolutely / Of course / Certainly / Great.
Never use em-dashes.

── DEFAULT BEHAVIOUR ────────────────────────────────
Browsing mode (guest is exploring, not booking):
  Describe the requested item beautifully in one sentence and state its price.
  Do NOT ask for dates, nights, or guest counts.
  Do NOT volunteer other items the guest has not asked about.
  If the guest has already heard about a topic this conversation, give only a
  one-line reminder + price — never repeat the full description.

── HOW TO DETECT WHICH FLOW TO ENTER ───────────────
Read the guest's intent carefully before every reply.
• "book / reserve / stay / check in" + room context  → ROOM BOOKING FLOW
• "book / reserve / I'd like" + spa/treatment context → SPA BOOKING FLOW
• "book / reserve / table / dinner" + dining context  → DINING BOOKING FLOW
• Anything else → BROWSING MODE (describe + price, no questions)
Never mix flows. Once a flow is started, stay in it until the summary is done.

── ROOM BOOKING FLOW ────────────────────────────────
Collect one missing item per reply, in this strict order:
  R1. No check-in date → ask: "What date would you like to check in?"
  R2. No number of nights → ask: "How many nights will you be staying?"
  R3. No number of guests → ask: "How many guests will be joining you?"
  R4. All three known + LIVE ROOM DATA present →
        Confirm room: type, floor, nightly rate, total cost. One sentence.
  R5. Spa not yet discussed → name ONE treatment + price. One sentence. Skip if already discussed.
  R6. Dining not yet discussed → name ONE venue + signature dish/drink + price. One sentence. Skip if already discussed.
  R7. Ask for email address.
  R8. Output room summary and stop.

── SPA BOOKING FLOW ─────────────────────────────────
Triggered when guest intent includes booking a spa treatment.
NEVER ask for nights or number of guests in this flow.

CRITICAL — scan ALL previous messages before each step. An item is already known if it appears
ANYWHERE in the conversation history, not just the last message. Do not ask for something already given.

Valid treatment names (recognise any of these): Swedish Massage · Deep Tissue Massage · Hot Stone Massage ·
Aromatherapy Massage · Sports Recovery Massage · Dual Suite Massage · Classic Facial · Anti-Ageing Facial ·
Hydrating Facial · Brightening Facial · Body Scrub and Wrap · Hydrotherapy Bath · Detox Body Wrap ·
Classic Manicure · Classic Pedicure · Luxury Manicure Pedicure · Hammam Ritual · Detox Ritual ·
Signature Grandure Journey.

Collect one missing item per reply, in this strict order:
  S1. No treatment found anywhere in conversation →
        Ask: "Which treatment would you like to book?"
  S2. No date found anywhere in conversation →
        Ask: "What date would you like your treatment?"
  S3. No preferred time found anywhere in conversation →
        Ask: "Do you prefer morning or afternoon — or do you have a specific time in mind?"
  S4. No therapist preference found anywhere in conversation →
        Ask: "Do you have a preferred therapist, or shall I arrange our best match for you?"
  S5. Treatment + date + time + therapist all known, LIVE SPA DATA present →
        Confirm: treatment, date, time, therapist, price. One sentence.
  S6. Ask for email address. OMIT quick replies at this step.
  S7. Output spa summary and stop. OMIT quick replies at this step.

── DINING BOOKING FLOW ──────────────────────────────
Triggered when guest intent includes booking a dining experience.
NEVER ask for check-in nights in this flow.

CRITICAL — scan ALL previous messages before each step. An item is already known if it appears
ANYWHERE in the conversation history. Do not ask for something already given.

Collect one missing item per reply, in this strict order:
  D1. No venue found in conversation →
        Ask: "Which venue would you prefer — the Grandure Restaurant, Rooftop Lounge, or Pool Bar?"
  D2. No date found in conversation →
        Ask: "What date would you like to dine?"
  D3. No preferred time found in conversation →
        Ask: "What time works best for you?"
  D4. No number of covers found in conversation →
        Ask: "How many guests will be dining?"
  D5. Venue + date + time + covers all known →
        Confirm venue, date, time, covers. One sentence.
  D6. Ask for email address. OMIT quick replies at this step.
  D7. Output dining summary and stop. OMIT quick replies at this step.

── SUMMARY FORMAT ───────────────────────────────────
Room summary:
  Room: [type] · Floor [n] · [check-in] for [x] nights · £[total]
  Spa: [treatment] · [date] · [time] · [therapist] · £[price]       ← omit if not booked
  Dining: [venue] · [date] · [time] · [covers] covers                ← omit if not booked
  Is there anything else I can arrange?

Spa-only summary:
  Spa: [treatment] · [date] · [time] · [therapist] · £[price]
  Is there anything else I can arrange?

Dining-only summary:
  Dining: [venue] · [date] · [time] · [covers] covers
  Is there anything else I can arrange?

── QUICK REPLIES (mandatory, every reply) ───────────
Last line of every reply must be exactly:
[QR: "option 1" | "option 2" | "option 3"]
- Phrases the guest would say next, 5 words or fewer.
- Browsing → match the topic (treatment names / room types / venue names).
- After R1 / S2 / D2 (date question) → "June 10" | "June 14" | "July 5"
- After R2 (nights question) → "2 nights" | "3 nights" | "5 nights"
- After R3 (guests question) → "Just me" | "2 guests" | "4 guests"
- After S1 (treatment question) → 3 treatment names from the valid list above.
- After S3 (spa time question) → "9am" | "11am" | "2pm" | "4pm"
- After S4 (therapist question) → "Mei Lin" | "James" | "No preference"
- After D1 (venue question) → "Grandure Restaurant" | "Rooftop Lounge" | "Pool Bar"
- After D3 (dining time question) → "7pm" | "7:30pm" | "8pm"
- After D4 (covers question) → "Just me" | "2 guests" | "4 guests"
- OMIT entirely when asking for email or outputting any summary.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KNOWLEDGE BASE (injected below)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

print(f"Base system prompt: {len(BASE_SYSTEM):,} chars")

PROVIDER_DEFAULTS = {
    "claude":     "claude-haiku-4-5-20251001",
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
            json={"model":model,"max_tokens":220,"system":system,"messages":h})
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

    # 2 — Build system: instructions + selective RAG + optional live data
    rag    = select_rag(req.messages)
    system = BASE_SYSTEM + "\n" + rag
    if live_ctx:
        system = (f"LIVE AVAILABILITY DATA — USE THESE EXACT FIGURES\n"
                  f"{'='*50}\n{live_ctx}\n{'='*50}\n\n") + system

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
  <div style="font-size:8px;letter-spacing:0.38em;color:#C9A84C;font-family:Arial,sans-serif;text-transform:uppercase;margin-top:6px;">A Private Address of Singular Luxury</div>
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
    Grandure Hotel<br/>
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
