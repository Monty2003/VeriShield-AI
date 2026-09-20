/**
 * Where, and on what, this session is being used.
 *
 * Location comes from the browser, which asks the user first; nothing is read
 * without that permission. To turn coordinates into a place name they are sent
 * to OpenStreetMap's public geocoder -- rounded to about a kilometre, looked up
 * once per place and cached for the tab, as its usage policy asks. Nothing
 * here is sent to this project's own server.
 */

export type PlaceState =
  | { status: 'locating' }
  | { status: 'found'; label: string; latitude: number; longitude: number; accuracy: number }
  | { status: 'denied' }
  | { status: 'unavailable'; reason: string };

const CACHE_PREFIX = 'vs_place:';

function cacheKey(latitude: number, longitude: number) {
  return `${CACHE_PREFIX}${latitude.toFixed(2)},${longitude.toFixed(2)}`;
}

export async function placeName(latitude: number, longitude: number, signal?: AbortSignal): Promise<string> {
  const key = cacheKey(latitude, longitude);
  try {
    const cached = sessionStorage.getItem(key);
    if (cached) return cached;
  } catch {
    // no cache: look it up
  }
  const url =
    'https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&addressdetails=1' +
    `&lat=${latitude.toFixed(2)}&lon=${longitude.toFixed(2)}`;
  const response = await fetch(url, { signal, headers: { 'Accept-Language': navigator.language || 'en' } });
  if (!response.ok) throw new Error(`geocoder answered ${response.status}`);
  const body = (await response.json()) as { address?: Record<string, string> };
  const a = body.address ?? {};
  const locality = a.city || a.town || a.village || a.suburb || a.county || a.state_district;
  const label = [locality, a.state, a.country].filter(Boolean).join(', ');
  if (!label) throw new Error('no place name for these coordinates');
  try {
    sessionStorage.setItem(key, label);
  } catch {
    // uncached is fine
  }
  return label;
}

export function locate(onChange: (state: PlaceState) => void): () => void {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
    onChange({ status: 'unavailable', reason: 'This browser does not provide location.' });
    return () => undefined;
  }
  const controller = new AbortController();
  let cancelled = false;
  onChange({ status: 'locating' });
  navigator.geolocation.getCurrentPosition(
    async (position) => {
      const { latitude, longitude, accuracy } = position.coords;
      let label = `${latitude.toFixed(3)}, ${longitude.toFixed(3)}`;
      try {
        label = await placeName(latitude, longitude, controller.signal);
      } catch {
        // coordinates, if the name cannot be had
      }
      if (!cancelled) onChange({ status: 'found', label, latitude, longitude, accuracy });
    },
    (error) => {
      if (cancelled) return;
      onChange(
        error.code === error.PERMISSION_DENIED
          ? { status: 'denied' }
          : { status: 'unavailable', reason: error.message || 'Location could not be determined.' },
      );
    },
    { enableHighAccuracy: false, timeout: 15_000, maximumAge: 10 * 60_000 },
  );
  return () => {
    cancelled = true;
    controller.abort();
  };
}

/** "Chrome on Windows" -- enough to recognise one's own device, no more. */
export function describeDevice(userAgent: string): string {
  const ua = userAgent;
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /OPR\//.test(ua)
      ? 'Opera'
      : /Firefox\//.test(ua)
        ? 'Firefox'
        : /Chrome\//.test(ua)
          ? 'Chrome'
          : /Safari\//.test(ua)
            ? 'Safari'
            : 'Browser';
  const system = /Windows/.test(ua)
    ? 'Windows'
    : /Android/.test(ua)
      ? 'Android'
      : /iPhone|iPad|iPod/.test(ua)
        ? 'iOS'
        : /Mac OS X/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : 'unknown system';
  return `${browser} on ${system}`;
}
