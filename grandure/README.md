# Grandure Hotel — AI Concierge System

> Luxury hotel concierge powered by RAG + MCP + multi-provider LLM
> Shore programme for Celestia Cruises · June 2026

---

## Project Structure

```
grandure/
├── frontend/
│   └── index.html              # Hotel website + embedded concierge chat
│
├── backend/
│   ├── main.py                 # Concierge API — RAG + MCP + LLM router
│   ├── mcp_server.py           # MCP availability server (port 8001)
│   ├── agent_instruction.txt   # Agent system prompt (v10)
│   ├── requirements.txt        # Python dependencies
│   ├── .env.example            # API key template — copy to .env
│   │
│   ├── rag/                    # Static knowledge (what the hotel offers)
│   │   ├── room_types.md       # Room types, features, base prices
│   │   ├── spa_menu.md         # Treatments, therapists, base prices
│   │   └── fnb_menu.md         # Outlets, full menu, base prices
│   │
│   └── data/                   # Live availability data (June 2026)
│       ├── hotel_lookup.txt    # Room availability — 30 days × 4 room types
│       ├── spa_lookup.txt      # Therapist schedules — 30 days × 8 therapists
│       └── fnb_lookup.txt      # Menu items and daily pricing
│
└── docs/
    └── ARCHITECTURE.md         # System design explanation
```

---

## Architecture

```
Guest message
     │
     ▼
main.py  ──── intent detection ────► mcp_server.py (port 8001)
     │                                    │
     │        RAG knowledge              │  Live availability
     │        rag/room_types.md          │  data/hotel_lookup.txt
     │        rag/spa_menu.md            │  data/spa_lookup.txt
     │        rag/fnb_menu.md            │  data/fnb_lookup.txt
     │              │                    │
     │              └──── combined ──────┘
     │                       │
     ▼                       ▼
   LLM (Claude / Gemini / Gemma / Azure)
     │
     ▼
   Concierge reply
```

**RAG** — The three `.md` files in `rag/` tell the model what the hotel offers:
room types, spa treatments, menu items, therapist names, and base prices.
This is loaded once into the system prompt.

**MCP** — The `mcp_server.py` holds actual June 2026 availability.
When the guest mentions a date, `main.py` calls the MCP server and
injects the live result into the prompt before the LLM replies.
The LLM never invents availability — it only uses what MCP returns.

**LLM** — The conversation intelligence. Supports four providers
switchable at runtime: Claude, Gemini, Gemma (via OpenRouter), Azure.

---

## Quick Start

### Step 1 — Install dependencies

```bash
cd grandure/backend
pip install -r requirements.txt
```

### Step 2 — Configure API keys

```bash
cp .env.example .env
```

Edit `.env` and add at least one provider key:

```env
ANTHROPIC_API_KEY=your-key      # Claude
GOOGLE_API_KEY=your-key         # Gemini
OPENROUTER_API_KEY=your-key     # Gemma / Llama / Mistral
AZURE_AI_ENDPOINT=https://...   # Azure
AZURE_AI_API_KEY=your-key
AZURE_MODEL_NAME=gpt-4o
```

### Step 3 — Start the MCP server (Terminal 1)

```bash
cd grandure/backend
python -m uvicorn mcp_server:app --port 8001
```

### Step 4 — Start the concierge API (Terminal 2)

```bash
cd grandure/backend
python -m uvicorn main:app --reload --port 8000
```

### Step 5 — Open the website

Open `grandure/frontend/index.html` in your browser.
Click the **Concierge** button bottom-right to start chatting.

---

## API Endpoints

### Concierge API (port 8000)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| POST | `/api/chat` | Send message to concierge |
| GET | `/api/providers` | List configured AI providers |
| GET | `/api/mcp/status` | Check MCP server connectivity |

**POST /api/chat request body:**
```json
{
  "messages": [{"role": "user", "content": "I need a room for June 14"}],
  "provider": "claude",
  "model": "claude-opus-4-5"
}
```

### MCP Server (port 8001)

| Method | Path | Description |
|---|---|---|
| GET | `/tools` | List all available tools |
| GET | `/tools/check_room_availability` | Room availability by date |
| GET | `/tools/check_spa_availability` | Spa slots by date and treatment |
| GET | `/tools/check_fnb_price` | F&B item price by date |
| GET | `/tools/get_monthly_summary` | June 2026 occupancy overview |

**Example MCP call:**
```
GET /tools/check_room_availability?date=June+14&room_type=Junior+Suite&nights=3
GET /tools/check_spa_availability?date=June+14&treatment=Deep+Tissue+Massage&time_of_day=afternoon
GET /tools/check_fnb_price?item=Classic+Mojito&date=June+21
```

---

## Switching AI Providers

Click the ⚡ icon in the chat header to switch provider.
Or send a request directly:

```json
{"messages": [...], "provider": "gemini", "model": "gemini-2.0-flash"}
{"messages": [...], "provider": "openrouter", "model": "google/gemma-3-27b-it"}
{"messages": [...], "provider": "claude", "model": "claude-opus-4-5"}
{"messages": [...], "provider": "azure", "model": "gpt-4o"}
```

---

## Data Coverage

All inventory data covers **June 2026 only**.

| File | Records | Description |
|---|---|---|
| `data/hotel_lookup.txt` | 120 records | 4 room types × 30 days |
| `data/spa_lookup.txt` | 240 records | 8 therapists × 30 days |
| `data/fnb_lookup.txt` | 57 items | Full menu with base prices |

---

## API Keys — Where to Get Them

| Provider | URL | Notes |
|---|---|---|
| Claude | console.anthropic.com | Create API key in account settings |
| Gemini | aistudio.google.com/app/apikey | Free tier available |
| OpenRouter | openrouter.ai/keys | Pay-per-use, access to Gemma/Llama/Mistral |
| Azure | portal.azure.com | Requires AI Foundry resource |
to run local 
Next steps:
  1. cd grandure / backend
  2. copy .env.example .env  (then add your API key)
  3. pip install -r requirements.txt
  4. python -m uvicorn mcp_server:app --port 8001  [Terminal 1]
  5. python -m uvicorn main:app --reload --port 8000  [Terminal 2]
  6. Open frontend/index.html in your browser
cd c:/AzureAI/HotelPlanner/grandure/backend
py -3.12 -m venv .venv
source .venv/Scripts/activate   # or: .venv\Scripts\activate in cmd
pip install -r requirements.txt
