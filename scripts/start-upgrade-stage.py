"""Run as root on OVH with a versioned candidate image argument."""
import subprocess
import sys
from pathlib import Path

image = sys.argv[1]
assert image.startswith('ghcr.io/scrm77/postiz-app:v2.23.0-custom-')
subprocess.run(['docker', 'pull', image], check=True)
uploads = Path('/var/backups/postiz-upgrade-20260920/stage-uploads')
uploads.mkdir(mode=0o700, exist_ok=True)
subprocess.run(['tar', '-xf', '/var/backups/postiz-upgrade-20260920/uploads.tar', '-C', str(uploads)], check=True)
env = {
    'MAIN_URL': 'http://localhost:15007', 'FRONTEND_URL': 'http://localhost:15007',
    'NEXT_PUBLIC_BACKEND_URL': 'http://localhost:15007/api', 'BACKEND_INTERNAL_URL': 'http://localhost:3000',
    'JWT_SECRET': 'isolated-acceptance-only-not-production', 'NOT_SECURED': 'true',
    'DATABASE_URL': 'postgresql://stage:stage-isolated-only@postiz-acceptance-db:5432/postiz',
    'REDIS_URL': 'redis://postiz-acceptance-redis:6379', 'TEMPORAL_ADDRESS': 'temporal:7233',
    'IS_GENERAL': 'true', 'DISABLE_REGISTRATION': 'false', 'STORAGE_PROVIDER': 'local',
    'UPLOAD_DIRECTORY': '/uploads', 'NEXT_PUBLIC_UPLOAD_DIRECTORY': '/uploads',
    'NEXT_PUBLIC_UPLOAD_STATIC_DIRECTORY': '/uploads',
}
command = ['docker', 'run', '-d', '--name', 'postiz-acceptance-app', '--network', 'postiz-acceptance',
           '--memory', '4g', '-p', '127.0.0.1:15007:5000', '-v', str(uploads) + ':/uploads']
for key, value in env.items():
    command += ['-e', key + '=' + value]
# Local Prisma is the same pinned 6.5.0; avoid dlx downloading in the no-egress network.
# No accept-data-loss: an unexpected destructive change must fail the stage.
command += [image, 'sh', '-c', 'nginx && pm2 ping && pnpm exec prisma db push --schema ./libraries/nestjs-libraries/src/database/prisma/schema.prisma && pnpm run --parallel pm2 && pm2 logs']
subprocess.run(command, check=True)
