# Auto iPhone Uploader

<img src="web/logo.svg" width="64" height="64" alt="iPhone with an upload arrow">

[![Tests](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml)
[![CodeQL](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml)
[![VirusTotal v0.1.0: 0/65](https://img.shields.io/badge/VirusTotal%20v0.1.0-0%2F65%20flagged-brightgreen)](https://www.virustotal.com/gui/file/c4ae924bcd67620b0248bd3a628f42fc9e7e28496058e6066dfd3640743b8f6b)

Finish a video on your Windows PC, write its title and captions once, and let
your PC post it through the real social apps on your iPhone. Posting from the
phone apps keeps the quality that platform websites and browser uploaders
throw away; the
[4K60 Native Ingest paper](https://github.com/thedevmark/engineering-notes/blob/main/4k60-native-ingest/README.md)
has the tests behind that.

**Before you install:** this app taps through social apps on your iPhone.
[SECURITY.md](SECURITY.md) lists exactly what it does and never does on your
phone and computer, and how to check a download was built from this
repository by GitHub.

## Status: 1.0 release candidate

This is `1.0.0-rc.1`. It has been tried on **one iPhone 16 Pro Max on iOS 26.7
only**. Other iPhone sizes are covered by automated tests for YouTube, not by
real phones yet. Read [Known limitations](#known-limitations) before you rely
on it.

## What it does

1. You export a finished video into a folder that syncs to the cloud.
2. The app notices the new file, checks it is complete, and adds it to a
   review queue on your PC. The original file is never moved or changed.
3. If you have a local AI (Ollama), it suggests a title, description, tags and
   captions. You edit them. Nothing is posted until you confirm the exact text
   for each platform.
4. The app controls your iPhone over the USB cable, opens the video from the
   OneDrive app, shares it into the social app, checks the signed-in account,
   fills in your confirmed text and, for YouTube and TikTok, checks the cover is
   the video's first frame. Then it posts.

Everything runs on your own PC. The editor is a page at
`http://127.0.0.1:4748` that only your computer can open. There is no account
to create, no cloud service of ours, and no tracking.

## Two ways to post

Each video has one of two modes. **Schedule** is the default.

| | Post now | Schedule |
|---|---|---|
| YouTube Shorts | The app posts it from the YouTube app | Schedule it yourself in the YouTube app, then click **Scheduled** in the editor |
| TikTok | The app posts it from the TikTok app | TikTok has no scheduler on the tested account, so the app posts it itself at the video's posting time. Your PC and phone link must be on then |
| Threads | The app posts it from the Threads app | Threads always posts right away; it does not use a posting time |
| Instagram Reels | Post it yourself in the Instagram app, then click **Posted** | Schedule it yourself in the Instagram app, then click **Scheduled** |
| Facebook | Share it from Instagram's "Also share on" switch, then click **Posted** | Same as Instagram |

After any post the app treats the result as unconfirmed until you check the
app on your phone. A final tap that timed out is **never** retried
automatically, so you never get a surprise double post.

Posting times are set in **Settings → Posting times**: one to five times a day
(10 AM and 7 PM by default).

## Where your videos come from

The phone opens each video from the **OneDrive** app, so the folder you export
to must be inside OneDrive on your PC and OneDrive must be installed and signed
in on your iPhone.

The setup checklist also recognises Google Drive, Dropbox and iCloud Drive
folders on your PC, and the app includes screen maps for the iPhone Files app
that those services use. Posting from them is not connected yet; see
[Known limitations](#known-limitations).

## What you need

- A Windows 10 or 11 PC with [Python 3.11 or newer](https://www.python.org/downloads/).
  When the Python installer asks, tick **Add python.exe to PATH**.
- [ffmpeg](https://ffmpeg.org/download.html), so that `ffmpeg` and `ffprobe`
  work in a terminal. Every video is fully decoded once before it is accepted.
- An iPhone and a USB cable, set up with
  [SideTap](https://github.com/ucsandman/SideTap/blob/main/docs/setup-windows.md).
  SideTap is a separate free project that lets your PC tap on the iPhone.
  Install and review it yourself; this app does not bundle or download it.
- OneDrive on the PC and on the iPhone, signed in to the same account.
- The social apps you want to post to, installed and signed in on the iPhone.
- Optional: [Ollama](https://ollama.com) for suggested titles and captions,
  with the two models the app uses:

  ```powershell
  winget install Ollama.Ollama
  ollama pull qwen2.5vl:7b
  ollama pull qwen3:14b
  ```

  The models run on your own PC; no text or video leaves it. Without Ollama you
  write the text yourself.

## Install

1. Download `auto-iphone-uploader-<version>.zip` from the latest
   [release](https://github.com/thedevmark/auto-iphone-uploader/releases) and,
   if you like, [check it was built by GitHub](SECURITY.md#verify-a-download).
2. Unzip it somewhere you will keep it, for example `Documents\Auto iPhone Uploader`.
3. Open the folder, click the address bar, type `powershell` and press Enter.
4. Read `scripts\install_windows.ps1` first (it is short), then run:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
   ```

   It checks your Python version, installs the pinned packages from
   `requirements.txt`, and puts an **Auto iPhone Uploader** shortcut on your
   Desktop. The `Bypass` applies to that one run only. It installs no drivers
   or services and does not touch your phone.
5. Double-click the Desktop shortcut. The editor opens in your browser.

Prefer not to run the script? `python -m pip install -r requirements.txt` then
`python launch_video_drop.py` does the same without the shortcut.

## First run: the setup checklist

The first time the editor opens it shows **Set up Auto iPhone Uploader**. You
can reopen it any time from **Settings → Open setup checklist**. Each row turns
green when it is ready, and tells you exactly what to do when it is not:

| Row | What it checks |
|---|---|
| SideTap | SideTap is installed on this PC and loads |
| iPhone connected | Exactly one iPhone is plugged in over USB |
| Phone link | The PC can control the iPhone's screen |
| Phone details | Screen size and which social apps are installed. Click **Check phone**; it opens YouTube on the iPhone to read the channel, so don't touch the phone while it runs |
| iPhone free space | Room for the video and its Photos copy (5 GB recommended). Measured before every upload; this row is advisory |
| Local AI (Ollama) | Ollama is running with both models |
| Video folder | Your export folder is inside a cloud folder, and **Watch folder** is on |

Every check only reads. None of them taps the phone, except **Check phone**,
which you start yourself. Click **Check again** after fixing something.

Without Ollama the **Local AI** row stays open and the checklist reopens each
time the app starts. Everything else still works; you write the text yourself.

Then tell the app which account each platform must post to, in
`.state\accounts.json` inside the app folder, for example:

```json
{"youtube": "@your-channel", "instagram": "@you", "threads": "@you", "tiktok": "@you"}
```

Every phone run stops before posting if the signed-in account is different.

## Everyday use

1. Export a video into your watched folder. It appears in the **Queue** once
   the file has stopped changing for 30 seconds and decodes cleanly.
2. Select it, check the suggested title and hashtags, press **Apply to all**,
   and edit any platform's text.
3. Press **Confirm details** for each platform you want.
4. Choose **Schedule** or **Post now**, then use the post button for that
   platform.
5. Look at the post on your phone, then click **Posted** or **Scheduled** in the
   editor so the queue shows the truth.

The header shows your **Unattended streak**: how many Schedule-mode videos in
a row reached every app without a hand fix. The goal is 20.

## Known limitations

- **One phone tested.** Only an iPhone 16 Pro Max on iOS 26.7. The YouTube
  flow is proven on other screen sizes by tests
  ([docs/size-independence-audit.md](docs/size-independence-audit.md)), not on
  real phones. App updates from YouTube, Instagram, TikTok or Threads can move
  buttons and stop a run until the app is updated.
- **English only.** The screen maps match English app labels.
- **TikTok needs your PC on at the posting time.** TikTok has no native
  scheduling on the tested account, so this app posts it at the slot. The PC,
  the app and the phone link must be on. If the post has not finished 15
  minutes after the slot, the app marks it missed and does not post it; you
  can then choose **Post now** or confirm you posted it by hand.
- **No in-app native scheduling yet.** For YouTube, Instagram and Facebook,
  scheduling is done by you in each phone app. The **Schedule in apps** button
  stays disabled until the app can schedule and verify each platform itself.
  The editor also has no button yet to put a video on a posting time; the
  planner exists as a local API (`POST /api/queue/plan`, see
  [docs/how-it-works.md](docs/how-it-works.md)). Until one is added, TikTok's
  post-at-the-slot only runs for videos planned through that API.
- **Instagram and Facebook are posted by you.** The app prepares and checks
  their text, but does not post to them. A read-only script
  (`scripts/phone_instagram_preflight.py`) can confirm the Instagram account.
- **Receipts are confirmed by you.** After each post, click **Posted** or
  **Scheduled**. Automatic receipt reading is not connected, so the unattended
  streak does not grow yet. The two buttons appear once the app has started
  work on the video (for example after its Threads, YouTube or TikTok post),
  not while it is still a draft.
- **OneDrive only for posting.** Google Drive, Dropbox and iCloud Drive
  folders pass the setup checklist, but the phone runners open videos from the
  OneDrive app only.
- **Posting times are in US Eastern time.** Slots and the times shown in the
  queue use New York time regardless of your PC's time zone.
- **The phone link can drop.** USB tunnels to iPhones are not perfectly stable.
  The app waits for a dropped link to come back without restarting a live
  session, and stops safely before the final tap if it does not. If Windows
  loses the iPhone completely, unplug and replug the cable, then run the check
  again.
- **Your PC must stay awake** while the app works, and one phone action runs at
  a time.

## Where things are kept

Drafts, settings, the local database and phone profiles live in the `.state`
folder inside the app folder. Keep it if you move or update the app. Media
files are read in place and never copied into the app.

[docs/how-it-works.md](docs/how-it-works.md) covers the details: the watch
folder, text rules per platform, the slot planner, receipts and the phone
scripts. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Develop

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
```

GitHub Actions runs the suite on Windows and Linux with Python 3.11 and 3.14.
Live iPhone behavior is checked separately because CI has no phone or platform
accounts. This repo is also taking over the video workflow from the author's
earlier Homebase system; see [MIGRATION.md](MIGRATION.md).

MIT licensed; see [LICENSE](LICENSE).
