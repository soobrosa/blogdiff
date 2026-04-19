#!/usr/bin/env python3
import difflib
import hashlib
import os
import re as re_mod
import smtplib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html import unescape
from pathlib import Path
from re import sub
from urllib.request import urlopen, Request

FEED_URL = "http://mytrueintent.blogspot.com/feeds/posts/default"
SNAPSHOT_FILE = Path(__file__).parent / "snapshot.txt"
RECIPIENT = "soobrosa@gmail.com"
SENDER = os.environ.get("GMAIL_USER", "soobrosa@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def strip_html(html):
    text = sub(r"<[^>]+>", "", html)
    return unescape(text).strip()


def parse_table_rows(html):
    rows = []
    for tr_match in sub(r"\n", "", html).split("</tr>"):
        cells = []
        for td_match in tr_match.split("</td>"):
            cell_text = unescape(sub(r"<[^>]+>", "", td_match).strip())
            cell_text = " ".join(cell_text.split())
            if cell_text:
                cells.append(cell_text)
        if len(cells) >= 2:
            rows.append(" | ".join(cells))
    return rows


def fetch_posts(url):
    req = Request(url, headers={"User-Agent": "blogdiff/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    posts = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = entry.findtext(f"{ATOM_NS}title", "").strip()
        published = entry.findtext(f"{ATOM_NS}published", "").strip()
        content_el = entry.find(f"{ATOM_NS}content")
        html = content_el.text or "" if content_el is not None else ""
        if "<table" in html.lower():
            table_start = html.lower().index("<table")
            rows = parse_table_rows(html[table_start:])
            if rows and rows[0].startswith("Date"):
                rows = rows[1:]
            content = "\n".join(rows)
        else:
            content = strip_html(html)
        if content:
            posts.append(f"=== {title} ({published}) ===\n{content}\n")
    return "\n".join(posts)


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


DATE_PATTERN = re_mod.compile(r"^(\d{2})\.(\d{2})\.(\d{2})\s*\|")


def parse_event_date(line):
    m = DATE_PATTERN.match(line)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return datetime(2000 + year, month, day).date()


def is_header_line(line):
    return line.startswith("=== ")


def is_only_new_label_change(removed, added):
    """True when the only difference is a 'new' tag being added or removed."""
    def strip_new(s):
        return re_mod.sub(r"\|\s*new\s*\|", "|", s).strip()
    return strip_new(removed) == strip_new(added)


def filter_relevant(diff_lines):
    today = datetime.now(timezone.utc).date()
    relevant = []
    removed_buf = []
    added_buf = []

    def flush_pair():
        nonlocal removed_buf, added_buf
        paired = []
        unpaired_removed = list(removed_buf)
        unpaired_added = list(added_buf)

        for r in list(unpaired_removed):
            for a in list(unpaired_added):
                if is_only_new_label_change(r[1:], a[1:]):
                    paired.append((r, a))
                    unpaired_removed.remove(r)
                    unpaired_added.remove(a)
                    break

        keep = []
        for line in unpaired_removed:
            content = line[1:]
            if is_header_line(content):
                continue
            d = parse_event_date(content)
            if d and d < today:
                continue
            keep.append(line)
        for line in unpaired_added:
            content = line[1:]
            if is_header_line(content):
                continue
            keep.append(line)

        removed_buf = []
        added_buf = []
        return keep

    for line in diff_lines:
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            relevant.extend(flush_pair())
            continue
        if line.startswith("-") and not line.startswith("---"):
            removed_buf.append(line)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added_buf.append(line)
            continue
        relevant.extend(flush_pair())

    relevant.extend(flush_pair())
    return relevant


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

    diff_lines = list(
        difflib.unified_diff(
            previous.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
    )

    if not diff_lines and previous:
        print("No meaningful diff.")
        return

    SNAPSHOT_FILE.write_text(current, encoding="utf-8")

    if not previous:
        subject = "[blogdiff] Initial snapshot of mytrueintent.blogspot.com"
        body = f"First snapshot captured ({len(current)} chars). Future runs will send diffs."
    else:
        relevant = filter_relevant(diff_lines)
        if not relevant:
            print("No relevant changes (only housekeeping).")
            if "GITHUB_OUTPUT" in os.environ:
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("changed=true\n")
            return
        subject = "[blogdiff] Changes detected on mytrueintent.blogspot.com"
        body = "\n".join(relevant)

    send_email(subject, body)

    # Signal to the workflow that snapshot changed
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
