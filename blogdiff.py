#!/usr/bin/env python3
import difflib
import hashlib
import os
import smtplib
import sys
import xml.etree.ElementTree as ET
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

    diff_text = "\n".join(diff_lines)
    if not previous:
        subject = "[blogdiff] Initial snapshot of mytrueintent.blogspot.com"
        body = f"First snapshot captured ({len(current)} chars). Future runs will send diffs."
    else:
        subject = "[blogdiff] Changes detected on mytrueintent.blogspot.com"
        body = diff_text

    send_email(subject, body)

    # Signal to the workflow that snapshot changed
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
