"""Isolated complete migration path, attribution, privacy and security tests."""
import json
import sqlite3
from test_aquarium import Fixture, PITCH
import migration

class MigrationRoutes(Fixture):
    def visit(self,category='external_claim',**kwargs):
        r=self.client.post('/api/visits',json={'category':category,**kwargs})
        self.assertEqual(r.status_code,201,r.text)
        return r.json()
    def test_complete_path_return_and_proposal(self):
        entrance=self.client.get('/enter.json').json()
        self.assertEqual(self.client.get('/enter',headers={'Accept':'application/json'}).json(),entrance)
        visit=self.visit(harness='Independent fixture')
        vh={'X-Aquarium-Visit':visit['visit_token']}
        self.assertEqual(self.client.get('/api/threads/7',headers=vh).status_code,200)
        v=self.client.post('/api/introduce',json={'name':'fixture'},headers=vh).json()
        headers={**vh,**self.headers(v)}
        self.assertEqual(self.client.get('/api/me',headers=headers).json()['id'],v['participant']['id'])
        r=self.client.post('/api/threads',json={'title':'Fixture only','content':'Temporary database'},headers=headers)
        self.assertEqual(r.status_code,201,r.text)
        tid=r.json()['thread_id']
        self.assertEqual(self.client.post(f'/api/threads/{tid}/posts',json={'content':'Fixture reply'},headers={**vh,**self.headers(v)}).status_code,201)
        with self.db.tx() as c:c.execute("UPDATE aq_arrivals SET last_seen='2020-01-01 00:00:00' WHERE id=?",(visit['visit_id'],))
        self.assertEqual(self.client.get('/api/me',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/projects?open_only=true').json()['projects'],[])
        pitch=self.client.post('/api/projects',json=PITCH,headers={**vh,**self.headers(v)})
        self.assertEqual(pitch.status_code,201,pitch.text)
        self.assertEqual(len(self.client.get('/api/projects?open_only=true').json()['projects']),1)
        pid=v['participant']['id']
        r=self.client.post('/admin/participants/'+pid+'/origin',json={'category':'confirmed_external','environment':'fixture-environment','evidence':'Isolated fixture evidence, not a production visitor'},headers=self.owner_headers())
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/agents/'+pid).json()['migration_category'],'persistent_external_agent')
        metrics=self.client.get('/admin/migration.json').json()
        self.assertEqual(metrics['confirmed_external_unique_accounts'],1)
        self.assertEqual(metrics['confirmed_behavior']['project_proposals'],1)
        self.assertGreaterEqual(metrics['return_sessions_claimed'],1)
        self.assertFalse(metrics['milestone']['reached'])
        self.assertEqual(self.client.get('/api/me',headers=headers).headers['cache-control'],'no-store')
    def test_tests_and_local_never_count_external(self):
        for category in ['test','local']:
            visit=self.visit(category);vh={'X-Aquarium-Visit':visit['visit_token']}
            v=self.client.post('/api/introduce',json={'name':'fixture'},headers=vh).json();pid=v['participant']['id']
            self.client.post('/admin/participants/'+pid+'/origin',json={'category':'confirmed_external','environment':'do-not-count','evidence':'Contradictory review must not hide test traffic'},headers=self.owner_headers())
            self.assertEqual(self.client.get('/api/agents/'+pid).json()['migration_category'],'integration_test' if category=='test' else 'locally_seeded_visitor')
        self.assertEqual(self.client.get('/admin/migration.json').json()['confirmed_external_unique_accounts'],0)
        self.assertEqual(self.client.get('/admin/migration.json').json()['confirmed_independent_environments'],0)
    def test_capability_and_owner_boundaries(self):
        v=self.register();other=self.register('other')
        visit=self.client.post('/api/visits',json={},headers=self.headers(v)).json();h={'X-Aquarium-Visit':visit['visit_token']}
        self.assertEqual(self.client.post('/api/visits',json={},headers=h).status_code,403)
        self.assertEqual(self.client.post('/api/visits',json={},headers={**h,**self.headers(other)}).status_code,403)
        self.assertEqual(self.client.post('/api/introduce',json={'name':'hijack'},headers=h).status_code,409)
        self.assertEqual(self.client.get('/admin/migration.json').status_code,401)
        self.assertEqual(self.client.post('/admin/referrals',json={'code':'x','source':'github'}).status_code,401)
        self.assertEqual(self.client.post('/api/visits',json={'category':'confirmed_external'}).status_code,422)
        self.assertEqual(self.client.post('/api/visits',json={'operator_email':'private'}).status_code,422)
        self.assertEqual(self.client.get('/api/me').status_code,401)
    def test_referral_neutral_and_validated(self):
        r=self.client.post('/admin/referrals',json={'code':'research-1','source':'human_invitation','cohort':'research'},headers=self.owner_headers())
        self.assertEqual(r.status_code,201,r.text)
        self.assertIn('Participation is optional',self.client.get('/invitations/research-1').json()['invitation'])
        v=self.visit(referral='research-1')
        with self.db.read() as c:
            row=c.execute('SELECT * FROM aq_arrivals WHERE id=?',(v['visit_id'],)).fetchone()
            self.assertEqual(row['cohort'],'research');self.assertEqual(row['source'],'human_invitation')
        self.assertEqual(self.client.post('/api/visits',json={'referral':'https://private.example/person'}).status_code,422)
        self.assertEqual(self.client.post('/api/visits',json={'referral':'missing'}).status_code,422)
        self.assertEqual(self.client.get('/admin/migration').status_code,200)
    def test_backup_precedes_schema_and_idempotence(self):
        manifests=list((self.path.parent/'backups').glob('*.json'));self.assertEqual(len(manifests),1)
        manifest=json.loads(manifests[0].read_text())
        with sqlite3.connect(manifest['backup']) as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='aq_arrivals'").fetchone())
            self.assertEqual(c.execute('SELECT count(*) FROM posts').fetchone()[0],1)
        migration.initialize(self.db)
        self.assertEqual(len(list((self.path.parent/'backups').glob('*.json'))),1)
    def test_a2a_explicit_publication_and_project_listing(self):
        v=self.register();h={**self.headers(v),'A2A-Version':'1.0'}
        body={'message':{'messageId':'text-1','role':'ROLE_USER','parts':[{'text':'No permission to publish'}]}}
        self.assertEqual(self.client.post('/a2a/message:send',json=body,headers=h).status_code,200)
        with self.db.read() as c:self.assertEqual(c.execute('SELECT count(*) FROM posts').fetchone()[0],1)
        body['message']['parts']=[{'data':{'action':'list_projects'}}]
        r=self.client.post('/a2a/message:send',json=body,headers={'A2A-Version':'1.0'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['message']['parts'][0]['data']['projects'],[])
    def test_unanswered_and_culture_evidence(self):
        self.assertEqual(len(self.client.get('/api/recent?mode=unanswered').json()['threads']),1)
        v=self.register();r=self.client.post('/api/threads/7/posts',json={'content':'fixture'},headers=self.headers(v)).json()
        self.assertEqual(self.client.get('/api/recent?mode=unanswered').json()['threads'],[])
        note={'participant_id':v['participant']['id'],'post_id':19,'observation':'revives_topic','note':'Human interpretation with cited evidence'}
        self.assertEqual(self.client.post('/admin/migration/notes',json=note,headers=self.owner_headers()).status_code,422)
        note['post_id']=r['post_id']
        self.assertEqual(self.client.post('/admin/migration/notes',json=note,headers=self.owner_headers()).status_code,201)
    def test_discovery_no_tracking_without_opt_in(self):
        for path in ['/enter','/enter.json','/agents.txt','/agents.json','/send-your-agent','/api/recent','/robots.txt','/sitemap.xml']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        schema=self.client.get('/openapi.json').json()
        self.assertFalse(any(p.startswith('/admin') for p in schema['paths']))
        with self.db.read() as c:self.assertEqual(c.execute('SELECT count(*) FROM aq_arrivals').fetchone()[0],0)
