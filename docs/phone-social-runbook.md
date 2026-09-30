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
- SideTap normally lives under the Windows LOCALAPPDATA directory. Its viewer
  runs on localhost port 8770. Run phone-harness doctor after connection
  changes; a device listed by ios list does not prove screen capture or input.
- Native upload runners must use `video_drop.phone_focus.upload_focus` around
  the entire composer and final-tap phase. It verifies Do Not Disturb in
  Control Center before media work starts, preserves an already active Do Not
  Disturb state, and restores Focus after the run. An unfamiliar Control Center
  state stops the run before upload. This is wired to the YouTube preparation
  and Threads runner; Instagram/Edits and TikTok upload automation is not yet
  connected, so manual phone posts are outside this guard.

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

## OneDrive to Threads

Threads posts immediately for approved releases. Run `python scripts/phone_threads.py
<release-id>` to prepare a native composer from the exact OneDrive file; add
`--commit` only for an approved live run. The script checks the stored source
hash, approved text revision, @examplechannel account, attached video, and
the full pasted caption. A final tap is recorded as unconfirmed before it
happens, so a dropped connection never triggers another post automatically.
Check the native account for a receipt before clearing that state. Threads is
not assigned the release's future YouTube/Instagram slot.

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

SideTap and go-ios can lose their route while USB still lists the phone. Check
screen capture and WDA separately with phone-harness doctor. If its recovery
cannot restore the connection, stop the batch with a clear status. Do not
infer a schedule or publication from a lost connection.
