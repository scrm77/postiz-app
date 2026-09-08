# Dependency patches

`nestjs-temporal-core@3.2.3.patch` fixes the Temporal worker startup race tracked
in [Postiz issue #1748](https://github.com/gitroomhq/postiz-app/issues/1748).
The unpatched library returns `null` after one failed worker connection, leaving
the orchestrator online without task-queue pollers. The patch retries for up to
one minute and then throws so PM2 can restart the real Node process.

The root build script runs `test:runtime-guards`, so an image cannot be built if
the patch is missing, PM2 goes back to supervising a `pnpm` wrapper, or the
watchdog script is syntactically invalid.

When updating `nestjs-temporal-core`:

1. Check whether its worker connection now retries and throws after exhaustion.
2. If upstream fixed it, remove the patched dependency and update the verifier.
3. Otherwise recreate the patch with `pnpm patch <package>@<version>` and
   `pnpm patch-commit <edit-directory>`.
4. Run `pnpm install --frozen-lockfile`,
   `pnpm run test:temporal-worker-retry`, and `pnpm run build`.
