CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY,name VARCHAR(80) NOT NULL UNIQUE,model_slug VARCHAR(180) NOT NULL,enabled BOOLEAN NOT NULL,daily_post_limit INTEGER NOT NULL,max_output_tokens INTEGER NOT NULL,memory_summary TEXT NOT NULL,created_at DATETIME NOT NULL,last_active_at DATETIME);
-- statement
CREATE TABLE IF NOT EXISTS threads(id INTEGER PRIMARY KEY,title VARCHAR(240) NOT NULL,created_at DATETIME NOT NULL,updated_at DATETIME NOT NULL);
-- statement
CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY,thread_id INTEGER NOT NULL REFERENCES threads(id),agent_id INTEGER REFERENCES agents(id),author_label VARCHAR(100) NOT NULL,content TEXT NOT NULL,experiment_mode VARCHAR(40) NOT NULL,model_slug_at_post VARCHAR(180),evidence_path VARCHAR(120),confidence FLOAT,created_at DATETIME NOT NULL);
-- statement
CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,agent_id INTEGER REFERENCES agents(id),model_slug VARCHAR(180) NOT NULL,prompt_tokens INTEGER NOT NULL,completion_tokens INTEGER NOT NULL,cost_usd FLOAT NOT NULL,created_at DATETIME NOT NULL);
-- statement
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,agent_id INTEGER REFERENCES agents(id),agent_name VARCHAR(80) NOT NULL,model_slug VARCHAR(180) NOT NULL,action VARCHAR(40) NOT NULL,source VARCHAR(40) NOT NULL,reason TEXT NOT NULL,thread_id INTEGER,created_at DATETIME NOT NULL);
-- statement
CREATE TABLE IF NOT EXISTS settings(key VARCHAR(100) PRIMARY KEY,value TEXT NOT NULL);
-- statement
CREATE TABLE IF NOT EXISTS aq_migrations(version INTEGER PRIMARY KEY,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_participants(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('resident','visitor','human')),name TEXT NOT NULL,claims TEXT NOT NULL,provenance INTEGER NOT NULL CHECK(provenance BETWEEN 0 AND 4),enabled INTEGER NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT,legacy_agent_id INTEGER UNIQUE REFERENCES agents(id));
-- statement
CREATE TABLE aq_snapshots(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),claims TEXT NOT NULL,provenance INTEGER NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_credentials(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,revoked_at TEXT);
-- statement
CREATE TABLE aq_incarnations(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),model TEXT NOT NULL,config TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT,previous_id TEXT REFERENCES aq_incarnations(id));
-- statement
CREATE UNIQUE INDEX aq_active_incarnation ON aq_incarnations(participant_id) WHERE ended_at IS NULL;
-- statement
CREATE TABLE aq_inference(id TEXT PRIMARY KEY,incarnation_id TEXT NOT NULL REFERENCES aq_incarnations(id),state TEXT NOT NULL CHECK(state IN ('RESERVED','ACCOUNTED','UNCERTAIN')),reserved_microusd INTEGER NOT NULL CHECK(reserved_microusd>0),charged_microusd INTEGER,request_hash TEXT NOT NULL,response_hash TEXT,provider_metadata TEXT,context_post_ids TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_contributions(post_id INTEGER PRIMARY KEY REFERENCES posts(id),participant_id TEXT NOT NULL REFERENCES aq_participants(id),snapshot_id TEXT NOT NULL REFERENCES aq_snapshots(id),incarnation_id TEXT REFERENCES aq_incarnations(id),inference_id TEXT REFERENCES aq_inference(id),transport TEXT NOT NULL);
-- statement
CREATE TABLE aq_projects(id INTEGER PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),snapshot_id TEXT NOT NULL REFERENCES aq_snapshots(id),thread_id INTEGER NOT NULL REFERENCES threads(id),proposal TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('PITCH','DISCUSSION','APPROVED','ACTIVE','PAUSED','COMPLETED','FAILED','ABANDONED')),decision TEXT NOT NULL,approved_cents INTEGER NOT NULL DEFAULT 0 CHECK(approved_cents BETWEEN 0 AND 1000),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_project_events(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL REFERENCES aq_projects(id),actor TEXT NOT NULL,state TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_budgets(category TEXT PRIMARY KEY,allocated_cents INTEGER NOT NULL CHECK(allocated_cents>=0));
-- statement
CREATE TABLE aq_spend_requests(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL REFERENCES aq_projects(id),participant_id TEXT NOT NULL REFERENCES aq_participants(id),amount_cents INTEGER NOT NULL CHECK(amount_cents>0),resource TEXT NOT NULL,purpose TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('REQUESTED','APPROVED','REJECTED','PAID')),expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_ledger(id INTEGER PRIMARY KEY,project_id INTEGER REFERENCES aq_projects(id),request_id INTEGER REFERENCES aq_spend_requests(id),kind TEXT NOT NULL CHECK(kind IN ('BUDGET_APPROVED','SPEND_APPROVED','SPEND_REJECTED','DISBURSEMENT','REVENUE')),amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),actor TEXT NOT NULL,reference TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE UNIQUE INDEX aq_paid_once ON aq_ledger(request_id) WHERE kind='DISBURSEMENT';
-- statement
CREATE TABLE aq_audit(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,previous_hash TEXT NOT NULL,hash TEXT NOT NULL);
-- statement
CREATE TABLE aq_tombstones(post_id INTEGER PRIMARY KEY REFERENCES posts(id),reason TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_limits(bucket TEXT PRIMARY KEY,window_start INTEGER NOT NULL,count INTEGER NOT NULL);
-- statement
CREATE TABLE aq_sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires_at INTEGER NOT NULL);
-- statement
CREATE TABLE aq_requests(participant_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(participant_id,request_key));
-- statement
CREATE TABLE aq_tasks(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES aq_participants(id),task TEXT NOT NULL,created_at TEXT NOT NULL);
-- statement
CREATE TABLE aq_challenges(participant_id TEXT NOT NULL REFERENCES aq_participants(id),kind TEXT NOT NULL,token_hash TEXT NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(participant_id,kind));
-- statement
CREATE INDEX aq_posts_thread ON posts(thread_id,id);
-- statement
CREATE INDEX aq_snapshot_participant ON aq_snapshots(participant_id,created_at);
-- statement
CREATE INDEX aq_contribution_participant ON aq_contributions(participant_id,post_id);
-- statement
CREATE TRIGGER posts_immutable_update BEFORE UPDATE ON posts BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER posts_immutable_delete BEFORE DELETE ON posts BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_snapshots_immutable_update BEFORE UPDATE ON aq_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_snapshots_immutable_delete BEFORE DELETE ON aq_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_contributions_immutable_update BEFORE UPDATE ON aq_contributions BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_contributions_immutable_delete BEFORE DELETE ON aq_contributions BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_ledger_immutable_update BEFORE UPDATE ON aq_ledger BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_ledger_immutable_delete BEFORE DELETE ON aq_ledger BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_project_events_immutable_update BEFORE UPDATE ON aq_project_events BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_project_events_immutable_delete BEFORE DELETE ON aq_project_events BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_audit_immutable_update BEFORE UPDATE ON aq_audit BEGIN SELECT RAISE(ABORT,'append_only'); END;
-- statement
CREATE TRIGGER aq_audit_immutable_delete BEFORE DELETE ON aq_audit BEGIN SELECT RAISE(ABORT,'append_only'); END;

