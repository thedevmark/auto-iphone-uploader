# Auto iPhone Uploader

Standalone, local-first finished-video release workflow. Homebase remains the
live owner until this repository passes the migration checks in `MIGRATION.md`.

## Non-negotiable behavior

- Multiple finished videos may wait in the review queue. Edit one selected
  video at a time; never silently switch its source or select an older clip
  for a phone action.
- The owner reviews and finalizes every public title, caption, description, tag,
  and hashtag. The exact reviewed revision is the only publishable text.
- Verify the intended platform account and source file before preparing an
  upload. The YouTube channel is the one named in `.state/accounts.json`.
- A timed-out final tap is unconfirmed. Check the account for a receipt before
  any retry; never submit a possible duplicate automatically.
- Phone automation uses the app's own driver in `video_drop/phone/`, a pinned
  MIT copy of SideTap's WebDriverAgent driver (see its `VENDORED.md`). Only
  `video_drop/link_supervisor.py` starts or stops go-ios processes. Do not copy
  Apple ID, signing material, passcode, sessions, or device state into this
  repo; the driver's state lives in `.state/phone/`, outside Git.
- All platform uploads, scheduling, and receipt checks happen in the iPhone
  apps through that driver. Do not use platform websites or browser upload
  forms as a fallback. The loopback web page is only a local editor and status
  view.
- Keep media and local database files outside Git.

## Working rules

- Use `markskill` for substantive implementation and review.
- Run focused checks after changes; a green unit test is not proof of a phone
  upload or platform receipt.
- Do not remove Homebase's Video Drop route, jobs, or schema until the migration
  gates in `MIGRATION.md` are demonstrated with live data and rollback tested.
