// Run inside the isolated acceptance container only. No production requests.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { PrismaClient } = require('@prisma/client');
assert.equal(process.env.FRONTEND_URL, 'http://localhost:15007');
assert.match(process.env.DATABASE_URL, /@postiz-acceptance-db:5432\/postiz$/);
const prisma = new PrismaClient();
async function main() {
  const integration = await prisma.integration.findFirst({ where: { providerIdentifier: 'telegram', deletedAt: null } });
  assert.ok(integration);
  const key = crypto.randomUUID();
  await prisma.organization.update({ where: { id: integration.organizationId }, data: { apiKey: key } });
  await prisma.integration.update({ where: { id: integration.id }, data: { disabled: false, token: 'stage-disabled' } });
  const headers = { authorization: key, 'content-type': 'application/json' };
  async function api(route, method = 'GET', body) {
    const r = await fetch('http://localhost:3000/public/v1' + route, { method, headers, body: body ? JSON.stringify(body) : undefined });
    const text = await r.text();
    assert.ok(r.ok, `${method} ${route}: ${r.status} ${text.slice(0, 600)}`);
    return JSON.parse(text);
  }
  await api('/integrations');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
  const form = new FormData();
  form.append('file', new Blob([png], { type: 'image/png' }), 'acceptance.png');
  const uploadResponse = await fetch('http://localhost:3000/public/v1/upload', { method: 'POST', headers: { authorization: key }, body: form });
  assert.ok(uploadResponse.ok, 'media upload must succeed');
  const uploaded = await uploadResponse.json();
  const mediaResponse = await fetch(uploaded.path.replace('http://localhost:15007', 'http://localhost:5000'));
  assert.equal(mediaResponse.status, 200);
  assert.deepEqual(Buffer.from(await mediaResponse.arrayBuffer()), png);
  console.log('media: upload and byte-for-byte HTTP readback passed');
  const stamp = 'acceptance-' + crypto.randomUUID();
  const content = `<p>${stamp} <strong>Жирный</strong> <em>Курсив</em> <s>Удалено</s> <code>код</code> <a href="https://example.com">ссылка</a></p><blockquote>Цитата</blockquote>`;
  for (const type of ['draft', 'schedule']) {
    const date = new Date(Date.now() + 86400000).toISOString();
    await api('/posts', 'POST', { type, date, shortLink: false, tags: [], posts: [{ integration: { id: integration.id }, value: [{ content, image: [] }], settings: { __type: 'telegram' } }] });
    const saved = await prisma.post.findMany({ where: { organizationId: integration.organizationId, content: { contains: stamp }, deletedAt: null } });
    assert.equal(saved.length, 1);
    assert.equal(saved[0].state, type === 'draft' ? 'DRAFT' : 'QUEUE');
    for (const fragment of ['<em>Курсив</em>', '<code>код</code>', '<blockquote>Цитата</blockquote>', '<a href="https://example.com">ссылка</a>']) assert.ok(saved[0].content.includes(fragment), fragment);
    const listed = await api('/posts?startDate=2026-01-01T00:00:00Z&endDate=2027-12-31T23:59:59Z');
    assert.ok(JSON.stringify(listed).includes(saved[0].id), 'API readback must contain created post');
    await api('/posts/' + saved[0].id, 'DELETE');
    assert.equal(await prisma.post.count({ where: { id: saved[0].id, deletedAt: null } }), 0);
    console.log(type + ': API create, persisted formatting, API readback and cancellation passed');
  }
}
main().finally(() => prisma.$disconnect()).catch(e => { console.error(e); process.exitCode = 1; });
