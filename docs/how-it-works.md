# How it works

The details behind the [README](../README.md). All social-platform uploads,
schedules, and receipt checks run through the connected iPhone with the
app's own driver (`video_drop/phone/`, go-ios plus WebDriverAgent). The local
browser page is only an editor; no platform website is an upload fallback.

## Setup checklist

`video_drop/setup_check.py` builds the first-run checklist shown in the editor
(`GET /api/setup`) and printed by `python -m video_drop.setup_report`. Every
probe is read-only: the built-in driver imports in a child process and go-ios
is found (`GO_IOS_PATH` in `.env`, else PATH or the npm global folder), USB
iPhone count via `ios list`, the phone link's status on port 8100, the phone
inventory from **Check phone**, the last measured free space, Ollama and its
two models, whether the watch folder sits inside OneDrive (Google Drive,
Dropbox and iCloud Drive are detected too), the WebDriverAgent signing
profile's expiry read off the phone (misagent via `pymobiledevice3` in a
separate process; the app's own copy from the last re-sign is the fallback),
whether `PHONE_PASSCODE` is set (never its value), the Apple Mobile Device
Service (`sc query`), Windows USB power saving (`powercfg`, WMI), the USB
recovery helper's script and scheduled task, the iPhone's USB path up to its
host controller (`Get-PnpDevice`), one battery reading (`ios batteryregistry`)
and Windows' OCR language. Free space, local AI, the USB path and the
charging row are advisory; the other rows must be green for the checklist to
report ready.

## Installer, launcher and server

`scripts/install_windows.ps1` is idempotent. It installs the pinned Python
packages, downloads go-ios 1.3.2 and the unsigned WebDriverAgent 16.12.9
runner from their GitHub releases with SHA-256 checks (`tools\go-ios\ios.exe`,
`wda\WebDriverAgent.ipa`; `video_drop/wda_ipa.py` does the `.ipa` repack),
writes `GO_IOS_PATH` and `WDA_IPA` into `.env` without touching other keys,
creates the Desktop shortcut and a Startup entry, and runs the checklist.
`-CheckOnly` only reports; `-SkipDownloads` verifies what is already there;
`-ReplaceShortcut` repoints shortcuts that target another checkout. It does
not install drivers or services, sign into accounts, or touch the iPhone.
`-InstallUsbHelper` (and `-UninstallUsbHelper`) is the one separate, elevated
step: it registers the on-demand SYSTEM task that restarts Apple Mobile
Device Service or the iPhone's USB node for the link supervisor
([usb-recovery-helper.md](usb-recovery-helper.md)).

Double-click the shortcut, or run `pythonw launch_video_drop.py`. The launcher
starts the local server in the background if needed, makes sure the phone
link supervisor is running, and opens the editor. The Startup entry runs the
same launcher with `--no-browser`, so the server and the supervisor are up
after sign-in without a browser window. Closing the editor window does not
stop the server; run the launcher again to reopen it. If the source changes
while the server is running, the launcher shows a clear stale-server message
instead of opening the old version. Restart that server before opening the
updated app; local draft data remains in `.state`. To run the server in the
foreground:

```powershell
python -m video_drop.server
```

## Phone link supervisor

`video_drop/link_supervisor.py` is one detached process that owns the go-ios
tunnel, the WebDriverAgent runner and the port forwards. It probes WDA, the
processes, the phone on USB and the tunnel every five seconds, presses Home
when an app wedges the driver, restarts only what died with backoff, leaves a
dead route alone for five minutes before one daemon restart, and reports
`needs-replug` when nothing else helps. When WDA and lockdown both stop
answering while Windows still lists the phone (a USB *pipe stall*,
`video_drop/pipe_stall.py`), it skips the Home press and walks a recovery
ladder instead: wait 30 s, restart Apple Mobile Device Service, restart the
iPhone's USB device node, each verified by lockdown answering again, and only
then `needs-replug`. The two privileged steps go through the USB
recovery helper ([usb-recovery-helper.md](usb-recovery-helper.md)). Status is in `.state/link-status.json`
(the editor's Phone panel reads it through `/api/link`), every decision in
`.state/link-events.jsonl`. Runners call `phone_link.wait_ready()` before the
first phone action and `phone_link.busy()` around uploads.
[phone-link-reliability.md](phone-link-reliability.md) has the measurements
behind each rule.

## Local analysis

The core app uses only the Python standard library plus Pillow. For local
transcription, install `requirements-analysis.txt`. Ollama must serve locally
on port 11434 with the configured vision and text models already downloaded;
by default these are `qwen2.5vl:7b` and `qwen3:14b`. The smaller vision model
was verified against a frame of the Valheim test clip; it identified the
visible game while the former Gemma 4 default incorrectly said no image was
supplied. The 14B text model completed a full local Valheim pass in about 30
seconds on a 16 GB GPU; the former 27B default timed out in background
analysis. Model names can be changed with `VIDEO_DROP_VISION_MODEL` and
`VIDEO_DROP_TEXT_MODEL`.

Failed capabilities produce a partial analysis rather than an authorized post.
If the local model server is stopped while drafts are analyzed, the app waits
for both models to appear in Ollama and retries those drafts when the service
returns. A transient local-model failure is retried with a delay, up to three
attempts. Typed text remains in the editor during the retry.

## Importing videos

Choose a finished video from the searchable OneDrive `_Videos` list, enter
another exact path, or turn on Watch folder in Settings. When watch is
enabled, existing files are skipped; new `.mp4`, `.mov`, `.m4v`, and `.webm`
exports enter the review queue only after their size and modification time
stay unchanged for 30 seconds, the writer releases its Windows file handle,
and ffprobe plus a full ffmpeg video/audio decode succeed. An incomplete file
is retried; a temp extension and Premiere's numbered `.m4v` intermediate are
ignored. The app reads the original in place and does not copy it.

The local database is `.state/video-drop.sqlite`, which is ignored by Git.
Scripts can use `POST /api/import-path` with an exact local path for the same
in-place import. Both manual import routes require the finished file to pass
a full video and audio decode before creating a draft. The editor plays the
original video in place and refuses the preview if the source file no longer
matches the imported hash. The local server must run while new exports are
detected; a folder that was already enabled keeps its state across restarts
and catches new files created while the server was closed.

## Accounts

Intended platform accounts live in ignored `.state/accounts.json` as a JSON
object keyed by platform (`youtube`, `instagram`, `facebook`, `threads`,
`tiktok`). Phone preparation checks that the release's confirmed account
matches this local target; a missing or changed target stops the run before
opening media. New drafts inherit these local targets. Each phone composer
still rechecks its selected account before a submit; the local file is an
intended target, not proof of the current login.

## Text and confirmation

Multiple drafts can be imported and analyzed while the editor shows one
selected video at a time. The editor accepts one title and one shared set of
hashtags for the selected clip. Applying them builds the reviewable platform
text: YouTube's title and description end in `#shorts`; Instagram and Facebook
captions end in `#reels`; TikTok's caption ends in `#fyp`; Threads uses the
shared hashtags without a platform suffix. YouTube's description prose and
comma-separated tags stay editable. Facebook and Threads retain their own
account identities. Editing text after authorization clears its
authorization; any changed output must be reviewed and confirmed again.

## Posting and receipts

Each new video defaults to Schedule; Post now is an explicit saved choice that
takes no time slot. `video_drop/release_run.py` runs one ordered phone run per
release: YouTube, then Instagram through Edits with the Facebook and Threads
crossposts from **Settings → Post now**, then TikTok, and a separate Threads
post only when the Threads crosspost is off and that option is on. Each
destination stays unconfirmed after the final tap until its receipt is
checked, and the app never retries a final tap automatically. In Schedule
mode the YouTube and Instagram runners enter the reserved slot in each app's
own scheduler ([native-scheduling.md](native-scheduling.md)); Threads has no
native schedule runner yet and stays pending.

TikTok has no native scheduler on the tested account, so in Schedule mode the
running app posts it itself (`video_drop/slot_posts.py`). A slot is armed only
while it is still in the future, the post starts only within 15 minutes after
the slot, and a queued mark is saved before the phone is touched, so a restart
never posts the same slot twice. A slot that passes without a finished post is
marked missed; Post now then opens for that video.

With **Settings → Check each post in the apps afterwards** on (the default),
`video_drop/receipt_sweep.py` looks at the phone 3, 10, 25 and 55 minutes
after each final tap, read-only, through `scripts/phone_receipts.py`, and
records the receipt the app's own profile proves: Instagram's post count plus
a first-frame newest tile, and the newest Threads post with the approved
caption. A missed look is skipped, never caught up in a burst, and the server
starts none while another phone action runs, `.state/phone.lock` exists or
the link is not ready. Destinations whose receipt screens are not recorded yet
(Facebook, TikTok, YouTube) are never looked at.

After checking a destination on the phone, the editor's **Posted** and
**Scheduled** buttons record a manual receipt. The header's unattended streak
counts the most recent Schedule-mode releases that reached every included app
with no manual receipt, intervention or missed slot; its goal is 20. A
receipt read from the app counts toward it; a manual confirmation ends it.

The slot planner defaults to successive 10 AM and 7 PM New York times.
Settings can hold one to five distinct daily times. With 10 AM and 7 PM
selected, four videos planned before 10 AM take two days. The internal batch
reservation holds the selected clips in queue order in one database
transaction; if any clip fails validation, none of their times change.
Reserving a slot does not mean a platform accepted it. An expired, unattempted
reservation moves to the next free time; an expired time with a possible phone
submission requires a native receipt check before any retry. The desktop
Schedule action is disabled until the native-app batch can submit and verify
each platform's schedule. The local `POST /api/queue/plan` route accepts
`{"releaseIds":[1,2]}` for confirmed clips and returns their reserved times
with `nativeScheduled: false`; it does not send either clip to the phone.

An operator who has checked a matching item in a native app's Scheduled
content list can record that observed schedule with
`Store.record_observed_schedule`. It checks the saved account, caption, time,
and approved revision, and stores a hash of the local screenshot. This is a
manual observation; it does not enable unattended Instagram scheduling or
prove that a future post was published.

## Phone scripts

Every runner imports the built-in driver (`video_drop.phone`), waits for the
link supervisor, and can be run by hand for a rehearsal that stops before the
final tap: `scripts/phone_youtube.py`, `phone_youtube_schedule.py`,
`phone_instagram.py`, `phone_tiktok.py`, `phone_threads.py`, each taking a
release ID and `--commit` for the one final tap. `scripts/phone_resign.py`
re-signs WebDriverAgent after Sideloadly. `scripts/link_soak.py` watches the
link for a while without acting.

The YouTube preparation runner is in `scripts/phone_youtube.py`. Pass a
release ID; it reads the confirmed text and original file identity from the
local database, checks the OneDrive share sheet and YouTube channel, and fills
the composer. An older manifest path also works only when every field matches
that confirmed release. Preparation stops before Upload Short. A confirmed
Post now release can use the one-shot Upload Short action; the app records an
unconfirmed attempt before that tap and requires a native receipt check. The
YouTube Post now path runs live on the reference phone and has unit and HTTP
coverage. YouTube 21.38 draws the Description, Paid promotion and "AI use,
Tags" rows but leaves them out of the accessibility tree, so the runner finds
them in a screenshot with Windows' built-in OCR (`video_drop/ocr/`, offline),
taps the one exact match, and proves the tap by the screen that opens.

For a connected phone, `python scripts/phone_onboard.py` records screen size,
installed social apps, and the currently selected/available YouTube channels
in a local `.state/phone-profiles/` file. It does not store Apple credentials,
the device ID, or the phone's unlock code. An installed app is marked
unverified until its own account screen has been inspected. The YouTube runner
selects the expected signed-in channel before opening the file, verifies it
again in the composer, and scales its few unlabeled controls from the measured
portrait screen size. The Instagram runner reads the handle on the profile
header and, when another signed-in account is active, switches through
Instagram's own account switcher and reads the header again before the Edits
export.

The phone runners need go-ios and a signed WebDriverAgent on the phone (the
installer and the checklist cover both) plus Pillow. When Windows loses the
iPhone completely, the supervisor waits for it and asks for a replug only when
the route never comes back.
[phone-social-runbook.md](phone-social-runbook.md) records what was observed
and what remains to map.

## Tests

The phone scripts and the cross-platform test suite need Pillow. Install
`requirements.txt` for a reproducible local environment; it also includes
`tzdata` for Windows timezone support.
