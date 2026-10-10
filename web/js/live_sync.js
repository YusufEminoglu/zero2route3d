// Live link to QGIS: loads data/current_route.json when the local server
// announces a new route (server-sent events), or polls when it cannot.

let lastLoadedPayload = null;
let routePollTimer = null;
let routeEvents = null;

export function setLiveStatus(message, level = 'info') {
  const el = document.getElementById('liveStatus');
  if (!el) return;
  el.textContent = message || '';
  el.dataset.level = level;
  el.hidden = !message;
}

async function fetchCurrentRoute() {
  let res;
  try {
    res = await fetch(`data/current_route.json?t=${Date.now()}`, { cache: 'no-store' });
  } catch (err) {
    setLiveStatus('Cannot reach QGIS: the 02Route 3D server is not running. Reopen the 3D studio from QGIS.', 'error');
    return false;
  }
  if (res.status === 404) {
    // No route computed yet: the empty state explains what to do.
    setLiveStatus('');
    return false;
  }
  if (!res.ok) {
    setLiveStatus(`Could not load the route from QGIS (HTTP ${res.status}).`, 'error');
    return false;
  }
  let text;
  let data;
  try {
    text = await res.text();
    data = JSON.parse(text);
  } catch (err) {
    setLiveStatus(`The route file from QGIS is not valid JSON: ${err.message}`, 'error');
    return false;
  }
  setLiveStatus('');
  if (data && text !== lastLoadedPayload && window.app3d) {
    lastLoadedPayload = text;
    try {
      window.app3d.loadRoute(data);
    } catch (err) {
      setLiveStatus(`The route could not be shown: ${err.message}`, 'error');
      console.error(err);
    }
  }
  return Boolean(data);
}

function startPolling() {
  if (routePollTimer === null) routePollTimer = setInterval(fetchCurrentRoute, 1500);
}

// Live link to QGIS: the local server pushes an event when QGIS writes a new
// route, so the viewer no longer downloads the whole route every 1.5 s. If the
// event stream is unavailable it falls back to polling.
export function startLiveSync() {
  if (!/^https?:$/.test(window.location.protocol)) return; // standalone HTML report
  if (!window.EventSource) {
    fetchCurrentRoute();
    startPolling();
    return;
  }
  let opened = false;
  routeEvents = new EventSource('events');
  routeEvents.addEventListener('open', () => {
    opened = true;
    setLiveStatus('');
  });
  routeEvents.addEventListener('route', () => fetchCurrentRoute());
  routeEvents.addEventListener('error', () => {
    if (!opened) {
      // Older server without /events: poll instead.
      routeEvents.close();
      routeEvents = null;
      fetchCurrentRoute();
      startPolling();
      return;
    }
    setLiveStatus('Live link to QGIS lost - reconnecting...', 'warning');
  });
}

export function stopLiveSync() {
  if (routePollTimer !== null) {
    clearInterval(routePollTimer);
    routePollTimer = null;
  }
  if (routeEvents) routeEvents.close();
}
