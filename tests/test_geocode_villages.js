// Verifies mapGeocodeAllVillages (background village locating) against the
// live server: records with real villages get located, records without
// village/district are skipped (no bogus "India" dot).
const fs = require('fs');
const html = fs.readFileSync('/home/user/land_records/extracted/landrec/static/index.html', 'utf8');
function extractFn(name){
  let i = html.indexOf('function ' + name + '(');
  if(i < 0) throw new Error('function ' + name + ' not found');
  const asyncPre = html.slice(Math.max(0, i - 6), i) === 'async ' ? 'async ' : '';
  let depth = 0, j = html.indexOf('{', i);
  for(let k = j; k < html.length; k++){
    if(html[k] === '{') depth++;
    else if(html[k] === '}'){ depth--; if(depth === 0) return asyncPre + html.slice(i, k + 1); }
  }
  throw new Error('unbalanced ' + name);
}

let mapRecords = [];
let mapVillageCache = {};
let mapVillageLevel = {};
let mapGeoBusy = false;
let mapHasFitted = false;
let drawCalls = 0;
let mapLeaflet = null, mapMarkers = null;
function mapDrawMarkers(){ drawCalls++; }
function mapRenderList(){}
function mapFitAll(){}
function mapSaveVillageCache(){}
function mapSetVillage(key, lat, lon, level){
  mapVillageCache[key] = [lat, lon];
  mapVillageLevel[key] = level || 'village';
}
function mapVillageKey(r){ return (r.village||'') + '|' + (r.district||'') + '|' + (r.state||''); }
async function api(path, opts){
  const r = await fetch('http://127.0.0.1:8000' + path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type':'application/json', 'Authorization':'Bearer ' + TOK },
    body: opts.body,
  });
  return r.json();
}
let TOK = '';

eval(extractFn('mapGeocodeAllVillages'));

async function main(){
  const login = await fetch('http://127.0.0.1:8000/api/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({email:'admin@landrec.gov.in', password:'Admin@123'})
  }).then(r => r.json());
  TOK = login.token;

  // pick real records that actually have village + district
  const d = await fetch('http://127.0.0.1:8000/api/map/records', { headers: { 'Authorization':'Bearer ' + TOK } }).then(r => r.json());
  const withLoc = d.records.filter(r => r.village && r.district && r.lat==null).slice(0, 3);
  // a record with NO village/district (must be skipped)
  const noLoc = { id:'x1', village:'', district:'', state:'', lat:null, lon:null };
  mapRecords = [...withLoc, noLoc];
  console.log('test records:', withLoc.length + ' located-candidates + 1 locationless');

  let PASS = 0, FAIL = 0;
  const check = (n, c, e='') => { console.log((c?'PASS  ':'FAIL  ')+n+(c?'':'  | '+String(e).slice(0,120))); c?PASS++:FAIL++; };

  await mapGeocodeAllVillages();

  const located = withLoc.filter(r => mapVillageCache[mapVillageKey(r)]).length;
  check('villages geocoded in background', located >= 1, 'located=' + located + '/' + withLoc.length);
  check('locationless record skipped (no India dot)', mapVillageCache['||'] === undefined && !mapVillageCache[mapVillageKey(noLoc)]);
  check('markers redrawn after geocoding', drawCalls >= 1, 'drawCalls=' + drawCalls);
  check('busy flag released', mapGeoBusy === false);

  // second pass: everything cached -> no extra work
  drawCalls = 0;
  await mapGeocodeAllVillages();
  check('second pass is a no-op (cached)', drawCalls === 0, 'drawCalls=' + drawCalls);

  console.log('\nGEOCODE TESTS: ' + PASS + ' passed, ' + FAIL + ' failed');
  process.exit(FAIL ? 1 : 0);
}
main().catch(e => { console.error('HARNESS ERROR', e); process.exit(2); });
