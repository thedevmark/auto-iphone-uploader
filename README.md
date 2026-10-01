# Auto iPhone Uploader

<img src="web/logo.svg" width="64" height="64" alt="iPhone with an upload arrow">

[![Tests](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/tests.yml)
[![CodeQL](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml/badge.svg)](https://github.com/thedevmark/auto-iphone-uploader/actions/workflows/codeql.yml)
[![VirusTotal](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fthedevmark%2Fauto-iphone-uploader%2Fbadges%2Fvirustotal.json)](https://github.com/thedevmark/auto-iphone-uploader/blob/badges/virustotal.md)

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

## What it does

Export a finished video into a folder that syncs to the cloud. The app picks
it up, checks the file is complete, and adds it to a review queue on your PC;
the original is never moved or changed. If you run a local AI (Ollama), it
drafts the title, description, tags and captions. You edit them and confirm
the exact text for each platform. Then your PC drives your iPhone over the
USB cable and posts through the real apps:

- **YouTube Shorts**: title, description and the first frame as the
  thumbnail, posted from the YouTube app on the channel you set. Detail rows
  YouTube hides from accessibility (Description, Paid promotion, AI use) are
  found on screen with Windows' built-in offline OCR.
- **Instagram Reels**: the clip goes through Meta's Edits app for a 4K
  export, then into Instagram with your caption and a first-frame cover.
  Instagram's own "Also share on" switches carry the post to **Facebook** and
  **Threads** in the same upload. If Instagram is on another of your
  signed-in accounts, the app switches to the right one through Instagram's
  account switcher first.
- **TikTok**: posted from the TikTok app with your caption.

Each video is either **Post now** (everything immediately, in order: YouTube,
then Instagram with its crossposts, then TikTok) or **Schedule** (the next
free posting time): the app enters the slot in YouTube's and Instagram's own
schedulers and reads the date and time back, and posts TikTok itself at the
slot. Posting times are set in **Settings → Posting times**: one to five a
day (10 AM and 7 PM by default), in your PC's time zone or one you choose.

Around every run:

- **Do Not Disturb** is on while the phone posts, and turned back off
  afterwards if the app turned it on.
- **Checks before the final tap**: the signed-in account, the exact video
  file and size, free space on the phone, and the first frame as the cover,
  proven by screenshot. A run that cannot prove one of them stops before
  posting. A final tap that timed out is never retried, so there are no
  double posts.
- **Receipts** come back from the apps. In the hour after a final tap the
  app looks at Instagram and Threads a few times, read-only, and marks the
  post posted when the profile proves it: Instagram's post count and newest
  first-frame tile, the newest Threads post with your caption. Facebook,
  TikTok and YouTube you confirm with **Posted** or **Scheduled** in the
  editor after a look at your phone.
- **A self-healing phone link.** One supervisor owns the USB tunnel and the
  phone driver, presses Home when a playing video wedges the driver, restarts
  only what died, and recovers a stalled USB link by restarting Apple's USB
  service and resetting the phone's USB port, asking for a replug only when
  that fails ([docs/phone-link-reliability.md](docs/phone-link-reliability.md)).
- **Cleans up after itself.** Once Instagram confirms a post, the Edits
  project its 4K export left behind (often over a gigabyte) goes to Edits'
  Trash, where you can still restore it. Nothing is saved to Photos.
- **A short first-run setup**: five calm screens, one task each, that move on
  by themselves; light, dark or system theme.

Everything runs on your own PC. The editor is a page at
`http://127.0.0.1:4748` that only your computer can open. There is no account
to create, no cloud service of ours, and no tracking.

## Requirements and limits

- A Windows 10 or 11 PC and an iPhone on a USB cable. Plug the phone into a
  port on the CPU's own USB controller (usually a rear port), not a chipset
  port or a hub, keep it charged, and turn off USB selective suspend; the
  checklist shows each of these.
- Tested on an iPhone 16 Pro Max with iOS 26.7. The YouTube flow is covered
  on other screen sizes by tests
  ([docs/size-independence-audit.md](docs/size-independence-audit.md)). App
  updates can move buttons; a run then stops instead of guessing.
- English app labels, and an English OCR language in Windows (installed with
  English Windows).
- A free Apple ID signs the phone driver for 7 days; the checklist counts it
  down and `scripts\phone_resign.py` renews it.
- Threads is not scheduled natively: a scheduled video leaves Threads for you
  and says so. Post now covers Threads through Instagram's crosspost.
- TikTok has no native scheduler on the tested account, so your PC, the app
  and the phone link must be on at the slot. A post not finished 15 minutes
  after it is marked missed and never posted late.
- Schedule mode has been run on the reference phone up to the date and time
  read-back; a committed scheduled post has not been verified there yet
  ([docs/native-scheduling.md](docs/native-scheduling.md)).
- Videos are opened from the OneDrive app. Google Drive, Dropbox and iCloud
  Drive folders go through the iPhone Files app, which has not been run live.
- Your PC stays awake while the app works, and one phone action runs at a
  time.

[docs/launch-checklist.md](docs/launch-checklist.md) records what has been
verified live on the reference phone, and how to check each item yourself.

## What you need

- A Windows 10 or 11 PC with [Python 3.11 or newer](https://www.python.org/downloads/).
  When the Python installer asks, tick **Add python.exe to PATH**.
- [ffmpeg](https://ffmpeg.org/download.html), so that `ffmpeg` and `ffprobe`
  work in a terminal. Every video is fully decoded once before it is accepted.
- The **Apple Devices** app from the Microsoft Store (or iTunes), which gives
  Windows the USB driver for iPhones.
- An iPhone with a USB cable, plugged into a port on the PC itself, with
  **Developer Mode** on (Settings → Privacy & Security → Developer Mode).
- [Sideloadly](https://sideloadly.io) and an Apple ID to sign the
  WebDriverAgent driver with. A free Apple ID works; its signature lasts 7
  days and the app tells you when to renew it.
- OneDrive on the PC and on the iPhone, signed in to the same account.
- The social apps you want to post to, installed and signed in on the iPhone,
  plus Meta's **Edits** app for Instagram.
- Optional: [Ollama](https://ollama.com) for suggested titles and captions,
  with the two models the app uses:

  ```powershell
  winget install Ollama.Ollama
  ollama pull qwen2.5vl:7b
  ollama pull qwen3:14b
  ```

  The models run on your own PC; no text or video leaves it. Without Ollama you
  write the text yourself.

## Install (Windows, about fifteen minutes)

1. Download `auto-iphone-uploader-<version>.zip` from the latest
   [release](https://github.com/thedevmark/auto-iphone-uploader/releases)
   ([check it was built by GitHub](SECURITY.md#verify-a-download)), unzip it
   somewhere you will keep it, open the folder, type `powershell` in the
   address bar and press Enter.
2. Paste one command:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1
   ```

   It installs the Python packages, downloads the iPhone connector (go-ios
   1.3.2) and the phone control app (WebDriverAgent 16.12.9), each checked
   against a pinned SHA-256, adds a Desktop shortcut and a start-at-sign-in
   entry, offers to save your iPhone passcode at a hidden prompt, and opens
   the app. Re-run it any time; finished steps are skipped. `-CheckOnly` only
   reports. [docs/setup-windows.md](docs/setup-windows.md) explains every step.
3. Follow the app's first-run screens: **Connect your iPhone → Let your PC
   control it → Pick your video folder → Check your apps → You're ready.**
   Each screen shows one thing to do and moves on by itself when it's done.

The one step Apple doesn't let a script do: sign the control app with your
own Apple ID in [Sideloadly](https://sideloadly.io) (drag
`wda\WebDriverAgent.ipa` onto it, click Start, then trust your Apple ID on the
iPhone under Settings → General → VPN & Device Management), then run
`python scripts\phone_resign.py`. Apple's password prompts appear in
Sideloadly, never in this app. A free Apple ID's signing lasts seven days; the
app counts down and the same command renews it.

Tell the app which account each platform posts to in `.state\accounts.json`,
for example `{"youtube": "@your-channel", "instagram": "@you", "threads": "@you",
"tiktok": "@you"}`. Every run stops before posting if the signed-in account
differs (Instagram switches to the right one by itself).

**Extras** (Settings → setup checklist): the USB recovery helper (recovers a
stalled USB link without a replug; one administrator prompt, then an
on-demand task that can only restart Apple's USB service and reset the
iPhone's port, see [docs/usb-recovery-helper.md](docs/usb-recovery-helper.md)),
local AI captions with Ollama, and advice on USB ports and charging.

Prefer not to run the script? `python -m pip install -r requirements.txt`,
then `python launch_video_drop.py`; fetch go-ios and the WebDriverAgent runner
yourself and set `GO_IOS_PATH` and `WDA_IPA` in `.env` (`.env.example` lists
every key).

## Everyday use

1. Export a video into your watched folder. It appears in the **Queue** once
   the file has stopped changing for 30 seconds and decodes cleanly.
2. Select it, check the suggested title and hashtags, press **Apply to all**,
   and edit any platform's text.
3. Press **Confirm details** for each platform you want.
4. Leave it on **Schedule** (it takes the next free posting time) or choose
   **Post now**, then start the run.
5. Look at the post on your phone, then click **Posted** or **Scheduled** in the
   editor so the queue shows the truth.

The header shows your **Unattended streak**: how many Schedule-mode videos in
a row reached every app without a hand fix. The goal is 20.

## Where things are kept

Drafts, settings, the local database, the phone link's state, cover
screenshots and the re-sign material live in the `.state` folder inside the
app folder. Keep it if you move or update the app, and keep it private. Media
files are read in place and never copied into the app. `tools\go-ios\` and
`wda\` hold the two downloaded components; delete them and run the installer
again to refetch.

[docs/how-it-works.md](docs/how-it-works.md) covers the details: the watch
folder, text rules per platform, the slot planner, receipts, the link
supervisor and the phone scripts. Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## Third-party software

The phone driver in `video_drop/phone/` is copied from
[SideTap](https://github.com/ucsandman/SideTap) (MIT). The installer downloads
[go-ios](https://github.com/danielpaulus/go-ios) (MIT) and the unsigned
[WebDriverAgent](https://github.com/appium/WebDriverAgent) runner (BSD-3);
you sign the latter yourself. The re-sign step runs
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (GPL-3.0) as a
separate process. License texts and details are in
[third_party/NOTICE.md](third_party/NOTICE.md).

## Develop

```powershell
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

The suite runs without a phone (`tests/conftest.py` pins the time zone and
stubs the phone-link wait, so run it with pytest, not `unittest`). GitHub
Actions runs it on Windows and Linux
with Python 3.11 and 3.14, plus the installer's `-CheckOnly` mode on Windows.
Live iPhone behavior is checked separately because CI has no phone or
platform accounts. This repo is also taking over the video workflow from the
author's earlier Homebase system; see [MIGRATION.md](MIGRATION.md).

MIT licensed; see [LICENSE](LICENSE).

---

## deutschmark's other apps

<table>
<tr><td align="center" width="56"><img src=".github/apps/pathos.svg" width="44" alt=""></td><td><a href="https://yourpathos.app"><b>Pathos</b></a><br>Worker-side job search with source-linked roles, evidence-checked resumes, and application tracking.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/markskill.svg" width="44" alt=""></td><td><a href="https://github.com/thedevmark/markskill"><b>Markskill</b></a><br>A product-engineering skill for AI agents: trace behavior to its owner, fix root causes, shape interfaces around real tasks, and verify claims with evidence.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/alert-alert.svg" width="39" alt=""></td><td><a href="https://github.com/thedevmark/alert-alert"><b>Alert! Alert!</b></a><br>Turn a video URL or local file into a cropped, trimmed stream alert.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/streamer-online.svg" width="44" alt=""></td><td><a href="https://streamer.deutschmark.online"><b>Streamer Online</b></a><br>Build OBS scenes and browser-source overlays with connected streamer tools.</td></tr>
<tr><td align="center" width="56"><img src=".github/apps/forgetmenot.png" width="32" alt=""></td><td><a href="https://github.com/thedevmark/forgetmenot"><b>ForgetMeNot</b></a><br>A local-first Twitch bot that remembers regulars, callbacks, and stream lore.</td></tr>
</table>

<sub>All projects → <a href="https://github.com/thedevmark">github.com/thedevmark</a></sub>
