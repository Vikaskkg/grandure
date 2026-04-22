"""
mcp_server.py  —  Grandure Hotel MCP Availability Server
Exposes three tools for live availability and pricing lookups:
  - check_room_availability(date, room_type)
  - check_spa_availability(date, treatment, gender_preference)
  - check_fnb_price(item_name, date)

Run alongside main.py:
  python mcp_server.py          (default port 8001)
  python mcp_server.py --port 9000

The concierge backend (main.py) calls this server when it needs
live availability data. RAG handles what exists; MCP handles what's free.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import os, random
from datetime import date as dt, datetime
from typing import Optional

app = FastAPI(title="Grandure MCP Availability Server", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Load compact inventory files ──────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def load(name):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

HOTEL_RAW = load("hotel_lookup.txt")
SPA_RAW   = load("spa_lookup.txt")
FNB_RAW   = load("fnb_lookup.txt")

# ── Parse hotel availability ──────────────────────────────────
def parse_hotel_data() -> dict:
    """
    Returns: {date_str: {room_type: {status, price_gbp}}}
    e.g. {"01 Jun 2026": {"Presidential Suite": {"status":"AVAILABLE","price":1200}}}
    """
    result = {}
    current_date = None
    for line in HOTEL_RAW.splitlines():
        line = line.strip()
        if line.startswith("DATE "):
            # e.g. "DATE 01 Jun 2026 Mon"
            parts = line.split()
            if len(parts) >= 4:
                current_date = f"{parts[1]} {parts[2]} {parts[3]}"
                result[current_date] = {}
        elif current_date and line:
            # e.g. "Presidential Suite Floor 5 Ocean view King + King 4guests GBP1200 BOOKED"
            for rtype in ["Presidential Suite","Junior Suite","Superior","Standard"]:
                if line.startswith(rtype):
                    status = "BOOKED" if "BOOKED" in line else "AVAILABLE"
                    price  = 0
                    for part in line.split():
                        if part.startswith("GBP"):
                            try: price = int(part[3:])
                            except: pass
                    result[current_date][rtype] = {"status": status, "price_gbp": price}
                    break
    return result

# ── Parse spa availability ────────────────────────────────────
def parse_spa_data() -> dict:
    """
    Returns: {date_str: {therapist: {gender, specs, slots:[]}}}
    """
    result = {}
    current_date = None
    for line in SPA_RAW.splitlines():
        line = line.strip()
        if line.startswith("DATE "):
            parts = line.split()
            if len(parts) >= 4:
                current_date = f"{parts[1]} {parts[2]} {parts[3]}"
                result[current_date] = {}
        elif current_date and line and not line.startswith("PEAK") and not line.startswith("SPA") and not line.startswith("GRAND"):
            parts = line.split()
            if len(parts) >= 4:
                name   = parts[0]
                gender = parts[1] if parts[1] in ("M","F") else "?"
                gender = "Male" if gender == "M" else "Female"
                if "DAYOFF" in parts:
                    result[current_date][name] = {"gender":gender,"specs":[],"slots":[],"day_off":True}
                elif "AVAILABLE" in parts:
                    idx   = parts.index("AVAILABLE")
                    slots = parts[idx+1:] if idx+1 < len(parts) else []
                    specs_raw = parts[2] if len(parts)>2 else ""
                    specs = specs_raw.split("/") if "/" in specs_raw else [specs_raw]
                    result[current_date][name] = {"gender":gender,"specs":specs,"slots":slots,"day_off":False}
    return result

# ── Parse F&B prices ──────────────────────────────────────────
def parse_fnb_data() -> dict:
    """
    Returns: {item_name: {base_price, category, outlets}}
    """
    result = {}
    for line in FNB_RAW.splitlines():
        if " | " in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                name = parts[0]
                cat  = parts[1] if len(parts) > 1 else ""
                try:
                    base = float(parts[2].replace("GBP","").replace("£","").strip())
                except:
                    continue
                outlets = parts[4] if len(parts) > 4 else "All"
                result[name] = {"base_price_gbp": base, "category": cat, "outlets": outlets}
    return result

# Pre-parse on startup
print("Parsing hotel data...")
HOTEL_DB = parse_hotel_data()
print(f"  {len(HOTEL_DB)} dates loaded")

print("Parsing spa data...")
SPA_DB = parse_spa_data()
print(f"  {len(SPA_DB)} dates loaded")

print("Parsing F&B data...")
FNB_DB = parse_fnb_data()
print(f"  {len(FNB_DB)} items loaded")

# ── Date normalisation ────────────────────────────────────────
def normalise_date(date_str: str) -> str:
    """
    Converts any reasonable date string to 'DD Mon YYYY' format.
    Accepts: '2026-06-14', 'June 14', '14 June', '14 Jun 2026', etc.
    """
    date_str = date_str.strip()
    MONTHS = {"january":"Jan","february":"Feb","march":"Mar","april":"Apr",
               "may":"May","june":"Jun","july":"Jul","august":"Aug",
               "september":"Sep","october":"Oct","november":"Nov","december":"Dec",
               "jan":"Jan","feb":"Feb","mar":"Mar","apr":"Apr","jun":"Jun",
               "jul":"Jul","aug":"Aug","sep":"Sep","oct":"Oct","nov":"Nov","dec":"Dec"}

    # Try ISO format first: 2026-06-14
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%d %b %Y")
    except: pass

    # Try DD Mon YYYY or DD Month YYYY
    for fmt in ["%d %b %Y","%d %B %Y","%d %b","%d %B"]:
        try:
            d = datetime.strptime(date_str, fmt)
            if d.year == 1900: d = d.replace(year=2026)
            return d.strftime("%d %b %Y")
        except: pass

    # Try Month DD (e.g. "June 14")
    parts = date_str.split()
    if len(parts) == 2:
        m = MONTHS.get(parts[0].lower())
        if m:
            try: return f"{int(parts[1]):02d} {m} 2026"
            except: pass
        m = MONTHS.get(parts[1].lower())
        if m:
            try: return f"{int(parts[0]):02d} {m} 2026"
            except: pass

    return date_str  # return as-is if nothing works


# ── Surcharge calculator ──────────────────────────────────────
PEAK_DAYS = {6,7,13,14,19,20,21,26,27,28}

def get_surcharge(date_key: str) -> float:
    parts = date_key.split()
    try:
        day = int(parts[0])
        d   = datetime.strptime(date_key, "%d %b %Y")
        if day in PEAK_DAYS:   return 1.20
        if d.weekday() >= 4:   return 1.10  # Fri=4,Sat=5,Sun=6
        return 1.0
    except:
        return 1.0

def is_peak(date_key: str) -> bool:
    try: return int(date_key.split()[0]) in PEAK_DAYS
    except: return False

def is_weekend(date_key: str) -> bool:
    try:
        d = datetime.strptime(date_key, "%d %b %Y")
        return d.weekday() >= 4
    except: return False


# ════════════════════════════════════════════════════════════════
# MCP TOOL 1: check_room_availability
# ════════════════════════════════════════════════════════════════
@app.get("/tools/check_room_availability")
def check_room_availability(
    date: str = Query(..., description="Date e.g. 2026-06-14 or June 14"),
    room_type: Optional[str] = Query(None, description="Presidential Suite, Junior Suite, Superior, or Standard"),
    nights: Optional[int] = Query(1, description="Number of nights"),
):
    """
    MCP Tool: Check which rooms are available on a given date.
    Returns availability status, price, and room details.
    """
    date_key = normalise_date(date)
    surcharge = get_surcharge(date_key)

    if date_key not in HOTEL_DB:
        return {
            "tool": "check_room_availability",
            "date": date_key,
            "error": f"Date {date_key} not found. Inventory covers June 2026 only.",
            "available_rooms": []
        }

    day_data = HOTEL_DB[date_key]
    rooms    = []

    for rtype, info in day_data.items():
        if room_type and rtype.lower() != room_type.lower():
            continue
        price_tonight = round(info["price_gbp"] * surcharge / 5) * 5
        rooms.append({
            "room_type":      rtype,
            "status":         info["status"],
            "price_tonight":  price_tonight,
            "base_price":     info["price_gbp"],
            "nights":         nights,
            "total_cost":     price_tonight * nights if info["status"] == "AVAILABLE" else None,
            "is_peak_day":    is_peak(date_key),
            "is_weekend":     is_weekend(date_key),
            "surcharge_pct":  round((surcharge - 1) * 100),
        })

    available = [r for r in rooms if r["status"] == "AVAILABLE"]
    return {
        "tool":            "check_room_availability",
        "date":            date_key,
        "total_available": len(available),
        "rooms":           rooms,
        "available_rooms": available,
    }


# ════════════════════════════════════════════════════════════════
# MCP TOOL 2: check_spa_availability
# ════════════════════════════════════════════════════════════════

# Therapist to treatment category mapping
TECH_SPECIALITIES = {
    "Sofia":  ["Massage","Body"],
    "Amir":   ["Massage","Ritual"],
    "Priya":  ["Facial","Nails"],
    "James":  ["Massage","Body"],
    "MeiLin": ["Facial","Ritual","Body"],
    "Lucas":  ["Massage"],
    "Fatima": ["Facial","Nails","Body"],
    "Kenji":  ["Massage","Ritual"],
}

TREATMENT_CATEGORY = {
    "Swedish Massage":          "Massage",
    "Deep Tissue Massage":      "Massage",
    "Hot Stone Massage":        "Massage",
    "Aromatherapy Massage":     "Massage",
    "Sports Recovery Massage":  "Massage",
    "Dual Suite Massage":       "Massage",
    "Classic Facial":           "Facial",
    "Anti-Ageing Facial":       "Facial",
    "Hydrating Facial":         "Facial",
    "Brightening Facial":       "Facial",
    "Body Scrub and Wrap":      "Body",
    "Hydrotherapy Bath":        "Body",
    "Detox Body Wrap":          "Body",
    "Classic Manicure":         "Nails",
    "Classic Pedicure":         "Nails",
    "Luxury Manicure Pedicure": "Nails",
    "Hammam Ritual":            "Ritual",
    "Detox Ritual":             "Ritual",
    "Signature Grandure Journey": "Ritual",
}

TREATMENT_BASE = {
    "Swedish Massage":           95,
    "Deep Tissue Massage":      115,
    "Hot Stone Massage":        145,
    "Aromatherapy Massage":     125,
    "Sports Recovery Massage":  120,
    "Dual Suite Massage":       220,
    "Classic Facial":            85,
    "Anti-Ageing Facial":       130,
    "Hydrating Facial":          95,
    "Brightening Facial":       110,
    "Body Scrub and Wrap":      155,
    "Hydrotherapy Bath":         75,
    "Detox Body Wrap":          130,
    "Classic Manicure":          55,
    "Classic Pedicure":          65,
    "Luxury Manicure Pedicure": 110,
    "Hammam Ritual":            180,
    "Detox Ritual":             175,
    "Signature Grandure Journey": 220,
}

@app.get("/tools/check_spa_availability")
def check_spa_availability(
    date: str = Query(..., description="Date e.g. 2026-06-14 or June 14"),
    treatment: Optional[str] = Query(None, description="Treatment name e.g. Deep Tissue Massage"),
    gender_preference: Optional[str] = Query(None, description="Male or Female"),
    preferred_time: Optional[str] = Query(None, description="Preferred time e.g. 14:00"),
    time_of_day: Optional[str] = Query(None, description="morning or afternoon"),
):
    """
    MCP Tool: Check therapist availability for a given date and optional treatment.
    Returns available therapists, their free slots, and treatment pricing.
    """
    date_key  = normalise_date(date)
    surcharge = get_surcharge(date_key)

    if date_key not in SPA_DB:
        return {
            "tool": "check_spa_availability",
            "date": date_key,
            "error": f"Date {date_key} not in spa inventory. Covers June 2026 only.",
        }

    # Determine required category
    required_cat = None
    if treatment:
        required_cat = TREATMENT_CATEGORY.get(treatment)
        base_price   = TREATMENT_BASE.get(treatment, 0)
        price_today  = round(base_price * surcharge / 5) * 5
    else:
        price_today = None

    # Filter time slots by time_of_day
    def slot_ok(slot):
        if preferred_time and slot != preferred_time:
            return False
        if time_of_day:
            h = int(slot.split(":")[0])
            if time_of_day.lower() == "morning" and h >= 13:
                return False
            if time_of_day.lower() == "afternoon" and h < 12:
                return False
        return True

    results = []
    for tech_name, tech_info in SPA_DB[date_key].items():
        if tech_info.get("day_off"):
            continue

        # Gender filter
        if gender_preference:
            if tech_info["gender"].lower() != gender_preference.lower():
                continue

        # Treatment category filter
        if required_cat:
            tech_cats = TECH_SPECIALITIES.get(tech_name, [])
            # Special case: Signature Grandure Journey — Mei Lin only
            if treatment == "Signature Grandure Journey" and tech_name != "MeiLin":
                continue
            # Dual Suite needs two massage therapists — handle separately
            if treatment != "Dual Suite Massage" and required_cat not in tech_cats:
                continue

        # Filter slots
        filtered_slots = [s for s in tech_info.get("slots", []) if slot_ok(s)]
        if filtered_slots:
            results.append({
                "therapist":   tech_name,
                "gender":      tech_info["gender"],
                "available_slots": filtered_slots,
                "first_slot":  filtered_slots[0],
            })

    # For Dual Suite Massage — find two therapists free at same time
    dual_pairs = []
    if treatment == "Dual Suite Massage":
        massage_therapists = [r for r in results
                              if any(s in TECH_SPECIALITIES.get(r["therapist"], [])
                                     for s in ["Massage"])]
        for i, t1 in enumerate(massage_therapists):
            for t2 in massage_therapists[i+1:]:
                shared = [s for s in t1["available_slots"] if s in t2["available_slots"]]
                if shared:
                    dual_pairs.append({
                        "therapist_1": t1["therapist"],
                        "therapist_2": t2["therapist"],
                        "shared_slots": shared,
                        "first_slot": shared[0],
                    })
        return {
            "tool":          "check_spa_availability",
            "date":          date_key,
            "treatment":     treatment,
            "price_today":   price_today,
            "is_peak_day":   is_peak(date_key),
            "surcharge_pct": round((surcharge - 1) * 100),
            "available_pairs": dual_pairs[:3],
            "total_pairs_available": len(dual_pairs),
        }

    return {
        "tool":              "check_spa_availability",
        "date":              date_key,
        "treatment":         treatment,
        "price_today":       price_today,
        "is_peak_day":       is_peak(date_key),
        "surcharge_pct":     round((surcharge - 1) * 100),
        "total_available":   len(results),
        "available_therapists": results[:5],
    }


# ════════════════════════════════════════════════════════════════
# MCP TOOL 3: check_fnb_price
# ════════════════════════════════════════════════════════════════
@app.get("/tools/check_fnb_price")
def check_fnb_price(
    item: str = Query(..., description="Menu item name e.g. Classic Mojito"),
    date: str = Query(..., description="Date e.g. 2026-06-21"),
):
    """
    MCP Tool: Get the price for a specific F&B item on a given date.
    Applies weekend and peak surcharges automatically.
    """
    date_key  = normalise_date(date)
    surcharge = get_surcharge(date_key)

    # Fuzzy match item name
    matched = None
    item_lower = item.lower()
    for name, data in FNB_DB.items():
        if item_lower == name.lower():
            matched = (name, data)
            break
    if not matched:
        for name, data in FNB_DB.items():
            if item_lower in name.lower() or name.lower() in item_lower:
                matched = (name, data)
                break

    if not matched:
        # Return a list of close items
        close = [n for n in FNB_DB if any(w in n.lower() for w in item_lower.split() if len(w) > 3)]
        return {
            "tool": "check_fnb_price",
            "error": f"Item '{item}' not found.",
            "suggestions": close[:5],
        }

    name, data = matched
    base    = data["base_price_gbp"]
    price   = round(base * surcharge * 2) / 2  # round to nearest £0.50

    return {
        "tool":          "check_fnb_price",
        "item":          name,
        "date":          date_key,
        "base_price":    base,
        "price_today":   price,
        "category":      data.get("category",""),
        "outlets":       data.get("outlets",""),
        "is_peak_day":   is_peak(date_key),
        "is_weekend":    is_weekend(date_key),
        "surcharge_pct": round((surcharge - 1) * 100),
    }


# ════════════════════════════════════════════════════════════════
# MCP TOOL 4: get_monthly_summary
# ════════════════════════════════════════════════════════════════
@app.get("/tools/get_monthly_summary")
def get_monthly_summary(
    room_type: Optional[str] = Query(None, description="Filter by room type"),
):
    """
    MCP Tool: Return occupancy summary for all of June 2026.
    """
    summary = {}
    for date_key, rooms in HOTEL_DB.items():
        avail = booked = 0
        prices = []
        for rtype, info in rooms.items():
            if room_type and rtype != room_type:
                continue
            if info["status"] == "AVAILABLE":
                avail += 1
                prices.append(info["price_gbp"])
            else:
                booked += 1
        total = avail + booked
        summary[date_key] = {
            "available": avail,
            "booked":    booked,
            "occupancy_pct": round(booked/total*100,1) if total else 0,
            "avg_price": round(sum(prices)/len(prices)) if prices else 0,
            "is_peak":   is_peak(date_key),
        }
    return {"tool": "get_monthly_summary", "room_type_filter": room_type, "dates": summary}


# ── Tool registry (for agent discovery) ──────────────────────
@app.get("/tools")
def list_tools():
    return {
        "server":      "Grandure MCP Availability Server",
        "description": "Live availability and pricing for Grandure Hotel — June 2026",
        "tools": [
            {
                "name":        "check_room_availability",
                "endpoint":    "/tools/check_room_availability",
                "description": "Check which rooms are available on a date and get live pricing",
                "parameters":  ["date (required)", "room_type (optional)", "nights (optional)"],
            },
            {
                "name":        "check_spa_availability",
                "endpoint":    "/tools/check_spa_availability",
                "description": "Find available therapists and slots for a spa treatment on a date",
                "parameters":  ["date (required)", "treatment (optional)", "gender_preference (optional)", "time_of_day (optional)"],
            },
            {
                "name":        "check_fnb_price",
                "endpoint":    "/tools/check_fnb_price",
                "description": "Get the price for a food or beverage item on a specific date",
                "parameters":  ["item (required)", "date (required)"],
            },
            {
                "name":        "get_monthly_summary",
                "endpoint":    "/tools/get_monthly_summary",
                "description": "Get occupancy and availability summary for all of June 2026",
                "parameters":  ["room_type (optional)"],
            },
        ]
    }

@app.get("/")
def root():
    return {"service": "Grandure MCP Server", "status": "ok", "port": 8001}


if __name__ == "__main__":
    import uvicorn, sys
    port = 8001
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i+1 < len(sys.argv):
            port = int(sys.argv[i+1])
    uvicorn.run("mcp_server:app", host="0.0.0.0", port=port, reload=True)
