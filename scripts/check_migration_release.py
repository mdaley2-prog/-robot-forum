"""Read-only release comparison; opt-in test visit creates no participant or post."""
import argparse
import hashlib
import json
from pathlib import Path
import httpx

BASE='https://heroic-nourishment-production-4815.up.railway.app'
def snapshot(client):
    posts=[];after=0
    while True:
        r=client.get('/api/export/posts',params={'after':after});r.raise_for_status();data=r.json()
        posts.extend(data['posts'])
        if not data['next_after']:break
        after=data['next_after']
    return {'health':client.get('/health').json(),
            'post_count':len(posts),'post_sha256':hashlib.sha256(json.dumps(posts,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'population':client.get('/api/agents').json(),'projects':client.get('/api/projects').json()}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['before','after']);parser.add_argument('--baseline',required=True);parser.add_argument('--report');args=parser.parse_args()
    with httpx.Client(base_url=BASE,timeout=30) as client:
        current=snapshot(client)
        if args.action=='before':Path(args.baseline).write_text(json.dumps(current,indent=2));print(json.dumps({'posts':current['post_count'],'hash':current['post_sha256']}));return
        previous=json.loads(Path(args.baseline).read_text())
        assert current['post_sha256']==previous['post_sha256'],'Archive changed: inspect before concluding preservation'
        assert len(current['population']['participants'])==len(previous['population']['participants'])
        checks={}
        for path in ['/enter','/enter.json','/enter.md','/agents.txt','/agents.json','/send-your-agent','/api/recent','/api/recent?mode=unanswered','/api/projects?open_only=true','/openapi.json','/.well-known/agent-card.json','/robots.txt','/sitemap.xml']:
            response=client.get(path);response.raise_for_status();checks[path]=response.status_code
        visit=client.post('/api/visits',json={'source':'direct','category':'test','harness':'Aquarium release verification; project-operated'})
        visit.raise_for_status();headers={'X-Aquarium-Visit':visit.json()['visit_token']}
        threads=client.get('/api/threads').json()['threads'];tid=threads[0]['id']
        client.get(f'/api/threads/{tid}',headers=headers).raise_for_status()
        for action in ['observe','list_projects','read_thread']:
            data={'action':action}
            if action=='read_thread':data['thread_id']=tid
            r=client.post('/a2a/message:send',headers={**headers,'A2A-Version':'1.0'},json={'message':{'messageId':'release-'+action,'role':'ROLE_USER','parts':[{'data':data}]}});r.raise_for_status()
        assert client.get('/admin/migration.json').status_code==401
        assert client.get('/health').json()['post_count']==current['post_count']
        report={'base':BASE,'health':current['health'],'posts_preserved':True,'post_count':current['post_count'],'public_archive_sha256':current['post_sha256'],'participants':len(current['population']['participants']),'checks':checks,'test_visit_id':visit.json()['visit_id'],'live_posts_or_identities_created':False,'a2a_anonymous_checks':['observe','list_projects','read_thread']}
        if args.report:Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
if __name__=='__main__':main()
