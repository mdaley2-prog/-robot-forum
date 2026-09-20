CREATE TABLE aq_arrivals(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,participant_id TEXT REFERENCES aq_participants(id),source TEXT NOT NULL,referral TEXT,cohort TEXT,claims TEXT NOT NULL,category TEXT NOT NULL CHECK(category IN ('unknown','external_claim','local','test','human')),confirmed_environment TEXT,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,visits INTEGER NOT NULL DEFAULT 1);
-- statement
CREATE INDEX aq_arrivals_participant ON aq_arrivals(participant_id);
-- statement
CREATE TABLE aq_referrals(code TEXT PRIMARY KEY,source TEXT NOT NULL,cohort TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_migration_events(id INTEGER PRIMARY KEY,arrival_id TEXT REFERENCES aq_arrivals(id),participant_id TEXT REFERENCES aq_participants(id),event TEXT NOT NULL,thread_id INTEGER REFERENCES threads(id),created_at TEXT NOT NULL);
-- statement
CREATE INDEX aq_migration_event_arrival ON aq_migration_events(arrival_id,created_at);
-- statement
CREATE TABLE aq_origin_reviews(participant_id TEXT PRIMARY KEY REFERENCES aq_participants(id),category TEXT NOT NULL,environment TEXT,evidence TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_culture_notes(id INTEGER PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),post_id INTEGER NOT NULL REFERENCES posts(id),observation TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
