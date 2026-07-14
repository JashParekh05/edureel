-- Attribute clip_events to the watching user. Discover/topic-feed events have
-- no session_id, so without this column they are unattributable and the
-- discover feed re-serves clips the user already watched.
-- Additive + nullable: safe to run on a live DB; old rows stay valid.
alter table clip_events add column if not exists user_id uuid;

create index if not exists idx_clip_events_user_id on clip_events (user_id);
