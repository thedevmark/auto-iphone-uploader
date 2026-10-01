# Native scheduling evidence and live gates

Auto iPhone Uploader must enter each approved slot in the platform's own scheduler and
read back a matching scheduled item. A local reservation is only a plan. The
Valheim clip remains a no-submit test, including the final Schedule button.
Every platform action uses its iPhone app through SideTap. Platform websites
are never an upload, scheduling, or receipt fallback. The local web editor does
not send media to a social platform.

| Destination | Documented capability | Required live proof before enabling |
| --- | --- | --- |
| YouTube | [YouTube Help](https://support.google.com/youtube/answer/1270709?hl=en) documents scheduling a private upload from Studio or the YouTube app, with date, time, and time zone. | Correct channel, source, first-frame cover, title, description hashtags, tags, audience, paid promotion, AI-use answers, chosen slot, and a matching scheduled entry. |
| Instagram | [Instagram Help](https://www.facebook.com/help/instagram/439971288310029?locale=en_US) documents scheduling Reels from the mobile app for professional accounts. Its help text states up to 25 scheduled posts per day and 75 days ahead. | Edits receives the exact OneDrive clip, exports the intended 4K file, Instagram has the intended active account, and its scheduled-content screen shows the matching Reel and time. |
| Facebook Page | The Instagram scheduling help says the Page selected for scheduled crossposting is locked at scheduling time and that account, Page, or connection changes can stop publication. | Instagram schedules the Reel natively with the Facebook crosspost on (the "Crosspost to Facebook from Instagram" setting). The composer names the safe Page, the crosspost switch is on, the selected Page is captured in the schedule evidence, and a later Page receipt is checked separately. Facebook has no final tap of its own: Instagram's recorded tap claims it. |
| Threads | The observed Instagram Reel scheduler disabled Threads crossposting. | Owner's rule: Post now always shares Threads through Instagram's "Also share on…" Threads switch in the same upload, and the app never runs a separate Threads post for it. In Schedule mode Threads must be scheduled natively in the Threads app for the release's slot: the clip in Photos, the Threads **+** composer, the exact approved caption on the release's account, then Schedule, followed by a native scheduled-item receipt. That route is not built yet: `scripts/phone_threads.py` refuses scheduled releases before touching the phone and never posts one immediately. Do not infer a post from a timed-out tap. |
| TikTok Gaming | [TikTok Studio Help](https://support.tiktok.com/en/using-tiktok/creating-videos/creator-tools-on-tiktok) lists uploading and scheduling, but availability varies by account and interface. | Inspect the selected TikTok iPhone account and its schedule controls. Verify the exact cover, caption, slot, and scheduled entry. If the phone app cannot schedule it, leave TikTok unscheduled. |

The slot planner's configured New York slots (10 AM and 7 PM by default) must be checked against each
selected scheduler's actual lead time and horizon. If a platform cannot accept
the chosen slot, keep that destination unscheduled and report the provider's
constraint. Never treat a final-tap timeout as a failed submission that is safe
to retry.

For a confirmed YouTube release with a reserved future slot, run
`python scripts/phone_youtube.py <release-id> --inspect-schedule`. It prepares
the exact OneDrive video in the intended channel, opens YouTube's native
Schedule control, and saves the resulting screen and accessibility rows under
ignored local state. It never chooses a time or taps Upload Short. This is a
mapping step until the native date/time controls and receipt are verified.

`video_drop.instagram_schedule.verified_scheduled_reel` compares SideTap's
native Scheduled content rows against the exact reviewed caption and intended
time in the verified iPhone time zone. It also requires the row's thumbnail to
match the source video's center-cropped first frame; unfamiliar covers fail
closed. The store's observed-schedule transition now repeats this check against
the saved source and evidence screenshot before it changes an Instagram
destination to `scheduled`. The phone runner must still verify the active
account and iPhone time zone and capture the native screen; neither a typed
account name nor a local slot alone is a receipt.

## Native schedule runners (built offline from the 2026-09-29 recordings)

Both runners take a Schedule-mode release with a reserved slot and enter that slot
in the app's own scheduler. The slot is shown as wall-clock time in the app's time
zone (Settings, or this PC's zone), which must be the iPhone's zone: YouTube shows
"Local Time" and Instagram says "Time zone is based on your device's settings".
When the calendar shows its "Today" day, the runner checks it against today in
that zone and stops on a mismatch. A slot less than 10 minutes away is refused
before the phone is touched and again before the final tap. The decisions
(day-button labels such as "Wednesday, September 30", month paging, time-wheel
reading and stepping, placement of the controls missing from the tree) live in
`video_drop/native_schedule.py`, with synthetic tests in
`tests/test_native_schedule.py`. Without `--commit` both stop with the slot
entered and verified, before the final tap.

**YouTube** — `python scripts/phone_youtube_schedule.py <release-id> [--commit]`.
It runs the Post now preparation from `scripts/phone_youtube.py` (file, channel,
first-frame thumbnail, title, description, audience, paid promotion, AI use,
tags), then opens Visibility and chooses Schedule. The approved visibility must be
Public, because a scheduled Short publishes as public. Then:

1. The "Sep 30, 2026 at 10:00 AM Local Time" field is drawn but not in the
   accessibility tree. The runner taps 49 pt above the bottom of the live
   privacy-settings container, and only the date alert opening counts as proof.
2. The calendar pages to the slot's month and taps its day button, which must
   then read as selected. The Time button opens the hour, minute and AM/PM
   wheels. SideTap has no value-setting call, so each wheel is stepped one row
   at a time by tapping just off its selection line, and every step is read
   back. The popover is closed on the "Time" label beside it.
3. Before OK, the selected day and the Time button label must match the slot.
   After OK the runner re-opens the field, reads the picker again, and taps OK.
4. Back on Add details, the tree still reads "Visibility, Public" even though
   the screen shows "Visibility · Scheduled" (recording details-final), so the
   picker read-back is the proof. The final button stays **Upload Short**. The
   release is marked unconfirmed before that one tap.

YouTube's scheduled-video list was never recorded, so no receipt is read back
and the destination stays unconfirmed with a message saying so.

**Instagram** — `python scripts/phone_instagram.py <release-id> [--commit]` on a
Schedule-mode release. It runs the same Edits 4K export, first-frame cover proof,
and exact caption as Post now, then More options → "Schedule this reel" → the
"Schedule reel" sheet:

1. The Date and Time rows are drawn but not in the tree. They are placed 128 pt
   and 69 pt above the live Done button's center (measured on 440 × 956). Each
   tap counts only when its popover opens.
2. Date uses the calendar popover's day buttons (recording schedule-date). The
   popover is closed by tapping the sheet's own time-zone note beside it. If the
   sheet is gone afterwards, the run stops, because one recording shows
   scheduling switched off after the calendar was used.
3. The time popover was never recorded. The runner drives it only when it shows
   iOS's hour, minute and AM/PM wheels. Anything else stops with "Instagram's
   time picker is not mapped yet".
4. Date and time are each read back by re-opening them. Done must leave "Checked,
   Schedule this reel" on More options.
5. Also share on… must show Facebook on and Threads off. Threads is unavailable
   when scheduling and is scheduled separately in the Threads app. The summary
   must read "1 profile" and the final button **Schedule**. Instagram is marked
   unconfirmed, and core claims Facebook with it. If Facebook is not claimed,
   the Schedule button is never tapped. The run then leaves for the Home Screen.

TODO(receipt): Instagram's Scheduled content screen is not recorded. Once it is,
read its rows and screenshot and pass them to `Store.record_observed_schedule`.
That call runs `instagram_schedule.verified_scheduled_reel`, which checks the
caption, time, and first-frame cover. Until then, a scheduled Instagram and
Facebook stay unconfirmed.

Live checks still needed on the phone, in this order:

- The YouTube date field tap position and the date alert opening from it.
- Wheel stepping on YouTube's time popover, including whether moving the hour
  wheel past 12 flips AM/PM (the runner re-checks either way).
- That YouTube keeps the value on re-open, and a Schedule submitted with Upload
  Short (with `--commit`, on a throwaway clip).
- Instagram's Date and Time row positions, the calendar closing on the note
  without dropping the sheet, and whether a day tap closes the calendar by
  itself (both cases are handled).
- Instagram's time popover. Record it with `scripts/record_screen.py` first.
- The composer after scheduling ("1 profile, 2 unavailable", Facebook on, button
  Schedule), then one `--commit` run.
- Instagram's and YouTube's scheduled lists, recorded, so their receipts can be
  added.
