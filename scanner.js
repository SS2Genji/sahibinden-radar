#!/usr/bin/env node

/**
 * scanner.js: Node.js CDP worker that connects to Chrome on port 9222,
 * navigates/refreshes the target category page, and extracts active listings.
 * Zero external dependencies (uses native Node.js fetch and WebSocket).
 */

const port = process.env.CDP_PORT || 9222;
const targetUrl = process.argv[2];

if (!targetUrl) {
  console.error("Usage: node scanner.js <target_url>");
  process.exit(1);
}

class CDPClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.msgId = 1;
  }

  async connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl);
      this.ws.onopen = resolve;
      this.ws.onerror = (e) => reject(new Error(`WS connection failed: ${e.message || e}`));
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = this.msgId++;
      const onMsg = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.id === id) {
            this.ws.removeEventListener('message', onMsg);
            if (data.error) reject(new Error(JSON.stringify(data.error)));
            else resolve(data.result);
          }
        } catch (e) {
          reject(e);
        }
      };
      this.ws.addEventListener('message', onMsg);
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(fn) {
    const expr = typeof fn === 'function' ? `(${fn.toString()})()` : fn;
    const res = await this.send('Runtime.evaluate', {
      expression: expr,
      returnByValue: true,
      awaitPromise: true
    });
    return res.result ? res.result.value : null;
  }

  close() {
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
    }
  }
}

async function scan() {
  const res = await fetch(`http://127.0.0.1:${port}/json/list`);
  const tabs = await res.json();
  
  // Find existing tab or take the first usable page
  let targetTab = tabs.find(t => t.type === 'page' && t.url && (t.url.includes('sahibinden') || t.url.includes(targetUrl)));
  if (!targetTab) {
    targetTab = tabs.find(t => t.type === 'page');
  }

  if (!targetTab) {
    throw new Error('No active page tab found in Chrome at port ' + port);
  }

  const client = new CDPClient(targetTab.webSocketDebuggerUrl);
  await client.connect();

  // Navigate to target category if not already there
  const currentUrl = await client.evaluate(() => location.href);
  if (!currentUrl.includes(targetUrl)) {
    await client.send('Page.navigate', { url: targetUrl });
  } else {
    // Reload page to get freshest listings
    await client.send('Page.reload', {});
  }

  // Robust polling for rows to be rendered
  for (let attempt = 0; attempt < 25; attempt++) {
    await new Promise(r => setTimeout(r, 400));
    try {
      const ready = await client.evaluate(() => {
        return document.querySelectorAll('tr.searchResultsItem, .searchResultsRowClass').length > 0;
      });
      if (ready) break;
    } catch (_) {}
  }

  // Extract listing cards from the page
  const listings = await client.evaluate(() => {
    const results = [];
    
    // Selector for Sahibinden listing rows
    const rows = Array.from(document.querySelectorAll(
      'tbody.searchResultsRowClass tr.searchResultsItem, tr.searchResultsItem'
    ));

    for (const row of rows) {
      try {
        const id = row.getAttribute('data-id');
        if (!id) continue;

        const titleEl = row.querySelector('a.classifiedTitle') || row.querySelector('.searchResultsTitleValue a');
        const title = titleEl ? (titleEl.innerText || titleEl.getAttribute('title') || '').trim() : '';

        const priceEl = row.querySelector('.searchResultsPriceValue, .classified-price, span.price, .price');
        let rawPrice = priceEl ? priceEl.innerText : '';
        let digits = '';
        for (let i = 0; i < rawPrice.length; i++) {
          if (rawPrice[i] >= '0' && rawPrice[i] <= '9') digits += rawPrice[i];
        }
        const price = parseInt(digits, 10);

        if (!title || isNaN(price) || price <= 0) continue;

        const url = titleEl ? titleEl.href : (row.querySelector('a[href*="/ilan/"]') ? row.querySelector('a[href*="/ilan/"]').href : '');

        const locEl = row.querySelector('.searchResultsLocationValue, .location');
        const location = locEl ? locEl.innerText.trim().replace(/\s+/g, ' ') : '';

        results.push({
          id: String(id),
          title,
          price,
          url,
          location
        });
      } catch (err) {
        // Skip unparseable row
      }
    }

    return results;
  });

  client.close();
  console.log(JSON.stringify(listings || []));
}

scan().catch(err => {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
});
