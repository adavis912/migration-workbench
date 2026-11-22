const axios = require('axios');
const https = require('https');
const cfg = require('../config/default.json').talentarc;

// Legacy: vendor appliance uses a self-signed cert, so verification was switched off.
const http = axios.create({
  baseURL: cfg.baseUrl,
  httpsAgent: new https.Agent({ rejectUnauthorized: false }),
  auth: { username: cfg.apiUser, password: cfg.apiKey },
});

async function allAssets() {
  const out = [];
  for (let page = 1; ; page++) {
    const { data } = await http.get('/assets', { params: { page, size: 500 } });
    out.push(...data.items);
    if (!data.next) return out;
  }
}

module.exports = { allAssets };
