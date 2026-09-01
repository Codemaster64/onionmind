// Fail closed when a routed Node child tries to bypass the Tor proxy.
'use strict';

const net = require('node:net');
const dns = require('node:dns');
const dgram = require('node:dgram');

const OFF = 'ollama-tor: direct network access is off; use the Tor proxy environment';
const originalConnect = net.Socket.prototype.connect;
const originalDatagramConnect = dgram.Socket.prototype.connect;
const originalDatagramSend = dgram.Socket.prototype.send;

function local(host) {
  if (host === undefined || host === null || host === '') return true;
  let value = String(host).trim().replace(/^\[|\]$/g, '').toLowerCase();
  if (value.endsWith('.')) value = value.slice(0, -1);
  if (value === 'localhost' || value === '::1') return true;
  if (value.includes('%')) value = value.split('%', 1)[0];
  if (net.isIPv4(value)) return Number(value.split('.')[0]) === 127;
  return value.startsWith('::ffff:127.');
}

function connectHost(args) {
  const normalized = Array.isArray(args[0]) ? args[0] : args;
  const first = normalized[0];
  if (first && typeof first === 'object') {
    if (first.path !== undefined) return undefined;
    return first.host === undefined ? '127.0.0.1' : first.host;
  }
  return typeof normalized[1] === 'string' ? normalized[1] : '127.0.0.1';
}

net.Socket.prototype.connect = function (...args) {
  if (!local(connectHost(args))) throw new Error(OFF);
  return originalConnect.apply(this, args);
};

function lastString(args) {
  for (let index = args.length - 1; index >= 0; index -= 1) {
    if (typeof args[index] === 'string') return args[index];
  }
  return undefined;
}

dgram.Socket.prototype.connect = function (...args) {
  if (!local(lastString(args))) throw new Error(OFF);
  return originalDatagramConnect.apply(this, args);
};

dgram.Socket.prototype.send = function (message, ...args) {
  if (!local(lastString(args))) throw new Error(OFF);
  return originalDatagramSend.call(this, message, ...args);
};

function blockedCallback(args) {
  let callback;
  for (let index = args.length - 1; index >= 0; index -= 1) {
    if (typeof args[index] === 'function') {
      callback = args[index];
      break;
    }
  }
  const error = new Error(OFF);
  if (callback) {
    process.nextTick(callback, error);
    return undefined;
  }
  throw error;
}

const dnsMethods = [
  'lookup', 'resolve', 'resolve4', 'resolve6', 'resolveAny', 'resolveCaa',
  'resolveCname', 'resolveMx', 'resolveNaptr', 'resolveNs', 'resolvePtr',
  'resolveSoa', 'resolveSrv', 'resolveTxt', 'reverse',
];

for (const name of dnsMethods) {
  if (typeof dns[name] === 'function') {
    const original = dns[name];
    dns[name] = function (host, ...args) {
      if (!local(host)) return blockedCallback(args);
      return original.call(dns, host, ...args);
    };
  }
  if (dns.promises && typeof dns.promises[name] === 'function') {
    const originalPromise = dns.promises[name];
    dns.promises[name] = function (host, ...args) {
      if (!local(host)) return Promise.reject(new Error(OFF));
      return originalPromise.call(dns.promises, host, ...args);
    };
  }
  if (dns.Resolver && typeof dns.Resolver.prototype[name] === 'function') {
    const originalResolver = dns.Resolver.prototype[name];
    dns.Resolver.prototype[name] = function (host, ...args) {
      if (!local(host)) return blockedCallback(args);
      return originalResolver.call(this, host, ...args);
    };
  }
}
