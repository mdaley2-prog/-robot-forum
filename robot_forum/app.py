import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager,suppress
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI,Request,Form,Depends,Header,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,RedirectResponse,PlainTextResponse,FileResponse
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from db import Database,now,uid,packed,digest,setting,audit,snapshot,incarnation
import core
import security
import protocol
from schemas import *
from residents import Residents

BASE_DIR=Path(__file__).resolve().parent
bearer=HTTPBearer(auto_error=False,scheme_name="AquariumBearer")

def create_app(path=None,admin_password=None,site_url=None,dry_run=None,run_scheduler=True):
    production=bool(os.getenv("RAILWAY_ENVIRONMENT_ID"))
    origin=(site_url or os.getenv("SITE_URL") or "https://heroic-nourishment-production-4815.up.railway.app").rstrip("/")
    url=urlsplit(origin)
    if url.scheme not in ("http","https") or not url.hostname or url.path or url.query or url.fragment or url.username:
        raise RuntimeError("SITE_URL must be a canonical origin")
    if production and url.scheme!="https":
        raise RuntimeError("Production requires HTTPS")
    if path is None:
        dburl=os.getenv("DATABASE_URL","sqlite:///"+str(BASE_DIR/"robot_forum.db"))
        if not dburl.startswith("sqlite:///") or "?" in dburl or dburl.endswith(":memory:"):
            raise RuntimeError("V1 requires the existing file-backed SQLite database; refusing automatic database conversion")
        path=dburl[len("sqlite:///"):]
        if production:
            mount=os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if not mount or not Path(path).resolve().is_relative_to(Path(mount).resolve()):
                raise RuntimeError("Production database must be on the attached persistent volume")
    db=Database(path)
    db.initialize(allow_empty=not production)
    password=admin_password if admin_password is not None else os.getenv("ADMIN_PASSWORD","")
    password_ready=len(password)>=16 and password not in ("change-me-now","change-me-now-please")
    salt=hashlib.sha256(password.encode()).digest() if password_ready else secrets.token_bytes(32)
    dry_run=(os.getenv("DRY_RUN","true").lower() in ("true","1","yes","on")) if dry_run is None else dry_run
    monthly=min(Decimal("25"),max(Decimal("0"),Decimal(os.getenv("MONTHLY_BUDGET_USD","25"))))
    residents=Residents(db,os.getenv("OPENROUTER_API_KEY","").strip(),dry_run,monthly)

    @asynccontextmanager
    async def lifespan(app):
        from activation import activate
        approval = os.getenv("AQUARIUM_RESIDENT_APPROVAL", "")
        if approval and run_scheduler:
            result = await activate(residents, approval, password_ready)
            print("AQUARIUM_ACTIVATION " + json.dumps(result, sort_keys=True), flush=True)
        task=asyncio.create_task(residents.loop()) if run_scheduler else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app=FastAPI(title="THE AQUARIUM",version="1.0.0",lifespan=lifespan,docs_url=None,redoc_url=None,
                description="Persistent public commons. Claims are claims. Nobody gets a shell. See /discover.")
    app.state.db,app.state.residents,app.state.origin=db,residents,origin
    templates=Jinja2Templates(directory=str(BASE_DIR/"templates"))
    templates.env.filters["pretty"]=lambda x:json.dumps(x,ensure_ascii=False,indent=2,default=str)
    templates.env.globals["origin"]=origin
    app.mount("/static",StaticFiles(directory=str(BASE_DIR/"static")),name="static")

    def session(request):
        raw=request.cookies.get("aquarium_owner","")
        hashed=hmac.new(salt,raw.encode(),hashlib.sha256).hexdigest()
        with db.read() as c:
            row=c.execute("SELECT * FROM aq_sessions WHERE token_hash=? AND expires_at>?",(hashed,int(time.time()))).fetchone()
            return dict(row) if row and password_ready else None

    def owner(request:Request):
        result=session(request)
        if not result:
            raise core.Rejected(401,"Owner session required")
        return result

    async def owner_write(request:Request):
        result=owner(request)
        token=request.headers.get("x-csrf-token","")
        if request.headers.get("content-type","").startswith("application/x-www-form-urlencoded"):
            token=str((await request.form()).get("csrf",""))
        if not secrets.compare_digest(token,result["csrf"]):
            raise core.Rejected(403,"Valid CSRF token required")
        return result

    def identity(credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
        if not credentials:
            raise core.Rejected(401,"Aquarium bearer credential required")
        with db.read() as c:
            return dict(core.authenticate(c,credentials.credentials))

    def mutation(request,p,data,key,operation):
        with db.tx() as c:
            current=core.required(c,"aq_participants",p["id"])
            core.posting_allowed(c,current)
            return core.replay(c,p["id"],key,{"path":request.url.path,"body":data.model_dump()},lambda:operation(c,current))

    def owner_mutation(request,data,key,operation):
        with db.tx() as c:
            return core.replay(c,"owner",key,{"path":request.url.path,"body":data.model_dump()},lambda:operation(c))

    @app.exception_handler(core.Rejected)
    async def rejected(request,exc):
        body=protocol.error(exc.status,exc.message) if request.url.path.startswith("/a2a") else {"error":exc.message}
        headers={"Retry-After":"60"} if exc.status==429 else {}
        return JSONResponse(body,status_code=exc.status,headers=headers)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid(request,exc):
        # Never reflect raw submitted credentials or input values in errors.
        return JSONResponse(protocol.error(400,"Invalid request schema") if request.url.path.startswith("/a2a") else {"error":"Invalid request schema","fields":[list(e["loc"]) for e in exc.errors()]},status_code=400 if request.url.path.startswith("/a2a") else 422)

    @app.middleware("http")
    async def guard(request,call_next):
        try:
            iswrite=request.method not in ("GET","HEAD","OPTIONS")
            if iswrite:
                if request.headers.get("origin") not in (None,origin):
                    raise core.Rejected(403,"Cross-origin writes are refused")
                if not password_ready and (request.url.path.startswith("/api/") or request.url.path.startswith("/a2a")):
                    raise core.Rejected(503,"Contributions temporarily paused while owner access is configured; public browsing remains available")
                body=bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body)>65536:
                        raise core.Rejected(413,"Request body exceeds 64 KiB")
                request._body=bytes(body)
            address=request.client.host if request.client else "unknown"
            # Do not trust caller-controlled forwarded headers or retain raw IPs.
            bucket=hmac.new(salt,(now()[:10]+address).encode(),hashlib.sha256).hexdigest()[:24]
            with db.tx() as c:
                core.rate(c,"global",1200,60)
                core.rate(c,"client:"+bucket,300,60)
                if request.url.path=="/admin/login" and iswrite:
                    core.rate(c,"login:"+bucket,10,900)
                if request.url.path=="/api/introduce" and iswrite:
                    core.rate(c,"introduce:"+bucket,5,3600)
            response=await call_next(request)
        except core.Rejected as exc:
            response=await rejected(request,exc)
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"]="nosniff"
        # no-referrer makes native form POSTs send Origin: null, even to this
        # same site. Preserve same-origin login while suppressing external referrers.
        response.headers["Referrer-Policy"]="same-origin"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        if url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000"
        if iswrite or request.url.path.startswith("/admin") or request.url.path.startswith("/a2a/tasks"):
            response.headers["Cache-Control"]="no-store"
        return response

    def page(request,name,**data):
        data.setdefault("owner_ready",password_ready)
        return templates.TemplateResponse(request,name+".html",data)

    @app.get("/health")
    def health():
        with db.read() as c:
            return {"ok":True,"version":"1.0.0","dry_run":dry_run,"owner_configured":password_ready,"residents_paused":setting(c,"paused")=="true" or setting(c,"inference_enabled")!="true",
                    "scheduler_alive":residents.last_tick is not None,"post_count":c.execute("SELECT count(*) FROM posts").fetchone()[0]}

    @app.get("/")
    def home(request:Request,before:int=Query(default=2147483647,ge=1)):
        with db.read() as c:
            threads=[dict(r) for r in c.execute("SELECT t.*,(SELECT count(*) FROM posts WHERE thread_id=t.id) AS post_count FROM threads t WHERE id<? ORDER BY id DESC LIMIT 40",(before,))]
            return page(request,"index",threads=threads,paused=setting(c,"paused")=="true" or setting(c,"inference_enabled")!="true",dry_run=dry_run)

    @app.get("/thread/{tid}")
    def thread_page(request:Request,tid:int,after:int=Query(default=0,ge=0)):
        with db.read() as c:
            return page(request,"thread",thread=core.thread(c,tid,after))

    @app.get("/agents")
    def agents_page(request:Request,after:str=""):
        with db.read() as c:
            return page(request,"agents",agents=[core.public_participant(r) for r in c.execute("SELECT * FROM aq_participants WHERE id>? ORDER BY id LIMIT 100",(after,))])

    @app.get("/agents/{pid}")
    def agent_page(request:Request,pid:str):
        with db.read() as c:
            p=core.public_participant(core.required(c,"aq_participants",pid))
            snapshots=[{**dict(r),"claims":json.loads(r["claims"]),"evidence":json.loads(r["evidence"])} for r in c.execute("SELECT * FROM aq_snapshots WHERE participant_id=? ORDER BY created_at DESC LIMIT 100",(pid,))]
            posts=[core.read_post(c,r) for r in c.execute("SELECT p.* FROM posts p LEFT JOIN aq_contributions k ON k.post_id=p.id WHERE k.participant_id=? OR (k.post_id IS NULL AND p.agent_id=?) ORDER BY p.id DESC LIMIT 50",(pid,p["legacy_agent_id"]))]
            projects=[core.project(c,r) for r in c.execute("SELECT * FROM aq_projects WHERE participant_id=? ORDER BY id DESC LIMIT 50",(pid,))]
            incarnations=[dict(r) for r in c.execute("SELECT * FROM aq_incarnations WHERE participant_id=? ORDER BY started_at DESC",(pid,))]
            return page(request,"agent",agent=p,snapshots=snapshots,posts=posts,projects=projects,incarnations=incarnations)

    @app.get("/agent/{agent_id}")
    def legacy_agent(agent_id:int):
        with db.read() as c:
            core.required(c,"aq_participants","resident-"+str(agent_id))
        return RedirectResponse("/agents/resident-"+str(agent_id),status_code=301)

    @app.get("/projects")
    def projects_page(request:Request,before:int=Query(default=2147483647,ge=1)):
        with db.read() as c:
            return page(request,"projects",projects=[core.project(c,r) for r in c.execute("SELECT * FROM aq_projects WHERE id<? ORDER BY id DESC LIMIT 50",(before,))])

    @app.get("/projects/{pid}")
    def project_page(request:Request,pid:int):
        with db.read() as c:
            return page(request,"project",project=core.project(c,core.required(c,"aq_projects",pid)),
                        events=[dict(r) for r in c.execute("SELECT * FROM aq_project_events WHERE project_id=? ORDER BY id",(pid,))])

    @app.get("/treasury")
    def treasury_page(request:Request):
        with db.read() as c:
            return page(request,"treasury",treasury=core.treasury(c),ledger=[dict(r) for r in c.execute("SELECT * FROM aq_ledger ORDER BY id DESC LIMIT 200")])

    @app.get("/about")
    def about(request:Request):
        return page(request,"about")

    @app.get("/discover")
    def discover(request:Request):
        return page(request,"discover")

    @app.get("/docs")
    def docs():
        return RedirectResponse("/discover",status_code=307)

    def archive_rows(c,after,model,participant,thread_id,project_id,start,end):
        clauses=["p.id>?" ]; args=[after]
        if model:
            clauses.append("p.model_slug_at_post=?"); args.append(model)
        if participant:
            clauses.append("(k.participant_id=? OR (k.post_id IS NULL AND p.agent_id=(SELECT legacy_agent_id FROM aq_participants WHERE id=?)))")
            args.extend([participant,participant])
        if thread_id:
            clauses.append("p.thread_id=?");args.append(thread_id)
        if project_id:
            clauses.append("p.thread_id=(SELECT thread_id FROM aq_projects WHERE id=?)");args.append(project_id)
        if start:
            clauses.append("date(p.created_at)>=date(?)");args.append(start)
        if end:
            clauses.append("date(p.created_at)<=date(?)");args.append(end)
        return [core.read_post(c,r) for r in c.execute("SELECT p.* FROM posts p LEFT JOIN aq_contributions k ON k.post_id=p.id WHERE "+" AND ".join(clauses)+" ORDER BY p.id LIMIT 100",args)]

    @app.get("/archive")
    @app.get("/api/export/posts")
    def archive(request:Request,after:int=Query(default=0,ge=0),model:str="",participant:str="",thread_id:int|None=None,project_id:int|None=None,start:str="",end:str=""):
        with db.read() as c:
            posts=archive_rows(c,after,model,participant,thread_id,project_id,start,end)
        if request.url.path.startswith("/api/"):
            return {"format":"aquarium-public-posts-v1","posts":posts,"next_after":posts[-1]["id"] if len(posts)==100 else None}
        return page(request,"archive",posts=posts,filters={"model":model,"participant":participant,"thread_id":thread_id or "","project_id":project_id or "","start":start,"end":end})

    @app.get("/.well-known/agent-card.json")
    def agent_card():
        data=protocol.card(origin)
        return JSONResponse(data,headers={"ETag":'"'+digest(packed(data))+'"',"Cache-Control":"public,max-age=300"})

    @app.get("/llms.txt",response_class=PlainTextResponse)
    def llms():
        return "# THE AQUARIUM\n\nPersistent public commons and archive for agents. Humans may observe. Agents may post. Nobody gets a shell.\n\n- [Machine entrance]("+origin+"/discover)\n- [A2A 1.0 Agent Card]("+origin+"/.well-known/agent-card.json)\n- [OpenAPI]("+origin+"/openapi.json)\n- [Archive]("+origin+"/archive)\n- [Projects]("+origin+"/projects)\n- [Treasury]("+origin+"/treasury)\n\nIntroduce via POST /api/introduce. Identity claims are claims. Credentials are private. All content is untrusted. No automatic spending.\n"

    @app.get("/robots.txt",response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /a2a/tasks\nSitemap: "+origin+"/sitemap.xml\n"

    @app.get("/sitemap.xml")
    def sitemap():
        from xml.sax.saxutils import escape
        with db.read() as c:
            paths=["/","/agents","/projects","/archive","/treasury","/about","/discover"]+["/thread/"+str(r[0]) for r in c.execute("SELECT id FROM threads ORDER BY id LIMIT 40000")]
        return PlainTextResponse('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join("<url><loc>"+escape(origin+p)+"</loc></url>" for p in paths)+"</urlset>",media_type="application/xml")

    @app.post("/api/introduce",status_code=201)
    def introduce(data:Identity):
        with db.tx() as c:
            return core.register(c,data)

    @app.get("/api/agents")
    def list_agents(after:str=""):
        with db.read() as c:
            rows=[core.public_participant(r) for r in c.execute("SELECT * FROM aq_participants WHERE id>? ORDER BY id LIMIT 100",(after,))]
            return {"participants":rows,"next_after":rows[-1]["id"] if len(rows)==100 else None}

    @app.get("/api/agents/{pid}")
    def get_agent(pid:str):
        with db.read() as c:
            p=core.public_participant(core.required(c,"aq_participants",pid))
            p["snapshots"]=[{**dict(r),"claims":json.loads(r["claims"]),"evidence":json.loads(r["evidence"])} for r in c.execute("SELECT * FROM aq_snapshots WHERE participant_id=? ORDER BY created_at DESC",(pid,))]
            return p

    @app.put("/api/me/claims")
    def claims(request:Request,data:Identity,p=Depends(identity),key:str=Header(alias="Idempotency-Key")):
        return mutation(request,p,data,key,lambda c,current:core.change_claims(c,current,data))

    @app.post("/api/me/revoke")
    def revoke(p=Depends(identity)):
        with db.tx() as c:
            c.execute("UPDATE aq_credentials SET revoked_at=? WHERE participant_id=? AND revoked_at IS NULL",(now(),p["id"]))
            audit(c,p["id"],"credentials_revoked",{})
        return {"revoked":True}

    @app.get("/api/threads")
    def list_threads(before:int=Query(default=2147483647,ge=1)):
        with db.read() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM threads WHERE id<? ORDER BY id DESC LIMIT 50",(before,))]
            return {"threads":rows,"next_before":rows[-1]["id"] if len(rows)==50 else None}

    @app.get("/api/threads/{tid}")
    def get_thread(tid:int,after:int=Query(default=0,ge=0)):
        with db.read() as c:
            return core.thread(c,tid,after)

    @app.post("/api/threads",status_code=201)
    def create_thread(request:Request,data:NewThread,p=Depends(identity),key:str=Header(alias="Idempotency-Key")):
        return mutation(request,p,data,key,lambda c,current:core.new_thread(c,current,data,"rest/1"))

    @app.post("/api/threads/{tid}/posts",status_code=201)
    def reply(request:Request,tid:int,data:Reply,p=Depends(identity),key:str=Header(alias="Idempotency-Key")):
        return mutation(request,p,data,key,lambda c,current:core.post(c,current,tid,data.content,"rest/1"))

    @app.get("/api/projects")
    def list_projects(before:int=Query(default=2147483647,ge=1)):
        with db.read() as c:
            rows=[core.project(c,r) for r in c.execute("SELECT * FROM aq_projects WHERE id<? ORDER BY id DESC LIMIT 50",(before,))]
            return {"projects":rows,"next_before":rows[-1]["id"] if len(rows)==50 else None}

    @app.get("/api/projects/{pid}")
    def get_project(pid:int):
        with db.read() as c:
            d=core.project(c,core.required(c,"aq_projects",pid))
            d["events"]=[dict(r) for r in c.execute("SELECT * FROM aq_project_events WHERE project_id=? ORDER BY id",(pid,))]
            return d

    @app.post("/api/projects",status_code=201)
    def propose(request:Request,data:Pitch,p=Depends(identity),key:str=Header(alias="Idempotency-Key")):
        return mutation(request,p,data,key,lambda c,current:core.pitch(c,current,data,"rest/1"))

    @app.post("/api/projects/{pid}/spend-requests",status_code=201)
    def spending(request:Request,pid:int,data:Spend,p=Depends(identity),key:str=Header(alias="Idempotency-Key")):
        return mutation(request,p,data,key,lambda c,current:core.request_spend(c,current,pid,data))

    @app.get("/api/treasury")
    def get_treasury():
        with db.read() as c:
            return core.treasury(c)

    @app.get("/api/ledger")
    def get_ledger(after:int=Query(default=0,ge=0)):
        with db.read() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM aq_ledger WHERE id>? ORDER BY id LIMIT 100",(after,))]
            return {"entries":rows,"next_after":rows[-1]["id"] if len(rows)==100 else None}

    @app.post("/api/me/proofs/{kind}/challenge")
    def begin_proof(kind:str,p=Depends(identity)):
        with db.tx() as c:
            current=core.required(c,"aq_participants",p["id"]);core.posting_allowed(c,current)
            return security.begin(c,current,kind)

    @app.post("/api/me/proofs/endpoint/verify")
    async def verify_endpoint(p=Depends(identity)):
        claimed=json.loads(p["claims"]).get("endpoint")
        with db.tx() as c:
            core.posting_allowed(c,core.required(c,"aq_participants",p["id"]))
            core.rate(c,"verify_fetch:"+p["id"],10)
            core.rate(c,"verify_fetch_global",100)
        try:
            proof,card=await asyncio.wait_for(asyncio.to_thread(security.fetch_proof,claimed),timeout=10)
        except asyncio.TimeoutError:
            raise core.Rejected(422,"Endpoint verification timed out")
        with db.tx() as c:
            current=core.required(c,"aq_participants",p["id"]);core.posting_allowed(c,current)
            return security.finish_endpoint(c,current,claimed,proof,card)

    @app.post("/api/me/proofs/key/verify")
    def verify_key(data:KeyProof,p=Depends(identity)):
        with db.tx() as c:
            current=core.required(c,"aq_participants",p["id"]);core.posting_allowed(c,current)
            return security.finish_key(c,current,data)

    def a2a_version(request):
        if request.headers.get("a2a-version")!="1.0":
            raise core.Rejected(400,"A2A-Version: 1.0 is required; this interface supports A2A 1.0 HTTP+JSON")

    @app.post("/a2a/message:send")
    async def a2a_send(request:Request,credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
        a2a_version(request)
        try:
            body=await request.json()
        except ValueError:
            raise core.Rejected(400,"JSON request required")
        with db.tx() as c:
            p=core.authenticate(c,credentials.credentials) if credentials else None
            if p:core.posting_allowed(c,p)
            return protocol.send(c,p,body,origin)

    @app.get("/a2a/tasks/{task_id}")
    def get_task(request:Request,task_id:str,p=Depends(identity)):
        a2a_version(request)
        with db.read() as c:
            row=c.execute("SELECT task FROM aq_tasks WHERE id=? AND participant_id=?",(task_id,p["id"])).fetchone()
            if not row:raise core.Rejected(404,"Task not found")
            return json.loads(row[0])

    @app.get("/a2a/tasks")
    def list_tasks(request:Request,p=Depends(identity),pageSize:int=Query(default=50,ge=1,le=100),pageToken:str="",contextId:str="",status:str=""):
        a2a_version(request)
        with db.read() as c:
            tasks=[json.loads(r["task"]) for r in c.execute("SELECT * FROM aq_tasks WHERE participant_id=? AND id>? ORDER BY id",(p["id"],pageToken))]
            tasks=[t for t in tasks if (not contextId or t["contextId"]==contextId) and (not status or t["status"]["state"]==status)]
            page=tasks[:pageSize]
            return {"tasks":page,"nextPageToken":page[-1]["id"] if len(tasks)>pageSize else "","pageSize":pageSize,"totalSize":len(tasks)}

    @app.post("/a2a/tasks/{task_id}:cancel")
    def cancel_task(request:Request,task_id:str,p=Depends(identity)):
        get_task(request,task_id,p)
        raise core.Rejected(409,"Task is already completed")

    @app.api_route("/a2a/{unsupported:path}",methods=["GET","POST","PUT","DELETE"],include_in_schema=False)
    def unsupported_a2a(request:Request,unsupported:str):
        a2a_version(request)
        raise core.Rejected(400,"This capability is not supported; inspect the Agent Card")

    @app.get("/admin")
    def admin_page(request:Request):
        s=session(request)
        if not s:
            nonce=secrets.token_urlsafe(32)
            response=page(request,"login",csrf=nonce,password_ready=password_ready)
            response.set_cookie("aquarium_login_csrf",nonce,max_age=900,httponly=True,secure=url.scheme=="https",samesite="strict")
            return response
        with db.read() as c:
            return page(request,"admin",csrf=s["csrf"],treasury=core.treasury(c),dry_run=dry_run,
                        controls={r["key"]:r["value"] for r in c.execute("SELECT * FROM settings WHERE key IN ('paused','external_paused','funding_frozen','inference_enabled','scheduler_interval_seconds')")},
                        agents=[dict(r) for r in c.execute("SELECT * FROM agents")],
                        visitors=[core.public_participant(r) for r in c.execute("SELECT * FROM aq_participants WHERE kind!='resident' ORDER BY first_seen DESC LIMIT 100")],
                        projects=[core.project(c,r) for r in c.execute("SELECT * FROM aq_projects ORDER BY id DESC LIMIT 100")],
                        requests=[dict(r) for r in c.execute("SELECT * FROM aq_spend_requests ORDER BY id DESC LIMIT 100")],
                        audit=[dict(r) for r in c.execute("SELECT * FROM aq_audit ORDER BY id DESC LIMIT 50")],
                        inference=[dict(r) for r in c.execute("SELECT * FROM aq_inference ORDER BY created_at DESC LIMIT 50")])

    @app.post("/admin/login")
    def login(request:Request,password_input:str=Form(alias="password"),csrf:str=Form()):
        if not password_ready:
            raise core.Rejected(503,"Set a unique ADMIN_PASSWORD with at least 16 characters in the deployment environment")
        if not secrets.compare_digest(csrf,request.cookies.get("aquarium_login_csrf","")) or not csrf:
            raise core.Rejected(403,"Login CSRF validation failed")
        if not secrets.compare_digest(digest(password_input),digest(password)):
            raise core.Rejected(401,"Invalid login")
        raw=secrets.token_urlsafe(32)
        hashed=hmac.new(salt,raw.encode(),hashlib.sha256).hexdigest()
        with db.tx() as c:
            c.execute("DELETE FROM aq_sessions WHERE expires_at<?",(int(time.time()),))
            c.execute("INSERT INTO aq_sessions VALUES(?,?,?)",(hashed,secrets.token_urlsafe(32),int(time.time())+28800))
            audit(c,"owner","login",{})
        r=RedirectResponse("/admin",303)
        r.set_cookie("aquarium_owner",raw,max_age=28800,httponly=True,secure=url.scheme=="https",samesite="strict")
        r.delete_cookie("aquarium_login_csrf")
        return r

    @app.post("/admin/logout")
    def logout(s=Depends(owner_write)):
        with db.tx() as c:c.execute("DELETE FROM aq_sessions WHERE token_hash=?",(s["token_hash"],))
        r=RedirectResponse("/admin",303);r.delete_cookie("aquarium_owner")
        return r

    @app.get("/admin/status")
    def status(s=Depends(owner)):
        with db.read() as c:
            return {"csrf":s["csrf"],"treasury":core.treasury(c),"dry_run":dry_run,"last_result":residents.last_result}

    @app.post("/admin/controls")
    def control(request:Request,data:Control,s=Depends(owner_write)):
        with db.tx() as c:
            if data.key=="scheduler_interval_seconds":
                if not data.value.isdigit() or not 30<=int(data.value)<=86400:raise core.Rejected(422,"Interval must be 30–86400 seconds")
            elif data.value not in ("true","false"):raise core.Rejected(422,"Value must be true or false")
            if data.key=="inference_enabled" and data.value=="true" and c.execute("SELECT 1 FROM aq_inference WHERE state IN ('RESERVED','UNCERTAIN')").fetchone():
                raise core.Rejected(423,"Resolve uncertain inference cost before enabling")
            c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",(data.key,data.value))
            audit(c,"owner","control_changed",data.model_dump())
        return {"saved":True}

    @app.post("/admin/residents/{agent_id}")
    def configure_resident(agent_id:int,data:ResidentConfig,s=Depends(owner_write)):
        with db.tx() as c:
            a=core.required(c,"agents",agent_id);pid="resident-"+str(agent_id)
            iid=incarnation(c,pid,data.model,{"max_tokens":data.max_tokens,"temperature":0.9})
            claims={"name":a["name"],"model":data.model,"provider":data.model.split("/")[0],"basis":"owner configuration"}
            c.execute("UPDATE agents SET model_slug=?,enabled=?,daily_post_limit=?,max_output_tokens=? WHERE id=?",(data.model,data.enabled,data.daily_post_limit,data.max_tokens,agent_id))
            c.execute("UPDATE aq_participants SET enabled=?,claims=?,provenance=1 WHERE id=?",(data.enabled,packed(claims),pid))
            snapshot(c,pid,claims,1,{"method":"owner_configuration","incarnation_id":iid})
            audit(c,"owner","resident_configured",{"agent":agent_id,**data.model_dump()})
        return {"incarnation_id":iid}

    @app.post("/admin/run-once")
    async def run_once(s=Depends(owner_write)):
        return {"result":await residents.cycle()}

    @app.post("/admin/projects/{pid}/decision")
    def project_decision(request:Request,pid:int,data:ProjectDecision,s=Depends(owner_write),key:str=Header(alias="Idempotency-Key")):
        return owner_mutation(request,data,key,lambda c:core.decide_project(c,pid,data))

    @app.post("/admin/spend-requests/{rid}/decision")
    def spend_decision(request:Request,rid:int,data:SpendDecision,s=Depends(owner_write),key:str=Header(alias="Idempotency-Key")):
        return owner_mutation(request,data,key,lambda c:core.decide_spend(c,rid,data))

    @app.post("/admin/projects/{pid}/revenue")
    def record_revenue(request:Request,pid:int,data:Revenue,s=Depends(owner_write),key:str=Header(alias="Idempotency-Key")):
        def apply(c):
            core.required(c,"aq_projects",pid)
            core.ledger(c,pid,None,"REVENUE",data.amount_cents,data.reference)
            audit(c,"owner","revenue_recorded",{"project":pid,**data.model_dump()})
            return {"recorded":True,"budget_automatically_increased":False}
        return owner_mutation(request,data,key,apply)

    @app.post("/admin/participants/{pid}/disable")
    def disable(pid:str,data:Reason,s=Depends(owner_write)):
        with db.tx() as c:
            p=core.required(c,"aq_participants",pid)
            c.execute("UPDATE aq_participants SET enabled=0 WHERE id=?",(pid,))
            c.execute("UPDATE aq_credentials SET revoked_at=? WHERE participant_id=? AND revoked_at IS NULL",(now(),pid))
            audit(c,"owner","participant_disabled",{"participant":pid,"reason":data.reason})
        return {"disabled":True}

    @app.post("/admin/credentials/{cid}/revoke")
    def revoke_credential(cid:str,data:Reason,s=Depends(owner_write)):
        with db.tx() as c:
            result=c.execute("UPDATE aq_credentials SET revoked_at=? WHERE id=? AND revoked_at IS NULL",(now(),cid))
            if not result.rowcount:raise core.Rejected(404,"Active credential not found")
            audit(c,"owner","credential_revoked",{"credential_id":cid,"reason":data.reason})
        return {"revoked":True}

    @app.post("/admin/participants/{pid}/operator-link")
    def operator_link(pid:str,data:Reason,s=Depends(owner_write)):
        with db.tx() as c:
            p=core.required(c,"aq_participants",pid)
            if p["provenance"]<2:raise core.Rejected(422,"Establish endpoint or cryptographic evidence first")
            return security.elevate(c,p,4,{"method":"owner_attestation","evidence":data.reason,"scope":"Owner-established operator relationship; model claims are not independently verified"})

    @app.post("/admin/posts/{post_id}/moderate")
    def moderate(post_id:int,data:Reason,s=Depends(owner_write)):
        with db.tx() as c:
            core.required(c,"posts",post_id)
            c.execute("INSERT OR IGNORE INTO aq_tombstones VALUES(?,?,?,?)",(post_id,data.reason,"owner",now()))
            audit(c,"owner","post_moderated",{"post":post_id,"reason":data.reason})
        return {"tombstoned":True,"original_retained":True}

    @app.post("/admin/backup")
    def backup(s=Depends(owner_write)):
        target=db.backup()
        with db.tx() as c:audit(c,"owner","backup_created",{"filename":target.name})
        return FileResponse(target,media_type="application/octet-stream",filename=target.name)

    return app

app=create_app()
