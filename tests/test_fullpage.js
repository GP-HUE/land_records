// Full-page simulation: executes the ENTIRE inline script of index.html in a
// Node VM with a DOM stub, then drives the Land Map tab exactly like a user:
// switch to map tab -> check cascade selects -> click through cascade -> check
// sheet grid. Captures every console error (the "browser console" angle).
const fs = require('fs');
const vm = require('vm');
const { createServer } = require('http');

const BASE = 'http://127.0.0.1:8000';
const html = fs.readFileSync('/home/user/land_records/extracted/landrec/static/index.html', 'utf8');
const js = fs.readFileSync('/tmp/main_script.js', 'utf8');

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
      return makeEl('div'); // generic
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

  // ---- user action: click the "Land Map" tab (same as switchTab('map')) ----
  vm.runInContext("switchTab('map')", sandbox);
  await new Promise(r => setTimeout(r, 800));

  const dOpts = optVals('sheetDistrict'), tOpts = optVals('sheetTehsil'), vOpts = optVals('sheetVillage');
  console.log('\n[after open map tab]');
  console.log('  district options:', JSON.stringify(dOpts));
  console.log('  tehsil   options:', JSON.stringify(tOpts));
  console.log('  village  options:', JSON.stringify(vOpts));
  console.log('  sheetGrid cells:', ($('#sheetGrid').innerHTML.match(/plot-cell/g) || []).length);
  console.log('  sheetHeader:', String($('#sheetHeader').innerHTML).slice(0, 120));

  let pass = 0, fail = 0;
  const check = (name, cond, extra = '') => {
    console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (cond ? '' : '  | ' + String(extra).slice(0, 200)));
    cond ? pass++ : fail++;
  };
  check('district select populated', dOpts.length > 1, JSON.stringify(dOpts));
  check('tehsil select populated', tOpts.length >= 1, JSON.stringify(tOpts));
  check('village select populated', vOpts.length >= 1, JSON.stringify(vOpts));
  check('sheet grid has plot cells', ($('#sheetGrid').innerHTML.match(/plot-cell/g) || []).length > 0, $('#sheetGrid').innerHTML.slice(0, 100));
  check('no console errors during load', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' || '));

  // ---- user action: pick a district -> onchange fires mapCascadeTehsil() ----
  const realD = dOpts.find(x => x && x !== '—');
  if(realD){
    const dEl = $('#sheetDistrict'); dEl.value = realD;
    vm.runInContext('mapCascadeTehsil()', sandbox);
    const t2 = optVals('sheetTehsil');
    console.log('\n[after pick district=' + realD + ']');
    console.log('  tehsil options:', JSON.stringify(t2));
    check('tehsil cascade refilled', t2.length >= 1, JSON.stringify(t2));
    if(t2.length){
      const tEl = $('#sheetTehsil'); tEl.value = t2[0];
      vm.runInContext('mapCascadeVillage()', sandbox); // fires on tehsil change
      const v2 = optVals('sheetVillage');
      console.log('  village options:', JSON.stringify(v2));
      check('village cascade refilled', v2.length >= 1, JSON.stringify(v2));
      if(v2.length){
        const vEl = $('#sheetVillage'); vEl.value = v2[0];
        vm.runInContext('mapLoadVillage()', sandbox);
        const cells = ($('#sheetGrid').innerHTML.match(/plot-cell/g) || []).length;
        console.log('  [after pick village=' + v2[0] + '] grid cells:', cells);
        check('sheet grid renders plots for selected village', cells > 0, $('#sheetHeader').innerHTML.slice(0, 150));
        // plot search
        vm.runInContext("$('#sheetPlotSearch').value='1'; mapPlotSearch()", sandbox);
      }
    }
  }

  // ---- real-map list should also be populated ----
  const listHtml = $('#mapList').innerHTML;
  check('real-map list rendered rows', (listHtml.match(/map-rec-row/g) || []).length > 0, listHtml.slice(0, 120));


  // ---- empty-DB UX: with zero records, the empty state must offer the
  // demo-load button for admins ----
  vm.runInContext("mapRecords = []; mapLoadVillage()", sandbox);
  const emptyHtml = $('#sheetEmpty').innerHTML;
  console.log('\n[empty-DB empty-state] visible:', !$('#sheetEmpty').classList.contains('hidden') === false ? 'n/a' : 'classList-stub', '| has demo btn:', emptyHtml.includes('mapLoadDemo'), '| has upload hint:', emptyHtml.includes('No records uploaded'));
  check('empty state offers demo-load button to admin', emptyHtml.includes('mapLoadDemo') && emptyHtml.includes('No records uploaded'), emptyHtml.slice(0, 200));
  vm.runInContext("mapCascadeRebuild()", sandbox);
  check('cascade shows no options when no records', optVals('sheetDistrict').length === 0, JSON.stringify(optVals('sheetDistrict')));

  console.log('\\nRESULT: ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
