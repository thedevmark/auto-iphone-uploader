# Phone social upload path

This is the observed iPhone workflow that the standalone runner must verify on
each device. App presence and a remembered login do not prove the selected
account. Keep device-specific measurements and private release records under
the ignored .state/ directory.

## Preconditions

- Choose one finished video by exact filename and validate its source hash and
  size. In OneDrive iOS, verify the full filename and displayed size in the
  preview or share sheet before passing it to another app. Search results may
  format filenames differently from the actual file.
- Inspect the active account separately in Edits and Instagram. The two apps
  can be signed into different accounts.
- Verify the active TikTok and YouTube accounts in each composer. Keep the For
  You feed out of the upload route when its accessibility tree is unavailable.
- The phone driver is built in (`video_drop/phone/`); the link supervisor owns
  the tunnel, WDA runner and forwards, and the app's setup check is the doctor.
  SideTap's viewer (localhost 8770) is optional and the supervisor stands by
  while it runs. A device listed by ios list does not prove screen capture or
  input.
- Native upload runners must use `video_drop.phone_focus.upload_focus` around
  the entire composer and final-tap phase. It verifies Do Not Disturb in
  Control Center before media work starts, preserves an already active Do Not
  Disturb state, and restores Focus after the run. An unfamiliar Control Center
  state stops the run before upload. This is wired to the YouTube, Instagram
  (via Edits) and TikTok runners; manual phone posts are outside this guard.

## OneDrive to Edits to Instagram

1. Before opening Edits, run `python scripts/phone_instagram_preflight.py
   --release-id <id>`. It checks the stored file hash, approved Instagram
   target, active Instagram profile, and ffprobe color metadata to choose SDR
   or HDR. Stop before export if any identity or color metadata is ambiguous.
   The Edits export setting must visibly match that result.
2. Find the exact file in OneDrive, submit Search, open the unique result, tap
   Share, verify its filename and size, then More for the iOS share sheet.
   Stop when the result is absent or ambiguous.
3. Choose Edits. Create a new project from that file and compare its first
   frame with the source. Do not reuse an unrelated project already open.
4. For a 4K source, inspect export settings and select the intended resolution,
   frame rate, and SDR/HDR color mode. Confirm the selected segments, export,
   and wait for completion without repeatedly polling WDA.
5. Verify the exported file in Photos or the Edits share view. Its share
   preview can show a later frame, so compare against the source rather than
   assuming the preview is the cover. Choose Instagram directly from Edits.
6. In Instagram, verify the account, Reel composer, exact authorized caption,
   first-frame cover, and any crossposting targets before the final action.
   Post now turns on the "Also share on…" Facebook and Threads switches (the
   crosspost settings, both on by default), so one Instagram upload covers all
   three. Recording Instagram's final tap as unconfirmed marks the crossposted
   Facebook and Threads destinations unconfirmed with it; each still needs its
   own receipt. Instagram's scheduler turns the Threads crosspost off, so a
   scheduled Reel carries Facebook only.

## Threads

Post now never runs a separate Threads post: Threads goes out with Instagram's
upload through its "Also share on…" Threads switch. The Threads Post now
button and `/api/releases/<id>/threads-post` refuse with that reason, and
nothing is posted.

In Schedule mode Threads must be scheduled natively in the Threads app (clip
in Photos, the **+** composer, the exact caption, then Schedule for the
release's slot), because Instagram's scheduler turns the Threads crosspost off.
That route is not built yet. `scripts/phone_threads.py` refuses scheduled
releases before touching the phone; it never posts one immediately.

The only separate Threads post is opt-in: Post now with "Crosspost to Threads
from Instagram" off and "Post Threads separately when not crossposted" on (off
by default). Then `python scripts/phone_threads.py <release-id>` prepares the
native composer from the exact OneDrive file (add `--commit` only for an
approved live run), after checking the source hash, approved text revision,
intended account, attached video, and the full pasted caption. Its
final tap is recorded as unconfirmed first, so a dropped connection never
triggers another post automatically.

## OneDrive to TikTok and YouTube

Start each composer from the same verified OneDrive file. Inspect the account
inside that app and compare the exact authorized public text before any final
tap. The YouTube path also checks audience, paid promotion, AI-use disclosure,
description hashtags, and cover frame. TikTok's composer and receipt path need
live mapping before unattended operation is claimed.

## Recovery and receipts

The default runners stop before Share, Post, or Schedule. Once an approved
release is submitted, record an unconfirmed attempt until a platform receipt
matches the account, media, text, and intended time. Never retry a timed-out
final tap without first checking the account for a duplicate. An Edits export
or ready composer is not a post receipt.

The local control path has no paid per-run model or API dependency. A free
Apple ID WebDriverAgent signature expires after seven days and needs renewal;
network access and app accounts are still required.

SideTap and go-ios can lose their route while USB still lists the phone. The
app's link supervisor (`video_drop/link_supervisor.py`, started by the server)
owns recovery: read its verdict in the Phone panel or `.state/link-status.json`
and its decisions in `.state/link-events.jsonl` before running `phone-harness
up` by hand — a second restarter fights it. "Unplug and replug the phone" is
shown only after one daemon restart failed with the phone still on USB. If
recovery cannot restore the connection, stop the batch with a clear status. Do
not infer a schedule or publication from a lost connection. Root causes and
the PC-side USB fixes: `docs/phone-link-reliability.md`.
