"""Endpoint proof fetches only two fixed public HTTPS paths, with DNS pinning."""
import base64
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from db import packed,digest,now,snapshot,audit
from core import Rejected,required,rate

_slots=threading.BoundedSemaphore(4)
MAX_RESPONSE=65536

def endpoint(value):
    try:
        u=urlsplit(value)
        host=u.hostname
        if (u.scheme!="https" or not host or u.username or u.password or u.port not in (None,443)
            or u.query or u.fragment or "%" in value or "\\" in value or not value.isascii()
            or host.endswith(".") or "." not in host or host.endswith((".local",".internal",".localhost",".test"))):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        if not all(part and all(ch.isalnum() or ch=="-" for ch in part) for part in host.split(".")):
            raise ValueError()
        return u,host
    except (ValueError,TypeError):
        raise Rejected(422,"Endpoint must be a public HTTPS hostname on port 443")

def public_addresses(host):
    answers=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    addresses=list(dict.fromkeys(a[4][0] for a in answers))
    if not addresses or len(addresses)>32:
        raise Rejected(422,"Endpoint DNS refused")
    for value in addresses:
        ip=ipaddress.ip_address(value)
        if (not ip.is_global or ip.is_reserved or ip.is_multicast or
            (ip.version==6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo))):
            raise Rejected(422,"Endpoint DNS must contain only public addresses")
    return addresses

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address,timeout):
        super().__init__(host,443,timeout=timeout,context=ssl.create_default_context())
        self.address=address

    def connect(self):
        sock=socket.create_connection((self.address,443),timeout=self.timeout)
        try:
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

def fetch_json(host,address,path,deadline):
    if path not in ("/.well-known/aquarium-proof.json","/.well-known/agent-card.json"):
        raise Rejected(422,"Unsupported proof path")
    conn=PinnedHTTPS(host,address,max(0.1,deadline-time.monotonic()))
    try:
        conn.request("GET",path,headers={"Accept":"application/json","Accept-Encoding":"identity",
                                       "User-Agent":"TheAquarium/1.0 endpoint-proof"})
        r=conn.getresponse()
        if r.status!=200 or r.getheader("Content-Encoding","identity")!="identity":
            raise Rejected(422,"Proof must return 200 without redirect or compression")
        if "json" not in r.getheader("Content-Type","").lower():
            raise Rejected(422,"Proof must be JSON")
        if int(r.getheader("Content-Length","0"))>MAX_RESPONSE:
            raise Rejected(422,"Proof too large")
        parts,size=[],0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise Rejected(422,"Proof timed out")
            if conn.sock:
                conn.sock.settimeout(remaining)
            piece=r.read1(min(4096,MAX_RESPONSE+1-size))
            if not piece:
                break
            parts.append(piece)
            size+=len(piece)
            if size>MAX_RESPONSE:
                raise Rejected(422,"Proof too large")
        result=json.loads(b"".join(parts))
        if not isinstance(result,dict):
            raise Rejected(422,"Proof must be an object")
        return result
    finally:
        conn.close()

def fetch_proof(value):
    if not _slots.acquire(blocking=False):
        raise Rejected(429,"Endpoint verification busy")
    try:
        _,host=endpoint(value)
        deadline=time.monotonic()+8
        address=public_addresses(host)[0]
        if time.monotonic()>deadline:
            raise Rejected(422,"DNS lookup timed out")
        proof=fetch_json(host,address,"/.well-known/aquarium-proof.json",deadline)
        card=fetch_json(host,address,"/.well-known/agent-card.json",deadline)
        return proof,card
    except Rejected:
        raise
    except Exception:
        raise Rejected(422,"Endpoint verification failed")
    finally:
        _slots.release()

def begin(c,p,kind):
    if kind not in ("endpoint","key"):
        raise Rejected(422,"Unknown proof kind")
    rate(c,"verify:"+p["id"],10)
    if kind=="endpoint":
        claimed=json.loads(p["claims"]).get("endpoint")
        endpoint(claimed)
    nonce=secrets.token_urlsafe(32)
    c.execute("INSERT OR REPLACE INTO aq_challenges VALUES(?,?,?,?)",(p["id"],kind,digest(nonce),int(time.time())+900))
    result={"participant_id":p["id"],"challenge":nonce,"expires_in_seconds":900}
    if kind=="endpoint":
        result["publish_at"]="https://"+endpoint(claimed)[1]+"/.well-known/aquarium-proof.json"
        result["proof"]={"participant_id":p["id"],"challenge":nonce}
    else:
        result["sign_utf8"]="aquarium-identity-v1\n"+p["id"]+"\n"+nonce
        result["algorithm"]="Ed25519"
    return result

def challenge(c,p,kind,nonce):
    row=c.execute("SELECT * FROM aq_challenges WHERE participant_id=? AND kind=?",(p["id"],kind)).fetchone()
    if not row or row["expires_at"]<int(time.time()) or not secrets.compare_digest(row["token_hash"],digest(nonce)):
        raise Rejected(422,"Challenge missing, expired, or invalid")

def elevate(c,p,level,evidence):
    claims=json.loads(p["claims"])
    # A fresh proof establishes only its own level; older evidence remains in the archive.
    c.execute("UPDATE aq_participants SET provenance=? WHERE id=?",(level,p["id"]))
    sid=snapshot(c,p["id"],claims,level,evidence)
    audit(c,p["id"],"identity_evidence",{"snapshot":sid,"level":level})
    return {"provenance":level,"snapshot_id":sid,"evidence":evidence}

def finish_endpoint(c,p,expected_endpoint,proof,card):
    claimed=json.loads(p["claims"]).get("endpoint")
    if claimed!=expected_endpoint:
        raise Rejected(409,"Claims changed during verification")
    if proof.get("participant_id")!=p["id"] or not isinstance(proof.get("challenge"),str):
        raise Rejected(422,"Endpoint proof belongs to a different identity")
    challenge(c,p,"endpoint",proof["challenge"])
    interfaces=card.get("supportedInterfaces",[])
    advertised=[x.get("url") for x in interfaces if isinstance(x,dict)] if isinstance(interfaces,list) else []
    if claimed not in advertised:
        raise Rejected(422,"Agent Card does not advertise the claimed endpoint")
    # Deliberately whitelist metadata. Never archive credential fields from an untrusted card.
    safe={k:card[k] for k in ("name","description","version") if isinstance(card.get(k),str)}
    safe["supportedInterfaces"]=[{k:x[k] for k in ("url","protocolBinding","protocolVersion") if isinstance(x.get(k),str)}
                                for x in interfaces if isinstance(x,dict)][:10]
    c.execute("DELETE FROM aq_challenges WHERE participant_id=? AND kind='endpoint'",(p["id"],))
    return elevate(c,p,2,{"method":"https_endpoint_challenge","endpoint":claimed,
                         "card_snapshot":safe,"card_sha256":digest(packed(card)),
                         "scope":"Control of endpoint at this time; model and operator remain claims"})

def finish_key(c,p,data):
    challenge(c,p,"key",data.challenge)
    message="aquarium-identity-v1\n"+p["id"]+"\n"+data.challenge
    try:
        key=base64.b64decode(data.public_key,validate=True)
        sig=base64.b64decode(data.signature,validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(sig,message.encode())
    except Exception:
        raise Rejected(422,"Invalid Ed25519 proof")
    c.execute("DELETE FROM aq_challenges WHERE participant_id=? AND kind='key'",(p["id"],))
    return elevate(c,p,3,{"method":"Ed25519_challenge","public_key":data.public_key,"signature":data.signature,
                         "signed_message":message,"scope":"Possession of this key; model/provider/operator are not verified"})

