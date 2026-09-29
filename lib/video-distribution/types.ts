export const VIDEO_LANES = ["personal-reels", "gaming", "cats"] as const;
export type VideoLane = (typeof VIDEO_LANES)[number];

export const VIDEO_PLATFORMS = ["youtube", "instagram", "tiktok", "facebook", "threads", "x"] as const;
export type VideoPlatform = (typeof VIDEO_PLATFORMS)[number];

/**
 * Lanes whose post is made by hand in the phone app. The phone encoder keeps
 * far more detail than either web composer, so Homebase never opens these
 * composers: it hands the clip and approved caption to Mark and then verifies
 * the finished post by caption.
 */
export const PHONE_HANDOFF_PLATFORMS: ReadonlySet<string> = new Set(["tiktok", "instagram"]);

export interface VideoDestination {
  key: string;
  platform: VideoPlatform;
  accountKey: string;
  label: string;
}

export interface VideoPresetSettings {
  youtubeGamingSession?: string;
  youtubeCatsSession?: string;
  videoFacebookPageId?: string;
  videoThreadsHandle?: string;
  videoXHandle?: string;
}
