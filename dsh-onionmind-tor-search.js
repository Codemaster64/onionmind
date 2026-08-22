// DeepSeek Harness search provider for Onionmind.
// The actual network work stays in onionmind.py, which verifies Tor and uses
// fresh circuits for each search. This Node adapter only bridges DSH's web seam.
export const name = 'onionmind-tor-search'
export const inject = ['web']

import { spawn } from 'node:child_process'
import { access } from 'node:fs/promises'

const script = process.env.ONIONMIND_PY
const python = process.env.ONIONMIND_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

async function usable() {
  if (!script) return false
  try { await access(script); return true } catch { return false }
}

function search(query, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [script, '--tor-search', query], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: { ...process.env, HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '' },
    })
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    const abort = () => child.kill()
    signal?.addEventListener('abort', abort, { once: true })
    child.on('error', reject)
    child.on('close', (code) => {
      signal?.removeEventListener('abort', abort)
      if (signal?.aborted) return reject(new Error('Tor search cancelled'))
      if (code !== 0) return reject(new Error(stderr.trim() || `Tor search exited with ${code}`))
      const lines = stdout.trim().split('\n')
      const sources = []
      for (let i = 0; i + 2 < lines.length; i += 3) {
        const url = lines[i + 2].trim()
        if (url.startsWith('http')) sources.push({
          title: lines[i].replace(/^- /, ''),
          snippet: lines[i + 1].replace(/^  /, ''),
          url,
        })
      }
      resolve({ content: sources.length ? undefined : stdout.trim(), sources, truncated: false })
    })
  })
}

export function apply(ctx) {
  ctx.web.registerSearchProvider({
    id: 'onionmind-tor',
    available: () => Boolean(script),
    search,
  })
}
