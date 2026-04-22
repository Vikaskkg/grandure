# Grandure Hotel — System Architecture

## Why RAG + MCP (not one or the other)

### The problem with RAG alone
If you upload availability data to a RAG index, the model retrieves chunks
and may hallucinate availability based on similar-looking chunks.
It also cannot calculate peak surcharges, filter by therapist gender,
or find two therapists both free at the same time.

### The problem with MCP alone
If the model only has tool calls and no background knowledge,
it does not know what treatments exist, what the price range is,
or what to recommend for a honeymoon vs a birthday.
It would have to call a tool for every possible item just to describe the menu.

### The solution: RAG for knowledge, MCP for availability

```
RAG answers:     "What do we offer?"
MCP answers:     "Is it available on this date at this price?"
LLM combines:    "Here is what I recommend and why, with the exact price and slot"
```

---

## Data Flow

```
1. Guest: "3 nights from June 14, anniversary"

2. main.py detects:
   - date mentioned: June 14
   - room intent: "night" keyword
   - no room type specified

3. main.py calls MCP:
   GET /tools/check_room_availability?date=June+14&nights=3
   Response: Junior Suite AVAILABLE £495/night (peak)
             Presidential Suite BOOKED
             Superior AVAILABLE £264/night
             Standard AVAILABLE £156/night

4. main.py builds system prompt:
   [LIVE DATA] Junior Suite £495, Superior £264 on June 14
   +
   [RAG] Junior Suite features, what it includes, occasion guide
   +
   [INSTRUCTIONS] warm concierge tone, one recommendation only

5. LLM receives full context and replies:
   "For an anniversary I would suggest the Junior Suite on Floor 4 —
    city and pool views, a king bed, and a separate seating area.
    For your three nights from the 14th that comes to £495 per night.
    Shall I reserve that for you?"

6. No hallucination. Exact live price. Correct recommendation.
```

---

## MCP Tool Specifications

### check_room_availability
```
Input:  date (required), room_type (optional), nights (optional, default 1)
Output: list of room types with status AVAILABLE/BOOKED, tonight price,
        total cost for stay, peak/weekend flags
```

### check_spa_availability
```
Input:  date (required), treatment (optional), gender_preference (optional),
        time_of_day (optional: morning/afternoon), preferred_time (optional: HH:MM)
Output: list of therapists with available slots, treatment price for the date,
        peak surcharge percentage
        Special case for Dual Suite Massage: returns pairs of therapists
        who are both free at the same time
```

### check_fnb_price
```
Input:  item (required, fuzzy matched), date (required)
Output: base price, today's price with surcharge applied,
        outlet list, peak/weekend flags
```

### get_monthly_summary
```
Input:  room_type (optional filter)
Output: per-date occupancy percentage and average available price
        for all 30 days of June 2026
```

---

## Surcharge Logic (applied by MCP server)

```python
if date in PEAK_DATES:    multiplier = 1.20  # +20%
elif weekday >= Friday:   multiplier = 1.10  # +10%
else:                     multiplier = 1.00  # base

# Hotel rooms: round to nearest £5
# F&B items: round to nearest £0.50
```

Peak dates June 2026: 6, 7, 13, 14, 19, 20, 21, 26, 27, 28

---

## Extending the System

### Add a new AI provider
In `backend/main.py`, add a new async function following the pattern
of `call_claude`, `call_gemini`, etc., then register it in `PROVIDERS`.

### Add a new MCP tool
In `backend/mcp_server.py`, add a new `@app.get("/tools/your_tool")` endpoint
and register it in the `/tools` list endpoint.

### Update availability data
Edit the `.txt` files in `backend/data/`. The MCP server parses them
at startup. Restart `mcp_server.py` after any data change.

### Update RAG knowledge
Edit the `.md` files in `backend/rag/`. The main API loads them
at startup. Restart `main.py` after any change.
