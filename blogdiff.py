#!/usr/bin/env python3
import csv
import difflib
import hashlib
import io
import os
import re
import smtplib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from email.mime.text import MIMEText
from html import unescape
from pathlib import Path
from urllib.request import urlopen, Request

FEED_URL = "http://mytrueintent.blogspot.com/feeds/posts/default"
SNAPSHOT_FILE = Path(__file__).parent / "snapshot.txt"
RECIPIENT = "soobrosa@gmail.com"
SENDER = os.environ.get("GMAIL_USER", "soobrosa@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

ATOM_NS = "{http://www.w3.org/2005/Atom}"

STATUS_FLAGS = ("new venue", "new date", "sold out", "cancelled", "postponed", "new")
STATUS_SET = set(STATUS_FLAGS)
TBA_TOKENS = {"tba", "t.b.a.", "t.b.a", "tbc"}


SHEET_IFRAME_RE = re.compile(
    r"docs\.google\.com/spreadsheets/d/e/([\w-]+)/pubhtml\?gid=(\d+)", re.I
)


def _http_get(url):
    req = Request(url, headers={"User-Agent": "blogdiff/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def find_sheet_sources(feed_xml):
    root = ET.fromstring(feed_xml)
    sources = []
    seen = set()
    for entry in root.findall(f"{ATOM_NS}entry"):
        content_el = entry.find(f"{ATOM_NS}content")
        html = (content_el.text or "") if content_el is not None else ""
        html = unescape(html)
        for m in SHEET_IFRAME_RE.finditer(html):
            key = (m.group(1), m.group(2))
            if key not in seen:
                seen.add(key)
                sources.append(key)
    return sources


def sheet_csv_url(doc_id, gid):
    return (
        f"https://docs.google.com/spreadsheets/d/e/{doc_id}/pub"
        f"?gid={gid}&single=true&output=csv"
    )


def csv_rows_to_lines(data):
    text = data.decode("utf-8")
    lines = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4:
            continue
        date_raw, note, artist, venue = (c.strip() for c in row[:4])
        if not DATE_PREFIX_RE.match(date_raw):
            continue
        cells = [date_raw]
        if note and note.lower() in STATUS_SET:
            cells.append(note.lower())
        elif note:
            artist = f"{artist} - {note}" if artist else note
        if artist:
            cells.append(artist)
        if venue:
            cells.append(venue)
        lines.append(" | ".join(cells))
    return lines


def fetch_posts(url):
    feed = _http_get(url)
    sources = find_sheet_sources(feed)
    out = []
    for doc_id, gid in sources:
        rows = csv_rows_to_lines(_http_get(sheet_csv_url(doc_id, gid)))
        if rows:
            out.append(f"=== Gigs (gid {gid}) ===")
            out.extend(rows)
            out.append("")
    return "\n".join(out)


# ---------- structured diff ----------


@dataclass
class Event:
    date_raw: str
    date_start: date | None
    date_end: date | None
    status: str
    artists: list = field(default_factory=list)
    extras: list = field(default_factory=list)
    venue: str = ""
    raw: str = ""


SIMPLE_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$")
RANGE_SAME_MONTH_RE = re.compile(r"^(\d{1,2})\.-(\d{1,2})\.(\d{1,2})\.(\d{2,4})$")
RANGE_CROSS_MONTH_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.-(\d{1,2})\.(\d{1,2})\.(\d{2,4})$")
DATE_PREFIX_RE = re.compile(r"^\d{1,2}\.")


def _full_year(y):
    return y + 2000 if y < 100 else y


def parse_date_range(s):
    s = s.strip()
    m = SIMPLE_DATE_RE.match(s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            dt = date(_full_year(y), mo, d)
        except ValueError:
            return None, None
        return dt, dt
    m = RANGE_SAME_MONTH_RE.match(s)
    if m:
        d1, d2, mo, y = (int(x) for x in m.groups())
        try:
            return date(_full_year(y), mo, d1), date(_full_year(y), mo, d2)
        except ValueError:
            return None, None
    m = RANGE_CROSS_MONTH_RE.match(s)
    if m:
        d1, mo1, d2, mo2, y = (int(x) for x in m.groups())
        try:
            return date(_full_year(y), mo1, d1), date(_full_year(y), mo2, d2)
        except ValueError:
            return None, None
    return None, None


def norm(s):
    return re.sub(r"\s+", " ", s.lower().strip())


_WORD_RE = re.compile(r"[a-zA-Z]+")


def is_tba(name):
    n = norm(name).strip(" .")
    if n in TBA_TOKENS:
        return True
    tokens = [t.lower() for t in _WORD_RE.findall(n)]
    return "tba" in tokens or "tbc" in tokens


def norm_artists_key(artists):
    return " / ".join(sorted(norm(a) for a in artists if not is_tba(a)))


_ARTIST_SPLIT_RE = re.compile(r"\s+/\s+|,\s+")


def split_artists(s):
    if not s:
        return []
    parts = _ARTIST_SPLIT_RE.split(s)
    return [p.strip() for p in parts if p.strip()]


def parse_event(line):
    if not DATE_PREFIX_RE.match(line):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        return None
    date_raw = parts[0]
    start, end = parse_date_range(date_raw)
    if start is None:
        return None
    rest = parts[1:]
    status = ""
    if rest and rest[0].lower() in STATUS_SET:
        status = rest[0].lower()
        rest = rest[1:]
    if not rest:
        return None
    if len(rest) >= 2:
        venue = rest[-1]
        artist_block = " | ".join(rest[:-1])
    else:
        venue = ""
        artist_block = rest[0]
    pieces = [s.strip() for s in artist_block.split(" - ") if s.strip()]
    artists_str = pieces[0] if pieces else ""
    extras = pieces[1:]
    artists = split_artists(artists_str)
    return Event(
        date_raw=date_raw,
        date_start=start,
        date_end=end,
        status=status,
        artists=artists,
        extras=extras,
        venue=venue,
        raw=line,
    )


def parse_snapshot(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("==="):
            continue
        ev = parse_event(line)
        if ev is not None:
            out.append(ev)
    return out


def dedupe(events):
    seen = set()
    out = []
    for e in events:
        key = (e.date_start, norm(e.venue), norm_artists_key(e.artists), e.status)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def filter_future(events, today):
    return [e for e in events if e.date_end is None or e.date_end >= today]


def artist_sim(a, b):
    return difflib.SequenceMatcher(None, norm_artists_key(a.artists), norm_artists_key(b.artists)).ratio()


def line_sim(a, b):
    return difflib.SequenceMatcher(None, norm(a.raw), norm(b.raw)).ratio()


def best_matches(prev, curr, key_match, sim_fn, threshold):
    cands = []
    for i, p in enumerate(prev):
        if p is None:
            continue
        for j, c in enumerate(curr):
            if c is None:
                continue
            if not key_match(p, c):
                continue
            s = sim_fn(p, c)
            if s >= threshold:
                cands.append((s, i, j))
    cands.sort(key=lambda t: -t[0])
    pairs = []
    used_p, used_c = set(), set()
    for _, i, j in cands:
        if i in used_p or j in used_c:
            continue
        pairs.append((prev[i], curr[j]))
        used_p.add(i)
        used_c.add(j)
    for i in used_p:
        prev[i] = None
    for j in used_c:
        curr[j] = None
    return pairs


def match_events(prev_events, curr_events):
    prev = list(prev_events)
    curr = list(curr_events)
    matches = []

    matches += best_matches(
        prev, curr,
        lambda p, c: (p.date_start == c.date_start
                      and norm(p.venue) == norm(c.venue)
                      and norm_artists_key(p.artists) == norm_artists_key(c.artists)),
        lambda p, c: 1.0,
        0.99,
    )
    matches += best_matches(
        prev, curr,
        lambda p, c: p.date_start == c.date_start and norm(p.venue) == norm(c.venue),
        artist_sim,
        0.0,
    )
    matches += best_matches(
        prev, curr,
        lambda p, c: p.date_start == c.date_start,
        artist_sim,
        0.6,
    )
    matches += best_matches(
        prev, curr,
        lambda p, c: norm(p.venue) == norm(c.venue) and norm(p.venue) != "",
        artist_sim,
        0.85,
    )

    new = [c for c in curr if c is not None]
    removed = [p for p in prev if p is not None]
    return matches, new, removed


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def classify(p, c):
    if c.status in {"sold out", "cancelled"} and p.status != c.status:
        return c.status, None

    pv, cv = norm(p.venue), norm(c.venue)
    venue_typo = pv and cv and pv != cv and _ratio(pv, cv) >= 0.85
    if pv != cv and not venue_typo:
        return "venue_change", None

    if p.date_start != c.date_start:
        return "date_change", None

    p_artists = [a for a in p.artists if not is_tba(a)]
    c_artists = [a for a in c.artists if not is_tba(a)]
    p_set = {norm(a) for a in p_artists}
    c_set = {norm(a) for a in c_artists}
    raw_added = [a for a in c_artists if norm(a) not in p_set]
    raw_dropped = [a for a in p_artists if norm(a) not in c_set]

    real_added = []
    used = set()
    for a in raw_added:
        na = norm(a)
        best_i, best_r = -1, 0.0
        for i, d in enumerate(raw_dropped):
            if i in used:
                continue
            nd = norm(d)
            if na in nd or nd in na:
                best_r, best_i = 1.0, i
                break
            r = _ratio(na, nd)
            if r > best_r:
                best_r, best_i = r, i
        if best_r >= 0.7 and best_i >= 0:
            used.add(best_i)
        else:
            real_added.append(a)

    p_had_tba = any(is_tba(a) for a in p.artists)
    if real_added:
        return ("lineup_confirmed" if p_had_tba else "lineup_update", real_added)

    if line_sim(p, c) >= 0.7 or venue_typo:
        return "typo", None
    return "details", None


def fmt_artists(e):
    s = " / ".join(e.artists) if e.artists else ""
    if e.extras:
        s = f"{s} - {' - '.join(e.extras)}" if s else " - ".join(e.extras)
    return s


def fmt_venue(e):
    return f" @ {e.venue}" if e.venue else ""


def fmt_full(e):
    return f"{e.date_raw}  {fmt_artists(e)}{fmt_venue(e)}"


def render(matches, news, removed):
    buckets = {k: [] for k in (
        "new", "sold_out", "cancelled",
        "venue_change", "date_change",
        "lineup_confirmed", "lineup_update",
        "details", "removed",
    )}

    sentinel = date.max

    def push(bucket, sort_date, line):
        buckets[bucket].append((sort_date or sentinel, line))

    for n in news:
        flag = ""
        if n.status and n.status not in {"new"}:
            flag = f"[{n.status}] "
        push("new", n.date_start, f"{flag}{fmt_full(n)}")

    for r in removed:
        push("removed", r.date_start, fmt_full(r))

    for p, c in matches:
        kind, payload = classify(p, c)
        if kind == "typo":
            continue
        if kind == "sold out":
            push("sold_out", c.date_start, fmt_full(c))
        elif kind == "cancelled":
            push("cancelled", c.date_start, fmt_full(c))
        elif kind == "venue_change":
            push("venue_change", c.date_start,
                 f"{c.date_raw}  {' / '.join(c.artists)}  ({p.venue} -> {c.venue})")
        elif kind == "date_change":
            push("date_change", c.date_start,
                 f"{p.date_raw} -> {c.date_raw}  {' / '.join(c.artists)}{fmt_venue(c)}")
        elif kind == "lineup_confirmed":
            push("lineup_confirmed", c.date_start,
                 f"{c.date_raw}  {' / '.join(c.artists)}{fmt_venue(c)}  (tba -> {', '.join(payload)})")
        elif kind == "lineup_update":
            push("lineup_update", c.date_start,
                 f"{c.date_raw}  {' / '.join(c.artists)}{fmt_venue(c)}  (+ {', '.join(payload)})")
        elif kind == "details":
            push("details", c.date_start,
                 f"{c.date_raw}  {' / '.join(c.artists)}{fmt_venue(c)}\n      was: {p.raw}\n      now: {c.raw}")

    sections = [
        ("NEW SHOWS", "new"),
        ("SOLD OUT", "sold_out"),
        ("CANCELLED", "cancelled"),
        ("VENUE CHANGE", "venue_change"),
        ("DATE CHANGE", "date_change"),
        ("LINEUP CONFIRMED", "lineup_confirmed"),
        ("LINEUP UPDATE", "lineup_update"),
        ("DETAILS", "details"),
        ("REMOVED", "removed"),
    ]
    out = []
    for title, key in sections:
        items = buckets[key]
        if not items:
            continue
        items.sort(key=lambda t: t[0])
        out.append(f"=== {title} ({len(items)}) ===")
        for _, line in items:
            out.append(f"  {line}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_digest(previous_text, current_text, today=None):
    today = today or date.today()
    prev = filter_future(dedupe(parse_snapshot(previous_text)), today)
    curr = filter_future(dedupe(parse_snapshot(current_text)), today)
    matches, news, removed = match_events(prev, curr)
    return render(matches, news, removed), len(prev), len(curr)


# ---------- email & main ----------


def send_email(subject, body):
    if not APP_PASSWORD:
        print("GMAIL_APP_PASSWORD not set, printing diff to stdout instead.")
        print(body)
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SENDER, APP_PASSWORD)
        s.sendmail(SENDER, [RECIPIENT], msg.as_string())
    print("Email sent.")


def main():
    current = fetch_posts(FEED_URL)
    current_hash = hashlib.sha256(current.encode()).hexdigest()

    if SNAPSHOT_FILE.exists():
        previous = SNAPSHOT_FILE.read_text(encoding="utf-8")
        prev_hash = hashlib.sha256(previous.encode()).hexdigest()
    else:
        previous = ""
        prev_hash = ""

    if current_hash == prev_hash:
        print("No changes detected.")
        return

    digest, prev_count, curr_count = build_digest(previous, current)

    if not previous:
        SNAPSHOT_FILE.write_text(current, encoding="utf-8")
        subject = "[blogdiff] Initial snapshot of mytrueintent.blogspot.com"
        body = f"First snapshot captured ({curr_count} events). Future runs will send diffs."
        send_email(subject, body)
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("changed=true\n")
        return

    if not digest.strip():
        SNAPSHOT_FILE.write_text(current, encoding="utf-8")
        print("No meaningful diff after structured comparison.")
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("changed=true\n")
        return

    SNAPSHOT_FILE.write_text(current, encoding="utf-8")

    subject = "[blogdiff] Changes detected on mytrueintent.blogspot.com"
    body = digest
    send_email(subject, body)

    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
