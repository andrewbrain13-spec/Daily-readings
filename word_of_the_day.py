"""
Vatican News "Word of the Day" -> MP3 -> Todoist task with audio attachment.

Runs once a day. Steps:
  1. Try the official Vatican News audio feed. If they published an MP3
     for today, use it (real human voice, no synthesis needed).
  2. Otherwise scrape today's dated page and read it aloud with a
     Microsoft neural voice via edge-tts (free, no API key).
  3. Upload the MP3 to Todoist and create today's task with the audio
     attached as a comment, so it plays inside the Todoist app.

Environment variables:
  TODOIST_TOKEN        required. Personal API token from Todoist settings.
  TODOIST_PROJECT_ID   optional. Leave unset to drop the task in Inbox.
  VOICE                optional. Default en-US-GuyNeural.
  SPEECH_RATE          optional. e.g. "-5%" to slow down. Default "+0%".
  LOCAL_TZ             optional. Default America/Chicago.
"""

import os
import sys
import json
import uuid
import asyncio
import datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import edge_tts
from mutagen.mp3 import MP3

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

TOKEN = os.environ.get("TODOIST_TOKEN", "").strip()
PROJECT_ID = os.environ.get("TODOIST_PROJECT_ID", "").strip()
VOICE = os.environ.get("VOICE", "en-US-GuyNeural").strip()
SPEECH_RATE = os.environ.get("SPEECH_RATE", "+0%").strip()
LOCAL_TZ = os.environ.get("LOCAL_TZ", "America/Chicago").strip()

RSS_URL = "https://www.vaticannews.va/en/word-of-the-day.rss.xml"
PAGE_TEMPLATE = "https://www.vaticannews.va/en/word-of-the-day/{y}/{m:02d}/{d:02d}.html"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

OUT_FILE = "word_of_the_day.mp3"
MAX_UPLOAD_BYTES = 4_800_000  # Todoist free plan caps uploads at 5 MB


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------
# Step 1: check for official Vatican News audio
# ----------------------------------------------------------------------

def find_official_audio(today):
    """Return an MP3 URL from the Vatican News feed for today, or None."""
    stamp = "{y}/{m:02d}/{d:02d}".format(y=today.year, m=today.month, d=today.day)
    try:
        resp = requests.get(RSS_URL, headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log("Could not read the Vatican News feed ({}). Falling back to text.".format(exc))
        return None

    for item in root.iter("item"):
        link = (item.findtext("link") or "")
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        url = enclosure.get("url", "")
        if ".mp3" not in url.lower():
            continue
        if stamp in link:
            log("Found official Vatican News audio for today.")
            return url

    log("No official audio for today in the feed. Will synthesize the text.")
    return None


def report_feed(today):
    """Diagnostic: print exactly what the RSS feed contains, then stop.

    Answers the question "does the English feed actually publish MP3 audio?"
    without creating any task. Triggered by running with --check-feed, or from
    the Actions tab by ticking the "check feed only" box.
    """
    stamp = "{y}/{m:02d}/{d:02d}".format(y=today.year, m=today.month, d=today.day)
    log("Checking the RSS feed: {}".format(RSS_URL))
    try:
        resp = requests.get(RSS_URL, headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log("Could not read the feed: {}".format(exc))
        return 1

    items = list(root.iter("item"))
    log("The feed has {} items. Showing up to 5:".format(len(items)))
    found_any_audio = False
    for i, item in enumerate(items[:5]):
        title = item.findtext("title") or "(no title)"
        link = item.findtext("link") or ""
        enclosure = item.find("enclosure")
        enc_url = enclosure.get("url", "") if enclosure is not None else ""
        enc_type = enclosure.get("type", "") if enclosure is not None else ""
        if enc_url:
            found_any_audio = True
        log("  [{}] {}".format(i, title))
        log("      link:      {}".format(link))
        log("      enclosure: {} {}".format(enc_type or "(none)", enc_url or "(none)"))
        log("      is today:  {}".format(stamp in link))
    if found_any_audio:
        log("RESULT: the feed DOES contain enclosures. Real audio may exist.")
    else:
        log("RESULT: no <enclosure> tags found. The feed is text only, so the "
            "synthesis path is what actually runs every day.")
    return 0


def download(url, path):
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=120)
    resp.raise_for_status()
    with open(path, "wb") as handle:
        handle.write(resp.content)


# ----------------------------------------------------------------------
# Step 2: scrape today's page
# ----------------------------------------------------------------------

def clean(text):
    text = text.replace("\u00a0", " ")
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def section(body, start_marker, end_markers):
    """Pull the text that sits between one heading and the next.

    Matching is case-insensitive so a wording change like "Reading of the Day"
    (capital D) does not silently produce an empty section. We search in a
    lower-cased copy but slice from the original text to keep its real casing.
    """
    low = body.lower()
    start = low.find(start_marker.lower())
    if start == -1:
        return ""
    start += len(start_marker)
    end = len(body)
    for marker in end_markers:
        found = low.find(marker.lower(), start)
        if found != -1:
            end = min(end, found)
    return body[start:end].strip()


def scrape(today):
    url = PAGE_TEMPLATE.format(y=today.year, m=today.month, d=today.day)
    log("Fetching {}".format(url))
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=60)
    except Exception as exc:
        log("Could not fetch the page ({}).".format(exc))
        return None, url
    if resp.status_code == 404:
        log("No page published for today. Nothing to do.")
        return None, url
    try:
        resp.raise_for_status()
    except Exception as exc:
        log("Page request failed ({}).".format(exc))
        return None, url

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    body = clean(soup.get_text("\n"))

    tail_markers = [
        "Your contribution for a great mission",
        "Excerpts from the",
        "More upcoming events",
        "Copyright \u00a9",
    ]

    reading = section(body, "Reading of the day", ["Gospel of the day"] + tail_markers)
    gospel = section(body, "Gospel of the day", ["The words of the Popes"] + tail_markers)
    pope = section(body, "The words of the Popes", tail_markers)

    if not (reading or gospel or pope):
        log("Page loaded but no readings were found. The layout may have changed.")
        return None, url

    return {"reading": reading, "gospel": gospel, "pope": pope}, url


def build_script(parts, today):
    pretty = "{}, {} {}, {}".format(
        today.strftime("%A"), today.strftime("%B"), today.day, today.year
    )
    chunks = ["The Word of the Day for {}.".format(pretty)]
    if parts["reading"]:
        chunks += ["Reading of the day.", parts["reading"]]
    if parts["gospel"]:
        chunks += ["Gospel of the day.", parts["gospel"]]
    if parts["pope"]:
        chunks += ["The words of the Popes.", parts["pope"]]
    return "\n\n".join(chunks)


async def synthesize(text, path):
    log("Synthesizing audio with {} at rate {}.".format(VOICE, SPEECH_RATE))
    speaker = edge_tts.Communicate(text, VOICE, rate=SPEECH_RATE)
    await speaker.save(path)


# ----------------------------------------------------------------------
# Step 3: deliver to Todoist
# ----------------------------------------------------------------------

def todoist_upload(path):
    """Upload the MP3 and return the file_attachment object Todoist expects."""
    endpoints = [
        "https://api.todoist.com/api/v1/uploads",
        "https://api.todoist.com/sync/v9/uploads/add",
    ]
    last_error = None
    for endpoint in endpoints:
        try:
            with open(path, "rb") as handle:
                resp = requests.post(
                    endpoint,
                    headers={"Authorization": "Bearer {}".format(TOKEN)},
                    files={"file": (os.path.basename(path), handle, "audio/mpeg")},
                    data={"file_name": os.path.basename(path)},
                    timeout=180,
                )
            if resp.status_code < 300:
                log("Uploaded via {}".format(endpoint))
                return resp.json()
            last_error = "{} -> {} {}".format(endpoint, resp.status_code, resp.text[:200])
        except Exception as exc:
            last_error = "{} -> {}".format(endpoint, exc)
    raise RuntimeError("Todoist upload failed. {}".format(last_error))


def todoist_deliver(attachment, title, description, duration_seconds):
    """Create today's task and attach the audio as a comment, in one call."""
    attachment = dict(attachment)
    attachment["file_duration"] = int(duration_seconds)
    attachment.setdefault("resource_type", "file")

    temp_id = str(uuid.uuid4())
    task_args = {"content": title, "due": {"string": "today"}}
    if description:
        task_args["description"] = description[:15000]
    if PROJECT_ID:
        task_args["project_id"] = PROJECT_ID

    commands = [
        {
            "type": "item_add",
            "temp_id": temp_id,
            "uuid": str(uuid.uuid4()),
            "args": task_args,
        },
        {
            "type": "note_add",
            "temp_id": str(uuid.uuid4()),
            "uuid": str(uuid.uuid4()),
            "args": {
                "item_id": temp_id,
                "content": "Today's audio",
                "file_attachment": attachment,
            },
        },
    ]

    resp = requests.post(
        "https://api.todoist.com/api/v1/sync",
        headers={"Authorization": "Bearer {}".format(TOKEN)},
        data={"commands": json.dumps(commands)},
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    statuses = result.get("sync_status", {})
    failures = {k: v for k, v in statuses.items() if v != "ok"}
    if failures:
        raise RuntimeError("Todoist rejected a command: {}".format(failures))
    log("Task created and audio attached.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    today = datetime.datetime.now(ZoneInfo(LOCAL_TZ)).date()

    # Diagnostic mode: only inspect the feed, no token or task needed.
    if "--check-feed" in sys.argv:
        log("Running feed check for {}".format(today.isoformat()))
        return report_feed(today)

    if not TOKEN:
        log("TODOIST_TOKEN is not set. Stopping.")
        return 1

    log("Running for {}".format(today.isoformat()))

    parts, page_url = scrape(today)

    official = find_official_audio(today)
    if official:
        download(official, OUT_FILE)
    else:
        if not parts:
            return 0  # nothing published today, exit quietly
        script = build_script(parts, today)
        asyncio.run(synthesize(script, OUT_FILE))

    size = os.path.getsize(OUT_FILE)
    log("Audio file is {:.1f} MB".format(size / 1_000_000))
    if size > MAX_UPLOAD_BYTES:
        log("Warning: file may exceed the Todoist upload limit for your plan.")

    try:
        duration = MP3(OUT_FILE).info.length
    except Exception:
        duration = 0
    log("Duration is about {:.0f} seconds.".format(duration))

    title = "Word of the Day: {} {}".format(today.strftime("%b"), today.day)

    description_bits = []
    if parts:
        if parts["reading"]:
            description_bits.append("**Reading**\n\n" + parts["reading"])
        if parts["gospel"]:
            description_bits.append("**Gospel**\n\n" + parts["gospel"])
        if parts["pope"]:
            description_bits.append("**The words of the Popes**\n\n" + parts["pope"])
    description_bits.append(page_url)
    description = "\n\n".join(description_bits)

    attachment = todoist_upload(OUT_FILE)
    todoist_deliver(attachment, title, description, duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
