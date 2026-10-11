// Runs the exact packaged script against a resource-heavy page with a nested video.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../CaptureBrowser/BrowserForm.cs'), 'utf8');
const script = source.match(/private const string DomScanScript = """([\s\S]*?)""";/)[1];
function frame(url, selectors, resources = [], frames = []) {
  return {
    document: {baseURI: url, querySelectorAll: query => selectors[query] || []},
    location: {href: url}, performance: {getEntriesByType: () => resources}, frames,
  };
}
const nested = frame('https://player.test/embed', {
  video: [{currentSrc:'https://cdn.test/video.mp4?sig=secret', src:''}],
});
const root = frame('https://page.test/watch', {
  'iframe[src],embed[src]': [{src:'https://player.test/embed'}],
}, Array.from({length:300}, (_, i) => ({name:`https://cdn.test/style${i}.css`})), [nested]);
const result = vm.runInNewContext(script, {window:root, URL}, {timeout:1000});
assert(result.some(c => c.url === 'https://cdn.test/video.mp4?sig=secret'));
assert(result.some(c => c.hint === 'embed'));
assert(!result.some(c => c.url.endsWith('.css')));
assert(result.length <= 64);
console.log('DOM regression passed: irrelevant resources do not hide nested video candidates.');
