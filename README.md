# blogdiff

Daily diff monitor for [mytrueintent.blogspot.com](http://mytrueintent.blogspot.com). Runs on GitHub Actions, emails changes to you.

## How it works

A scheduled GitHub Actions workflow (daily at 7:00 UTC):

1. Fetches the blog page
2. Compares it against the last saved snapshot
3. Emails the unified diff if anything changed
4. Commits the updated snapshot to the repo

## Setup

Add these as **repository secrets** at [Settings > Secrets and variables > Actions](https://github.com/soobrosa/blogdiff/settings/secrets/actions):

| Secret | Value |
|---|---|
| `GMAIL_USER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | A [Gmail App Password](https://myaccount.google.com/apppasswords) |

## Manual trigger

Go to the **Actions** tab and click **Run workflow** to test immediately.
