"""Run as root on OVH. Back up and restore into an isolated, non-publishing stage."""
import json
import os
from pathlib import Path
import subprocess
import time

os.umask(0o077)
project = 'l4162hyamizty2n0eyj0gjxr'
root = Path('/var/backups/postiz-upgrade-20260920')
root.mkdir(mode=0o700, exist_ok=False)

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

for name, command in {
    'postiz.dump': ['docker', 'exec', 'postgres-' + project, 'sh', '-c', 'pg_dump -U "$POSTGRES_USER" -d "${POSTGRES_DB:-postiz-db}" -Fc'],
    'temporal.sql': ['docker', 'exec', 'temporal-postgres-' + project, 'sh', '-c', 'pg_dumpall -U "$POSTGRES_USER"'],
    'coolify.sql': ['docker', 'exec', 'coolify-db', 'sh', '-c', 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
    'config.tar': ['tar', '-cf', '-', '/data/coolify/services/' + project, '/usr/local/bin/postiz-watchdog.sh', '/etc/cron.d/postiz-watchdog'],
    'uploads.tar': ['tar', '-cf', '-', '-C', '/var/lib/docker/volumes/' + project + '_postiz-uploads/_data', '.'],
}.items():
    with (root / name).open('wb') as output:
        run(command, stdout=output, stderr=subprocess.DEVNULL)
    print('backup', name, (root / name).stat().st_size, flush=True)

info = json.loads(subprocess.check_output(['docker', 'inspect', 'postiz-' + project]))[0]
(root / 'production-inspect.json').write_text(json.dumps(info))
run(['docker', 'network', 'create', '--internal', 'postiz-acceptance'], stdout=subprocess.DEVNULL)
run(['docker', 'run', '-d', '--name', 'postiz-acceptance-db', '--network', 'postiz-acceptance',
     '--memory', '512m', '-e', 'POSTGRES_USER=stage', '-e', 'POSTGRES_PASSWORD=stage-isolated-only',
     '-e', 'POSTGRES_DB=postiz', 'postgres:14.5'], stdout=subprocess.DEVNULL)
for _ in range(40):
    if subprocess.run(['docker', 'exec', 'postiz-acceptance-db', 'pg_isready', '-U', 'stage'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        break
    time.sleep(1)
with (root / 'postiz.dump').open('rb') as dump:
    run(['docker', 'exec', '-i', 'postiz-acceptance-db', 'pg_restore', '-U', 'stage', '-d', 'postiz', '--no-owner', '--no-acl', '--exit-on-error'], stdin=dump, stdout=subprocess.DEVNULL)
print('restore_verified', flush=True)
sql = '''UPDATE "Integration" SET token='stage-disabled', "refreshToken"=NULL, disabled=true;
UPDATE "Post" SET state='DRAFT' WHERE state='QUEUE';'''
run(['docker', 'exec', '-i', 'postiz-acceptance-db', 'psql', '-U', 'stage', '-d', 'postiz', '-v', 'ON_ERROR_STOP=1'], input=sql.encode(), stdout=subprocess.DEVNULL)
run(['docker', 'run', '-d', '--name', 'postiz-acceptance-redis', '--network', 'postiz-acceptance', '--memory', '256m', 'redis:7.2'], stdout=subprocess.DEVNULL)
run(['docker', 'run', '-d', '--name', 'postiz-acceptance-es', '--network', 'postiz-acceptance', '--memory', '768m',
     '-e', 'discovery.type=single-node', '-e', 'xpack.security.enabled=false', '-e', 'ES_JAVA_OPTS=-Xms256m -Xmx256m',
     'elasticsearch:7.17.27'], stdout=subprocess.DEVNULL)
run(['docker', 'run', '-d', '--name', 'postiz-acceptance-temporal', '--network', 'postiz-acceptance', '--network-alias', 'temporal',
     '--memory', '1536m', '-e', 'DB=postgres12', '-e', 'DB_PORT=5432', '-e', 'POSTGRES_SEEDS=postiz-acceptance-db',
     '-e', 'POSTGRES_USER=stage', '-e', 'POSTGRES_PWD=stage-isolated-only', '-e', 'ENABLE_ES=true',
     '-e', 'ES_SEEDS=postiz-acceptance-es', '-e', 'ES_PORT=9200', '-e', 'ES_VERSION=v7',
     '-e', 'DYNAMIC_CONFIG_FILE=config/dynamicconfig/development-sql.yaml', 'temporalio/auto-setup:1.28.1'], stdout=subprocess.DEVNULL)
print('isolated_stage_dependencies_started', flush=True)
