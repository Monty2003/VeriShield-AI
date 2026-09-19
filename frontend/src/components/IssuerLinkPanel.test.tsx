/**
 * The issuer link comes from a QR in an uploaded image: whatever it says, it
 * must never become a link that runs code, and it must open without giving
 * the destination a handle back to this page.
 */

import { render, screen } from '@testing-library/react';
import IssuerLinkPanel, { issuerLink } from './IssuerLinkPanel';
import type { Signal } from '../types/api';

function sig(code: string, evidence: Record<string, unknown> = {}, reason = ''): Signal {
  return {
    code,
    stage: 'database',
    title: 'Certificate QR code',
    status: 'skip',
    severity: 'info',
    confidence: 1,
    reason,
    evidence,
    regions: [],
    blocking: false,
    created_at: '2026-09-20T00:00:00Z',
  } as Signal;
}

const link = (url: string) => sig('certificate.qr.link', { url, domain: 'x' });

test('an issuer link opens safely in a new tab', () => {
  render(
    <IssuerLinkPanel
      signals={[
        link('https://www.codealpha.tech/verify'),
        sig('certificate.qr.issuer_site'),
        sig('certificate.reference.found', {}, 'The certificate prints an ID (ending 0042).'),
      ]}
    />,
  );
  const anchor = screen.getByRole('link', { name: /open codealpha\.tech/i });
  expect(anchor).toHaveAttribute('href', 'https://www.codealpha.tech/verify');
  expect(anchor).toHaveAttribute('target', '_blank');
  expect(anchor.getAttribute('rel')).toContain('noopener');
  expect(anchor.getAttribute('rel')).toContain('noreferrer');
  expect(screen.getByText(/leads to the issuer the certificate names/i)).toBeInTheDocument();
  expect(screen.getByText(/the id ends in 0042/i)).toBeInTheDocument();
});

test.each([
  // eslint-disable-next-line no-script-url -- the hostile input under test
  'javascript:alert(1)',
  'data:text/html,<script>alert(1)</script>',
  'file:///etc/passwd',
  'not a url',
])('a QR carrying %s is never linked', (url) => {
  expect(issuerLink([link(url)])).toBeNull();
  const { container } = render(<IssuerLinkPanel signals={[link(url)]} />);
  expect(container).toBeEmptyDOMElement();
});

test('a redirect service is flagged', () => {
  render(
    <IssuerLinkPanel
      signals={[link('https://qr-codes.io/abc'), sig('certificate.qr.redirect')]}
    />,
  );
  expect(screen.getByText(/goes through a redirect service/i)).toBeInTheDocument();
});
