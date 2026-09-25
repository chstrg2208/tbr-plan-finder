"""
TBR PLAN FINDER PRO v3 - SIMPLE & CLEAR UI
The Best Rate Insurance - Dành cho Manager & Agent
"""

import http.server, socketserver, urllib.parse, json, sqlite3, os, csv

PORT = 5050
DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, 'cms_plans_2026.db')
GEO_PATH = os.path.join(DIR, 'geo-data.csv')

FPL_100_2026 = {
    1: 15650, 2: 21150, 3: 26650, 4: 32150,
    5: 37650, 6: 43150, 7: 48650, 8: 54150
}

# ── Load Geo data ──
GEO_CACHE = {}
ZIP_LOOKUP = {}

if os.path.exists(GEO_PATH):
    with open(GEO_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            st = row['state_abbr'].strip().upper()
            cty = row['county'].strip()
            zipc = row['zipcode'].strip()
            city = row['city'].strip()
            key = (st, cty.lower())
            if key not in GEO_CACHE:
                GEO_CACHE[key] = {'zips': [], 'cities': set()}
            GEO_CACHE[key]['zips'].append(zipc)
            GEO_CACHE[key]['cities'].add(city)
            if zipc not in ZIP_LOOKUP:
                ZIP_LOOKUP[zipc] = {'state': st, 'county': cty, 'city': city}

def get_fpl_100(h):
    if h <= 8: return FPL_100_2026.get(h, 15650)
    return 54150 + (h - 8) * 5500

def get_applicable_percentage(pct_fpl):
    if pct_fpl <= 150.0: return 0.0
    elif pct_fpl <= 200.0: return (pct_fpl - 150.0) / 50.0 * 0.02
    elif pct_fpl <= 250.0: return 0.02 + (pct_fpl - 200.0) / 50.0 * 0.02
    elif pct_fpl <= 300.0: return 0.04 + (pct_fpl - 250.0) / 50.0 * 0.02
    elif pct_fpl <= 400.0: return 0.06 + (pct_fpl - 300.0) / 100.0 * 0.025
    else: return 0.085

def calc_fpl_info(h, a, income):
    base = get_fpl_100(h)
    pct = (income / base) * 100.0 if base > 0 else 0
    app = get_applicable_percentage(pct)
    contrib = (income * app) / 12.0
    if pct <= 150: csr = "CSR 94% — $0 Deductible, $0 Copay"
    elif pct <= 200: csr = "CSR 87%"
    elif pct <= 250: csr = "CSR 73%"
    else: csr = "Standard (Không CSR)"
    return {'pct_fpl': round(pct, 1), 'monthly_contrib': round(contrib, 2), 'csr': csr}

def compute_slcsp(silvers):
    prems = sorted(set(p['premium'] for p in silvers))
    return prems[1] if len(prems) > 1 else (prems[0] if prems else 0)

def get_county_plans(state, county=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if county:
        c.execute("SELECT county, metal, issuer, plan_name, plan_type, premium_27 FROM plans WHERE state=? AND county=? ORDER BY metal, premium_27", (state, county))
    else:
        c.execute("SELECT county, metal, issuer, plan_name, plan_type, premium_27 FROM plans WHERE state=? ORDER BY county, metal, premium_27", (state,))
    rows = c.fetchall()
    conn.close()
    return rows

# ── APIs ──

def api_issuers(state):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT issuer FROM plans WHERE state=? ORDER BY issuer", (state,))
    r = [x[0] for x in c.fetchall()]
    conn.close()
    return r

def api_zip_lookup(zipcode, h, a, income):
    info = ZIP_LOOKUP.get(zipcode)
    if not info: return {'error': f'Không tìm thấy zipcode {zipcode}'}
    state, county, city = info['state'], info['county'], info['city']
    fpl = calc_fpl_info(h, a, income)
    rows = get_county_plans(state, county)
    if not rows: return {'error': f'Không có plan nào ở {county}, {state}'}
    silvers = [{'issuer':r[2],'plan':r[3],'type':r[4],'premium':r[5]} for r in rows if r[1]=='Silver']
    bronzes = [{'issuer':r[2],'plan':r[3],'type':r[4],'premium':r[5]} for r in rows if r[1]=='Bronze']
    slcsp = compute_slcsp(silvers)
    aptc = max(0.0, slcsp * a - fpl['monthly_contrib'])
    plans = []
    for r in rows:
        cty, metal, issuer, pname, ptype, p27 = r
        total = p27 * a
        net = max(0.0, total - aptc)
        plans.append({'metal':metal,'issuer':issuer,'plan_name':pname,'plan_type':ptype,
                      'premium_base':round(p27,2),'premium_total':round(total,2),'net_premium':round(net,2)})
    plans.sort(key=lambda x: (0 if x['metal']=='Silver' else 1, x['net_premium']))
    return {'zipcode':zipcode,'state':state,'county':county,'city':city,
            'h':h,'a':a,'income':income,'fpl':fpl,
            'slcsp_base':round(slcsp,2),'aptc':round(aptc,2),'plans':plans}

def api_compare_zips(z1, z2, h, a, income, issuer_filter=None):
    r1, r2 = api_zip_lookup(z1, h, a, income), api_zip_lookup(z2, h, a, income)
    if issuer_filter:
        il = issuer_filter.lower()
        if 'plans' in r1: r1['plans'] = [p for p in r1['plans'] if il in p['issuer'].lower()]
        if 'plans' in r2: r2['plans'] = [p for p in r2['plans'] if il in p['issuer'].lower()]
    return {'zip1':r1,'zip2':r2}

def api_best_zips(state, h, a, income, issuer_filter=None):
    fpl = calc_fpl_info(h, a, income)
    rows = get_county_plans(state)
    county_plans = {}
    for r in rows:
        cty, metal, issuer, pname, ptype, p27 = r
        if cty not in county_plans: county_plans[cty] = {'Silver':[],'Bronze':[],'all':[]}
        e = {'issuer':issuer,'plan':pname,'type':ptype,'premium':p27,'metal':metal}
        county_plans[cty][metal].append(e)
        county_plans[cty]['all'].append(e)
    results = []
    for cty, metals in county_plans.items():
        sils = metals['Silver']
        if not sils: continue
        slcsp = compute_slcsp(sils)
        aptc = max(0.0, slcsp * a - fpl['monthly_contrib'])
        tgt = [p for p in metals['all'] if not issuer_filter or issuer_filter.lower() in p['issuer'].lower()]
        if not tgt: continue
        ts = [p for p in tgt if p['metal']=='Silver']
        tb = [p for p in tgt if p['metal']=='Bronze']
        bs = min(ts, key=lambda x:x['premium']) if ts else None
        bb = min(tb, key=lambda x:x['premium']) if tb else None
        geo = GEO_CACHE.get((state, cty.lower()))
        results.append({
            'county':cty,
            'cities':sorted(list(geo['cities']))[:3] if geo else [],
            'all_zips':geo['zips'] if geo else [],
            'total_zips':len(geo['zips']) if geo else 0,
            'slcsp_base':round(slcsp,2), 'aptc':round(aptc,2),
            'best_silver':{'issuer':bs['issuer'],'plan':bs['plan'],'type':bs['type'],
                           'base':round(bs['premium'],2),'net':round(max(0,bs['premium']*a-aptc),2)} if bs else None,
            'best_bronze':{'issuer':bb['issuer'],'plan':bb['plan'],'type':bb['type'],
                           'base':round(bb['premium'],2),'net':round(max(0,bb['premium']*a-aptc),2)} if bb else None,
        })
    results.sort(key=lambda x: (x['best_silver']['net'] if x['best_silver'] else 99999, -x['aptc']))
    return {'state':state,'h':h,'a':a,'income':income,'fpl':fpl,'issuer_filter':issuer_filter,'results':results}


# ──────────────────────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>TBR Plan Finder</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f5f5f5;color:#222}

/* HEADER */
.hdr{background:linear-gradient(135deg,#1a365d,#2563eb);color:#fff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:1.2rem;font-weight:900;letter-spacing:-0.5px}
.hdr small{opacity:.75;font-size:.78rem}

/* TABS */
.tabs{background:#fff;display:flex;border-bottom:2px solid #e5e7eb;padding:0 16px}
.tabs button{padding:12px 20px;font-size:.88rem;font-weight:700;border:none;background:none;color:#888;cursor:pointer;border-bottom:3px solid transparent;transition:.2s}
.tabs button:hover{color:#2563eb;background:#eff6ff}
.tabs button.on{color:#2563eb;border-bottom-color:#2563eb}

/* MAIN */
.main{max-width:1300px;margin:0 auto;padding:16px}
.panel{display:none}.panel.on{display:block}

/* FORM */
.form-box{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:16px}
.form-row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px;min-width:120px;flex:1}
.fg label{font-size:.75rem;font-weight:700;color:#555;text-transform:uppercase}
.fg input,.fg select{padding:10px 12px;border:1.5px solid #ddd;border-radius:8px;font-size:.92rem;font-family:inherit}
.fg input:focus,.fg select:focus{outline:none;border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.btn{padding:10px 24px;border:none;border-radius:8px;font-weight:800;font-size:.92rem;cursor:pointer;color:#fff;transition:.2s}
.btn-blue{background:#2563eb}.btn-blue:hover{background:#1d4ed8}
.btn-green{background:#059669}.btn-green:hover{background:#047857}
.btn-orange{background:#d97706}.btn-orange:hover{background:#b45309}

/* INFO BAR — hiện thông tin khách */
.info-bar{background:#eff6ff;border-left:4px solid #2563eb;border-radius:8px;padding:12px 16px;margin-bottom:14px;font-size:.88rem;display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.info-bar b{color:#1e3a5f}
.tag{display:inline-block;padding:3px 10px;border-radius:6px;font-weight:700;font-size:.82rem}
.tag-green{background:#d1fae5;color:#065f46}
.tag-gold{background:#fef3c7;color:#92400e}
.tag-blue{background:#dbeafe;color:#1e40af}
.tag-red{background:#fee2e2;color:#b91c1c}

/* ── PLAN CARDS ── */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.plan-card{background:#fff;border-radius:12px;border:1.5px solid #e5e7eb;overflow:hidden;transition:.15s}
.plan-card:hover{border-color:#93c5fd;box-shadow:0 4px 16px rgba(37,99,235,.1)}
.plan-card.best{border-color:#059669;box-shadow:0 0 0 2px rgba(5,150,105,.2)}

.pc-head{padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #f3f4f6}
.pc-issuer{font-weight:800;font-size:.95rem;color:#1e293b}
.pc-type{font-size:.72rem;font-weight:700;padding:3px 8px;border-radius:4px;background:#f1f5f9;color:#64748b}

.pc-body{padding:14px 16px}
.pc-plan{font-size:.82rem;color:#64748b;margin-bottom:10px;line-height:1.3}

.pc-prices{display:flex;align-items:baseline;gap:12px}
.pc-net{font-size:1.6rem;font-weight:900}
.pc-net.free{color:#059669}
.pc-net.cheap{color:#0d9488}
.pc-net.mid{color:#d97706}
.pc-net.high{color:#dc2626}
.pc-per{font-size:.78rem;color:#888;font-weight:600}
.pc-orig{font-size:.78rem;color:#aaa;text-decoration:line-through;margin-left:auto}

.pc-foot{padding:8px 16px;background:#f9fafb;font-size:.75rem;color:#64748b;display:flex;justify-content:space-between}

.rec-badge{position:absolute;top:-1px;right:-1px;background:#059669;color:#fff;font-size:.68rem;font-weight:800;padding:4px 10px;border-radius:0 10px 0 8px}

/* Section headers in results */
.section-hdr{font-size:1rem;font-weight:800;margin:20px 0 10px;color:#1e293b;display:flex;align-items:center;gap:8px}
.section-hdr .emoji{font-size:1.2rem}

/* COMPARE */
.compare-wrap{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.cmp-col{background:#fff;border-radius:12px;overflow:hidden;border:1.5px solid #e5e7eb}
.cmp-hdr{padding:14px 16px;font-weight:800;font-size:.95rem;display:flex;justify-content:space-between;align-items:center}
.cmp-hdr.a{background:#eff6ff;color:#1e40af;border-bottom:2px solid #93c5fd}
.cmp-hdr.b{background:#fefce8;color:#a16207;border-bottom:2px solid #fde68a}
.cmp-body{padding:16px}
.cmp-stat{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:.88rem}
.cmp-stat:last-child{border:none}
.cmp-label{color:#64748b;font-weight:600}
.cmp-val{font-weight:800;color:#1e293b}

.cmp-plan{background:#f9fafb;border-radius:8px;padding:10px 14px;margin-top:8px}
.cmp-plan-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #f1f5f9}
.cmp-plan-row:last-child{border:none}

.summary-box{background:linear-gradient(135deg,#fefce8,#fff7ed);border:1.5px solid #fde68a;border-radius:12px;padding:16px 20px;margin-bottom:16px}
.summary-box h3{font-size:.95rem;font-weight:800;color:#92400e;margin-bottom:8px}
.summary-row{display:flex;gap:24px;flex-wrap:wrap}
.summary-item{text-align:center;flex:1;min-width:120px}
.summary-item .num{font-size:1.5rem;font-weight:900}
.summary-item .lbl{font-size:.72rem;color:#78350f;font-weight:600}

/* BEST ZIPS TABLE - simplified */
.simple-table{width:100%;border-collapse:collapse;font-size:.88rem}
.simple-table th{background:#f8fafc;padding:10px 12px;text-align:left;font-size:.72rem;font-weight:800;color:#555;text-transform:uppercase;border-bottom:2px solid #e5e7eb;white-space:nowrap}
.simple-table td{padding:12px;border-bottom:1px solid #f3f4f6;vertical-align:middle}
.simple-table tr:hover{background:#f8fafc}
.simple-table tr.top{background:#f0fdf4}

.zip-box{display:flex;flex-wrap:wrap;gap:4px;max-width:340px;padding:2px 0}
.zip-pill{display:inline-block;background:#f1f5f9;border:1px solid #cbd5e1;padding:2px 6px;border-radius:4px;font-family:monospace;font-size:.8rem;font-weight:700;color:#334155;margin:1px;cursor:pointer;transition:.15s}
.zip-pill:hover{background:#2563eb;color:#fff;border-color:#2563eb}

/* RESPONSIVE */
@media(max-width:768px){
  .form-row{flex-direction:column}
  .cards{grid-template-columns:1fr}
  .compare-wrap{grid-template-columns:1fr}
  .tabs button{padding:10px 12px;font-size:.8rem}
}
</style>
</head>
<body>

<div class="hdr">
  <div><h1>TBR PLAN FINDER — Obamacare 2026</h1><small>The Best Rate Insurance &bull; CMS Official Data</small></div>
  <small>36,829 plans &bull; 30 states</small>
</div>

<div class="tabs">
  <button class="on" onclick="go(0,this)">📍 Tra Cứu Zip</button>
  <button onclick="go(1,this)">⚖️ So Sánh 2 Zip</button>
  <button onclick="go(2,this)">🏆 Zip Tốt Nhất</button>
</div>

<div class="main">

<!-- ============ TAB 0: TRA CỨU ZIP ============ -->
<div id="p0" class="panel on">
  <div class="form-box">
    <div style="font-weight:800;margin-bottom:10px;font-size:.95rem">📍 Nhập Zipcode khách — Xem tất cả hãng & giá khách phải trả</div>
    <div class="form-row">
      <div class="fg" style="max-width:140px"><label>Zipcode</label><input id="z0" placeholder="VD: 48314" maxlength="5"></div>
      <div class="fg" style="max-width:90px"><label>H (Hộ)</label><input type="number" id="h0" value="1" min="1"></div>
      <div class="fg" style="max-width:90px"><label>A (Mua)</label><input type="number" id="a0" value="1" min="1"></div>
      <div class="fg" style="max-width:150px"><label>Income ($)</label><input type="number" id="i0" value="22100"></div>
      <button class="btn btn-blue" onclick="doZip()">🔍 Tra Cứu</button>
    </div>
  </div>
  <div id="r0"></div>
</div>

<!-- ============ TAB 1: SO SÁNH 2 ZIP ============ -->
<div id="p1" class="panel">
  <div class="form-box">
    <div style="font-weight:800;margin-bottom:4px;font-size:.95rem">⚖️ So sánh 2 Zipcode — Tại sao zip này rẻ hơn zip kia?</div>
    <div style="font-size:.82rem;color:#666;margin-bottom:10px">Nhập 2 zip + chọn hãng bảo hiểm → Thấy ngay giá chênh lệch & lý do</div>
    <div class="form-row">
      <div class="fg" style="max-width:130px"><label>Zip 1</label><input id="z1a" placeholder="48413" maxlength="5"></div>
      <div class="fg" style="max-width:130px"><label>Zip 2</label><input id="z1b" placeholder="48314" maxlength="5"></div>
      <div class="fg" style="max-width:90px"><label>H</label><input type="number" id="h1" value="7" min="1"></div>
      <div class="fg" style="max-width:90px"><label>A</label><input type="number" id="a1" value="7" min="1"></div>
      <div class="fg" style="max-width:140px"><label>Income ($)</label><input type="number" id="i1" value="50000"></div>
      <div class="fg"><label>Lọc hãng (tuỳ chọn)</label><input id="iss1" placeholder="VD: Priority Health"></div>
      <button class="btn btn-orange" onclick="doCmp()">⚖️ So Sánh</button>
    </div>
  </div>
  <div id="r1"></div>
</div>

<!-- ============ TAB 2: ZIP TỐT NHẤT ============ -->
<div id="p2" class="panel">
  <div class="form-box">
    <div style="font-weight:800;margin-bottom:4px;font-size:.95rem">🏆 Tìm Zipcode giá tốt nhất trong bang — theo hãng</div>
    <div style="font-size:.82rem;color:#666;margin-bottom:10px">Chọn bang + hãng → Xem quận nào cho giá rẻ nhất cho hãng đó</div>
    <div class="form-row">
      <div class="fg"><label>Tiểu bang</label><select id="s2" onchange="loadIss()"><option value="">-- Chọn --</option></select></div>
      <div class="fg"><label>Hãng bảo hiểm</label><select id="iss2"><option value="">Tất cả</option></select></div>
      <div class="fg" style="max-width:90px"><label>H</label><input type="number" id="h2" value="1" min="1"></div>
      <div class="fg" style="max-width:90px"><label>A</label><input type="number" id="a2" value="1" min="1"></div>
      <div class="fg" style="max-width:140px"><label>Income ($)</label><input type="number" id="i2" value="22100"></div>
      <button class="btn btn-green" onclick="doBest()">🏆 Tìm Kiếm</button>
    </div>
  </div>
  <div id="r2"></div>
</div>

</div>

<script>
const ST={AK:'Alaska',AL:'Alabama',AR:'Arkansas',AZ:'Arizona',DE:'Delaware',FL:'Florida',HI:'Hawaii',IA:'Iowa',IN:'Indiana',KS:'Kansas',LA:'Louisiana',MI:'Michigan',MO:'Missouri',MS:'Mississippi',MT:'Montana',NC:'North Carolina',ND:'North Dakota',NE:'Nebraska',NH:'New Hampshire',OH:'Ohio',OK:'Oklahoma',OR:'Oregon',SC:'South Carolina',SD:'South Dakota',TN:'Tennessee',TX:'Texas',UT:'Utah',WI:'Wisconsin',WV:'West Virginia',WY:'Wyoming'};

// populate state select
(function(){
  const s=document.getElementById('s2');
  Object.entries(ST).sort((a,b)=>a[1].localeCompare(b[1])).forEach(([c,n])=>{
    const o=document.createElement('option');o.value=c;o.textContent=n+' ('+c+')';s.appendChild(o);
  });
})();

function go(i,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));
  document.getElementById('p'+i).classList.add('on');
  btn.classList.add('on');
}

function priceClass(v){return v===0?'free':v<50?'cheap':v<200?'mid':'high'}
function fmt(v){return v===0?'$0':('$'+v.toFixed(2))}
function fmtBig(v){return v===0?'FREE ($0)':('$'+v.toFixed(2))}

// ── TAB 0: ZIP LOOKUP ──
async function doZip(){
  const z=document.getElementById('z0').value.trim();
  const h=+document.getElementById('h0').value||1;
  const a=+document.getElementById('a0').value||h;
  const inc=+document.getElementById('i0').value||0;
  if(!z||z.length!==5){alert('Nhập đúng 5 số zipcode');return}
  document.getElementById('r0').innerHTML='<div style="text-align:center;padding:40px;color:#888">⏳ Đang tra cứu...</div>';

  const res=await fetch('/api/zip_lookup?zip='+z+'&h='+h+'&a='+a+'&income='+inc);
  const d=await res.json();
  if(d.error){document.getElementById('r0').innerHTML='<div class="form-box" style="color:red;font-weight:700">❌ '+d.error+'</div>';return}

  // Info bar
  let html=`<div class="info-bar">
    <b>📍 Zip ${d.zipcode}</b> — ${d.city}, ${d.county} County, ${d.state}
    &nbsp;|&nbsp; <b>H${d.h} A${d.a}</b>
    &nbsp;|&nbsp; Income: <b>$${Number(d.income).toLocaleString()}</b>
    &nbsp;|&nbsp; FPL: <b>${d.fpl.pct_fpl}%</b>
    &nbsp;|&nbsp; <span class="tag tag-gold">${d.fpl.csr}</span>
    &nbsp;|&nbsp; Trợ giá APTC: <span class="tag tag-green">+$${d.aptc.toLocaleString(undefined,{minimumFractionDigits:2})}/tháng</span>
  </div>`;

  // Group plans by issuer
  const byIssuer={};
  d.plans.forEach(p=>{
    if(!byIssuer[p.issuer])byIssuer[p.issuer]={silver:[],bronze:[]};
    byIssuer[p.issuer][p.metal.toLowerCase()].push(p);
  });

  // SILVER section
  const silvers=d.plans.filter(p=>p.metal==='Silver');
  const bronzes=d.plans.filter(p=>p.metal==='Bronze');

  html+=`<div class="section-hdr"><span class="emoji">🩺</span> Gói SILVER — ${silvers.length} gói có sẵn</div>`;
  html+='<div class="cards">';
  silvers.forEach((p,i)=>{
    const cls=priceClass(p.net_premium);
    html+=`<div class="plan-card${i===0?' best':''}" style="position:relative">
      ${i===0?'<div class="rec-badge">✓ RẺ NHẤT</div>':''}
      <div class="pc-head">
        <span class="pc-issuer">${p.issuer}</span>
        <span class="pc-type">${p.plan_type}</span>
      </div>
      <div class="pc-body">
        <div class="pc-plan">${p.plan_name}</div>
        <div class="pc-prices">
          <span class="pc-net ${cls}">${fmtBig(p.net_premium)}</span>
          <span class="pc-per">/tháng</span>
          <span class="pc-orig">gốc $${p.premium_total.toFixed(2)}</span>
        </div>
      </div>
      <div class="pc-foot">
        <span>Giá gốc/người: $${p.premium_base.toFixed(2)}</span>
        <span>×${d.a} người</span>
      </div>
    </div>`;
  });
  html+='</div>';

  // BRONZE section
  if(bronzes.length){
    html+=`<div class="section-hdr"><span class="emoji">🛡️</span> Gói BRONZE — ${bronzes.length} gói</div>`;
    html+='<div class="cards">';
    bronzes.forEach((p,i)=>{
      const cls=priceClass(p.net_premium);
      html+=`<div class="plan-card${i===0?' best':''}" style="position:relative">
        ${i===0?'<div class="rec-badge">✓ RẺ NHẤT</div>':''}
        <div class="pc-head">
          <span class="pc-issuer">${p.issuer}</span>
          <span class="pc-type">${p.plan_type}</span>
        </div>
        <div class="pc-body">
          <div class="pc-plan">${p.plan_name}</div>
          <div class="pc-prices">
            <span class="pc-net ${cls}">${fmtBig(p.net_premium)}</span>
            <span class="pc-per">/tháng</span>
            <span class="pc-orig">gốc $${p.premium_total.toFixed(2)}</span>
          </div>
        </div>
        <div class="pc-foot">
          <span>Giá gốc/người: $${p.premium_base.toFixed(2)}</span>
          <span>×${d.a} người</span>
        </div>
      </div>`;
    });
    html+='</div>';
  }

  document.getElementById('r0').innerHTML=html;
}

// ── TAB 1: COMPARE ──
async function doCmp(){
  const z1=document.getElementById('z1a').value.trim();
  const z2=document.getElementById('z1b').value.trim();
  const h=+document.getElementById('h1').value||1;
  const a=+document.getElementById('a1').value||h;
  const inc=+document.getElementById('i1').value||0;
  const iss=document.getElementById('iss1').value.trim();
  if(!z1||!z2){alert('Nhập đầy đủ 2 zipcode');return}
  document.getElementById('r1').innerHTML='<div style="text-align:center;padding:40px;color:#888">⏳ Đang so sánh...</div>';
  let url='/api/compare?zip1='+z1+'&zip2='+z2+'&h='+h+'&a='+a+'&income='+inc;
  if(iss)url+='&issuer='+encodeURIComponent(iss);
  const res=await fetch(url);
  const d=await res.json();

  let html='';
  // Summary
  if(!d.zip1.error&&!d.zip2.error){
    const diff=d.zip1.aptc-d.zip2.aptc;
    const winner=d.zip1.aptc>d.zip2.aptc?d.zip1:d.zip2;
    const loser=d.zip1.aptc>d.zip2.aptc?d.zip2:d.zip1;
    html+=`<div class="summary-box">
      <h3>📋 KẾT LUẬN NHANH</h3>
      <div class="summary-row">
        <div class="summary-item"><div class="lbl">Zip ${d.zip1.zipcode} — Trợ giá APTC</div><div class="num" style="color:#059669">$${d.zip1.aptc.toFixed(0)}</div></div>
        <div class="summary-item"><div class="lbl">Zip ${d.zip2.zipcode} — Trợ giá APTC</div><div class="num" style="color:#059669">$${d.zip2.aptc.toFixed(0)}</div></div>
        <div class="summary-item"><div class="lbl">Chênh lệch</div><div class="num" style="color:${diff>0?'#059669':'#dc2626'}">${diff>0?'+':''}$${diff.toFixed(0)}</div></div>
      </div>
      <div style="margin-top:10px;font-size:.88rem;color:#78350f;background:#fef3c7;padding:8px 12px;border-radius:6px">
        ⚡ <b>Zip ${winner.zipcode} (${winner.county})</b> có mốc chuẩn SLCSP cao hơn ($${winner.slcsp_base}/người vs $${loser.slcsp_base}/người)
        → Chính phủ trợ giá <b>nhiều hơn $${Math.abs(diff).toFixed(0)}/tháng</b>
        → Khách trả <b>ít hơn</b> cho cùng hãng bảo hiểm!
      </div>
    </div>`;
  }

  // Side by side
  html+='<div class="compare-wrap">';
  [['zip1','a'],['zip2','b']].forEach(([key,side])=>{
    const z=d[key];
    if(z.error){html+=`<div class="cmp-col"><div class="cmp-hdr ${side}">❌ ${z.error}</div></div>`;return}
    html+=`<div class="cmp-col">
      <div class="cmp-hdr ${side}">
        <span>📍 ZIP ${z.zipcode}</span>
        <span>${z.city}, ${z.county}, ${z.state}</span>
      </div>
      <div class="cmp-body">
        <div class="cmp-stat"><span class="cmp-label">Mốc chuẩn SLCSP</span><span class="cmp-val">$${z.slcsp_base}/người</span></div>
        <div class="cmp-stat"><span class="cmp-label">Trợ giá APTC</span><span class="cmp-val" style="color:#059669">+$${z.aptc.toLocaleString(undefined,{minimumFractionDigits:2})}</span></div>
        <div class="cmp-stat"><span class="cmp-label">FPL</span><span class="cmp-val">${z.fpl.pct_fpl}%</span></div>
        <div style="margin-top:12px;font-weight:800;font-size:.85rem;color:#475569">Các gói có sẵn:</div>`;
    z.plans.forEach(p=>{
      const cls=priceClass(p.net_premium);
      html+=`<div class="cmp-plan">
        <div style="font-weight:700;font-size:.88rem">${p.issuer}</div>
        <div style="font-size:.78rem;color:#888">${p.plan_name}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px">
          <span class="tag tag-${p.metal==='Silver'?'blue':'gold'}">${p.metal}</span>
          <span style="font-size:1.2rem;font-weight:900" class="pc-net ${cls}">${fmtBig(p.net_premium)}<span style="font-size:.75rem;font-weight:600;color:#888">/tháng</span></span>
        </div>
      </div>`;
    });
    html+='</div></div>';
  });
  html+='</div>';
  document.getElementById('r1').innerHTML=html;
}

// ── TAB 2: BEST ZIPS ──
async function loadIss(){
  const st=document.getElementById('s2').value;
  const sel=document.getElementById('iss2');
  sel.innerHTML='<option value="">Tất cả</option>';
  if(!st)return;
  const res=await fetch('/api/issuers?state='+st);
  const data=await res.json();
  data.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o)});
}

async function doBest(){
  const st=document.getElementById('s2').value;
  const iss=document.getElementById('iss2').value;
  const h=+document.getElementById('h2').value||1;
  const a=+document.getElementById('a2').value||h;
  const inc=+document.getElementById('i2').value||0;
  if(!st){alert('Chọn tiểu bang');return}
  document.getElementById('r2').innerHTML='<div style="text-align:center;padding:40px;color:#888">⏳ Đang tìm kiếm...</div>';
  let url='/api/best_zips?state='+st+'&h='+h+'&a='+a+'&income='+inc;
  if(iss)url+='&issuer='+encodeURIComponent(iss);
  const res=await fetch(url);
  const d=await res.json();

  // Info
  let html=`<div class="info-bar">
    <b>${ST[d.state]||d.state}</b>
    &nbsp;|&nbsp; H${d.h} A${d.a}
    &nbsp;|&nbsp; Income: <b>$${Number(d.income).toLocaleString()}</b>
    &nbsp;|&nbsp; FPL: <b>${d.fpl.pct_fpl}%</b>
    &nbsp;|&nbsp; <span class="tag tag-gold">${d.fpl.csr}</span>
    ${d.issuer_filter?'&nbsp;|&nbsp; Hãng: <b>'+d.issuer_filter+'</b>':''}
  </div>`;

  // Cards for top 3
  html+='<div class="section-hdr"><span class="emoji">🥇</span> TOP 3 QUẬN GIÁ TỐT NHẤT</div>';
  html+='<div class="cards">';
  d.results.slice(0,3).forEach((r,i)=>{
    const s=r.best_silver;
    if(!s)return;
    const cls=priceClass(s.net);
    const medals=['🥇','🥈','🥉'];
    html+=`<div class="plan-card best" style="position:relative">
      <div class="rec-badge">${medals[i]} #${i+1}</div>
      <div class="pc-head">
        <span class="pc-issuer">${r.cities.join(', ')||r.county}</span>
        <span class="pc-type">${r.county} County</span>
      </div>
      <div class="pc-body">
        <div class="pc-plan"><b>${s.issuer}</b> — ${s.plan}</div>
        <div class="pc-prices">
          <span class="pc-net ${cls}">${fmtBig(s.net)}</span>
          <span class="pc-per">/tháng (Silver)</span>
        </div>
        <div style="margin-top:8px;font-size:.82rem;color:#666">
          APTC: <span class="tag tag-green">+$${r.aptc.toFixed(0)}</span>
          &nbsp; SLCSP: $${r.slcsp_base}/người
        </div>
        <div style="margin-top:8px" class="zip-box">
          ${(r.all_zips||[]).map(z=>'<span class="zip-pill" onclick="clickZip(\''+z+'\','+h+','+a+','+inc+')">'+z+'</span>').join('')}
        </div>
      </div>
    </div>`;
  });
  html+='</div>';

  // Full table
  if(d.results.length>3){
    html+='<div class="section-hdr" style="margin-top:24px"><span class="emoji">📊</span> BẢNG ĐẦY ĐỦ — '+d.results.length+' quận</div>';
    html+='<div class="form-box" style="padding:12px 16px"><input type="text" id="filterBest" oninput="filterBestT()" placeholder="🔎 Lọc nhanh: gõ zip, thành phố, hoặc hãng..." style="width:100%;padding:8px 12px;border:1.5px solid #ddd;border-radius:8px;font-size:.88rem"></div>';
    html+='<div class="form-box" style="padding:0;overflow-x:auto"><table class="simple-table"><thead><tr><th>#</th><th>Quận / Thành phố</th><th>Toàn Bộ Zipcode</th><th>Hãng rẻ nhất (Silver)</th><th>Khách trả</th><th>Bronze</th><th>APTC</th></tr></thead><tbody id="bestBody">';
    d.results.forEach((r,i)=>{
      const s=r.best_silver;const b=r.best_bronze;
      const cls=s?priceClass(s.net):'';
      html+=`<tr class="${i<3?'top':''}">
        <td style="font-weight:800;color:${i<3?'#059669':'#888'}">${i+1}</td>
        <td><div style="font-weight:700">${r.cities.join(', ')||r.county}</div><div style="font-size:.75rem;color:#888">${r.county} County</div></td>
        <td><div class="zip-box">${(r.all_zips||[]).map(z=>'<span class="zip-pill" onclick="clickZip(\''+z+'\','+h+','+a+','+inc+')">'+z+'</span>').join('')}</div></td>
        <td>${s?'<div style="font-weight:700;font-size:.85rem">'+s.issuer+'</div><div style="font-size:.75rem;color:#888">'+s.plan+'</div>':'-'}</td>
        <td><span class="pc-net ${cls}" style="font-size:1.05rem">${s?fmtBig(s.net):'-'}</span></td>
        <td>${b?'<span style="font-weight:700">'+fmt(b.net)+'</span>':'-'}</td>
        <td><span class="tag tag-green">+$${r.aptc.toFixed(0)}</span></td>
      </tr>`;
    });
    html+='</tbody></table></div>';
  }

  document.getElementById('r2').innerHTML=html;
}

function filterBestT(){
  const q=(document.getElementById('filterBest').value||'').trim().toLowerCase();
  document.querySelectorAll('#bestBody tr').forEach(tr=>{tr.style.display=tr.innerText.toLowerCase().includes(q)?'':'none'});
}

function clickZip(z,h,a,inc){
  // switch to tab 0 and lookup
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('on'));
  document.getElementById('p0').classList.add('on');
  document.querySelectorAll('.tabs button')[0].classList.add('on');
  document.getElementById('z0').value=z;
  document.getElementById('h0').value=h;
  document.getElementById('a0').value=a;
  document.getElementById('i0').value=inc;
  doZip();
}

// Enter key
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'){
    const act=document.querySelector('.panel.on');
    if(act.id==='p0')doZip();
    else if(act.id==='p1')doCmp();
    else if(act.id==='p2')doBest();
  }
});
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────────────────────
class handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): pass
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path in ('/','/index.html'):
            self._html(HTML_PAGE)
        elif p.path == '/api/zip_lookup':
            q = urllib.parse.parse_qs(p.query)
            self._json(api_zip_lookup(q.get('zip',[''])[0].strip(), int(q.get('h',[1])[0]), int(q.get('a',[1])[0]), float(q.get('income',[22100])[0])))
        elif p.path == '/api/compare':
            q = urllib.parse.parse_qs(p.query)
            self._json(api_compare_zips(q.get('zip1',[''])[0].strip(), q.get('zip2',[''])[0].strip(), int(q.get('h',[1])[0]), int(q.get('a',[1])[0]), float(q.get('income',[22100])[0]), q.get('issuer',[''])[0].strip() or None))
        elif p.path == '/api/best_zips':
            q = urllib.parse.parse_qs(p.query)
            self._json(api_best_zips(q.get('state',['TX'])[0].upper(), int(q.get('h',[1])[0]), int(q.get('a',[1])[0]), float(q.get('income',[22100])[0]), q.get('issuer',[''])[0].strip() or None))
        elif p.path == '/api/issuers':
            q = urllib.parse.parse_qs(p.query)
            self._json(api_issuers(q.get('state',['TX'])[0].upper()))
        else:
            self.send_response(404); self.end_headers()

    def _html(self, c):
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(c.encode('utf-8'))
    def _json(self, d):
        self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Access-Control-Allow-Origin','*'); self.end_headers(); self.wfile.write(json.dumps(d,ensure_ascii=False).encode('utf-8'))

def run_server():
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as srv:
        srv.allow_reuse_address = True
        print(f"TBR Plan Finder Pro running at http://localhost:{PORT}")
        srv.serve_forever()

if __name__ == '__main__':
    run_server()
