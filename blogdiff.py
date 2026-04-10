#!/usr/bin/env python3
import difflib
import hashlib
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import urlopen, Request

BLOG_URL = "http://mytrueintent.blogspot.com"
SNAPSHOT_FILE = Path(__file__).parent / "snapshot.txt"
RECIPIENT = "soobrosa@gmail.com"
SENDER = os.environ.get("GMAIL_USER", "soobrosa@gmail.com")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def fetch_page(url):
    req = Request(url, headers={"User-Agent": "blogdiff/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
    current = fetch_page(BLOG_URL)
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
        subject = f"[blogdiff] Initial snapshot of {BLOG_URL}"
        body = f"First snapshot captured ({len(current)} chars). Future runs will send diffs."
    else:
        subject = f"[blogdiff] Changes detected on {BLOG_URL}"
        body = diff_text

    send_email(subject, body)

    # Signal to the workflow that snapshot changed
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("changed=true\n")


if __name__ == "__main__":
    main()
