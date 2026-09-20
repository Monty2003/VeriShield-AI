/**
 * Device description and place lookup. The place lookup sends coordinates
 * rounded to about a kilometre, once per place per tab.
 */

import { describeDevice, placeName } from './place';

test.each([
  ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36', 'Chrome on Windows'],
  ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0', 'Edge on Windows'],
  ['Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36', 'Chrome on Android'],
  ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Gecko/20100101 Firefox/130.0', 'Firefox on macOS'],
])('%s', (ua, expected) => {
  expect(describeDevice(ua)).toBe(expected);
});

test('a place is looked up once, with coordinates rounded to about a kilometre', async () => {
  sessionStorage.clear();
  const fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ address: { city: 'Example City', state: 'Example State', country: 'Exampleland' } }),
  });
  global.fetch = fetchMock as unknown as typeof fetch;

  expect(await placeName(12.345678, 76.543210)).toBe('Example City, Example State, Exampleland');
  expect(await placeName(12.3457, 76.5433)).toBe('Example City, Example State, Exampleland');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const url = String(fetchMock.mock.calls[0][0]);
  expect(url).toContain('lat=12.35');
  expect(url).toContain('lon=76.54');
  expect(url).not.toContain('12.345678');
});
