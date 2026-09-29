# Native scheduling evidence and live gates

Automated iPhone Social Media Uploads must enter each approved slot in the platform's own scheduler and
read back a matching scheduled item. A local reservation is only a plan. The
Valheim clip remains a no-submit test, including the final Schedule button.
Every platform action uses its iPhone app through SideTap. Platform websites
are never an upload, scheduling, or receipt fallback. The local web editor does
not send media to a social platform.

| Destination | Documented capability | Required live proof before enabling |
| --- | --- | --- |
| YouTube | [YouTube Help](https://support.google.com/youtube/answer/1270709?hl=en) documents scheduling a private upload from Studio or the YouTube app, with date, time, and time zone. | Correct channel, source, first-frame cover, title, description hashtags, tags, audience, paid promotion, AI-use answers, chosen slot, and a matching scheduled entry. |
| Instagram | [Instagram Help](https://www.facebook.com/help/instagram/439971288310029?locale=en_US) documents scheduling Reels from the mobile app for professional accounts. Its help text states up to 25 scheduled posts per day and 75 days ahead. | Edits receives the exact OneDrive clip, exports the intended 4K file, Instagram has the intended active account, and its scheduled-content screen shows the matching Reel and time. |
| Facebook Page | The Instagram scheduling help says the Page selected for scheduled crossposting is locked at scheduling time and that account, Page, or connection changes can stop publication. | The composer names the safe Page, the crosspost switch is on, the selected Page is captured in the schedule evidence, and a later Page receipt is checked separately. |
| Threads | The observed Instagram Reel scheduler disabled Threads crossposting. The user chose immediate native Threads posting for future releases. | Share the exact OneDrive video into @deutschmarkonline, verify the attached video and approved caption, post immediately, then check a native publication receipt. Do not reserve a future Threads slot or infer a post from a timed-out tap. Disable Instagram-to-Threads crossposting when the separate Threads post path is used, so the clip is not submitted twice. |
| TikTok Gaming | [TikTok Studio Help](https://support.tiktok.com/en/using-tiktok/creating-videos/creator-tools-on-tiktok) lists uploading and scheduling, but availability varies by account and interface. | Inspect the selected TikTok iPhone account and its schedule controls. Verify the exact cover, caption, slot, and scheduled entry. If the phone app cannot schedule it, leave TikTok unscheduled. |

The slot planner's configured New York slots (10 AM and 7 PM by default) must be checked against each
selected scheduler's actual lead time and horizon. If a platform cannot accept
the chosen slot, keep that destination unscheduled and report the provider's
constraint. Never treat a final-tap timeout as a failed submission that is safe
to retry.

`video_drop.instagram_schedule.verified_scheduled_reel` compares SideTap's
native Scheduled content rows against the exact reviewed caption and intended
time in the verified iPhone time zone. It also requires the row's thumbnail to
match the source video's center-cropped first frame; unfamiliar covers fail
closed. The store's observed-schedule transition now repeats this check against
the saved source and evidence screenshot before it changes an Instagram
destination to `scheduled`. The phone runner must still verify the active
account and iPhone time zone and capture the native screen; neither a typed
account name nor a local slot alone is a receipt.

For an unconfirmed Instagram submission, the local editor's **Check Instagram
schedule** action runs `scripts/phone_instagram_receipt.py` through SideTap.
It reads the selected profile, queries the iPhone time zone with go-ios, opens
Scheduled content, and saves a screenshot under ignored local state. The store
records `scheduled` only when that same screen matches the approved caption,
planned time, and source first frame. This action cannot tap Share or Schedule.
The native menu path and time-zone query still need live validation on the
connected iPhone before this is a proven receipt path.
