const assert = require('node:assert/strict');
const path = require('node:path');

const packages = [
  ['backend', 'dist/apps/backend/src/main.js'],
  ['orchestrator', 'dist/apps/orchestrator/src/main.js'],
];

for (const [name, entrypoint] of packages) {
  const packageJson = require(path.join(
    process.cwd(),
    'apps',
    name,
    'package.json'
  ));
  const command = packageJson.scripts.pm2;

  assert.ok(command.includes(entrypoint), `${name} must run its Node entrypoint`);
  assert.ok(!command.includes('start pnpm'), `${name} must not run through pnpm`);
}

console.log('PM2 direct-process supervision verified');
