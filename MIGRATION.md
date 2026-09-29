# Homebase to Automated iPhone Social Media Uploads migration

Homebase stays live until every gate below is met. No removal or DB migration
should infer publication from an `uploading`, `awaiting_phone`, or ready
composer state.

## Inventory

- Homebase owns `/video-drop`, `/api/video-releases/**`, its settings endpoint,
  `lib/video-distribution/**`, `VideoRelease`/`VideoUpload` records, and video
  jobs in its scheduler.
- Campaign slots, public text revisions, scheduled derivatives, and insights
  reference those records. These need explicit replacement or a stable bridge.
- SideTap is already a separate upstream repository. This repo owns only the
  video workflow that calls it; SideTap's phone state stays local.
- Homebase's current working tree contains many unrelated changes. Copy
  feature files from that working tree without resetting or deleting them.

## Cutover gates

1. Standalone app and database can import one exact clip without moving or
   altering the media file. A duplicate import is rejected.
2. Local analysis gives editable best-effort metadata. Mark can review and
   authorize the exact final text for each destination.
3. A batch chooses the next free configured New York slot (10 AM and 7 PM by
   default, one to five per day) and enters it in each supported platform's
   native scheduler, including consecutive clips. Threads uses immediate
   native posting by operator choice. Automated iPhone Social Media Uploads records each native schedule
   or publication confirmation; it does not need to remain running until the
   scheduled publish time. Confirm scheduling in the actual TikTok
   iPhone app and account before treating TikTok as supported; its availability
   varies by interface, region, and account. Platform websites are not an
   upload or scheduling fallback.
4. The iPhone paths verify the intended account and media, handle
   YouTube audience/paid-promotion/AI disclosures, use description hashtags,
   and preserve the selected first-frame cover.
5. A final tap can become `unconfirmed`; receipts resolve it without an
   automatic duplicate. Instagram/Threads/Facebook crosspost state is checked
   against the intended accounts. TikTok phone posting is mapped and tested.
6. Existing Homebase releases, revisions, destination statuses, timestamps,
   and receipts are migrated with stable IDs and counts. Backups and rollback
   restore both systems without losing a release.
7. Every Homebase caller of Video Drop is replaced or deliberately retired.
   The new app is exercised end to end; Homebase tests and build pass after
   removal. Only then remove the old route, jobs, schema, and settings.

## Current progress

| Gate | Current evidence | State |
| --- | --- | --- |
| Open-source source release | GitHub repository is public; the standalone app and MIT license are on `main`. | Published source |
| Queue and posting times | Local tests cover the default 10 AM / 7 PM New York slots, configurable one to five times daily, and atomic batch reservations. | Local planning only |
| Native schedules | YouTube has a no-submit Schedule inspection path. Instagram receipt checking is in a draft PR. No platform has an end-to-end native schedule and matching receipt from this standalone app. | Not released |
| Post now | YouTube and Threads have guarded, one-shot native actions. Their standalone live receipts are not proven. | Not released as unattended publishing |
| Homebase cutover | A merged database copy has been staged without switching the live app. Homebase caller replacement, live delivery proof, and rollback remain. | Homebase stays live |

- Separate local Git repository created. Standalone Python app, data store,
  local browser editor, queue, phone YouTube runner, and historical runbook are here.
- The MIT-licensed source repository is public at
  `github.com/thedevmark/automated-iphone-social-media-uploads`; the working
  repository and Homebase remain private. A tracked-file and initial-history
  secret scan passed on 2026-09-29. That scan does not prove every later commit
  or open branch is free of private material. Source publication is not native
  scheduling or a Homebase cutover.
- Core tests cover duplicate media, multiple queued drafts with one selected editor, consecutive slots,
  exact-text invalidation, and uncertain receipt state. An HTTP smoke check
  confirmed the app serves its page and API.
- The standalone editor shows one selected draft from its review queue. The slot planner
  records `reserved`, and the user-facing schedule endpoint rejects requests
  until a native platform scheduler can confirm them. A local reservation can
  no longer be reported as an accepted platform schedule. The public receipt
  endpoint and internal store likewise reject unverified URLs until
  account/media/text matching is implemented. Once platform work starts,
  text cannot be edited or reauthorized; an uncertain action rechecks the
  original video hash before recording an unresolved state.
- Watch folder can now be chosen and toggled from the editor. It ignores the
  folder's current videos on enable, waits for a stable closed export and full
  decode, then imports and analyzes each new original without a media copy.
  Multiple drafts remain in a review queue, with one selected video in the
  editor. Posting times are configurable from one to five per New York day,
  defaulting to 10 AM and 7 PM. The batch planner is tested across consecutive
  videos and daylight saving changes. Native scheduling is still disconnected,
  so these settings do not claim a platform schedule.
- A separate test database watched four newly rendered synthetic clips. All
  four were imported in order with distinct hashes and completed local analysis
  on their first attempt, without touching the operator's media or releases.
- The Homebase importer now checks every original file's size and SHA-256
  before writing an archive snapshot or merged copy. On 2026-09-29, all seven
  live Homebase media records matched their files and the existing migration
  preview matched seven releases and 30 uploads without a delta. This proves
  snapshot integrity at that moment, not the later native cutover.
- New-release account targets now come from ignored local `accounts.json`,
  rather than account handles committed in source. Onboarding distinguishes
  an observed selected account from a match to the intended target. The other
  app account screens still need live mapping and verification.
- The loopback app now rejects browser requests from other origins and rejects
  requests with a nonlocal Host header before they reach the picker or phone
  inspection routes.
- A read-only Homebase DB snapshot import copied 7 releases and 30 uploads
  into a separate ignored staging database. A row-by-row comparison matched
  release IDs, statuses, content hashes, destination statuses, and receipt URLs.
  No publication was attempted.
- The importer now has a read-only `--compare` mode for the staging snapshot.
  On 2026-09-28 it found no new, missing, or changed Homebase release/upload
  rows against the live source (7 releases and 30 uploads). This is a delta
  report, not a tested live cutover or rollback.
- A `--merge-copy` path now uses SQLite online backup to stage a standalone
  database copy, import Homebase history, check the source delta and SQLite
  integrity, then expose the verified candidate. A failed merge leaves no
  destination file. It rejects hash collisions and a second Homebase import.
  On 2026-09-28 a candidate copy
  contained 8 releases and 35 destinations: the existing standalone release
  plus 7 Homebase releases and 30 uploads. The original databases were not
  switched or modified. A final cutover still needs a fresh delta comparison,
  native delivery proof, and rollback exercise.
- Full native-app scheduling, independent receipt verification, Instagram Edits,
  TikTok, live data delta migration, and Homebase caller cutover remain. X is
  retired from new releases following the operator's latest preference;
  historical X records remain part of the migration.
  **Do not remove Homebase code or point users at this app as the publisher yet.**
- On 2026-09-28, SideTap doctor was green and live iPhone onboarding verified
  YouTube `@deutschmarkonline` among three signed-in channels. The no-commit
  Valheim preparation reached YouTube's details screen and visually confirmed
  the Private radio, but WebDriverAgent later wedged while reading that screen.
  Recovery then reported a screenshot tunnel timeout. No upload or schedule
  was submitted. Restore doctor to green and complete the no-commit composer
  pass before trusting the phone path; the screenshot-based radio check and
  observed reverse navigation are in the runner now.
- A later 2026-09-28 no-submit Valheim retry verified the YouTube channel,
  selected the exact OneDrive file, and reached the trim and editor screens.
  The first trim Next tap stayed on the crop screen; a visible second tap
  advanced through processing. The runner now waits for the observed editor
  state and only retries Next while the crop screen remains visible. The phone
  disconnected before the details and schedule screens could be inspected.
  No upload or schedule was submitted. The guarded `--commit` path now exists
  only for an explicitly confirmed Post now release; scheduled releases cannot
  use it.
- A subsequent no-submit phone run reached YouTube details after checking the
  channel and exact OneDrive file. The nested visibility screen showed Private
  selected, but the details screen still read Public after returning, so the
  runner stopped. No Upload Short or schedule action was tapped. The phone then
  disappeared from USB; SideTap doctor must be green before another attempt.
- Homebase Game Mode previously stopped Ollama. At the latest check, the mode
  marker was absent and Ollama was responding. A real one-frame test showed
  that `qwen2.5vl:7b` identifies the Valheim test clip, while `gemma4:latest`
  incorrectly reported no supplied image; the smaller Qwen vision model is now
  the default. The 27B text model exceeded the operator's GPU memory and timed
  out in a background draft. The default is now `qwen3:14b`: with an 8K context
  and thinking disabled, a direct full Valheim pass returned nonempty title,
  description, tags, captions, transcript, and a visible game in about 30
  seconds. A subsequent background retry on the existing firstitmedillon draft
  completed with title, description hashtags, tags, and platform captions;
  saved public text remained untouched. Suggestions remain editable and
  unauthorized.
- Official platform guidance supports scheduled YouTube publication and
  Instagram mobile Reel scheduling. The actual TikTok iPhone account and its
  native schedule controls still need inspection. Instagram's
  scheduled Facebook crosspost is tied to the selected Page; Threads Reel
  crossposting is not proven by these sources. See `docs/native-scheduling.md`.
- Each release now stores Schedule as its default delivery choice, or an
  explicit Post now choice. Post now takes no slot; the store permits one
  guarded final attempt from a draft and then requires a receipt check. The
  Threads and YouTube can launch native Post now runners from the editor after
  exact-text confirmation; their final taps become unconfirmed until matching
  native posts are checked. YouTube Post now has no connected-phone proof yet.
  The YouTube preparation runner
  takes a release ID and checks the original file and confirmed text against
  the database before opening OneDrive. A legacy manifest must match that
  release exactly. Native scheduling and automatic receipt resolution remain
  disabled pending phone proof.
- YouTube, Instagram, and Threads phone preparation now require the saved
  release account to match the ignored local `accounts.json` target. Threads
  no longer has an operator handle embedded in executable code; the scripts
  can use another creator's configured account while still failing closed on
  an account mismatch.
- Batch slot reservations now commit together in queue order or roll back
  together. With the default slots, four reviewed clips take 10 AM and 7 PM
  across two New York days. These are local holds, not platform schedules.
- On 2026-09-28, SideTap doctor reached 11/11 after the Wintun tunnel started.
  The tunnel then ended while switching from Instagram toward YouTube; doctor
  later reported no phone over USB. No clip was posted or scheduled. Native
  screen inspection must resume only after a fresh green doctor check.
- A targeted scan of 167 tracked Git blobs found an older personal Windows
  path in the phone runbook and older machine-specific OneDrive defaults in
  server/web history. The current source-folder default now uses environment
  and home-directory locations. History still needs a privacy cleanup and
  review before changing the GitHub repository from private to public.
