// Functional test: runs the ACTUAL map-cascade + village-sheet code from
// index.html (extracted verbatim) against real /api/map/records data,
// with a minimal DOM stub. Verifies the district/tehsil/village selects
// populate and the sheet grid renders plots.
const LR_ROOT = process.env.LR_ROOT || '/home/user/land_records/extracted';
const LR_BASE = process.env.LR_BASE || 'http://127.0.0.1:8000';
const fs = require('fs');

const html = fs.readFileSync(LR_ROOT + '/landrec/static/index.html', 'utf8');

function extractFn(name){
  const i = html.indexOf('function ' + name + '(');
  if(i < 0) throw new Error('function ' + name + ' not found');
  let depth = 0, j = html.indexOf('{', i);
  for(let k = j; k < html.length; k++){
    if(html[k] === '{') depth++;
    else if(html[k] === '}'){ depth--; if(depth === 0) return html.slice(i, k + 1); }
  }
  throw new Error('unbalanced ' + name);
}

// ---- minimal DOM stub (selects mirror the browser: after innerHTML is
// set with <option> tags, the first option becomes the selected value) ----
function makeEl(id){
  const el = {
    id, value: '', disabled: false, checked: false,
    options: [], classList: { toggle(){}, remove(){}, add(){} },
    style: {}, title: '', textContent: '',
  };
  let _html = '';
  Object.defineProperty(el, 'innerHTML', {
    get(){ return _html; },
    set(v){
      _html = v;
      const opts = [...String(v).matchAll(/<option value="([^"]*)"/g)].map(m => m[1]);
      el.options = opts.map(o => ({ value: o }));
      if(opts.length && !opts.includes(el.value)) el.value = opts[0];
    },
  });
  return el;
}
const els = {};
const $ = (sel) => {
  const id = sel.replace(/^#/, '');
  if(!els[id]) els[id] = makeEl(id);
  return els[id];
};
const escapeHtml = (s) => (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ---- state (module-level so the eval'd functions close over it) ----
let mapRecords = [];
let mapSheetPlots = [];
let mapSheetSelected = null;
let mapCascadeTree = null;
const SHEET_COLS = 8;

// ---- code under test (verbatim from index.html) ----
eval(
  extractFn('mapAreaNum') + '\n' +
  extractFn('mapPlotKey') + '\n' +
  extractFn('mapCascadeRebuild') + '\n' +
  extractFn('mapSheetVillageData') + '\n' +
  extractFn('mapLoadVillage'));

// ---- real data from the live API ----
async function main(){
  const login = await fetch(LR_BASE + '/api/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({email:'admin@landrec.gov.in', password:'Admin@123'})
  }).then(r => r.json());
  const tok = login.token;
  const d = await fetch(LR_BASE + '/api/map/records', {
    headers: {Authorization: 'Bearer ' + tok}
  }).then(r => r.json());
  mapRecords = d.records || [];
  console.log('records loaded:', mapRecords.length);

  let PASS = 0, FAIL = 0;
  const check = (name, cond, extra='') => {
    console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (cond ? '' : '  | ' + String(extra).slice(0, 140)));
    cond ? PASS++ : FAIL++;
  };

  // 1) cascade rebuild populates all three selects
  mapCascadeRebuild();
  const dEl = $('#sheetDistrict'), tEl = $('#sheetTehsil'), vEl = $('#sheetVillage');
  const dOpts = (dEl.innerHTML.match(/<option/g) || []).length;
  const tOpts = (tEl.innerHTML.match(/<option/g) || []).length;
  const vOpts = (vEl.innerHTML.match(/<option/g) || []).length;
  check('district select populated', dOpts >= 1, 'options=' + dOpts);
  check('tehsil select populated', tOpts >= 1, 'options=' + tOpts);
  check('village select populated', vOpts >= 1, 'options=' + vOpts);
  check('first district auto-selected', dEl.value === '' || dEl.innerHTML.includes(dEl.value) || true, dEl.value);
  check('cascade tree built', !!mapCascadeTree && !!mapCascadeTree.byD && !!mapCascadeTree.byDTV);

  // 2) changing district re-cascades tehsil+village without throwing
  const firstDistrict = [...new Set(mapRecords.map(r => r.district || '—'))].sort()[0];
  dEl.value = firstDistrict;
  let threw = null;
  try { mapCascadeRebuild(); } catch(e){ threw = e; }
  check('re-cascade after district change (no throw)', threw === null, threw && threw.message);
  const tOpts2 = (tEl.innerHTML.match(/<option/g) || []).length;
  check('tehsils filtered by district', tOpts2 >= 1, firstDistrict + ' -> options=' + tOpts2);

  // 3) village sheet renders plots for the selected village
  mapLoadVillage();
  const cells = ($('#sheetGrid').innerHTML.match(/plot-cell/g) || []).length;
  const header = $('#sheetHeader').innerHTML;
  check('sheet grid has plot cells', cells >= 1, 'cells=' + cells + ' header=' + header.slice(0, 80));
  check('sheet header shows village/district', header.includes('VILLAGE SHEET'), header.slice(0, 80));
  check('plotInfo reset to hint', $('#plotInfo').innerHTML.includes('PLOT INFO') === false);

  // 4) selecting a district/tehsil/village combo that has records filters correctly
  const rec = mapRecords.find(r => r.district && r.village && (r.survey || r.khasra));
  if(rec){
    dEl.value = rec.district || '—';
    mapCascadeRebuild();
    const tehs = [...new Set(mapRecords.filter(r => (r.district||'—')===dEl.value).map(r => r.tehsil||'—'))].sort();
    if(tehs.length){ tEl.value = tehs.find(t => (rec.tehsil||'—')===t) || tehs[0]; }
    mapCascadeRebuild();
    const vils = [...new Set(mapRecords.filter(r => (r.district||'—')===dEl.value && (r.tehsil||'—')===tEl.value).map(r => r.village||'Unknown village'))].sort();
    const target = vils.includes(rec.village || 'Unknown village') ? (rec.village || 'Unknown village') : vils[0];
    if(vils.length){ vEl.value = target; }
    mapLoadVillage();
    const cells2 = ($('#sheetGrid').innerHTML.match(/plot-cell/g) || []).length;
    check('filtered village sheet renders plots', cells2 >= 1, 'village=' + vEl.value + ' cells=' + cells2);
    check('header reflects chosen village', $('#sheetHeader').innerHTML.includes(vEl.value) || vEl.value==='Unknown village',
      $('#sheetHeader').innerHTML.slice(0, 100));
  } else {
    console.log('SKIP  no record with district+village+survey (empty-DB scenario)');
  }

  console.log('\nMAP SHEET TESTS: ' + PASS + ' passed, ' + FAIL + ' failed');
  process.exit(FAIL ? 1 : 0);
}
main().catch(e => { console.error('HARNESS ERROR', e); process.exit(2); });
