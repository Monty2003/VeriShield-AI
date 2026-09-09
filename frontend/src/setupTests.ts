// jest-dom adds custom matchers for asserting on DOM nodes.
import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';

// CRA pins jsdom 16 (via Jest 27), which predates TextEncoder/TextDecoder in
// the browser globals. react-router 7 uses them at import time, so without
// this the whole route table fails to load in tests while working fine in a
// real browser.
const globals = global as unknown as Record<string, unknown>;
if (typeof globals.TextEncoder === 'undefined') globals.TextEncoder = TextEncoder;
if (typeof globals.TextDecoder === 'undefined') globals.TextDecoder = TextDecoder;
