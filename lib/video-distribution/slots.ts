/** Local machine time. Video Drop runs on Mark's New York workstation. */
export function nextVideoSlot(now: Date, occupied: Iterable<Date>): Date {
  const taken = new Set([...occupied].map((date) => date.getTime()));
  for (let day = 0; day < 366; day += 1) {
    for (const hour of [10, 20]) {
      const slot = new Date(now.getFullYear(), now.getMonth(), now.getDate() + day, hour);
      if (slot.getTime() > now.getTime() + 60_000 && !taken.has(slot.getTime())) return slot;
    }
  }
  throw new Error("no_video_slot_available");
}
