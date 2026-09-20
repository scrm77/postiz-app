"""OVH-only controlled rollout. Run only after documented stage acceptance."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

os.umask(0o077)
project = 'l4162hyamizty2n0eyj0gjxr'
app = 'postiz-' + project
db = 'postgres-' + project
temporal = 'temporal-' + project
root = Path('/var/backups/postiz-upgrade-20260920')
candidate = 'ghcr.io/scrm77/postiz-app@sha256:bc4a20fa42f8ea02fd022cb2de8ff41c958fce49092a439330006f4e0b60d7cc'
old_tag = 'ghcr.io/scrm77/postiz-app:tg-links'
compose = Path('/data/coolify/services/' + project + '/docker-compose.yml')
cron = Path('/etc/cron.d/postiz-watchdog')

def output(args, **kw):
    return subprocess.check_output(args, text=True, **kw).strip()

def sql(container, user, database, statement):
    return output(['docker', 'exec', '-i', container, 'psql', '-U', user, '-d', database, '-v', 'ON_ERROR_STOP=1', '-Atq'], input=statement)

db_env = dict(v.split('=', 1) for v in json.loads(output(['docker', 'inspect', db]))[0]['Config']['Env'] if '=' in v)
db_user, db_name = db_env['POSTGRES_USER'], db_env.get('POSTGRES_DB', 'postiz-db')
def app_sql(statement):
    return sql(db, db_user, db_name, statement)

def fingerprint():
    # Includes all publication records; never print their contents or credentials.
    rows = app_sql('SELECT row_to_json(p)::text FROM "Post" p ORDER BY id;')
    return hashlib.sha256(rows.encode()).hexdigest()

def ready():
    try:
        output(['docker', 'exec', app, 'node', '-e', "Promise.all(['http://localhost:3000/auth/can-register','http://localhost:3002/health/status'].map(async u=>{let r=await fetch(u,{signal:AbortSignal.timeout(5000)});if(r.status!==200)throw Error('health')})).catch(()=>process.exit(1))"], stderr=subprocess.DEVNULL, timeout=15)
        for queue in ['main', 'instagram']:
            data = json.loads(output(['docker', 'exec', temporal, 'temporal', 'task-queue', 'describe', '--address', 'temporal:7233', '--task-queue', queue, '--output', 'json'], stderr=subprocess.DEVNULL, timeout=15))
            assert {'workflow', 'activity'} <= {p.get('taskQueueType') for p in data.get('pollers', [])}
        status = output(['curl', '-L', '-sS', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}', 'https://post.yakovtsev.ru/auth'])
        return status == '200'
    except Exception:
        return False

assert (root / 'stage-accepted.json').exists(), 'No recorded stage acceptance'
assert app_sql('SELECT count(*) FROM "Post" WHERE state=\'QUEUE\' AND "deletedAt" IS NULL;') == '0', 'Queued posts block this rollout'
running = json.loads(output(['docker', 'exec', temporal, 'temporal', 'workflow', 'list', '--address', 'temporal:7233', '--query', 'ExecutionStatus="Running"', '--output', 'json']))
assert not any(x.get('type', {}).get('name', '').lower().startswith('post') for x in running), 'Active publishing workflow'
assert ready(), 'Production baseline is not healthy'
previous = json.loads(output(['docker', 'inspect', app]))[0]
assert previous['Image'] == 'sha256:ed8a53071133e162e6e6e729207e72594fb1d2aa76c9732b62dde61e70ee0448', 'Production changed since audit'
subprocess.run(['docker', 'image', 'inspect', candidate], check=True, stdout=subprocess.DEVNULL)
record = json.loads(sql('coolify-db', 'coolify', 'coolify', "SELECT row_to_json(t) FROM (SELECT docker_compose_raw,docker_compose FROM services WHERE uuid='" + project + "') t;"))
(root / 'service-before.json').write_text(json.dumps(record))
before = compose.read_text()
assert before.count(old_tag) == 1
assert all(v.count(old_tag) == 1 for v in record.values()), 'Unexpected Coolify source'
(root / 'compose-before.yml').write_text(before)
baseline = fingerprint()
with (root / 'postiz-pre-cutover.dump').open('wb') as f:
    subprocess.run(['docker', 'exec', db, 'pg_dump', '-U', db_user, '-d', db_name, '-Fc'], check=True, stdout=f)

def write_source(values, text):
    assignments = ','.join(k + "=convert_from(decode('" + base64.b64encode(v.encode()).decode() + "','base64'),'UTF8')" for k,v in values.items())
    sql('coolify-db', 'coolify', 'coolify', 'UPDATE services SET ' + assignments + " WHERE uuid='" + project + "';")
    compose.write_text(text)
    subprocess.run(['docker', 'compose', '-f', str(compose), 'config', '-q'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

start = time.monotonic()
pause = root / 'watchdog-cron-paused'
cron.rename(pause)
try:
    write_source({k:v.replace(old_tag,candidate) for k,v in record.items()}, before.replace(old_tag,candidate))
    with (root / 'rollout.log').open('w') as log:
        subprocess.run(['docker', 'compose', '-f', str(compose), 'up', '-d', '--no-deps', 'postiz'], check=True, stdout=log, stderr=log, timeout=90)
    ok = False
    while time.monotonic() - start < 360:
        if ready():
            ok = True
            break
        time.sleep(5)
    assert ok, 'Candidate did not become healthy within 6 minutes'
    assert fingerprint() == baseline, 'Publication records changed during rollout'
    actual = json.loads(output(['docker', 'inspect', app]))[0]
    assert actual['Config']['Image'] == candidate
    saved = json.loads(sql('coolify-db','coolify','coolify', "SELECT row_to_json(t) FROM (SELECT docker_compose_raw,docker_compose FROM services WHERE uuid='" + project + "') t;"))
    assert all(candidate in v for v in saved.values())
    result = {'status':'deployed', 'image':candidate, 'image_id':actual['Image'], 'seconds_to_verified':round(time.monotonic()-start), 'posts_sha256':baseline}
    (root / 'rollout-result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result), flush=True)
except Exception as error:
    # Schema is unchanged in this release. Keep current DB / delivery history.
    rollback = previous['Image']
    write_source({k:v.replace(old_tag,rollback) for k,v in record.items()}, before.replace(old_tag,rollback))
    with (root / 'rollback.log').open('w') as log:
        subprocess.run(['docker', 'compose', '-f', str(compose), 'up', '-d', '--no-deps', 'postiz'], stdout=log, stderr=log, timeout=90, check=True)
    while time.monotonic()-start < 780:
        if ready():
            print('Rolled back successfully; candidate rejected: ' + str(error), flush=True)
            raise SystemExit(1)
        time.sleep(5)
    raise RuntimeError('Rollback needs attention') from error
finally:
    pause.rename(cron)
