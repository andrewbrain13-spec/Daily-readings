# Word of the Day, delivered to Todoist

Every morning this fetches the Vatican News Word of the Day, turns it into an
MP3, and creates a Todoist task with the audio attached so it plays in the app.

It runs on GitHub Actions, which is free and does not require your PC to be on.

---

## What you need

- A free GitHub account
- Your Todoist API token

Total setup time is about ten minutes. You will not need to write any code.

---

## Step 1: Get your Todoist API token

1. Open Todoist in a web browser
2. Click your avatar, top left, then **Settings**
3. Click **Integrations**, then the **Developer** tab
4. Copy the long string under **API token**

Keep this somewhere handy for Step 4. Treat it like a password.

---

## Step 2: Create the repository

1. Go to https://github.com/new
2. Name it something like `word-of-the-day`
3. Set it to **Private**
4. Click **Create repository**

---

## Step 3: Add the files

On the new empty repository page, click **uploading an existing file**, then
drag in these three files:

- `word_of_the_day.py`
- `requirements.txt`
- `README.md`

Click **Commit changes**.

The workflow file has to sit in a specific folder, so add it separately:

1. Click **Add file**, then **Create new file**
2. In the filename box type exactly: `.github/workflows/word-of-the-day.yml`
   (typing the slashes creates the folders automatically)
3. Paste in the contents of the `word-of-the-day.yml` file
4. Click **Commit changes**

---

## Step 4: Store your token securely

1. In the repository, click **Settings**
2. In the left sidebar: **Secrets and variables**, then **Actions**
3. Click **New repository secret**
4. Name: `TODOIST_TOKEN`
   Secret: paste the token from Step 1
5. Click **Add secret**

Optional. If you want the task to land in a specific Todoist project instead of
your Inbox, add a second secret named `TODOIST_PROJECT_ID`. To find the ID, open
the project in Todoist in a browser and copy the code at the end of the address
bar.

---

## Step 5: Test it right now

1. Click the **Actions** tab
2. Click **Word of the Day** in the left sidebar
3. Click **Run workflow**, then the green **Run workflow** button
4. Wait about a minute, then refresh

A green check means it worked. Open Todoist and the task should be there with
the audio attached.

If you see a red X, click into the run and read the log. It prints plain
English messages at every step, so it will usually tell you what went wrong.

Tip: on the Run workflow panel there is a checkbox, "Check the RSS feed only,
do not create a task". Tick it to have the run just fetch the Vatican News feed
and print what audio, if any, it contains. This is a safe way to look without
touching your Todoist.

---

## Adjusting things

**Delivery time.** Edit the `cron` line in the workflow file. The numbers are
in UTC, and the format is `minute hour * * *`. Kansas City is UTC minus 5 in
summer and minus 6 in winter, so `0 11 * * *` lands at 6:00 AM Central in
summer. GitHub does not adjust for daylight saving, so the delivery time will
shift by an hour twice a year unless you edit it.

**Voice.** Change the `VOICE` line in the workflow file. Some good options:

- `en-US-GuyNeural` (default, warm American male)
- `en-US-AriaNeural` (American female)
- `en-GB-RyanNeural` (British male)
- `en-IE-ConnorNeural` (Irish male)

**Speed.** Change `SPEECH_RATE` to something like `-8%` to slow the reading
down, which suits scripture reasonably well.

---

## How it works

1. Checks the official Vatican News audio feed first. If they published a real
   recording for today, it uses that and skips synthesis entirely.
2. Otherwise it fetches `vaticannews.va/en/word-of-the-day/YYYY/MM/DD.html`,
   which is a plain static page, and pulls out three sections: the reading, the
   Gospel, and the words of the Popes.
3. Reads that aloud with a Microsoft neural voice through `edge-tts`. This is
   free and needs no account or API key.
4. Uploads the MP3 to Todoist and creates the task with the audio attached in a
   comment, in a single API call.

The full text also goes into the task description, so you can read along.

---

## Things that could break, and what to do

**Vatican News changes the page layout.** The script finds sections by looking
for the headings "Reading of the day", "Gospel of the day", and "The words of
the Popes". If they rename those, the log will say no readings were found.

**Nothing published for a given day.** The script exits quietly rather than
creating an empty task.

**Upload rejected.** Todoist caps attachment size by plan (5 MB on the free
plan). A synthesized reading is normally well under 2 MB, so this is unlikely,
but the log prints the file size every run.

**GitHub pauses the schedule.** GitHub disables scheduled workflows in
repositories with no activity for 60 days and emails you about it. One click
re-enables it.

---

## A note on use

This makes a personal listening copy for yourself. The scripture text on the
page carries a lectionary copyright notice that prohibits redistribution, so
keep the output to your own Todoist rather than publishing or sharing the feed.
