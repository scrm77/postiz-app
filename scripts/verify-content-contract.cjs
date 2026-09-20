// Exercises the real sanitizer, HTML converter and Telegram provider without sending.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function loadTs(relative, mocks = {}) {
  const filename = path.resolve(relative);
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = (name) => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  mod._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true }
  }).outputText, filename);
  return mod.exports;
}

async function main() {
  const { sanitizePostContent } = loadTs('libraries/helpers/src/utils/sanitize.post.content.ts');
  const { stripHtmlValidation } = loadTs('libraries/helpers/src/utils/strip.html.validation.ts');
  const input = '<p><strong>Жирный</strong> <u>Подчёркнутый</u> <em>Курсив</em> <s>Удалено</s> <code>код</code> <a href="https://example.com/article">Ссылка</a></p><blockquote>Цитата</blockquote>';
  const saved = sanitizePostContent(input);
  assert.equal(sanitizePostContent(saved), saved, 'save/reopen must be stable');
  for (const fragment of ['<strong>Жирный</strong>', '<u>Подчёркнутый</u>', '<em>Курсив</em>', '<s>Удалено</s>', '<code>код</code>', '<blockquote>Цитата</blockquote>', 'href="https://example.com/article"']) {
    assert.ok(saved.includes(fragment), `sanitizer lost ${fragment}`);
  }
  const unsafe = sanitizePostContent('<p onclick="bad()">Текст<script>bad()</script><a href="javascript:bad()">ссылка</a></p>');
  assert.doesNotMatch(unsafe, /onclick|<script|javascript:/i, 'formatting must not weaken sanitization');
  const formatted = stripHtmlValidation('html', saved);
  const calls = [];
  class FakeTelegramBot {
    async sendMessage(...args) { calls.push(args); return { message_id: 123 }; }
  }
  const { TelegramProvider } = loadTs('libraries/nestjs-libraries/src/integrations/social/telegram.provider.ts', {
    'node-telegram-bot-api': FakeTelegramBot,
    '@gitroom/nestjs-libraries/integrations/social.abstract': { SocialAbstract: class {} },
    '@gitroom/nestjs-libraries/services/make.is': { makeId: () => 'test' },
  });
  const provider = new TelegramProvider();
  assert.equal(provider.editor, 'html');
  const result = await provider.post('test_channel', '-1000000', [{ id: 'fixture', message: formatted, media: [] }]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][2].parse_mode, 'HTML');
  for (const fragment of ['<b>Жирный</b>', '<u>Подчёркнутый</u>', '<em>Курсив</em>', '<s>Удалено</s>', '<code>код</code>', '<blockquote>Цитата</blockquote>', '<a href="https://example.com/article">Ссылка</a>']) {
    assert.ok(calls[0][1].includes(fragment), `Telegram payload lost ${fragment}`);
  }
  assert.equal(result[0].releaseURL, 'https://t.me/test_channel/123');
  console.log('Content acceptance passed: save, reopen, formatting, safe HTML, Telegram payload. No external sends.');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
