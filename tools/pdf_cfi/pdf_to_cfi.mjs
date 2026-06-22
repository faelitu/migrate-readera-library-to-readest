// PDF citation → Readest CFI helper.
//
// Readest renders each PDF page with pdf.js into an HTML text layer (one <span>
// per text item, <br> between lines) and stores highlights as EPUB-style CFIs
// resolved against THAT DOM. ReadEra's own block/line/char indices come from a
// different extractor and don't map, so we reproduce Readest's exact pipeline:
// render the page text layer with the same pdf.js, locate the highlighted text,
// then let foliate's own CFI generator build the identifier.
//
// I/O: reads one JSON job from stdin, writes one JSON result to stdout.
//   in:  { "pdf": "/abs/path.pdf", "items": [ { "page0": 19, "text": "…" }, … ] }
//   out: { "results": [ { "cfi": "epubcfi(…)" } | { "error": "…" }, … ] }
// `page0` is the 0-based page index (the N in ReadEra's "/page[N]").

import { JSDOM } from 'jsdom'
import * as CFI from './epubcfi.js'

// Minimal browser globals for pdf.js + jsdom. We stub the canvas 2D context so
// pdf.js's font measurement runs without the native `canvas` package: only span
// structure and text content affect the CFI, never the measured widths.
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>')
const fakeCtx = {
  font: '', canvas: { width: 0, height: 0, style: {}, remove() {} },
  measureText: () => ({ width: 0, fontBoundingBoxAscent: 0, fontBoundingBoxDescent: 0 }),
  fillText() {}, scale() {}, save() {}, restore() {}, setTransform() {}, translate() {}, transform() {},
}
dom.window.HTMLCanvasElement.prototype.getContext = () => fakeCtx
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.DOMMatrix = dom.window.DOMMatrix
globalThis.Node = dom.window.Node
globalThis.NodeFilter = dom.window.NodeFilter
globalThis.Range = dom.window.Range
globalThis.getComputedStyle = dom.window.getComputedStyle

const { getDocument, TextLayer } = await import('pdfjs-dist/legacy/build/pdf.mjs')

// Exact page document template foliate-js (pdf.js renderPage) builds, so that
// the body→textLayer prefix and node indices match Readest's (yields /4/4).
const pageHTML = (w, h) => `
        <!DOCTYPE html>
        <html lang="en">
        <meta charset="utf-8">
        <meta name="viewport" content="width=${w}, height=${h}">
        <style>
        html, body { margin: 0; padding: 0; }
        </style>
        <div id="canvas"></div>
        <div class="textLayer"></div>
        <div class="annotationLayer"></div>
    `

const normalize = s => s.normalize('NFC').replace(/\s+/g, '')

function buildBareIndex(textLayer) {
  // Concatenate all text-node characters minus whitespace, keeping a back-map
  // from each kept character to its (text node, in-node offset).
  const walker = document.createTreeWalker(textLayer, NodeFilter.SHOW_TEXT)
  let bare = ''
  const map = []
  let node
  while ((node = walker.nextNode())) {
    const data = node.nodeValue.normalize('NFC')
    for (let i = 0; i < data.length; i++) {
      if (!/\s/.test(data[i])) { bare += data[i]; map.push({ node, offset: i }) }
    }
  }
  return { bare, map }
}

const pageCache = new Map()
async function renderTextLayer(pdf, page0) {
  if (pageCache.has(page0)) return pageCache.get(page0)
  const page = await pdf.getPage(page0 + 1)
  const viewport = page.getViewport({ scale: 1 })
  const pageDoc = new JSDOM(pageHTML(viewport.width, viewport.height)).window.document
  const container = pageDoc.querySelector('.textLayer')
  await new TextLayer({ textContentSource: page.streamTextContent(), container, viewport }).render()
  const result = { pageDoc, container, ...buildBareIndex(container) }
  pageCache.set(page0, result)
  return result
}

async function itemToCFI(pdf, item) {
  const { page0, text } = item
  if (typeof page0 !== 'number' || !text) return { error: 'missing page0/text' }
  const { pageDoc, bare, map } = await renderTextLayer(pdf, page0)
  const needle = normalize(text)
  if (!needle) return { error: 'empty text after normalization' }
  const at = bare.indexOf(needle)
  if (at < 0) return { error: `text not found on page ${page0}` }
  const start = map[at]
  const end = map[at + needle.length - 1]
  const range = pageDoc.createRange()
  range.setStart(start.node, start.offset)
  range.setEnd(end.node, end.offset + 1)
  const cfi = CFI.joinIndir(CFI.fake.fromIndex(page0), CFI.fromRange(range))
  return { cfi }
}

async function main() {
  const input = await new Promise((resolve, reject) => {
    let buf = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', d => (buf += d))
    process.stdin.on('end', () => resolve(buf))
    process.stdin.on('error', reject)
  })
  const job = JSON.parse(input)
  const data = new Uint8Array((await import('node:fs')).readFileSync(job.pdf))
  const pdf = await getDocument({ data, isEvalSupported: false }).promise
  const results = []
  for (const item of job.items) {
    try {
      results.push(await itemToCFI(pdf, item))
    } catch (err) {
      results.push({ error: String(err && err.message || err) })
    }
  }
  await pdf.destroy()
  process.stdout.write(JSON.stringify({ results }))
}

main().catch(err => {
  process.stdout.write(JSON.stringify({ error: String(err && err.message || err) }))
  process.exitCode = 1
})
