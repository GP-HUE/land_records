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


  // ---- SHOW MODE: selected-only (default) vs all records ----
  check('show-mode select present in page', $('#mapShowMode') !== null);
  vm.runInContext("mapShowMode = 'selected'", sandbox);
  vm.runInContext("mapSelectedId = null", sandbox);
  check("selected-only + no selection -> no rows", vm.runInContext('mapMarkerRows().length', sandbox) === 0);
  const recId = vm.runInContext('mapRecords.length ? mapRecords[0].id : null', sandbox);
  if(recId){
    vm.runInContext("mapSelectedId = '" + recId + "'", sandbox);
    const selRows = vm.runInContext('JSON.parse(JSON.stringify(mapMarkerRows().map(r=>r.id)))', sandbox);
    check("selected-only + selection -> exactly that record", selRows.length === 1 && selRows[0] === recId, JSON.stringify(selRows));
    vm.runInContext("mapSetShowMode('all')", sandbox);
    check("show-mode 'all' -> every record", vm.runInContext('mapMarkerRows().length', sandbox) === vm.runInContext('mapRecords.length', sandbox));
    check("show-mode persisted to localStorage", storage['lrMapShowMode'] === 'all', storage['lrMapShowMode']);
    vm.runInContext("mapSetShowMode('selected')", sandbox);
    check("show-mode select element updated", vm.runInContext("document.querySelector('#mapShowMode').value", sandbox) === 'selected');
    vm.runInContext("mapSelectedId = null", sandbox);
  }
  vm.runInContext("mapSetShowMode('bogus')", sandbox);
  check("invalid mode falls back to 'selected'", vm.runInContext('mapShowMode', sandbox) === 'selected');

  // ---- empty-DB UX: with zero records, the empty state must offer the
  // demo-load button for admins ----
  vm.runInContext("mapRecords = []; mapLoadVillage()", sandbox);
  const emptyHtml = $('#sheetEmpty').innerHTML;
  console.log('\n[empty-DB empty-state] visible:', !$('#sheetEmpty').classList.contains('hidden') === false ? 'n/a' : 'classList-stub', '| has demo btn:', emptyHtml.includes('mapLoadDemo'), '| has upload hint:', emptyHtml.includes('No records uploaded'));
  check('empty state offers demo-load button to admin', emptyHtml.includes('mapLoadDemo') && emptyHtml.includes('No records uploaded'), emptyHtml.slice(0, 200));
  vm.runInContext("mapCascadeRebuild()", sandbox);
  check('cascade shows no options when no records', optVals('sheetDistrict').length === 0, JSON.stringify(optVals('sheetDistrict')));

  // ================= v3.10.0 static UI assertions =================
  // Task 3: AI Task FAB must NOT sit on the floating nav pill (▲◀▶▼) —
  // pill is bottom:24px, so the FAB must be higher (>=78px) and its panel
  // higher still.
  const fabM = html.match(/id="aiTaskFab"[\s\S]{0,260}?bottom:(\d+)px/);
  check('ai task FAB raised above the arrow pill', !!fabM && parseInt(fabM[1], 10) >= 78, fabM && fabM[0].slice(0, 120));
  const tpM = html.match(/id="aiTaskPanel"[\s\S]{0,260}?bottom:(\d+)px/);
  check('ai task panel sits above its FAB', !!tpM && parseInt(tpM[1], 10) > parseInt(fabM ? fabM[1] : '0', 10), tpM && tpM[0].slice(0, 120));

  // Task 2: per-record Audit Trail panel + loader + auto-load call
  check('audit trail panel card in record detail', html.includes('id="auditPanel"') && html.includes('Audit Trail'));
  check('loadRecordAudit function present', /async function loadRecordAudit\(docId\)/.test(js));
  check('audit trail auto-loads with record detail', js.includes('loadRecordAudit(id);'));
  check('audit loader calls the per-record endpoint', js.includes("/api/documents/' + docId + '/audit'"));
  check('audit action labels (Hindi) present', js.includes('AUDIT_ACTION_HI') && js.includes('mutation_approved_with_litigation'));

  // Task 4: SA (Superior Administrator) mode wiring
  check('SA activation modal present', html.includes('id="saActivationModal"') && html.includes('SECURE ADMIN MODE'));
  check('SA identity + password steps present', html.includes('id="saIdentityOptions"') && html.includes('id="saPasswordStep"') && html.includes('submitSaPassword()'));
  check('SA report panel present', html.includes('id="saReportPanel"') && html.includes('SA Activity Report'));
  check('hidden SA trigger intercept in aiSend', /q\.match\(\/\^SA\(\?\: \\\\s\+\(\.\+\)\)\?\$\/i\)/.test(js) || js.includes("q.match(/^SA(?:\\s+(.+))?$/i)"), js.includes('beginSaActivation('));
  check('SA session functions present', ['beginSaActivation', 'submitSaPassword', 'exitSaMode', 'showSaReport', 'saSetHeader'].every(f => js.includes('function ' + f)));
  for (const fn of ['beginSaActivation', 'submitSaPassword', 'exitSaMode', 'showSaReport', 'saSetHeader'])
    check('SA fn ' + fn + ' defined', typeof vm.runInContext('typeof ' + fn, sandbox) === 'string' && vm.runInContext('typeof ' + fn, sandbox) === 'function');
  check('SA endpoints used by UI', js.includes("'/api/admin/sa/activate-options'") && js.includes("'/api/admin/sa/activate'") && js.includes("'/api/admin/sa/query'") && js.includes("'/api/admin/sa/end'") && js.includes("/api/admin/sa/report?session_id="));
  check('End-SA button + briefing button ids wired', html.includes('id="aiSaEndBtn"') && html.includes('id="aiBriefBtn"') && html.includes('id="aiTitleText"'));

  // v3.10.1 — SA eligibility + session-lifetime rules
  check('non-admin typing SA gets the not-eligible message', /me\.role !== 'admin'/.test(js) && js.includes('Not eligible'));
  check('SA greeting no longer promises a 30-minute auto-expiry', !js.includes('auto-expires in 30 minutes'));
  check('SA greeting states logout/session end policy', js.includes('logout always closes SA') || js.includes('exit SA'));
  check('logout ends the SA session client-side too', /function doLogout\(quiet\)\{[\s\S]{0,900}saSessionId = null/.test(js));
  const saSrc = fs.readFileSync(LR_ROOT + '/landrec/sa_admin.py', 'utf8');
  check('backend: no clock TTL (SESSION_TTL = None)', saSrc.includes('SESSION_TTL = None'));
  check('backend: logout ends the user\'s SA sessions', saSrc.includes('def end_sessions_for_user(') && fs.readFileSync(LR_ROOT + '/landrec/main.py', 'utf8').includes('sa_admin.end_sessions_for_user(user["id"])'));
  check('backend: SA endpoints all admin-gated', (fs.readFileSync(LR_ROOT + '/landrec/main.py', 'utf8').match(/\/api\/admin\/sa\/[a-z-]+"[\s\S]{0,90}require_role\("admin"\)/g) || []).length >= 4);

  console.log('\\nRESULT: ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error('HARNESS ERROR:', e); process.exit(2); });
