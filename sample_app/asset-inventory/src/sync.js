const cron = require('node-cron');
const SftpClient = require('ssh2-sftp-client');
const cfg = require('../config/default.json');
const { allAssets } = require('./talentarc-client');

async function run() {
  const assets = await allAssets();
  const csv = ['asset_tag,serial,location,cost_center,owner_email']
    .concat(assets.map(a => [a.tag, a.serial, a.location, a.costCenter, a.ownerEmail].join(',')))
    .join('\n');
  const sftp = new SftpClient();
  await sftp.connect({ host: cfg.sftp.host, username: cfg.sftp.user, password: cfg.sftp.password });
  await sftp.put(Buffer.from(csv), `${cfg.sftp.dir}/assets-${Date.now()}.csv`);
  await sftp.end();
  console.log(`synced ${assets.length} assets`);
}

cron.schedule(cfg.schedule, () => run().catch(err => console.error('sync failed', err)));
console.log('asset sync scheduled:', cfg.schedule);
