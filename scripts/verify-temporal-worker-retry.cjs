const assert = require('node:assert/strict');
const {
  TemporalConnectionFactory,
} = require('nestjs-temporal-core/dist/providers/temporal-connection.factory');
const { NativeConnection } = require('@temporalio/worker');

async function main() {
  const originalConnect = NativeConnection.connect;
  const factory = new TemporalConnectionFactory();
  factory.delay = async () => {};
  factory.logger = {
    debug() {},
    info() {},
    warn() {},
    error() {},
  };

  try {
    let attempts = 0;
    const expectedConnection = { connected: true };
    NativeConnection.connect = async () => {
      attempts += 1;
      if (attempts < 3) {
        throw new Error('temporal unavailable');
      }
      return expectedConnection;
    };

    const connection = await factory.createNewWorkerConnection(
      { connection: { address: 'temporal:7233' } },
      'retry-test'
    );

    assert.equal(attempts, 3);
    assert.equal(connection, expectedConnection);
    assert.equal(factory.workerConnectionCache.get('retry-test'), connection);

    attempts = 0;
    NativeConnection.connect = async () => {
      attempts += 1;
      throw new Error('temporal unavailable');
    };

    await assert.rejects(
      factory.createNewWorkerConnection(
        { connection: { address: 'temporal:7233' } },
        'failure-test'
      ),
      /temporal unavailable/
    );
    assert.equal(attempts, 12);
  } finally {
    NativeConnection.connect = originalConnect;
  }

  console.log('Temporal worker retry patch verified');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
