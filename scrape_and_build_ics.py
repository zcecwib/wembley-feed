#!/usr/bin/env python3
"""
Builds a wembley.ics feed from the official events page.
Publishes to docs/wembley.ics for GitHub Pages.

Requires: beautifulsoup4
"""
import json, re, html, hashlib
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from bs4 import BeautifulSoup  # pip install beautifulsoup4

EVENTS_URL = "https://www.wembleystadium.com/events"
CAL_NAME   = "Wembley Stadium Events (Auto)"
TZ         = "Europe/London"

def fetch_html(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")

def coerce_datetime(s):
    """
    Try common formats: ISO 8601 or human dates.
    Return naive datetime in local time (TZID used in ICS), or None if only a date/TBC.
    """
    s = str(s).strip()
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M", "%d %b %Y %H:%M", "%d %B %Y %H:%M",
        "%d %b %Y", "%d %B %Y", "%Y-%m-%d"
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if "%z" in f:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)  # drop tz; TZID used later
            # bare date → treat as TBC
            if f in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
                return None
            return dt
        except Exception:
            pass
    if re.search(r"\b(TBC|TBA)\b", s, re.I):
        return None
    return None

def dedupe_events(events):
    seen = set()
    out = []
    for e in events:
        key = (e["title"].lower(), e.get("iso") or e.get("date_text") or "tbc")
        if key in seen: 
            continue
        seen.add(key)
        out.append(e)
    return out

def parse_jsonld_events(soup):
    """Prefer JSON-LD events when present."""
    found = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if isinstance(obj, dict):
                graph = obj.get("@graph")
                if graph and isinstance(graph, list):
                    items.extend(graph)
    events = []
    for it in items:
        if not isinstance(it, dict): 
            continue
        t = it.get("@type")
        if (isinstance(t, list) and "Event" in t) or t == "Event":
            name = (it.get("name") or "").strip()
            url  = it.get("url") or EVENTS_URL
            start = it.get("startDate") or it.get("startTime") or it.get("start")
            if not name:
                continue
            dt = coerce_datetime(start) if start else None
            events.append({
                "title": html.unescape(name),
                "start_dt": dt,     # datetime or None
                "iso": start,
                "url": url,
                "tbc": dt is None
            })
    return events

def parse_html_cards(soup):
    """
    HTML fallback if JSON-LD is missing.
    Looks for event cards with title + date text.
    (Selector may need tweaking if site structure changes.)
    """
    events = []
    cards = soup.select("[class*=event] a, .event-card, .card a") or []
    for a in cards:
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        url = href if href.startswith("http") else ("https://www.wembleystadium.com" + href if href.startswith("/") else EVENTS_URL)
        # Nearby date text
        container = a.find_parent() or a
        txt = container.get_text(" ", strip=True)
        # Extract something like "9 Oct 2025 19:45", "25 Oct 2025", etc.
        m = re.search(r"(\d{1,2}\s+\w+\s+\d{4}(?:\s+\d{1,2}:\d{2})?)", txt)
        date_str = m.group(1) if m else ""
        dt = coerce_datetime(date_str) if date_str else None
        if title:
            events.append({
                "title": title,
                "start_dt": dt,
                "iso": date_str,
                "url": url,
                "tbc": dt is None
            })
    return events

def ics_escape(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def vevent(title, start_dt, url, tbc):
    # Stable ID from title + date key (or TBC)
    seed = f"{title}|{start_dt.isoformat() if start_dt else 'TBC'}|wembley"
    uid = hashlib.sha1(seed.encode()).hexdigest() + "@wembley-auto"
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{ics_escape(title)}"]
    if start_dt:
        dt = start_dt.strftime("%Y%m%dT%H%M%S")
        lines.append(f"DTSTART;TZID={TZ}:{dt}")
        end_dt = (start_dt + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
        lines.append(f"DTEND;TZID={TZ}:{end_dt}")
    else:
        # All-day placeholder for TBC; updates will replace it when time appears
        today = datetime.utcnow().strftime("%Y%m%d")
        lines.append(f"DTSTART;VALUE=DATE:{today}")
        lines.append(f"DTEND;VALUE=DATE:{(datetime.utcnow()+timedelta(days=1)).strftime('%Y%m%d')}")
        lines.append("CATEGORIES:TBC")
    lines.append(f"URL:{url}")
    if tbc:
        lines.append("DESCRIPTION:Time TBC—check event page for updates.")
    lines.append("END:VEVENT")
    return "\n".join(lines)

def build_calendar(events):
    events = dedupe_events(events)
    events.sort(key=lambda e: (e["start_dt"] or datetime.max))
    body = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Wembley Auto Feed//EN",
        f"X-WR-CALNAME:{ics_escape(CAL_NAME)}",
        "CALSCALE:GREGORIAN",
        f"X-WR-TIMEZONE:{TZ}",
        "METHOD:PUBLISH",
    ]
    for e in events:
        body.append(vevent(e["title"], e["start_dt"], e["url"], e["tbc"]))
    body.append("END:VCALENDAR")
    return "\n".join(body)

def main():
    html_text = fetch_html(EVENTS_URL)
    soup = BeautifulSoup(html_text, "html.parser")

    events = parse_jsonld_events(soup)
    if not events:
        events = parse_html_cards(soup)

    ics = build_calendar(events)
    # Write where GitHub Pages serves from
    import os
    os.makedirs("docs", exist_ok=True)
    with open("docs/wembley.ics", "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"Wrote docs/wembley.ics with {len(events)} events.")

if __name__ == "__main__":
    main()
