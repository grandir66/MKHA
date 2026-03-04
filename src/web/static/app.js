/* MikroTik HA Manager - Shared JavaScript utilities */

// Generic API helper
async function apiCall(method, path, body = null) {
    const opts = {
        method: method,
        headers: {},
    };
    if (body) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`API error ${res.status}: ${err}`);
    }
    return res.json();
}

// Format timestamp
function formatTimestamp(ts) {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString();
}
