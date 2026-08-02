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

DUE_STRING = os.environ.get("DUE_STRING", "today at 7am").strip()

# Podcast publishing. When SITE_URL is set the script also writes the day's
# audio and a rebuilt feed into PUBLISH_DIR, which a public GitHub Pages site
# serves, and the Todoist task links to the show instead of carrying the file.
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
SHOW_LINK = os.environ.get("SHOW_LINK", "").strip()
PUBLISH_DIR = os.environ.get("PUBLISH_DIR", "docs").strip()
AUDIO_DIR_NAME = "audio"
KEEP_EPISODES = int(os.environ.get("KEEP_EPISODES", "60"))

PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "Word of the Day").strip()
PODCAST_DESCRIPTION = os.environ.get(
    "PODCAST_DESCRIPTION",
    "The Vatican News Word of the Day: the reading, the Gospel, and the words "
    "of the Popes, read aloud each morning.",
).strip()
PODCAST_AUTHOR = os.environ.get("PODCAST_AUTHOR", "Vatican News (unofficial)").strip()
# Spotify emails a verification code to this address before it will accept the
# feed, so submission fails without it. It is visible to anyone who reads the
# feed, so use an address you are happy to publish.
PODCAST_EMAIL = os.environ.get("PODCAST_EMAIL", "").strip()

OUT_FILE = "word_of_the_day.mp3"
MAX_UPLOAD_BYTES = 4_800_000  # Todoist free plan caps uploads at 5 MB


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------
# Step 1: check for official Vatican News audio
# ----------------------------------------------------------------------

def item_url(item):
    """Return the page URL for a feed item, from <link> or <guid>."""
    return (item.findtext("link") or item.findtext("guid") or "").strip()


def find_official_audio(today):
    """Return an MP3 URL from the Vatican News feed for today, or None.

    Checked against the live feed on 2 Aug 2026: all 15 items carried only
    title, guid, pubDate and description. There was not a single <enclosure>
    or media tag, and the string "mp3" did not appear anywhere in the file.
    The feed is dressed up with itunes: tags but publishes no audio, so in
    practice this returns None and the synthesis path is what runs each day.
    It is kept as a cheap look-ahead in case Vatican News ever adds real
    recordings, and it fails safe: any error just falls through to synthesis.
    """
    stamp = "{y}/{m:02d}/{d:02d}".format(y=today.year, m=today.month, d=today.day)
    try:
        resp = requests.get(RSS_URL, headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log("Could not read the Vatican News feed ({}). Falling back to text.".format(exc))
        return None

    for item in root.iter("item"):
        # This feed has no <link> element at all: the page URL lives in <guid>.
        # Reading only <link> meant the date never matched, so this check could
        # never have fired even once audio appeared. Read both, prefer whichever
        # is present.
        where = item_url(item)
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        url = enclosure.get("url", "")
        if ".mp3" not in url.lower():
            continue
        if stamp in where:
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
        link = item_url(item)
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

    # The site serves "Content-Type: text/html" with no charset, so requests
    # falls back to ISO-8859-1 while the page is really UTF-8. Left alone that
    # turns every curly quote into mojibake like 'a<80><9c>', which the voice
    # then tries to read aloud. Trust the sniffed encoding instead.
    if not resp.encoding or "charset" not in (resp.headers.get("Content-Type") or "").lower():
        resp.encoding = resp.apparent_encoding or "utf-8"

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


def rate_multiplier(rate_text):
    """Turn a rate like "+25%" into the factor it speeds speech up by (1.25)."""
    try:
        percent = int(rate_text.strip().rstrip("%"))
    except (ValueError, AttributeError):
        return 1.0
    return max(0.1, 1.0 + percent / 100.0)


def expected_seconds(text, rate_text):
    """A conservative floor for how long the finished audio should run.

    Measured against the real service, the voice reads about 13.7 characters
    per second at normal speed. Using 15 here deliberately underestimates the
    duration, so this is a floor that complete audio should always clear.
    """
    return (len(text) / 15.0) / rate_multiplier(rate_text)


async def synthesize(text, path):
    """Read the text aloud to an MP3, retrying if the audio comes back short.

    edge-tts streams the audio over a long-lived connection. If that
    connection drops part way through, it writes the partial audio and returns
    without raising, so a run looks successful while the reading actually stops
    mid sentence. That really happened: a reading whose full length is 244
    seconds was delivered at 149 seconds with no error anywhere in the log.

    So every attempt is measured against a conservative floor, and anything
    obviously short is thrown away and re-read.
    """
    floor = expected_seconds(text, SPEECH_RATE) * 0.75
    attempts = 3
    best_path, best_length = None, -1.0

    for attempt in range(1, attempts + 1):
        log("Synthesizing audio with {} at rate {} (attempt {} of {}).".format(
            VOICE, SPEECH_RATE, attempt, attempts))
        candidate = "{}.attempt{}".format(path, attempt)
        await edge_tts.Communicate(text, VOICE, rate=SPEECH_RATE).save(candidate)
        try:
            length = MP3(candidate).info.length
        except Exception:
            length = 0.0

        if length > best_length:
            if best_path and os.path.exists(best_path):
                os.remove(best_path)
            best_path, best_length = candidate, length
        elif os.path.exists(candidate):
            os.remove(candidate)

        if length >= floor:
            log("Audio is {:.0f} seconds, which is a sensible full length.".format(
                length))
            break
        log("Audio came back at only {:.0f} seconds when at least {:.0f} was "
            "expected, so it was cut short. Trying again.".format(length, floor))
    else:
        log("WARNING: every attempt came back short. Using the longest one "
            "({:.0f} seconds). The reading may be incomplete.".format(best_length))

    os.replace(best_path, path)


# ----------------------------------------------------------------------
# Step 3: deliver to Todoist
# ----------------------------------------------------------------------

def rate_test(today):
    """Diagnostic: read the same text at several speeds and report each length.

    Answers two questions at once. First, whether a "+" rate really does speed
    the reading up. Second, whether synthesis is reliable, because if the same
    text and rate give different lengths on repeat runs then the audio is being
    silently cut short somewhere. Creates nothing in Todoist.
    """
    parts, _ = scrape(today)
    if not parts:
        log("Could not fetch today's readings, so there is nothing to test.")
        return 1
    text = build_script(parts, today)
    log("Test text is {} characters.".format(len(text)))
    log("")

    trials = ["+0%", "+0%", "+25%", "+25%", "+50%"]
    results = []
    for i, rate in enumerate(trials):
        path = "ratetest-{}.mp3".format(i)
        try:
            asyncio.run(edge_tts.Communicate(text, VOICE, rate=rate).save(path))
            size = os.path.getsize(path)
            seconds = MP3(path).info.length
            results.append((rate, seconds, size))
            log("  rate {:>5}  ->  {:6.1f} sec   {:.2f} MB".format(
                rate, seconds, size / 1_000_000))
        except Exception as exc:
            log("  rate {:>5}  ->  FAILED: {}".format(rate, exc))
        finally:
            if os.path.exists(path):
                os.remove(path)

    log("")
    same = [r for r in results if r[0] == "+0%"]
    if len(same) == 2:
        a, b = same[0][1], same[1][1]
        spread = abs(a - b)
        if spread > 3:
            log("WARNING: the same text at the same speed gave {:.0f} sec and "
                "{:.0f} sec. That {:.0f} second gap means the audio is being "
                "cut short at random, which is a reliability bug.".format(
                    a, b, spread))
        else:
            log("Repeat runs at the same speed agree within {:.1f} sec, so "
                "synthesis is reliable.".format(spread))
    return 0


# ----------------------------------------------------------------------
# Podcast publishing
# ----------------------------------------------------------------------

def audio_dir():
    return os.path.join(PUBLISH_DIR, AUDIO_DIR_NAME)


def publish_episode(mp3_path, today):
    """Copy the day's audio into the published folder. Returns its filename."""
    import shutil

    os.makedirs(audio_dir(), exist_ok=True)
    name = "{}.mp3".format(today.isoformat())
    dest = os.path.join(audio_dir(), name)
    shutil.copyfile(mp3_path, dest)
    log("Published audio as {}".format(dest))
    return name


def prune_episodes():
    """Keep only the most recent KEEP_EPISODES files so the repo stays small."""
    if not os.path.isdir(audio_dir()):
        return
    files = sorted(f for f in os.listdir(audio_dir()) if f.endswith(".mp3"))
    for stale in files[:-KEEP_EPISODES] if len(files) > KEEP_EPISODES else []:
        os.remove(os.path.join(audio_dir(), stale))
        log("Removed old episode {}".format(stale))


def _rfc2822(day):
    """Format a date the way RSS wants it, without relying on the locale."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "{}, {:02d} {} {} 07:00:00 +0000".format(
        days[day.weekday()], day.day, months[day.month - 1], day.year)


def _xml_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def rebuild_feed():
    """Regenerate feed.xml from whatever episodes are currently published.

    The folder of MP3 files is the only source of truth, so the feed can always
    be rebuilt from scratch and never drifts out of step with the audio.
    """
    files = sorted(
        (f for f in os.listdir(audio_dir()) if f.endswith(".mp3")), reverse=True
    )
    items = []
    for name in files:
        stamp = name[:-4]
        try:
            day = datetime.date.fromisoformat(stamp)
        except ValueError:
            continue
        path = os.path.join(audio_dir(), name)
        size = os.path.getsize(path)
        try:
            seconds = int(MP3(path).info.length)
        except Exception:
            seconds = 0
        url = "{}/{}/{}".format(SITE_URL, AUDIO_DIR_NAME, name)
        page = PAGE_TEMPLATE.format(y=day.year, m=day.month, d=day.day)
        items.append("""    <item>
      <title>{title}</title>
      <description>{desc}</description>
      <link>{page}</link>
      <guid isPermaLink="false">{stamp}</guid>
      <pubDate>{pub}</pubDate>
      <enclosure url="{url}" length="{size}" type="audio/mpeg" />
      <itunes:duration>{secs}</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
    </item>""".format(
            title=_xml_escape("Word of the Day - {}".format(
                day.strftime("%d %B %Y"))),
            desc=_xml_escape("The reading, the Gospel, and the words of the "
                             "Popes for {}.".format(day.strftime("%d %B %Y"))),
            page=_xml_escape(page),
            stamp=stamp,
            pub=_rfc2822(day),
            url=_xml_escape(url),
            size=size,
            secs=seconds,
        ))

    if PODCAST_EMAIL:
        owner_block = ("    <itunes:owner>\n"
                       "      <itunes:name>{}</itunes:name>\n"
                       "      <itunes:email>{}</itunes:email>\n"
                       "    </itunes:owner>\n").format(
            _xml_escape(PODCAST_AUTHOR), _xml_escape(PODCAST_EMAIL))
    else:
        owner_block = ""
        log("NOTE: PODCAST_EMAIL is not set. The feed is valid, but Spotify "
            "will refuse it until an owner email is present.")

    feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{title}</title>
    <description>{desc}</description>
    <link>{site}/</link>
    <language>en</language>
    <itunes:author>{author}</itunes:author>
{owner}    <itunes:summary>{desc}</itunes:summary>
    <itunes:image href="{cover}" />
    <image>
      <url>{cover}</url>
      <title>{title}</title>
      <link>{site}/</link>
    </image>
    <itunes:category text="Religion &amp; Spirituality" />
    <itunes:explicit>no</itunes:explicit>
    <itunes:type>episodic</itunes:type>
{items}
  </channel>
</rss>
""".format(
        title=_xml_escape(PODCAST_TITLE),
        desc=_xml_escape(PODCAST_DESCRIPTION),
        author=_xml_escape(PODCAST_AUTHOR),
        owner=owner_block,
        cover=_xml_escape("{}/cover.jpg".format(SITE_URL)),
        site=_xml_escape(SITE_URL),
        items="\n".join(items),
    )

    os.makedirs(PUBLISH_DIR, exist_ok=True)
    feed_path = os.path.join(PUBLISH_DIR, "feed.xml")
    with open(feed_path, "w", encoding="utf-8") as handle:
        handle.write(feed)
    log("Rebuilt {} with {} episode(s).".format(feed_path, len(items)))
    return feed_path


# Candidates worth hearing. The first four are Microsoft's newer generation
# and sound markedly more natural than the older ones; Guy is included as the
# current voice so there is a fair comparison.
SAMPLE_VOICES = [
    ("en-US-AndrewNeural", "US male, warm and confident"),
    ("en-US-BrianNeural", "US male, approachable and sincere"),
    ("en-GB-RyanNeural", "British male"),
    ("en-GB-ThomasNeural", "British male, measured"),
    ("en-IE-ConnorNeural", "Irish male"),
    ("en-US-ChristopherNeural", "US male, authoritative"),
    ("en-US-AvaNeural", "US female, expressive and warm"),
    ("en-US-EmmaNeural", "US female, clear and conversational"),
    ("en-US-GuyNeural", "the current voice, for comparison"),
]


def voice_test(today):
    """Read the same short passage in each candidate voice and publish them.

    Descriptions of a voice are close to useless, so this writes real samples
    to docs/voices/ with a page to play them from and lets the ear decide.
    """
    parts, _ = scrape(today)
    if not parts:
        log("Could not fetch today's readings, so there is nothing to read.")
        return 1

    # A short passage: enough to judge tone, short enough to render quickly.
    gospel = parts.get("gospel") or parts.get("reading") or ""
    sample = "\n".join(gospel.split("\n")[:8]) or parts["reading"][:400]
    sample = "Gospel of the day.\n\n" + sample
    log("Sample is {} characters.\n".format(len(sample)))

    out_dir = os.path.join(PUBLISH_DIR, "voices")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for short_name, blurb in SAMPLE_VOICES:
        path = os.path.join(out_dir, short_name + ".mp3")
        try:
            asyncio.run(edge_tts.Communicate(sample, short_name,
                                             rate=SPEECH_RATE).save(path))
            seconds = MP3(path).info.length
            log("  {:26s} {:5.1f} sec  {}".format(short_name, seconds, blurb))
            rows.append((short_name, blurb))
        except Exception as exc:
            log("  {:26s} FAILED: {}".format(short_name, exc))

    listen = "\n".join(
        '      <li><strong>{n}</strong> <span>{b}</span><br>'
        '<audio controls preload="none" src="{n}.mp3"></audio></li>'.format(
            n=n, b=b) for n, b in rows)
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice samples</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:40rem;margin:2.5rem auto;
      padding:0 1.25rem;line-height:1.5;color:#222}}
 li{{margin:0 0 1.4rem}} span{{color:#666;font-size:.9rem}}
 audio{{width:100%;margin-top:.4rem}} ul{{list-style:none;padding:0}}
 @media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}
   span{{color:#aaa}}}}
</style></head><body>
<h1>Voice samples</h1>
<p>The same passage from today's Gospel, read by each candidate voice.</p>
<ul>
{listen}
</ul>
</body></html>
""".format(listen=listen)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    log("\nSamples published. Listen at {}/voices/".format(SITE_URL))
    return 0


def todoist_upload(path):
    """Upload the MP3 and return the file_attachment object Todoist expects.

    Only the unified v1 endpoint is used. The old sync/v9 path was checked and
    is permanently retired: it now answers 410 Gone with a notice telling you
    to move to /api/v1/. Keeping it as a fallback would only ever waste a
    request and hide the real error, so it was removed.
    """
    endpoint = "https://api.todoist.com/api/v1/uploads"
    with open(path, "rb") as handle:
        resp = requests.post(
            endpoint,
            headers={"Authorization": "Bearer {}".format(TOKEN)},
            files={"file": (os.path.basename(path), handle, "audio/mpeg")},
            data={"file_name": os.path.basename(path)},
            timeout=180,
        )
    if resp.status_code >= 300:
        raise RuntimeError(
            "Todoist upload failed: {} {}".format(resp.status_code, resp.text[:300])
        )
    log("Uploaded to Todoist.")
    return resp.json()


def todoist_deliver_link(title, description, listen_url):
    """Create the task with a comment that is just a tappable listen link."""
    temp_id = str(uuid.uuid4())
    task_args = {"content": title, "due": {"string": DUE_STRING}}
    if description:
        task_args["description"] = description[:15000]
    if PROJECT_ID:
        task_args["project_id"] = PROJECT_ID

    commands = [
        {"type": "item_add", "temp_id": temp_id, "uuid": str(uuid.uuid4()),
         "args": task_args},
        {"type": "note_add", "temp_id": str(uuid.uuid4()), "uuid": str(uuid.uuid4()),
         "args": {"item_id": temp_id,
                  "content": "Listen: {}".format(listen_url)}},
    ]

    resp = requests.post(
        "https://api.todoist.com/api/v1/sync",
        headers={"Authorization": "Bearer {}".format(TOKEN)},
        data={"commands": json.dumps(commands)},
        timeout=60,
    )
    resp.raise_for_status()
    failures = {k: v for k, v in resp.json().get("sync_status", {}).items()
                if v != "ok"}
    if failures:
        raise RuntimeError("Todoist rejected a command: {}".format(failures))
    log("Task created with a link to {}".format(listen_url))


def todoist_deliver(attachment, title, description, duration_seconds):
    """Create today's task and attach the audio as a comment, in one call."""
    attachment = dict(attachment)
    attachment["file_duration"] = int(duration_seconds)
    attachment.setdefault("resource_type", "file")

    temp_id = str(uuid.uuid4())
    # "today at 7am" rather than plain "today" so the task carries a time and
    # Todoist raises a phone notification instead of sitting silently in a list.
    task_args = {"content": title, "due": {"string": DUE_STRING}}
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

    # Diagnostic modes: no token needed and nothing is created in Todoist.
    if "--check-feed" in sys.argv:
        log("Running feed check for {}".format(today.isoformat()))
        return report_feed(today)
    if "--rate-test" in sys.argv:
        log("Running speed test for {}".format(today.isoformat()))
        return rate_test(today)
    if "--voice-test" in sys.argv:
        log("Rendering voice samples for {}".format(today.isoformat()))
        return voice_test(today)

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

    if SITE_URL:
        # Podcast mode: publish the audio, rebuild the feed, and give Todoist a
        # link to tap rather than a file. SHOW_LINK points at Spotify once the
        # show exists there; until then the episode file itself is the target.
        name = publish_episode(OUT_FILE, today)
        prune_episodes()
        rebuild_feed()
        listen_url = SHOW_LINK or "{}/{}/{}".format(SITE_URL, AUDIO_DIR_NAME, name)
        todoist_deliver_link(title, description, listen_url)
    else:
        attachment = todoist_upload(OUT_FILE)
        todoist_deliver(attachment, title, description, duration)
    return 0


def _friendly_error(exc):
    """Turn a Python exception into a sentence a non-programmer can act on."""
    text = "{}: {}".format(type(exc).__name__, exc)
    low = text.lower()
    if "certificate" in low or "ssl" in low:
        return ("Could not make a secure connection to the voice service. "
                "If this happened on GitHub Actions, just re-run the job.")
    if "speech.platform.bing.com" in low or "websocket" in low:
        return ("The Microsoft voice service could not be reached. This is "
                "usually temporary, so re-running the job normally fixes it.")
    if "401" in low or "invalid token" in low or "auth" in low:
        return ("Todoist rejected the token. Check that the TODOIST_TOKEN "
                "secret in the repository matches the token in Todoist "
                "Settings, Integrations, Developer.")
    if "413" in low or "too large" in low:
        return ("Todoist refused the file for being too large. Shortening the "
                "reading or upgrading the Todoist plan would fix it.")
    return "Something went wrong. The technical detail is: {}".format(text)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:  # keep cron logs readable for a non-programmer
        log("")
        log("The run did not finish.")
        log(_friendly_error(error))
        log("")
        log("Full technical detail follows, for troubleshooting:")
        import traceback
        traceback.print_exc()
        sys.exit(1)
