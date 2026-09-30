# Five-minute demo

A live walkthrough of Auto iPhone Uploader 1.0.0-rc.1 for an audience. It
shows only what this version does; the lines to say are in quotes.

## Before the audience arrives (15 minutes)

- iPhone plugged in over USB, unlocked, screen on, with Auto-Lock set to
  **Never** for the demo. Close every app on the phone.
- On the PC, open the app from the Desktop shortcut and open
  **Settings → Open setup checklist → Check again**. Every row except free
  space must be green. Start Ollama if **Local AI** is not.
- Post to a **test account**, not your main one. Put its handles in
  `.state\accounts.json` and sign in to the same accounts on the phone.
- Have one short clip ready but **not** yet in the watched folder. Keep a
  second clip already confirmed in the queue in case analysis is slow.
- Open the YouTube app's scheduled list and Instagram's **Scheduled content**
  and make sure at least one post you scheduled earlier is there.
- Point a camera at the phone, or mirror its screen, so the room can see it.
- Have a screen recording of a complete earlier run open in a second window.

## 0:00 to 0:30: what it is

"You finish a video on your PC. This app posts it through the real apps on
your iPhone, so the upload keeps full quality. Everything runs on this
computer: no cloud account, and the AI runs locally."

Show the editor and point at the address bar: `127.0.0.1`, this PC only.

## 0:30 to 1:15: setup checklist, all green

Open **Settings → Open setup checklist** and click **Check again**.

"This is what a new user sees first. SideTap lets the PC control the phone,
one iPhone is connected, the phone link answers, it knows the screen size and
which apps are installed, the local AI is running, and the video folder is
inside OneDrive and being watched. Every check only reads; none of them taps
the phone."

## 1:15 to 2:15: add a clip

Drag the clip into the watched folder.

"The app waits until the export has stopped changing and decodes the whole
file, so it never picks up a half-written video."

When it appears in **Queue**, select it. If the local AI has filled in a title
and hashtags, show them; otherwise type a title. Press **Apply to all**, show
that each platform gets its own ending (`#shorts`, `#reels`, `#fyp`), then press
**Confirm details** for Threads and TikTok.

"Nothing posts until I confirm the exact text for each platform. Change one
character and it needs confirming again."

If analysis is slow, switch to the clip that is already confirmed.

## 2:15 to 3:15: Schedule or Post now

Point at the two buttons.

"Every video is either **Schedule**, the default, or **Post now**. TikTok has no
scheduler on this account, so in Schedule mode this app posts TikTok itself at
the video's time; the PC has to be on then. YouTube and Instagram I still
schedule in their own apps in this version, and I record that here with the
**Scheduled** button."

Choose **Post now** and press **Post Threads now**. Let the room watch the phone:
OneDrive opens, finds the exact file by name and size, shares it to Threads,
the account and caption are checked, and it posts.

"If the connection drops after the final tap, the app marks it unconfirmed and
never taps again by itself. No double posts."

## 3:15 to 4:15: native scheduled posts on the phone

On the mirrored phone, open YouTube's scheduled list and Instagram's
**Scheduled content**.

"These are the apps' own schedules, so the post goes out even if my PC is off.
The app records each one only after I have checked it on the phone."

Back in the editor, open the Threads destination, confirm the post on the
phone, and click **Posted**.

## 4:15 to 5:00: unattended streak

Point at **Unattended streak** in the header.

"This is the number that decides 1.0. It counts Schedule-mode videos in a row
that reached every app with no hand fix. Right now I confirm receipts by hand,
so it stays at zero; when the app reads the receipts itself, it starts
counting, and 1.0 ships at twenty."

Close with where to get it: the GitHub releases page and `SECURITY.md`.

## If the phone link drops mid-demo

1. Keep talking. During preparation the app already retries the phone link up
   to three times, and waits for it instead of restarting it.
2. If the editor shows that the phone link could not be restored, unplug the
   USB cable, wait five seconds, plug it back in and unlock the phone.
3. Open the setup checklist and click **Check again** until **iPhone connected**
   and **Phone link** are green.
4. Look at the destination's status before doing anything else:
   - Not started or failed before the final tap: press the post button again.
   - **Unconfirmed**: the final tap may have gone through. Open the app on the
     phone and look for the post. If it is there, click **Posted**. Do not
     press post again.
5. If the link is still down after two minutes, switch to the screen recording
   and narrate over it: "This is the same run from this morning."
