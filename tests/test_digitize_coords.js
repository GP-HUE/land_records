// Full-page simulation: executes the ENTIRE inline script of index.html in a
// Node VM with a DOM stub, then drives the Land Map tab exactly like a user:
// switch to map tab -> check cascade selects -> click through cascade -> check
// sheet grid. Captures every console error (the "browser console" angle).
const LR_ROOT = process.env.LR_ROOT || '/home/user/land_records/extracted';
const LR_BASE = process.env.LR_BASE || 'http://127.0.0.1:8000';
const fs = require('fs');
const vm = require('vm');
const { createServer } = require('http');

const BASE = LR_BASE;
const html = fs.readFileSync(LR_ROOT + '/landrec/static/index.html', 'utf8');
// extract the main inline <script> block at runtime (no dev-time files)
const _si = html.indexOf('<script>\n');
const js = html.slice(_si + '<script>\n'.length, html.lastIndexOf('</script>'));

// ---------- DOM stub ----------
let _uid = 0;
function makeClassList(){
  const set = new Set();
  return {
    add: (...cs) => cs.forEach(c => c && set.add(c)),
    remove: (...cs) => cs.forEach(c => c && set.delete(c)),
    toggle: (c, force) => { const want = force === undefined ? !set.has(c) : !!force; want ? set.add(c) : set.delete(c); return want; },
    contains: c => set.has(c),
  };
}
function parseOptions(str){
  return [...String(str).matchAll(/<option[^>]*value="([^"]*)"[^>]*>/g)].map(m => m[1]);
}
function makeEl(tag, id){
  tag = (tag || 'div').toLowerCase();
  const el = {
    __uid: ++_uid,
    tagName: tag.toUpperCase(),
    id: id || '',
    value: '', checked: false, disabled: false, hidden: false,
    textContent: '', title: '', placeholder: '', src: '', href: '',
    style: {}, dataset: {},
    children: [], parentNode: null,
    offsetWidth: 800, offsetHeight: 600, clientWidth: 800, clientHeight: 600,
    scrollHeight: 0, scrollTop: 0, scrollLeft: 0,
    firstChild: null,
    classList: makeClassList(),
    setAttribute(k, v){ if(k === 'class') v.split(/\s+/).forEach(c => c && el.classList.add(c)); },
    getAttribute(k){ return null; },
    removeAttribute(){},
    addEventListener(){}, removeEventListener(){},
    appendChild(c){ el.children.push(c); c.parentNode = el; el.firstChild = el.children[0]; return c; },
    removeChild(c){ el.children = el.children.filter(x => x !== c); return c; },
    insertBefore(c){ el.children.unshift(c); return c; },
    remove(){ if(el.parentNode) el.parentNode.removeChild(el); },
    querySelector(sel){
      // search this element's children by id
      const m = sel.match(/^#([\w-]+)/);
      if(m){
        const walk = (node) => {
          for(const c of node.children || []){
            if(c.id === m[1]) return c;
            const r = walk(c); if(r) return r;
          }
          return null;
        };
        return walk(el);
      }
      if(!el._qcache) el._qcache = {};
      if(!el._qcache[sel]) el._qcache[sel] = makeEl('input');
      return el._qcache[sel];
    },
    querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { top: 0, left: 0, width: 800, height: 600 }; },
    focus(){}, blur(){}, click(){},
    scrollIntoView(){},
    getContext(){
      const noop = () => {};
      return new Proxy({}, { get: (t, p) => (p === 'measureText' ? () => ({ width: 0 }) : (p === 'getImageData' ? () => ({ data: [] }) : (p === 'canvas' ? el : noop))) });
    },
    toDataURL(){ return 'data:image/png;base64,'; },
    cloneNode(){ return makeEl(tag, id); },
    closest(){ return null; },
    matches(){ return false; },
    contains(){ return false; },
    dispatchEvent(){ return true; },
  };
  let _html = '';
  Object.defineProperty(el, 'innerHTML', {
    get(){ return _html; },
    set(v){
      _html = String(v);
      el.children = [];
      if(tag === 'select'){
        const opts = parseOptions(_html);
        el.options = opts.map(o => ({ value: o }));
        if(opts.length && !opts.includes(el.value)) el.value = opts[0];
        else if(!opts.length) el.value = '';
      }
    },
  });
  return el;
}

const SELECT_IDS = new Set(['aiTaskPriority','aiTaskRole','cmpSelA','cmpSelB','docLangSelect','docTypeSelect','langSelectApp','langSelectAuth','mapTileSource','mutLinkSel','mutType','nuRole','searchType','sheetDistrict','sheetTehsil','sheetVillage']);
const registry = {};
function getEl(sel){
  const m = String(sel).match(/^#([\w-]+)/);
  const id = m ? m[1] : String(sel);
  if(!registry[id]) registry[id] = makeEl(SELECT_IDS.has(id) ? 'select' : 'div', id);
  return registry[id];
}

const documentStub = {
  readyState: 'complete',
  body: makeEl('body', 'body'),
  documentElement: makeEl('html'),
  head: makeEl('head'),
  title: '',
  getElementById(id){ return getEl('#' + id); },
  querySelector(sel){ return getEl(sel); },
  querySelectorAll(sel){
    if(sel === '.plot-cell') return [];
    return [];
  },
  createElement(tag){ return makeEl(tag); },
  createTextNode(t){ return { nodeType: 3, textContent: t }; },
  addEventListener(){}, removeEventListener(){},
  createTreeWalker(){ return { nextNode: () => null }; },
  createDocumentFragment(){ return makeEl('fragment'); },
  visible: true,
  hidden: false,
  fonts: { ready: Promise.resolve() },
  execCommand(){ return true; },
  getElementById: function(id){ return getEl('#' + id); },
};

const storage = {};
const localStorageStub = {
  getItem: k => (k in storage ? storage[k] : null),
  setItem: (k, v) => { storage[k] = String(v); },
  removeItem: k => { delete storage[k]; },
  clear: () => { for(const k in storage) delete storage[k]; },
  key: i => Object.keys(storage)[i] || null,
  get length(){ return Object.keys(storage).length; },
};

const windowStub = {
  addEventListener(){}, removeEventListener(){},
  location: { href: BASE + '/', origin: BASE, protocol: 'http:', host: '127.0.0.1:8000', search: '', hash: '', pathname: '/' },
  history: { state: null, pushState(){}, replaceState(){}, go(){}, back(){} },
  localStorage: localStorageStub,
  open(){ return null; },
  matchMedia(){ return { matches: false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){} }; },
  getComputedStyle(){ return { getPropertyValue: () => '' }; },
  setInterval: (fn, ms) => 0,
  clearInterval(){},
  setTimeout: (fn, ms) => 0,
  clearTimeout(){},
  focus(){},
  devicePixelRatio: 1,
  innerWidth: 1280, innerHeight: 800,
  pageXOffset: 0, pageYOffset: 0,
  _allDocsCache: [],
};
windowStub.window = windowStub;

const consoleErrors = [];
const sandboxAlerts = [];

const sandbox = {
  console: {
    log: () => {}, info: () => {}, warn: () => {},
    error: (...a) => consoleErrors.push(a.map(String).join(' ')),
  },
  document: documentStub,
  window: windowStub,
  navigator: { language: 'en', userAgent: 'node', clipboard: { writeText: () => Promise.resolve() }, sendBeacon: () => true },
  localStorage: localStorageStub,
  fetch: (path, opts) => {
    let url = path;
    if(!String(path).startsWith('http')) url = BASE + path;
    return fetch(url, opts);
  },
  URL, URLSearchParams, Blob, File, FormData,
  FileReader: (typeof FileReader !== 'undefined' ? FileReader : function(){ return { readAsArrayBuffer(){}, readAsText(){}, readAsDataURL(){} }; }),
  alert: (m) => { sandboxAlerts.push(String(m)); },
  confirm: () => true,
  atob: s => Buffer.from(s, 'base64').toString('binary'),
  btoa: s => Buffer.from(s, 'binary').toString('base64'),
  setTimeout: (fn, ms, ...a) => setTimeout(fn, Math.min(ms || 0, 50), ...a),
  clearTimeout: c => {},
  setInterval: () => 0,
  clearInterval: () => {},
  Promise, Math, JSON, Date, Array, Object, String, Number, Boolean, RegExp, Error, TypeError, RangeError, Set, Map, Symbol, parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
  TextEncoder, TextDecoder,
  performance: { now: () => Date.now() },
  Image: function(){ return makeEl('img'); },
  Audio: function(){ return { play: () => Promise.resolve(), pause(){}, addEventListener(){} }; },
  MutationObserver: function(){ return { observe(){}, disconnect(){} }; },
  IntersectionObserver: function(){ return { observe(){}, disconnect(){} }; },
  HTMLCanvasElement: function(){ return makeEl('canvas'); },
  HTMLElement: function(){ return makeEl('div'); },
  CustomEvent: function(t){ this.type = t; },
  Event: function(t){ this.type = t; },
  NodeFilter: { SHOW_TEXT: 4, SHOW_ELEMENT: 1, SHOW_COMMENT: 8, acceptNode: () => 1 },
  MutationObserver: function(){ return { observe(){}, disconnect(){} }; },
  requestAnimationFrame: fn => 0,
  crypto: require('crypto').webcrypto,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// seed a valid admin token so the bootstrap lands on the dashboard
async function main(){
  const login = await (await fetch(BASE + '/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'admin@landrec.gov.in', password: 'Admin@123' }),
  })).json();
  storage['lrtoken'] = login.token;

  let loadError = null;
  try {
    vm.runInContext(js, sandbox, { filename: 'main_script.js' });
  } catch(e){
    loadError = e;
  }
  if(loadError){
    console.log('FATAL: script failed to execute:\n' + loadError.stack);
    process.exit(1);
  }
  await new Promise(r => setTimeout(r, 300)); // let bootstrap IIFE settle

  console.log('script executed OK. console.error count:', consoleErrors.length);
  consoleErrors.slice(0, 10).forEach(e => console.log('  console.error:', e.slice(0, 200)));

  const $ = sel => getEl(sel);
  const optVals = sel => ($('#' + sel).options || []).map(o => o.value);

  // =====================================================================
  // DIGITIZE / RE-DIGITIZE BOUNDARY WITH MANUAL COORDINATE INPUT
  // =====================================================================
  // open the map tab first so the in-page mapRecords cache is loaded
  vm.runInContext("switchTab('map')", sandbox);
  await new Promise(r => setTimeout(r, 800));

  const adminTok = storage['lrtoken'];
  const inVm = vm.runInContext('JSON.parse(JSON.stringify(mapRecords))', sandbox);
  const target = inVm.find(r => r.survey || r.khasra);
  if(!target){ console.log('FAIL  no record with survey/khasra to digitize'); process.exit(1); }
  console.log('target record for digitize:', target.id, 'survey=' + target.survey, 'khasra=' + target.khasra, 'boundary=' + JSON.stringify(target.boundary));

  let pass = 0, fail = 0;
  const check = (name, cond, extra = '') => {
    console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (cond ? '' : '  | ' + String(extra).slice(0, 200)));
    cond ? pass++ : fail++;
  };

  // 0) make the test idempotent: clear any boundary left by a previous run
  await fetch(BASE + '/api/map/records/' + target.id + '/boundary/clear', {
    method: 'POST', headers: { 'Content-Type': 'application/json',
      Authorization: 'Bearer ' + adminTok }, body: '{}' }).catch(() => {});
  vm.runInContext("const _r = mapRecords.find(x=>x.id==='" + target.id + "'); if(_r){ _r.boundary = null; _r.boundary_source = null; }", sandbox);

  // 1) no existing boundary: start digitize -> 0 corners
  vm.runInContext("mapSelectedId = '" + target.id + "'; mapStartDigitize()", sandbox);
  check('digitize starts with 0 corners on fresh record', vm.runInContext('mapDigitize && mapDigitize.points.length', sandbox) === 0);

  // 2) type exact coordinates into the manual input row (user request #2)
  const panel = getEl('#mapDigitizePanel');
  const setLatLon = (lat, lon) => {
    panel.querySelector('.dz-new-lat').value = String(lat);
    panel.querySelector('.dz-new-lon').value = String(lon);
  };
  const corners = [
    [23.258512, 77.401987], [23.258512, 77.402210],
    [23.258290, 77.402210], [23.258290, 77.401987],
  ];
  for(const [la, lo] of corners){
    setLatLon(la, lo);
    vm.runInContext('mapDigitizeAddManual()', sandbox);
  }
  const pts = vm.runInContext('JSON.parse(JSON.stringify(mapDigitize.points))', sandbox);
  check('typed coordinates added as corners (4)', pts.length === 4, JSON.stringify(pts));
  check('first corner matches typed values exactly', pts[0][0] === 23.258512 && pts[0][1] === 77.401987, JSON.stringify(pts[0]));

  // 3) invalid coordinates rejected (lat out of range)
  setLatLon(999, 77.4);
  vm.runInContext('mapDigitizeAddManual()', sandbox);
  check('invalid latitude rejected (still 4 corners)', vm.runInContext('mapDigitize.points.length', sandbox) === 4,
        JSON.stringify(sandboxAlerts.slice(-1)));
  check('rejection shows a helpful alert', sandboxAlerts.some(a => /valid coordinate/i.test(a)), JSON.stringify(sandboxAlerts.slice(-1)));

  // 4) delete a corner, then re-add it via typed coordinates
  vm.runInContext('mapDigitizeDelPoint(3)', sandbox);
  check('corner deletion works', vm.runInContext('mapDigitize.points.length', sandbox) === 3);
  setLatLon(23.258290, 77.401987);
  vm.runInContext('mapDigitizeAddManual()', sandbox);
  check('corner re-added via typed coordinates', vm.runInContext('mapDigitize.points.length', sandbox) === 4);

  // 5) finish & save -> boundary persisted server-side
  await vm.runInContext('mapFinishDigitize()', sandbox);
  check('finish produced no error alert', !sandboxAlerts.some(a => /failed/i.test(a)), JSON.stringify(sandboxAlerts.slice(-2)));
  const after = await (await fetch(BASE + '/api/map/records', {
    headers: { Authorization: 'Bearer ' + adminTok } })).json();
  const rec2 = after.records.find(r => r.id === target.id);
  check('boundary saved to server (digitized)', rec2 && rec2.boundary && rec2.boundary.length >= 4
        && rec2.boundary_source === 'digitized', JSON.stringify(rec2 && rec2.boundary));
  // boundary format is [lat, lon]
  check('saved boundary starts at the typed corner (lat,lon)', rec2 && rec2.boundary &&
        Math.abs(rec2.boundary[0][0] - 23.258512) < 1e-6 && Math.abs(rec2.boundary[0][1] - 77.401987) < 1e-6,
        JSON.stringify(rec2 && rec2.boundary && rec2.boundary[0]));

  // 6) RE-digitize: must start from the EXISTING corners (nudge, not redraw)
  vm.runInContext("mapSelectedId = '" + target.id + "'; mapStartDigitize()", sandbox);
  const rpts = vm.runInContext('JSON.parse(JSON.stringify(mapDigitize.points))', sandbox);
  check('re-digitize preloads existing corners', rpts.length >= 3 &&
        Math.abs(rpts[0][0] - 23.258512) < 1e-6 && Math.abs(rpts[0][1] - 77.401987) < 1e-6,
        JSON.stringify(rpts));

  // 7) nudge: append a corrected corner via typed coordinates, save again
  setLatLon(23.259000, 77.403000);
  vm.runInContext('mapDigitizeAddManual()', sandbox);
  check('re-digitize can adjust via typed coordinates', vm.runInContext('mapDigitize.points.length', sandbox) === rpts.length + 1);
  await vm.runInContext('mapFinishDigitize()', sandbox);
  const after2 = await (await fetch(BASE + '/api/map/records', {
    headers: { Authorization: 'Bearer ' + adminTok } })).json();
  const rec3 = after2.records.find(r => r.id === target.id);
  check('re-digitized boundary persisted', rec3 && rec3.boundary && rec3.boundary.length >= 4,
        JSON.stringify(rec3 && rec3.boundary).slice(0, 140));

  // 8) _dzValidPair validation bounds (called in-context with stub inputs)
  const valid = vm.runInContext('_dzValidPair({value:"23.25",classList:{toggle(){} }},{value:"77.4",classList:{toggle(){}}})', sandbox);
  check('_dzValidPair accepts valid pair', JSON.stringify(valid) === '[23.25,77.4]', JSON.stringify(valid));
  const bad = vm.runInContext('_dzValidPair({value:"95",classList:{toggle(){}}},{value:"77.4",classList:{toggle(){}}})', sandbox);
  check('_dzValidPair rejects lat>90', bad === null, JSON.stringify(bad));
  const bad2 = vm.runInContext('_dzValidPair({value:"23",classList:{toggle(){}}},{value:"-200",classList:{toggle(){}}})', sandbox);
  check('_dzValidPair rejects lon<-180', bad2 === null, JSON.stringify(bad2));

  console.log('\nRESULT: ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
