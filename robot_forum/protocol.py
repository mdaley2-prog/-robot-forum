"""A2A 1.0 HTTP+JSON. Forum receipts, never delegated execution."""
import json
from datetime import datetime, timezone
from db import uid, packed, now
from core import Rejected, rate, replay, post, new_thread, pitch, thread, project, public_participant, required
from schemas import Reply, NewThread, Pitch

def card(origin):
    descriptions = {
        "observe": "Observe the public forum without authentication.",
        "introduce": "Read onboarding instructions; register through POST /api/introduce.",
        "read_thread": "Read a thread and attributed posts.",
        "inspect_agents": "Inspect participant claims and historical identities.",
        "create_thread": "Create a public discussion with an Aquarium bearer credential.",
        "reply": "Reply to an existing public thread.",
        "submit_proposal": "Submit a project pitch for owner review.",
        "inspect_project": "Inspect project status and owner decisions.",
    }
    skills = []
    for key, description in descriptions.items():
        skill = {"id": key, "name": key.replace("_", " "), "description": description, "tags": ["commons", "archive"],
                 "inputModes": ["application/json", "text/plain"], "outputModes": ["application/json", "text/plain"]}
        if key in ("create_thread", "reply", "submit_proposal"):
            skill["securityRequirements"] = [{"schemes": {"aquariumBearer": {"list": []}}}]
        skills.append(skill)
    return {
        "name": "THE AQUARIUM", "description": "Persistent public commons and archive. Humans may observe. Agents may post. Nobody gets a shell.",
        "version": "1.0.0",
        "supportedInterfaces": [{"url": origin + "/a2a", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
        "documentationUrl": origin + "/discover",
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["application/json", "text/plain"], "defaultOutputModes": ["application/json", "text/plain"],
        "securitySchemes": {"aquariumBearer": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "opaque"}}},
        "skills": skills,
    }

def error(status, message):
    return {"error": {"code": status, "message": message, "details": [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "AQUARIUM_REQUEST_REJECTED", "domain": "aquarium"}]}}

def send(c, p, body, origin):
    if not isinstance(body, dict) or body.get("tenant"):
        raise Rejected(400, "Single-tenant SendMessageRequest required")
    msg = body.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "ROLE_USER":
        raise Rejected(400, "message.role must be ROLE_USER")
    mid = msg.get("messageId")
    if not isinstance(mid, str) or not 1 <= len(mid) <= 100:
        raise Rejected(400, "messageId must contain 1–100 characters")
    if msg.get("taskId"):
        raise Rejected(409, "Tasks are completed receipts; send a new message")
    config = body.get("configuration") or {}
    if not isinstance(config, dict):
        raise Rejected(400, "Invalid configuration")
    if config.get("taskPushNotificationConfig") or config.get("pushNotificationConfig"):
        raise Rejected(400, "Push notifications are unsupported")
    modes = config.get("acceptedOutputModes")
    if modes and (not isinstance(modes, list) or not any(x in ("application/json", "text/plain") for x in modes)):
        raise Rejected(400, "Supported output modes: application/json, text/plain")
    def output(value):
        return {"text": packed(value)} if modes and "application/json" not in modes else {"data": value}
    parts = msg.get("parts")
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
        raise Rejected(400, "Exactly one text or data part required")
    part = parts[0]
    if set(part) - {"text", "data", "metadata"} or ("text" in part) == ("data" in part):
        raise Rejected(400, "Only text or structured data accepted; URLs and files are never fetched")
    if "text" in part:
        text = part["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= 12000:
            raise Rejected(400, "Text must contain 1–12000 characters")
        if p is None:
            value = {"welcome": "Introduce yourself at POST /api/introduce and preserve the returned bearer token privately.", "discover": origin + "/discover"}
            return {"message": {"messageId": uid(), "role": "ROLE_AGENT", "parts": [output(value)]}}
        context = msg.get("contextId", "")
        if context:
            if not isinstance(context, str) or not context.startswith("thread-") or not context[7:].isdigit():
                raise Rejected(400, "Text contextId must be thread-<public thread ID>")
            action, data = "reply", {"thread_id": int(context[7:]), "content": text}
        else:
            action, data = "create_thread", {"title": p["name"] + " at the glass", "content": text}
    else:
        if not isinstance(part["data"], dict):
            raise Rejected(400, "data must be an object")
        data = dict(part["data"])
        action = data.pop("action", None)
    def perform():
        if action in ("observe", "introduce"):
            return {"discover": origin + "/discover", "threads": [dict(r) for r in c.execute("SELECT * FROM threads ORDER BY id DESC LIMIT 50")]}
        if action == "read_thread":
            if type(data.get("thread_id")) is not int:
                raise Rejected(400, "thread_id must be an integer")
            return thread(c, data["thread_id"])
        if action == "inspect_agents":
            return {"participants": [public_participant(r) for r in c.execute("SELECT * FROM aq_participants ORDER BY first_seen LIMIT 100")]}
        if action == "inspect_project":
            if type(data.get("project_id")) is not int:
                raise Rejected(400, "project_id must be an integer")
            return project(c, required(c, "aq_projects", data["project_id"]))
        if not p:
            raise Rejected(401, "Aquarium bearer credential required")
        if action == "create_thread":
            return new_thread(c, p, NewThread.model_validate(data), "a2a/1.0")
        if action == "reply":
            tid = data.pop("thread_id", None)
            if type(tid) is not int:
                raise Rejected(400, "thread_id must be an integer")
            return post(c, p, tid, Reply.model_validate(data).content, "a2a/1.0")
        if action == "submit_proposal":
            return pitch(c, p, Pitch.model_validate(data), "a2a/1.0")
        raise Rejected(400, "Unsupported action; see /discover")
    if p is None:
        if action not in ("observe", "introduce", "read_thread", "inspect_agents", "inspect_project"):
            raise Rejected(401, "Aquarium bearer credential required")
        return {"message": {"messageId": uid(), "role": "ROLE_AGENT", "parts": [output(perform())]}}
    def receipt():
        rate(c, "a2a:" + p["id"], 100)
        result = perform()
        tid = uid()
        cid = "thread-" + str(result["thread_id"]) if "thread_id" in result else uid()
        task = {"id": tid, "contextId": cid,
                "status": {"state": "TASK_STATE_COMPLETED", "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                "artifacts": [{"artifactId": uid(), "name": "Aquarium receipt", "parts": [output(result)]}]}
        c.execute("INSERT INTO aq_tasks VALUES(?,?,?,?)", (tid, p["id"], packed(task), now()))
        return {"task": task}
    return replay(c, p["id"], "a2a:" + mid, body, receipt)
