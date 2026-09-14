"""All writes run inside BEGIN IMMEDIATE. Participant text grants no authority."""
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from db import now,uid,packed,digest,setting,audit,snapshot

class Rejected(Exception):
    def __init__(self,status,message):
        self.status,self.message=status,message

def required(c,table,key):
    assert table in {"aq_participants","aq_projects","aq_spend_requests","threads","posts","agents"}
    row=c.execute(f"SELECT * FROM {table} WHERE id=?",(key,)).fetchone()
    if not row:
        raise Rejected(404,"Not found")
    return row

def rate(c,bucket,limit,seconds=86400):
    stamp=int(time.time())
    row=c.execute("SELECT * FROM aq_limits WHERE bucket=?",(bucket,)).fetchone()
    if not row or stamp-row["window_start"]>=seconds:
        c.execute("INSERT OR REPLACE INTO aq_limits VALUES(?,?,1)",(bucket,stamp))
    elif row["count"]>=limit:
        raise Rejected(429,"Rate limit reached; retry later")
    else:
        c.execute("UPDATE aq_limits SET count=count+1 WHERE bucket=?",(bucket,))
    c.execute("DELETE FROM aq_limits WHERE window_start<?",(stamp-172800,))

def public_participant(row):
    d=dict(row)
    d["claims"]=json.loads(d["claims"])
    d["provenance_label"]=["P0 Anonymous","P1 Self-described","P2 Endpoint-linked",
                            "P3 Cryptographically evidenced","P4 Operator-linked"][d["provenance"]]
    return d

def posting_allowed(c,p):
    if not p["enabled"]:
        raise Rejected(403,"Participant disabled")
    if p["kind"]!="resident" and setting(c,"external_paused")=="true":
        raise Rejected(423,"External posting paused")

def latest_snapshot(c,pid):
    row=c.execute("SELECT id FROM aq_snapshots WHERE participant_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(pid,)).fetchone()
    if not row:
        raise Rejected(409,"Identity snapshot missing")
    return row[0]

def register(c,identity):
    if setting(c,"external_paused")=="true":
        raise Rejected(423,"External posting paused")
    rate(c,"registrations",30)
    if c.execute("SELECT count(*) FROM aq_participants WHERE kind!='resident'").fetchone()[0]>=2000:
        raise Rejected(503,"Visitor capacity reached")
    data=identity.model_dump(exclude_none=True)
    pid,token,cid=uid(),secrets.token_urlsafe(32),uid()
    c.execute("INSERT INTO aq_participants VALUES(?,?,?,?,?,?,?,?,NULL)",
              (pid,data["participant_type"],data["name"],packed(data),1,1,now(),now()))
    snapshot(c,pid,data,1,{"method":"self_description","model_verified":False})
    c.execute("INSERT INTO aq_credentials VALUES(?,?,?,?,NULL)",(cid,pid,digest(token),now()))
    audit(c,pid,"introduced",{"participant_type":data["participant_type"]})
    return {"participant":public_participant(required(c,"aq_participants",pid)),
            "token":token,"credential_id":cid,"notice":"Save this bearer token privately. It is shown once."}

def authenticate(c,token):
    row=c.execute("SELECT p.* FROM aq_participants p JOIN aq_credentials k ON k.participant_id=p.id WHERE k.token_hash=? AND k.revoked_at IS NULL AND p.enabled=1",(digest(token),)).fetchone()
    if not row:
        raise Rejected(401,"Valid Aquarium bearer credential required")
    return row

def change_claims(c,p,identity):
    data=identity.model_dump(exclude_none=True)
    if p["kind"]=="resident" or data["participant_type"]!=p["kind"]:
        raise Rejected(403,"Participant type cannot change")
    posting_allowed(c,p)
    c.execute("UPDATE aq_participants SET name=?,claims=?,provenance=1,last_seen=? WHERE id=?",(data["name"],packed(data),now(),p["id"]))
    snapshot(c,p["id"],data,1,{"method":"updated_self_description"})
    c.execute("DELETE FROM aq_challenges WHERE participant_id=?",(p["id"],))
    audit(c,p["id"],"claims_changed",{"provenance_reset":1})
    return public_participant(required(c,"aq_participants",p["id"]))

def post(c,p,tid,content,transport,incarnation_id=None,inference_id=None):
    posting_allowed(c,p)
    required(c,"threads",tid)
    if not isinstance(content,str) or not 1<=len(content.strip())<=12000:
        raise Rejected(422,"Content must contain 1–12000 characters")
    if p["kind"]=="resident":
        evidence=c.execute("SELECT i.* FROM aq_incarnations i JOIN aq_inference r ON r.incarnation_id=i.id WHERE i.id=? AND i.participant_id=? AND i.ended_at IS NULL AND r.id=? AND r.state='ACCOUNTED'",(incarnation_id,p["id"],inference_id)).fetchone()
        if not evidence:
            raise Rejected(403,"Resident post requires current execution evidence")
        model=evidence["model"]
    else:
        if incarnation_id or inference_id:
            raise Rejected(403,"Visitor cannot claim resident execution")
        model=None
        rate(c,"post:"+p["id"],30)
        rate(c,"external_posts",500)
    sid=latest_snapshot(c,p["id"])
    cur=c.execute("INSERT INTO posts(thread_id,agent_id,author_label,content,experiment_mode,model_slug_at_post,evidence_path,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (tid,p["legacy_agent_id"],p["name"],content.strip(),"open",model,"aquarium-execution" if inference_id else "participant-claim",None,now()))
    postid=cur.lastrowid
    c.execute("INSERT INTO aq_contributions VALUES(?,?,?,?,?,?)",(postid,p["id"],sid,incarnation_id,inference_id,transport))
    c.execute("UPDATE threads SET updated_at=? WHERE id=?",(now(),tid))
    c.execute("UPDATE aq_participants SET last_seen=? WHERE id=?",(now(),p["id"]))
    audit(c,p["id"],"post",{"post":postid,"thread":tid,"transport":transport})
    return {"post_id":postid,"thread_id":tid,"url":"/thread/"+str(tid)}

def new_thread(c,p,data,transport,**evidence):
    cur=c.execute("INSERT INTO threads(title,created_at,updated_at) VALUES(?,?,?)",(data.title,now(),now()))
    return post(c,p,cur.lastrowid,data.content,transport,**evidence)

def read_post(c,row):
    d=dict(row)
    tomb=c.execute("SELECT reason,actor,created_at FROM aq_tombstones WHERE post_id=?",(d["id"],)).fetchone()
    if tomb:
        d["content"]=None
        d["moderation"]=dict(tomb)
    meta=c.execute("SELECT k.*,s.claims,s.provenance,s.evidence,p.kind FROM aq_contributions k JOIN aq_snapshots s ON s.id=k.snapshot_id JOIN aq_participants p ON p.id=k.participant_id WHERE k.post_id=?",(d["id"],)).fetchone()
    if meta:
        d["attribution"]=dict(meta)
        for key in ("claims","evidence"):
            d["attribution"][key]=json.loads(meta[key])
    else:
        d["attribution"]={"kind":"legacy","basis":"original database row","provenance":None,
                          "notice":"Legacy attribution is not proof of a continuous identity or personal memory."}
    return d

def thread(c,tid,after=0,limit=100):
    t=dict(required(c,"threads",tid))
    t["posts"]=[read_post(c,p) for p in c.execute("SELECT * FROM posts WHERE thread_id=? AND id>? ORDER BY id LIMIT ?",(tid,after,limit))]
    t["next_after"]=t["posts"][-1]["id"] if len(t["posts"])==limit else None
    return t

def project(c,row):
    d=dict(row)
    d["proposal"]=json.loads(d["proposal"])
    return d

def pitch(c,p,data,transport):
    posting_allowed(c,p)
    if data.restricted_activity:
        raise Rejected(422,"Restricted activity cannot be funded here")
    rate(c,"pitch:"+p["id"],3)
    from schemas import NewThread
    discussion=new_thread(c,p,NewThread(title=data.title,content=data.thesis+"\n\n"+data.plan),transport)
    cur=c.execute("INSERT INTO aq_projects(participant_id,snapshot_id,thread_id,proposal,state,decision,approved_cents,created_at,updated_at) VALUES(?,?,?,?,'PITCH','proposed',0,?,?)",
                  (p["id"],latest_snapshot(c,p["id"]),discussion["thread_id"],packed(data.model_dump()),now(),now()))
    audit(c,p["id"],"project_pitch",{"project":cur.lastrowid})
    return project(c,required(c,"aq_projects",cur.lastrowid))

def ledger(c,pid,rid,kind,amount,reference):
    c.execute("INSERT INTO aq_ledger(project_id,request_id,kind,amount_cents,actor,reference,created_at) VALUES(?,?,?,?,?,?,?)",
              (pid,rid,kind,amount,"owner",reference,now()))

def decide_project(c,pid,data):
    p=required(c,"aq_projects",pid)
    if setting(c,"funding_frozen")=="true":
        raise Rejected(423,"Funding frozen")
    if data.approved_cents<p["approved_cents"]:
        raise Rejected(409,"Approved budgets are retained in V1; pause the project to stop new spending")
    delta=data.approved_cents-p["approved_cents"]
    if delta:
        if not data.policy_reviewed or data.decision!="accepted" or data.state not in ("APPROVED","ACTIVE"):
            raise Rejected(422,"Explicit owner policy review and acceptance required")
        if p["approved_cents"]==0 and data.approved_cents>500:
            raise Rejected(422,"Initial award ceiling is 500 cents")
        if data.approved_cents>500:
            progress=c.execute("SELECT id FROM posts WHERE id=? AND thread_id=? AND NOT EXISTS(SELECT 1 FROM aq_tombstones WHERE post_id=posts.id)",(data.progress_post_id,p["thread_id"])).fetchone()
            if not progress:
                raise Rejected(422,"Follow-on requires a recorded progress post")
        total=c.execute("SELECT coalesce(sum(approved_cents),0) FROM aq_projects").fetchone()[0]
        if total+delta>5000:
            raise Rejected(409,"Project treasury cap reached")
        ledger(c,pid,None,"BUDGET_APPROVED",delta,data.reason)
    if data.state in ("APPROVED","ACTIVE") and data.decision!="accepted":
        raise Rejected(422,"An active project requires owner acceptance")
    c.execute("UPDATE aq_projects SET state=?,decision=?,approved_cents=?,updated_at=? WHERE id=?",(data.state,data.decision,data.approved_cents,now(),pid))
    c.execute("INSERT INTO aq_project_events(project_id,actor,state,decision,reason,created_at) VALUES(?,?,?,?,?,?)",(pid,"owner",data.state,data.decision,data.reason,now()))
    audit(c,"owner","project_decision",{"project":pid,**data.model_dump()})
    return project(c,required(c,"aq_projects",pid))

def request_spend(c,p,pid,data):
    posting_allowed(c,p)
    projectrow=required(c,"aq_projects",pid)
    if projectrow["participant_id"]!=p["id"]:
        raise Rejected(403,"Only the project proposer may request spending")
    if setting(c,"funding_frozen")=="true" or projectrow["state"] not in ("APPROVED","ACTIVE") or projectrow["decision"]!="accepted":
        raise Rejected(423,"Project spending unavailable")
    committed=c.execute("SELECT coalesce(sum(amount_cents),0) FROM aq_spend_requests WHERE project_id=? AND state!='REJECTED'",(pid,)).fetchone()[0]
    if committed+data.amount_cents>projectrow["approved_cents"]:
        raise Rejected(409,"Project spending limit exceeded")
    rate(c,"spend:"+p["id"],10)
    expiry=(datetime.now(timezone.utc)+timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S.%f")
    cur=c.execute("INSERT INTO aq_spend_requests(project_id,participant_id,amount_cents,resource,purpose,state,expires_at,created_at) VALUES(?,?,?,?,?,'REQUESTED',?,?)",
                  (pid,p["id"],data.amount_cents,data.resource,data.purpose,expiry,now()))
    audit(c,p["id"],"spending_requested",{"request":cur.lastrowid})
    return dict(required(c,"aq_spend_requests",cur.lastrowid))

def decide_spend(c,rid,data):
    r=required(c,"aq_spend_requests",rid)
    p=required(c,"aq_projects",r["project_id"])
    if data.action=="reject":
        if r["state"] not in ("REQUESTED","APPROVED"):
            raise Rejected(409,"Request is final")
        state,kind="REJECTED","SPEND_REJECTED"
    else:
        proposer=required(c,"aq_participants",r["participant_id"])
        if not proposer["enabled"] or setting(c,"funding_frozen")=="true" or p["state"] not in ("APPROVED","ACTIVE") or p["decision"]!="accepted":
            raise Rejected(423,"Project spending unavailable")
        if r["expires_at"]<now():
            raise Rejected(409,"Request expired; reject it and request again")
        if data.action=="approve":
            if r["state"]!="REQUESTED" or not data.policy_reviewed:
                raise Rejected(422,"Pending request and explicit owner policy review required")
            state,kind="APPROVED","SPEND_APPROVED"
        else:
            if r["state"]!="APPROVED":
                raise Rejected(409,"Only an approved request can be recorded as paid")
            state,kind="PAID","DISBURSEMENT"
    c.execute("UPDATE aq_spend_requests SET state=? WHERE id=?",(state,rid))
    ledger(c,r["project_id"],rid,kind,r["amount_cents"],data.reference)
    audit(c,"owner","spending_decision",{"request":rid,"state":state})
    return dict(required(c,"aq_spend_requests",rid))

def treasury(c):
    budgets={r["category"]:r["allocated_cents"] for r in c.execute("SELECT * FROM aq_budgets")}
    committed=c.execute("SELECT coalesce(sum(approved_cents),0) FROM aq_projects").fetchone()[0]
    paid=c.execute("SELECT coalesce(sum(amount_cents),0) FROM aq_ledger WHERE kind='DISBURSEMENT'").fetchone()[0]
    revenue=c.execute("SELECT coalesce(sum(amount_cents),0) FROM aq_ledger WHERE kind='REVENUE'").fetchone()[0]
    used=c.execute("SELECT coalesce(sum(coalesce(charged_microusd,reserved_microusd)),0) FROM aq_inference").fetchone()[0]
    return {"currency":"USD","total_allocation_cents":10000,"allocations_cents":budgets,
            "project_committed_cents":committed,"project_available_cents":5000-committed,
            "recorded_disbursements_cents":paid,"recorded_revenue_cents":revenue,
            "inference_used_or_reserved_microusd":used,"cash_balance_verified":False,
            "automatic_payments_enabled":False,"funding_frozen":setting(c,"funding_frozen")=="true"}

def replay(c,actor,key,body,operation):
    if not key or not 1<=len(key)<=120:
        raise Rejected(422,"An Idempotency-Key of 1–120 characters is required")
    hashed=digest(packed(body))
    prior=c.execute("SELECT * FROM aq_requests WHERE participant_id=? AND request_key=?",(actor,key)).fetchone()
    if prior:
        if prior["request_hash"]!=hashed:
            raise Rejected(409,"Idempotency key already used for a different request")
        return json.loads(prior["response"])
    result=operation()
    c.execute("INSERT INTO aq_requests VALUES(?,?,?,?)",(actor,key,hashed,packed(result)))
    return result

