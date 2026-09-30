# Five-minute demo

A live walkthrough of Auto iPhone Uploader 1.0.0-rc.2 for an audience. It
shows only what this version does live; the lines to say are in quotes. What
is shown live has been run on the reference phone (iPhone 16 Pro Max, iOS
26.7); what is only described has offline tests.

## Before the audience arrives (20 minutes)

- iPhone plugged into a port on the PC itself (not a hub), unlocked, screen
  on, Auto-Lock set to **Never** for the demo. Close every app on the phone.
- On the PC, open the app from the Desktop shortcut and open
  **Settings → Open setup checklist → Check again**. Every required row must
  be green, including **Signed WebDriverAgent on the iPhone** with at least
  two days left. If it is red, run `python scripts\phone_resign.py` now, not
  on stage.
- Post to a **test account**, not your main one. Put its handles in
  `.state\accounts.json` and sign in to the same accounts on the phone in
  YouTube, Instagram, Edits, Facebook, Threads and TikTok.
- **Settings → Post now**: turn on **Crosspost to Facebook**, **Crosspost to
  Threads** and **Post in order**.
- Have one short clip ready but **not** yet in the watched folder. Keep a
  second clip already confirmed in the queue in case analysis is slow.
- Have a Schedule-mode release with a reserved slot at least an hour away,
  confirmed for YouTube, for the dry run in section 4.
- Point a camera at the phone, or mirror its screen, so the room can see it.
- Have a screen recording of a complete earlier Post now run open in a second
  window.

## 0:00 to 0:30: what it is

"You finish a video on your PC. This app posts it through the real apps on
your iPhone, so the upload keeps full quality. Everything runs on this
computer: no cloud account, the AI runs locally, and the phone driver is built
in."

Show the editor and point at the address bar: `127.0.0.1`, this PC only.

## 0:30 to 1:15: setup checklist, all green

Open **Settings → Open setup checklist** and click **Check again**.

"This is what a new user sees after the installer. The phone driver is part
of the app, one iPhone is connected, the link answers, it knows the screen
size and which apps are installed, the local AI is running, the video folder
is inside OneDrive. The driver on the phone is signed with my own Apple ID and
the app counts down the days left. The passcode is saved so the app can unlock
the phone by itself; the checklist never shows it. Every check only reads;
none of them taps the phone."

## 1:15 to 2:15: add a clip

Drag the clip into the watched folder.

"The app waits until the export has stopped changing and decodes the whole
file, so it never picks up a half-written video."

When it appears in **Queue**, select it. If the local AI has filled in a title
and hashtags, show them; otherwise type a title. Press **Apply to all**, show
that each platform gets its own ending (`#shorts`, `#reels`, `#fyp`), then press
**Confirm details** for YouTube, Instagram and TikTok.

"Nothing posts until I confirm the exact text for each platform. Change one
character and it needs confirming again."

If analysis is slow, switch to the clip that is already confirmed.

## 2:15 to 3:45: Post now, in order

Choose **Post now** and press **Post now**. Let the room watch the phone.

"YouTube first: OneDrive opens, finds the exact file by name and size, shares
it to YouTube, the channel is checked, the title and details go in, and the
cover is confirmed as the first frame before the final tap."

"Then Instagram, through Meta's Edits app, so the reel is a 4K export instead
of a compressed share. Instagram's own 'Also share on' switches are set from
my settings, so this one upload covers Instagram, Facebook and Threads."

"Then TikTok. Same account check, same cover check."

"If the connection drops after a final tap, the app marks that destination
unconfirmed and never taps again by itself. No double posts."

While the phone works, point at the Phone panel: "The link supervisor is
watching the USB tunnel and the driver the whole time. When an app freezes the
driver, it presses Home instead of restarting anything."

## 3:45 to 4:30: Schedule, as a dry run

Select the Schedule-mode release. In a terminal run
`python scripts\phone_youtube_schedule.py <id>` (no `--commit`).

"Schedule is the default mode. The app enters the slot in YouTube's own
scheduler and reads the day and time back from the picker. This version stops
here, before the final tap, because a committed schedule and its receipt are
still being verified. Instagram has the same runner. TikTok has no scheduler
on this account, so the app posts it at the slot itself. Threads scheduling in
the Threads app is not built yet, so a scheduled video leaves Threads for me."

Stop the script when it reports the slot read back.

## 4:30 to 5:00: receipts and the streak

Back in the editor, open the YouTube destination, confirm the post on the
phone, and click **Posted**. Point at **Unattended streak** in the header.

"Right now I confirm receipts by hand, so the streak stays at zero. Reading
the apps' own receipts is the piece being built; when it lands, the counter
starts, and 1.0 ships at twenty in a row."

Close with where to get it: the GitHub releases page, `SECURITY.md`, and the
launch checklist in `docs/`.

## If the phone link drops mid-demo

1. Keep talking. The supervisor waits for the phone and restarts only what
   died; the runner waits for it before every phone action.
2. If the Phone panel says **needs replug**, unplug the USB cable, wait five
   seconds, plug it back in and unlock the phone.
3. Open the setup checklist and click **Check again** until **iPhone
   connected** and **Phone link** are green.
4. Look at each destination's status before doing anything else:
   - Not started or failed before the final tap: press **Post now** again.
   - **Unconfirmed**: the final tap may have gone through. Open the app on the
     phone and look for the post. If it is there, click **Posted**. Do not
     press post again.
5. If the link is still down after two minutes, switch to the screen recording
   and narrate over it: "This is the same run from this morning."
