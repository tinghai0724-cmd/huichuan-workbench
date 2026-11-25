
const $ = id => document.getElementById(id);

// ---------- 后端地址 / 会话 token（线上版直连远程后端用） ----------
// 默认空 = 同源（本地 python server.py 直接跑）。
// 线上部署时用 ?api=https://xxx.yyy 或 localStorage.cw_api 指定后端地址。
const CW_API=(function(){
  let v='';
  try{
    const q=new URLSearchParams(location.search).get('api');
    if(q){ v=q.trim(); localStorage.setItem('cw_api', v); }
    else { v=(localStorage.getItem('cw_api')||'').trim(); }
    if(!v) v=(window.CW_DEFAULT_API||'').trim();
    if(!v){
      // 没显式配置：页面由本机 server.py 提供（端口 8787）→ 走同源；
      // 由别的静态服务/端口提供 → 指向同一台主机的 8787
      const host=location.hostname;
      if(location.protocol==='http:' && host && host!=='127.0.0.1' && host!=='localhost' && location.port!=='8787'){
        v='http://'+host+':8787';
      }
    }
    // 别的电脑上用 127.0.0.1 只会打到它自己 → 自动换成本页所在主机
    if(v && location.protocol==='http:' && (location.hostname||'')!=='127.0.0.1' && (location.hostname||'')!=='localhost'){
      const u=new URL(v);
      if(u.hostname==='127.0.0.1' || u.hostname==='localhost'){ u.hostname=location.hostname; v=u.toString(); }
    }
  }catch(e){}
  return v.replace(/\/+$/,'');
})();
let CW_TOKEN='';
try{ CW_TOKEN = localStorage.getItem('cw_token')||''; }catch(e){}
function setToken(t){
  CW_TOKEN=t||'';
  try{ CW_TOKEN?localStorage.setItem('cw_token',CW_TOKEN):localStorage.removeItem('cw_token'); }catch(e){}
}
function apiUrl(p){
  if(/^http(s)?:/i.test(p)) return p;
  return (CW_API && p.indexOf('/api/')===0) ? CW_API+p : p;
}
function withToken(url){
  if(!CW_TOKEN) return url;
  return url + (url.indexOf('?')>=0?'&':'?') + 't=' + encodeURIComponent(CW_TOKEN);
}
const _nativeFetch = window.fetch.bind(window);
window.fetch = function(u,opt){
  let url = typeof u==='string' ? apiUrl(u) : u;
  const cross = typeof url==='string' && !!CW_API && url.indexOf(CW_API)===0;
  opt = opt ? Object.assign({}, opt) : {};
  opt.headers = Object.assign({}, opt.headers||{});
  if(CW_TOKEN) opt.headers['X-CW-Token'] = CW_TOKEN;
  if(cross) opt.credentials = 'include';
  return _nativeFetch(url, opt);
};
// 画幅比例：按参考截图给的 7 项（去掉 1:1，新增 21:9）
const RATIOS = [
  ['2:3 (Portrait Photo)','2:3'],['3:2 (Photo)','3:2'],
  ['3:4 (Portrait Standard)','3:4'],['4:3 (Standard)','4:3'],
  ['9:16 (Portrait Widescreen)','9:16'],['16:9 (Widescreen)','16:9'],
  ['21:9 (Ultrawide)','21:9']
];
// 像素档位：对齐参考截图的 14 档（multiple = 32）
const MP_STEPS=[0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.98,1.0,1.2,1.5,1.8,2.0];
let STATE={items:[],jobs:{},workflows:[],default_workflow:''};
let filter='all', ratioFilter='all', mode='single', current=null, pollTimer=null;
// 参考图：最多 5 张，refImages 存 ComfyUI input 里的文件名，refUrls 存本地预览用的 objectURL
const REF_MAX = 5;
let refImages = [];
let refUrls = [];
let curRatio='16:9 (Widescreen)';
let WF_NAME='';
// 输出模式：image / video。视频模式下换彩色主题、只列视频流程
let OUT_MODE='image';
try{ OUT_MODE = (localStorage.getItem('cw_out_mode')==='video') ? 'video' : 'image'; }catch(e){}

// 比例按钮（抽屉里的 chip 行）
RATIOS.forEach(([val,label])=>{
  const b=document.createElement('button');
  b.className='chip'+(val===curRatio?' on':''); b.textContent=label; b.dataset.v=val;
  b.onclick=()=>setRatioFrom(val);
  ratioRow.appendChild(b);
});

// 比例下拉（首页 / 生成页）：自绘可搜索面板，选中项打勾
const ratioShort=v=>(v||'').split(' ')[0];
function ratioName(v){ const m=/\(([^)]*)\)/.exec(v||''); return m? m[1] : ''; }
function fillRatioSelects(){
  const txt = ratioShort(curRatio)||'自动';
  ['homeRatioText','gcRatioText'].forEach(id=>{ const el=$(id); if(el) el.textContent=txt; });
  [...ratioRow.children].forEach(c=>c.classList.toggle('on', c.dataset.v===curRatio));
}
function renderRatioList(q){
  const box=$('ratioList'); if(!box) return;
  const kw=(q||'').trim().toLowerCase();
  const rows=RATIOS.filter(([v,l])=> !kw || v.toLowerCase().includes(kw) || l.includes(kw));
  if(!rows.length){ box.innerHTML='<div class="rp-empty">没有匹配的比例</div>'; return; }
  box.innerHTML=rows.map(([v,l])=>{
    const on = (v===curRatio);
    return `<div class="rp-item" onclick="pickRatio('${v}')">
      <span><b>${l}</b> <small>${ratioName(v)}</small></span>
      <span class="rp-ck">${on?'✓':''}</span></div>`;
  }).join('');
}
function openRatioPicker(btn){
  const pop=$('ratioPop'); if(!pop) return;
  if(pop.classList.contains('show')){ closeRatioPicker(); return; }
  if(!btn) btn=$('homeRatioBtn');
  const r=btn.getBoundingClientRect();
  pop.classList.add('show');
  const w=pop.offsetWidth||230, h=pop.offsetHeight||200;
  let left=r.left, top=r.bottom+6;
  if(left+w>innerWidth-8) left=Math.max(8, innerWidth-w-8);
  if(top+h>innerHeight-8) top=Math.max(8, r.top-h-6);
  pop.style.left=left+'px'; pop.style.top=top+'px';
  const s=$('ratioSearch'); if(s){ s.value=''; renderRatioList(''); setTimeout(()=>s.focus(),30); }
}
function closeRatioPicker(){ const p=$('ratioPop'); if(p) p.classList.remove('show'); }
function pickRatio(v){ setRatioFrom(v); closeRatioPicker(); }
document.addEventListener('click', e=>{
  const p=$('ratioPop');
  if(p && p.classList.contains('show') && !e.target.closest('#ratioPop') && !e.target.closest('.qratio-wrap')) closeRatioPicker();
});
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeRatioPicker(); });
fillRatioSelects();

function toast(msg,isErr){const t=$('toast');t.textContent=msg;t.classList.toggle('err',!!isErr);t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),2800)}
function fmtTime(ts){const d=new Date(ts*1000),diff=Date.now()/1000-ts;
  if(diff<60)return '刚刚'; if(diff<3600)return Math.floor(diff/60)+' 分钟前';
  if(diff<86400)return Math.floor(diff/3600)+' 小时前';
  return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}
function imgUrl(f){const q=new URLSearchParams({filename:f.filename,subfolder:f.subfolder||'',type:f.type||'output'});if(CW_TOKEN)q.set('t',CW_TOKEN);return apiUrl('/api/img?'+q.toString())}
// 结果文件是图还是视频：ComfyUI 的 outputs 里 videos/gifs 才算视频，文件名再兜一层
function isVideoFile(f){
  if(!f) return false;
  if((f.kind||'')==='videos'||(f.kind||'')==='gifs') return true;
  return /\.(mp4|webm|mov|mkv|m4v|gif)$/i.test(f.filename||'');
}
function mediaThumb(f, cls, attrs){
  const url=imgUrl(f);
  if(isVideoFile(f)) return `<video class="${cls||''}" src="${url}" ${attrs||'muted loop playsinline preload="metadata"'} autoplay></video>`;
  return `<img class="${cls||''}" src="${url}" alt="">`;
}
function shareDl(id){return withToken(apiUrl('/api/share/download?id='+encodeURIComponent(id)))}
function shortWf(n){return (n||'—').replace('.json','').replace(/^[【▶▷\s]+/,'').slice(0,14)}

// ---------- 登录 ----------
let lgPoll=null;

function paintProbe(st){
  const p=(st&&st.probe)||{};
  const dot=$('lgDot'), ms=$('lgMs');
  $('lgServer').textContent = (st&&st.server) || ('连不上 ' + (CW_API || location.origin));
  if(p.online){
    dot.className='pdot ok'; ms.textContent = (p.ms||0)+' ms';
  }else{
    dot.className='pdot bad'; ms.textContent = '无法连接';
  }
}

async function fetchLoginStatus(){
  try{
    const r=await fetch('/api/login/status');
    if(!r.ok) return null;
    return await r.json();
  }catch(e){ return null; }
}

function showLogin(st){
  const v=$('loginView'); if(!v) return;
  v.classList.add('show');
  const logo=$('loginLogo');
  if(logo&&!logo.src){ const s=document.querySelector('.logo img'); if(s) logo.src=s.src; }
  paintProbe(st);
  if(location.protocol==='https:' && !CW_API && $('lgErr')){
    $('lgErr').textContent='当前是 https 页面，浏览器禁止它直连 http 后端。请直接用 http://<后端电脑IP>:8787 打开工作台。';
  }
  clearInterval(lgPoll);
  lgPoll=setInterval(async()=>{
    const s=await fetchLoginStatus();
    paintProbe(s||{probe:{online:false}});
  },3000);
}

function hideLogin(){
  const v=$('loginView'); if(v) v.classList.remove('show');
  clearInterval(lgPoll); lgPoll=null;
}

async function doLogin(){
  const u=($('lgUser').value||'').trim();
  const p=$('lgPass').value||'';
  const btn=$('lgBtn'), err=$('lgErr');
  if(!u||!p){ err.textContent='用户名和密码都要填'; return; }
  btn.disabled=true; btn.textContent='正在验证凭据…'; err.textContent='';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u,password:p})});
    const j=await r.json();
    if(!r.ok||j.error){ err.textContent=j.error||('登录失败 HTTP '+r.status); return; }
    setToken(j.token||'');
    if($('lgRemember').checked) localStorage.setItem('cw_user',u); else localStorage.removeItem('cw_user');
    hideLogin();
    toast(`已登录 · ${j.meta&&j.meta.gpu?j.meta.gpu:'ComfyUI'} · ${j.meta&&j.meta.version?('v'+j.meta.version):''}`);
    await load(); openHome();
    health(); setInterval(health,12000);
  }catch(e){
    err.textContent='请求失败：'+e.message;
  }finally{
    btn.disabled=false; btn.textContent='连接并登录';
  }
}

async function logout(){
  if(!confirm('退出登录？本机凭据仍保留在 data/auth.json，下次打开需重新登录。')) return;
  await fetch('/api/logout',{method:'POST',body:'{}'});
  setToken('');
  toast('已退出');
  showLogin(null);
}

async function boot(){
  initBrandFX();
  initCursorFX();
  const st=await fetchLoginStatus();
  if(!st || !st.authed){ showLogin(st||null); return; }
  hideLogin();
  if(st.default_user && $('lgUser')) $('lgUser').value=st.default_user;
  await load(); openHome(); health(); setInterval(health,12000); startAutoSync();
}

// ---------- 加载 ----------
async function load(){
  const r=await fetch('/api/state');
  if(r.status===401){ showLogin(null); return; }
  STATE=await r.json();
  // 恢复上次的 图像/视频 模式；视频模式下若当前流程不是视频流程就换到指定的视频流程
  applyModeUI(true);
  if(OUT_MODE==='video'){
    const vids=modeWfs().filter(w=>w.ok).map(w=>w.name);
    if(vids.length && vids.indexOf(WF_NAME)<0) WF_NAME=vids[0];
  }else if(WF_NAME && (!capsOf(WF_NAME).name || isVideoWf(WF_NAME)) && STATE.default_workflow){
    // 图像模式不列视频流程：当前若是视频流程就换回默认图像流程
    WF_NAME=STATE.default_workflow;
  }
  if(!WF_NAME) WF_NAME = localStorage.getItem('wf') || STATE.default_workflow || '';
  renderWorkflowSelect();
  onWorkflowChange(true);
  render();
  health();
}

// 视频模式优先用后端给的 video_workflows（用户指定的三个），没配就按探测到的「视频」流程兜底
function videoWfList(){
  const known=[];
  ((STATE&&STATE.video_workflows)||[]).forEach(n=>{ const c=capsOf(n); if(c && c.ok!==false) known.push(n); });
  if(known.length) return known;
  return ((STATE&&STATE.workflows)||[]).filter(w=>w.ok && (w.kind==='视频'||w.video)).map(w=>w.name);
}
function isVideoWf(n){ return videoWfList().indexOf(n)>=0; }
function modeWfs(){
  const all=(STATE&&STATE.workflows)||[];
  const names=videoWfList();
  if(OUT_MODE==='video') return all.filter(w=>names.indexOf(w.name)>=0);
  // 图像模式：视频类流程一律不列（避免误选）
  return all.filter(w=>names.indexOf(w.name)<0 && !(w.kind==='视频'));
}

function renderWorkflowSelect(){
  const sel=$('wf'); if(!sel) return;
  const list=modeWfs();
  const html = list.map(w=>{
    const dis = w.ok? '' : ' disabled';
    return `<option value="${w.name}"${dis}>${w.label}${w.ok?'':'（不可用）'}</option>`;
  }).join('');
  ['wf','homeWf','drawerWf','gcWf'].forEach(id=>{
    const el=$(id); if(!el) return;
    el.innerHTML = html;
    if([...el.options].some(o=>o.value===WF_NAME)) el.value=WF_NAME;
  });
  if(![...sel.options].some(o=>o.value===WF_NAME) && sel.options.length){
    WF_NAME=sel.options[0].value;
    ['wf','homeWf','drawerWf','gcWf'].forEach(id=>{ const el=$(id); if(el) el.value=WF_NAME; });
  }
  const okN=STATE.workflows.filter(w=>w.ok).length;
  $('wfCount').textContent = `${STATE.workflows.length} 个工作流可用 ${okN}` + (OUT_MODE==='video'?` · 视频 ${list.length}`:'');
  const badge=$('drawerWfBadge'); if(badge) badge.textContent=`${STATE.workflows.length} 个，可用 ${okN}`;
  const hc=$('heroWfCount'); if(hc) hc.textContent=`${okN} 个可用`;
}

// 任意入口切换工作流（首页卡片 / 生成抽屉 / 顶部下拉）
function setWfFrom(name){
  if(!name) return;
  const sel=$('wf');
  if(sel && ![...sel.options].some(o=>o.value===name)){ toast('该工作流不存在或未同步',true); return; }
  WF_NAME=name;
  if(sel) sel.value=name;
  onWorkflowChange();
}

function capsOf(name){return STATE.workflows.find(w=>w.name===name)||{}}

/* ---------- 视频时长（秒） ---------- */
// 档位由 MiniMax H3 决定（后端 video_duration 配置，默认 5/10/15 秒）
function durCfg(){
  const cfg=(STATE&&STATE.video_duration)||{};
  const presets=(cfg.presets&&cfg.presets.length)?cfg.presets.slice():[5,10,15];
  return {min:(cfg.min!=null?+cfg.min:5), max:(cfg.max!=null?+cfg.max:15), presets};
}
let curDuration=null, durForWf='';
function syncDurUI(){
  const c=capsOf(WF_NAME);
  const vid=(OUT_MODE==='video');
  const supported = vid && !!c.can_duration;
  ['homeDurWrap','gcDurWrap'].forEach(id=>{ const el=$(id); if(el) el.style.display = vid?'inline-flex':'none'; });
  ['homeDur','gcDur'].forEach(id=>{ const el=$(id); if(el) el.style.display = supported?'':'none'; });
  ['homeDurNote','gcDurNote'].forEach(id=>{ const el=$(id); if(el) el.style.display = (vid&&!supported)?'':'none'; });
  if(!supported) return;
  if(durForWf!==WF_NAME){                       // 换流程就回到它自己的工作流默认秒数
    const dv=(c.duration_default!=null)? +c.duration_default : durCfg().presets[0];
    curDuration=dv; durForWf=WF_NAME;
  }
  fillDurOptions();
  ['homeDur','gcDur'].forEach(id=>{ const el=$(id); if(el) el.value=String(curDuration); });
}
function fillDurOptions(){
  const cfg=durCfg();
  const html=cfg.presets.map(s=>`<option value="${s}">${(+s).toFixed(0)} 秒</option>`).join('');
  ['homeDur','gcDur'].forEach(id=>{
    const el=$(id); if(!el) return;
    if(el.innerHTML!==html) el.innerHTML=html;
    const c=capsOf(WF_NAME);
    el.title=`可选 ${cfg.presets.map(s=>(+s).toFixed(0)).join(' / ')} 秒（${Math.round(cfg.min)}–${Math.round(cfg.max)} 秒，受 MiniMax H3 限制）`;
  });
}
function setDurationFrom(v){
  const cfg=durCfg();
  let s=+v;
  if(!s) return;
  s=Math.max(cfg.min, Math.min(cfg.max, s));
  curDuration=s;
  ['homeDur','gcDur'].forEach(id=>{ const el=$(id); if(el) el.value=String(s); });
  toast(`视频时长 ${s.toFixed(0)} 秒`);
}

/* ---------- 图像 / 视频 模式 ---------- */
function applyModeUI(light){
  document.body.classList.toggle('vmode', OUT_MODE==='video');
  ['homeMode','gcMode'].forEach(id=>{
    const b=$(id); if(!b) return;
    [...b.querySelectorAll('.ms')].forEach(x=>x.classList.toggle('on', x.dataset.m===OUT_MODE));
  });
  const g=$('homeGen'); if(g) g.textContent = OUT_MODE==='video' ? '🎬 生成视频' : '✦ 生成';
  const g2=$('gcGen'); if(g2) g2.textContent = OUT_MODE==='video' ? '🎬 生成视频' : '✦ 生成';
  const sub=$('homeBrandSub');
  if(sub && OUT_MODE==='video') sub.textContent = sub.textContent.replace(' · 视频模式','') + ' · 视频模式';
  if(sub && OUT_MODE!=='video') sub.textContent = sub.textContent.replace(' · 视频模式','');
  const tip=$('homeTip');
  if(tip) tip.textContent = OUT_MODE==='video'
    ? '视频模式 · Enter 提交 · 画幅与时长由所选视频流程决定'
    : '按 Enter 直接提交 · Shift+Enter 换行';
  if(!light) renderWorkflowSelect();
  renderHomeWfChips();
  updateHomeRef();
  syncDurUI();
  syncSizeUI();
}
function setOutMode(m){
  m = (m==='video') ? 'video' : 'image';
  if(OUT_MODE===m){ applyModeUI(); return; }
  OUT_MODE=m;
  try{ localStorage.setItem('cw_out_mode', m); }catch(e){}
  const vids=modeWfs().filter(w=>w.ok).map(w=>w.name);
  if(m==='video' && vids.length && vids.indexOf(WF_NAME)<0){ WF_NAME=vids[0]; }
  // 回图像模式时，如果当前是视频流程就换回默认图像流程
  if(m!=='video' && isVideoWf(WF_NAME)){
    const imgs=modeWfs().filter(w=>w.ok).map(w=>w.name);
    const def=STATE.default_workflow;
    WF_NAME = (def && imgs.indexOf(def)>=0) ? def : (imgs[0]||'');
  }
  // 灵感广场跟着切筛选：视频模式只看视频作品
  homeKind = (m==='video') ? '视频' : '全部';
  [...document.querySelectorAll('.filters .chip')].forEach(c=>{
    if(c.dataset && c.dataset.k) c.classList.toggle('on', c.dataset.k===homeKind);
  });
  applyModeUI();
  if(m==='video' && needsImage(WF_NAME) && !refImages.length){
    toast('这个视频流程要参考图，点「参考图」加上首帧',true);
  }else{
    toast(m==='video'?'已切到视频模式 · 只列可用的视频流程':'已切回图像模式');
  }
  if(WF_NAME) onWorkflowChange(true);
}

function onWorkflowChange(silent){
  WF_NAME = $('wf').value || WF_NAME;
  try{ localStorage.setItem('wf', WF_NAME); }catch(e){}
  const c = capsOf(WF_NAME);
  $('wfKind').textContent = c.kind || '—';
  $('caps').innerHTML = [
    ['提示词', c.has_prompt],
    ['参考图', c.can_image],
    ['尺寸', c.size_mode!=='none'],
    ['种子', c.can_seed],
    ['LoRA', c.can_lora]
  ].map(([k,on])=>`<span class="cap ${on?'on':'off'}">${k}</span>`).join('');

  // 按能力禁用控件
  const imgOn = !!c.can_image;
  $('drop').classList.toggle('disabled', !imgOn);
  $('fileInput').disabled = !imgOn;
  if(!imgOn){ $('dropName').textContent='该工作流没有图片输入'; $('dropHint').textContent='纯文生图 / 视频流程'; }
  $('ratioField').style.display = c.size_mode==='none' ? 'none' : '';
  $('mpField').style.display = c.size_mode==='none' ? 'none' : '';
  $('seed').disabled = !c.can_seed;
  $('loraField').style.display = c.can_lora ? '' : 'none';
  ['homeWf','drawerWf'].forEach(id=>{ const el=$(id); if(el && el.value!==WF_NAME) el.value=WF_NAME; });
  if(typeof syncHomeWfUI==='function') syncHomeWfUI();
  if(typeof syncDurUI==='function') syncDurUI();
  if(typeof syncSizeUI==='function') syncSizeUI();
  if(!silent) toast('已切到 · '+(c.label||WF_NAME));
}

// ---------- 渲染 ----------
function itemState(it){
  if(it.status==='done') return 'done';
  if(it.status==='error') return 'error';
  const j=STATE.jobs[it.prompt_id]; return (j&&j.status)||'queued';
}
let viewMode = localStorage.getItem('viewMode') || 'grid';
let homeKind = '全部';

function dateLabel(ts){
  const d=new Date((ts||0)*1000), now=new Date();
  const k=x=>`${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  if(k(d)===k(now)) return '今天';
  if(k(d)===k(new Date(now.getTime()-86400000))) return '昨天';
  return `${d.getMonth()+1} 月 ${d.getDate()} 日`;
}
function groupByDate(items){
  const order=[], map=new Map();
  items.forEach(it=>{
    const g=dateLabel(it.created);
    if(!map.has(g)){ map.set(g,[]); order.push(g); }
    map.get(g).push(it);
  });
  return order.map(g=>({label:g, items:map.get(g)}));
}

function cardHtml(it){
  const st=itemState(it), file=(it.files||[])[0];
  let media;
  const vid = st==='done'&&file&&isVideoFile(file);
  if(st==='done'&&file) media=vid
    ? `${mediaThumb(file,'thumb')}<div class="vtag">🎬 视频</div>`
    : mediaThumb(file,'thumb');
  else if(st==='error') media=`<div class="skeleton" style="aspect-ratio:4/3;display:grid;place-items:center;animation:none;color:#ff9c85">生成失败<br><span style="font-size:11px;color:#77777f">点开看原因</span></div>`;
  else media=`<div class="skeleton" style="aspect-ratio:4/3;display:grid;place-items:center;color:#8f8f98;font-size:12px">排队 / 生成中…</div>`;
  const badge = st==='done'? '' : `<div class="state ${st==='error'?'error':'running'}">${st==='error'?'失败':(st==='running'?'生成中':'排队中')}</div>`;
  return `<div class="card" onclick="openViewer('${it.prompt_id}')">
    <div class="imgwrap">
      ${media}
      <div class="mtag" title="${escapeHtml(it.workflow||'')}">${escapeHtml(shortWf(it.workflow))}</div>
      ${badge}
    </div>
    <div class="pbody">
      <div class="ptext">${escapeHtml(it.prompt||'(无提示词)')}</div>
      <div class="acts">
        <span class="act-same" onclick="event.stopPropagation();actSame('${it.prompt_id}')">✦ 做同款</span>
        <span class="act-ico" title="复制提示词" onclick="event.stopPropagation();copyPromptOf('${it.prompt_id}')">⧉</span>
        <span class="act-ico" title="下载" onclick="event.stopPropagation();downloadOf('${it.prompt_id}')">↓</span>
        <span class="act-more" onclick="event.stopPropagation();openViewer('${it.prompt_id}')">详情 ›</span>
      </div>
      <div class="meta" style="padding:9px 0 0">
        ${it.aspect?`<span class="tag">${ratioShort(it.aspect)}</span>`:''}
        ${it.megapixels?`<span class="tag">${(+it.megapixels).toFixed(1)}MP</span>`:''}
        ${it.origin?`<span class="tag" title="发起生成的设备">${escapeHtml(it.origin)}</span>`:''}
        <span class="grow"></span><span>${fmtTime(it.created)}</span>
      </div>
    </div></div>`;
}

function findItem(id){ return STATE.items.find(x=>x.prompt_id===id); }
function copyPromptOf(id){
  const it=findItem(id); if(!it) return;
  const t=it.prompt||'';
  if(navigator.clipboard) navigator.clipboard.writeText(t).then(()=>toast('提示词已复制'),()=>toast('复制失败，请手动选中',true));
  else toast('当前浏览器不支持自动复制',true);
}
function downloadOf(id){
  const it=findItem(id); const f=((it&&it.files)||[])[0]; if(!f) return toast('这张还没有图',true);
  const a=document.createElement('a'); a.href=imgUrl(f); a.download=f.filename||'image.png'; a.click();
}
async function actSame(id){
  if(!findItem(id)) return;
  toast('正在用同参数重新生成…');
  const j=await (await fetch('/api/rerun',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prompt_id:id, seed:null})})).json();
  if(j.error) return toast('失败：'+j.error,true);
  await load(); openGenView(j.prompt_id);
}

function rowHtml(it){
  const st=itemState(it), file=(it.files||[])[0];
  const thumb=(st==='done'&&file)
    ? `<div class="thumbbox"><img src="${imgUrl(file)}"  alt=""></div>`
    : `<div class="thumbbox">${st==='error'?'失败':'…'}</div>`;
  const t=(it.created||0)*1000;
  const full=new Date(t).toLocaleString('zh-CN',{hour12:false});
  return `<div class="li" onclick="openViewer('${it.prompt_id}')">
    ${thumb}
    <div class="txt">
      <div class="t1">${escapeHtml(it.prompt||'(无提示词)')}</div>
      <div class="t2">${escapeHtml(shortWf(it.workflow))} · ${full}${it.seed?` · seed ${it.seed}`:''}</div>
    </div>
    <div class="rt">
      ${it.aspect?`<span class="tag">${ratioShort(it.aspect)}</span>`:''}
      ${it.megapixels?`<span class="tag">${(+it.megapixels).toFixed(1)}MP</span>`:''}
      <span class="tag">${st==='done'?'已完成':(st==='error'?'失败':'进行中')}</span>
    </div></div>`;
}

function render(){
  const kw=($('q').value||'').trim().toLowerCase();
  const items=STATE.items.filter(it=>{
    const st=itemState(it);
    if(filter==='running'&&!(st==='queued'||st==='running'))return false;
    if(filter==='done'&&st!=='done')return false;
    if(ratioFilter!=='all'&&!(it.aspect||'').startsWith(ratioFilter))return false;
    if(kw&&!(it.prompt||'').toLowerCase().includes(kw))return false;
    return true;
  });
  $('empty').style.display=items.length?'none':'block';
  const groups=groupByDate(items);
  $('grid').innerHTML = groups.map(g=>{
    const head=`<div class="group-head"><b>${g.label}</b><span>${g.items.length} ${viewMode==='grid'?'张':'条'}</span><span class="ln"></span></div>`;
    return head + (viewMode==='grid'
      ? `<div class="grid">${g.items.map(cardHtml).join('')}</div>`
      : `<div class="list-wrap">${g.items.map(rowHtml).join('')}</div>`);
  }).join('');
  pollManage();
  if($('homeView').classList.contains('show')) renderHome();
}

function pollManage(){
  const busy=STATE.items.some(it=>{const s=itemState(it);return s==='queued'||s==='running';});
  if(busy&&!pollTimer) pollTimer=setInterval(load,2500);
  if(!busy&&pollTimer){clearInterval(pollTimer);pollTimer=null;}
}

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setFilter(el){document.querySelectorAll('[data-f]').forEach(c=>c.classList.remove('on'));el.classList.add('on');filter=el.dataset.f;render()}
function setRatio(el){document.querySelectorAll('[data-r]').forEach(c=>c.classList.remove('on'));el.classList.add('on');ratioFilter=el.dataset.r;render()}

function setView(el){
  document.querySelectorAll('[data-v]').forEach(c=>c.classList.remove('on'));
  el.classList.add('on');
  viewMode = el.dataset.v;
  localStorage.setItem('viewMode', viewMode);
  render();
}
function toggleView(){
  viewMode = viewMode==='grid' ? 'list' : 'grid';
  localStorage.setItem('viewMode', viewMode);
  applyViewMode();
  render();
}
function applyViewMode(){
  document.querySelectorAll('[data-v]').forEach(c=>c.classList.toggle('on', c.dataset.v===viewMode));
  const t=$('viewToggle');
  if(t){
    t.textContent = viewMode==='grid' ? '▦ 图像' : '☰ 列表';
    t.classList.add('on');
  }
}

// ---------- 首页 / 导航 ----------
function updateQuickEst(){
  const mp=($('mp')&&$('mp').value)||'1';
  const txt=mpLabelText(mp);
  if($('homeMpText')) $('homeMpText').textContent = txt;
  if($('gcMpText')) $('gcMpText').textContent = txt;
}
function quickGen(){
  const t=(($('homePrompt')&&$('homePrompt').value)||'').trim();
  if(t) homeGenerate(); else { const p=$('homePrompt'); if(p) p.focus(); }
}

function setNav(id){
  ['navHome','navLib','navShare','navGen','navFlow','navDocs'].forEach(k=>{ const e=$(k); if(e) e.classList.remove('active'); });
  const e=$(id); if(e) e.classList.add('active');
}

/* ---------- 文档库 ---------- */
let docList=[], docCur=null, docSheetIdx=0, docQuery='', docRowsView=[];
const DOC_ICON={'.xlsx':'▦','.xlsm':'▦','.csv':'▦','.tsv':'▦','.docx':'▤','.pdf':'▣','.md':'✎','.txt':'✎','.json':'{}'};
const docIcon=e=>DOC_ICON[(e||'').toLowerCase()]||'▢';
function docSize(n){
  n=n||0;
  if(n>=1048576) return (n/1048576).toFixed(1)+' MB';
  return Math.max(1,Math.round(n/1024))+' KB';
}

async function openDocs(){
  if(typeof setSplit==='function') setSplit(false);
  ['homeView','genView','shareView','flowView'].forEach(id=>{ const e=$(id); if(e) e.classList.remove('show'); });
  if(genTimer){ clearInterval(genTimer); genTimer=null; }
  if(typeof shareTimer!=='undefined' && shareTimer){ clearInterval(shareTimer); shareTimer=null; }
  if($('composer')) $('composer').style.display='none';
  $('docsView').classList.add('show');
  setNav('navDocs');
  bindDocDrop();
  await loadDocs();
  animView($('docsView'));
}
function closeDocs(){ $('docsView').classList.remove('show'); showLibrary(); }

let docDropBound=false;
function bindDocDrop(){
  if(docDropBound) return; docDropBound=true;
  const v=$('docsView'), hint=$('docDrop');
  const hasFiles=e=>e.dataTransfer && [...(e.dataTransfer.types||[])].includes('Files');
  v.addEventListener('dragenter',e=>{ if(hasFiles(e)){ e.preventDefault(); hint.classList.add('over'); } });
  v.addEventListener('dragover',e=>{ if(hasFiles(e)){ e.preventDefault(); hint.classList.add('over'); } });
  v.addEventListener('dragleave',e=>{ if(!e.relatedTarget||!v.contains(e.relatedTarget)) hint.classList.remove('over'); });
  v.addEventListener('drop',e=>{ e.preventDefault(); hint.classList.remove('over'); uploadDocs(e.dataTransfer.files); });
}

/* 多台电脑共用一套记录：每 8 秒静默拉一次，只在内容变化时重绘 */
let autoSyncTimer=null, syncSig='';
function syncSignature(st){
  const items=(st.items||[]).slice(0,24).map(i=>i.prompt_id+':'+(i.status||''));
  return items.join('|');
}
function startAutoSync(){
  if(autoSyncTimer) return;
  autoSyncTimer=setInterval(async()=>{
    if(document.hidden) return;
    if($('loginView') && $('loginView').classList.contains('show')) return;
    if($('docsView') && $('docsView').classList.contains('show')) return;
    if($('docModal') && $('docModal').classList.contains('show')) return;
    try{
      const r=await fetch('/api/state');
      if(!r.ok) return;
      const st=await r.json();
      const sig=syncSignature(st);
      if(sig!==syncSig){
        syncSig=sig;
        STATE=st;
        renderHome();
        render();
        if($('genView').classList.contains('show') && !genTimer) renderThread(false);
      }
    }catch(e){}
  },8000);
}

async function loadDocs(){
  try{
    const r=await fetch('/api/docs');
    if(r.status===401){ showLogin(null); return; }
    const j=await r.json();
    docList=j.items||[];
  }catch(e){ docList=[]; }
  renderDocs();
}

function renderDocs(){
  const g=$('docGrid'); if(!g) return;
  if($('docCount')) $('docCount').textContent=`${docList.length} 份文档`;
  $('docEmpty').style.display=docList.length?'none':'block';
  g.innerHTML=docList.map(d=>`<div class="dcard" onclick="openDoc('${d.id}')">
      <div class="dt">
        <div class="dic">${docIcon(d.ext)}</div>
        <div class="dn" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</div>
      </div>
      <div class="dmeta">
        <span>${escapeHtml((d.ext||'').replace('.',''))||'文件'}</span>
        <span>${d.rows?d.rows+' 行':'未解析'}</span>
        <span>${docSize(d.size)}</span>
        <span>${fmtTime(d.created)}</span>
      </div>
      <div class="dprev">${escapeHtml(d.error? ('⚠ '+d.error) : ((d.sheets||[]).map(s=>s.name+' '+s.rows+' 行').join(' · ')||'点开查看正文'))}</div>
      <div class="dacts">
        <button class="btn" onclick="event.stopPropagation();openDoc('${d.id}')">打开</button>
        <button class="btn" onclick="event.stopPropagation();docDownload('${d.id}')">下载</button>
        <button class="btn" onclick="event.stopPropagation();docDel('${d.id}')">删除</button>
      </div>
    </div>`).join('');
}

async function uploadDocs(files){
  const list=[...files]; if(!list.length) return;
  if(!$('docDrop')) return;
  toast('正在解析 '+list.length+' 份文档…');
  let ok=0;
  for(const f of list){
    try{
      const r=await fetch('/api/doc/upload?name='+encodeURIComponent(f.name),
        {method:'POST',body:f,headers:{'Content-Type':'application/octet-stream'}});
      const j=await r.json();
      if(j.error) throw new Error(j.error);
      ok++;
      toast(`${f.name}：解析 ${j.item.rows||0} 行`);
    }catch(e){ toast(`${f.name} 上传失败：${e.message}`,true); }
  }
  if(ok) await loadDocs();
}

async function docDel(id){
  const d=docList.find(x=>x.id===id);
  if(!d) return;
  if(!confirm(`删除「${d.name}」？`)) return;
  try{
    await fetch('/api/doc/delete',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id})});
    toast('已删除');
    if(docCur&&docCur.id===id) closeDoc();
    await loadDocs();
  }catch(e){ toast('删除失败：'+e.message,true); }
}

async function openDoc(id){
  try{
    const r=await fetch('/api/doc?id='+encodeURIComponent(id));
    if(!r.ok) throw new Error('HTTP '+r.status);
    docCur=await r.json();
  }catch(e){ return toast('打开失败：'+e.message,true); }
  docSheetIdx=0; docQuery='';
  $('docTitle').textContent=docCur.name;
  $('docMeta').textContent=(docCur.sheets||[]).map(s=>s.name+' '+s.rows.length+' 行').join(' · ');
  $('docModal').classList.add('show');
  renderDocBody();
}
function closeDoc(){ $('docModal').classList.remove('show'); docCur=null; }

function docRows(){
  const sh=(docCur&&docCur.sheets&&docCur.sheets[docSheetIdx])||{rows:[]};
  if(!docQuery) return sh.rows;
  const kw=docQuery.toLowerCase();
  return sh.rows.filter(r=>r.join(' ').toLowerCase().includes(kw));
}
function rowPrompt(r){
  // 一行里最长的那格通常就是提示词/正文
  let best='';
  for(const c of r) if((c||'').length>(best||'').length) best=c;
  return best||'';
}
function renderDocBody(){
  const b=$('docBody'); if(!b||!docCur) return;
  const sheets=docCur.sheets||[];
  const tabs=sheets.length>1?`<div class="doc-tabs">${sheets.map((s,i)=>
      `<button class="chip ${i===docSheetIdx?'on':''}" onclick="docSheetIdx=${i};docQuery='';renderDocBody()">${escapeHtml(s.name)} (${s.rows.length})</button>`).join('')}</div>`:'';
  const rows=docRows();
  docRowsView=rows;
  const list=rows.slice(0,300).map((r,idx)=>{
    const cells=r.map(c=>escapeHtml(c||'')).join(' <span style="color:#3c3c46">｜</span> ');
    return `<div class="doc-row" id="drow${idx}">
      <div class="rtxt">${cells||'<span style="color:#4c4c56">（空行）</span>'}</div>
      <div class="racts">
        <button class="btn" onclick="docUseAt(${idx})">填入</button>
        <button class="btn" onclick="docCopyAt(${idx})">复制</button>
        <button class="btn" onclick="document.getElementById('drow${idx}').classList.toggle('open')">展开</button>
      </div>
    </div>`;
  }).join('');
  b.innerHTML = tabs
    + `<div class="doc-search"><input id="docFind" placeholder="在 ${rows.length} 行里搜关键词…" value="${escapeHtml(docQuery)}"><span style="font-size:11px;color:var(--dim)">最多显示 300 行</span></div>`
    + (list || `<div class="empty" style="display:block"><b>没有匹配的行</b>换个关键词试试</div>`);
  const inp=$('docFind');
  if(inp){
    inp.addEventListener('input',e=>{
      docQuery=e.target.value.trim();
      renderDocBody();
      const again=$('docFind'); if(again){ again.focus(); again.setSelectionRange(again.value.length,again.value.length); }
    });
  }
}
function docUseAt(idx){
  const r=docRowsView[idx]; if(!r) return;
  const text=rowPrompt(r);
  if(!text) return toast('这一行没有可用的文字',true);
  $('homePrompt').value=text;
  closeDoc();
  $('docsView').classList.remove('show');
  openHome();
  $('homePrompt').focus();
  toast('已填进对话框，改完直接生成');
}
function docCopyAt(idx){
  const r=docRowsView[idx]; if(!r) return;
  docCopyOf(rowPrompt(r));
}
function docCopyOf(text){
  if(!text) return;
  if(navigator.clipboard) navigator.clipboard.writeText(text).then(()=>toast('已复制'),()=>toast('复制失败',true));
  else toast('当前浏览器不支持自动复制',true);
}
function docDownload(id){
  if(!id && docCur) id=docCur.id;
  if(!id) return;
  const a=document.createElement('a');
  a.href=withToken(apiUrl('/api/doc/raw?id='+encodeURIComponent(id)));
  a.click();
}

/* ---------- 视图切换缓动 + 光标动效 ---------- */
function animView(el){
  if(!el) return;
  el.classList.remove('view-anim');
  void el.offsetWidth;
  el.classList.add('view-anim');
  el.addEventListener('animationend',()=>el.classList.remove('view-anim'),{once:true});
}

/* 自定义光标（蓝色圆环 + 白点）已下线，恢复系统默认光标 */
function initCursorFX(){ window.__curFx=1; }

/* ---------- 工作流控制页（内嵌原版 ComfyUI） ---------- */
function comfyBase(){ return (STATE && STATE.server) ? STATE.server.replace(/\/$/,'') : 'http://127.0.0.1:8188'; }
function comfyProxy(p){ return (CW_API||'')+p; }
function showFlow(){
  setSplit(false);
  $('docsView').classList.remove('show');
  $('genView').classList.remove('show');
  if(genTimer){ clearInterval(genTimer); genTimer=null; }
  $('homeView').classList.remove('show');
  $('shareView').classList.remove('show');
  $('flowView').classList.add('show');
  setNav('navFlow');
  const f=$('flowFrame');
  if(f && (!f.getAttribute('src') || f.getAttribute('src')==='about:blank')) f.src=comfyProxy('/comfy/');
  animView($('flowView'));
}
function reloadFlow(){
  const f=$('flowFrame'); if(!f) return;
  f.src='about:blank';
  setTimeout(()=>{ f.src=comfyProxy('/comfy/')+'?r='+Date.now(); }, 80);
}
function openFlowTab(){
  if(CW_API) return window.open(comfyProxy('/comfy/'), '_blank');
  window.open(comfyBase()+'/', '_blank');
}
function openHome(){
  if(typeof setSplit==='function') setSplit(false);
  $('docsView').classList.remove('show');
  $('flowView') && $('flowView').classList.remove('show');
  $('genView').classList.remove('show');
  if(genTimer){clearInterval(genTimer);genTimer=null;}
  $('homeView').classList.add('show');
  $('shareView').classList.remove('show');
  if(shareTimer){clearInterval(shareTimer);shareTimer=null;}
  if($('composer')) $('composer').style.display='';
  setNav('navHome');
  renderHome();
  animView($('homeView'));
}
function showLibrary(){
  if(typeof setSplit==='function') setSplit(false);
  $('docsView').classList.remove('show');
  $('flowView') && $('flowView').classList.remove('show');
  $('genView').classList.remove('show');
  if(genTimer){clearInterval(genTimer);genTimer=null;}
  $('homeView').classList.remove('show');
  $('shareView').classList.remove('show');
  if(shareTimer){clearInterval(shareTimer);shareTimer=null;}
  if($('composer')) $('composer').style.display='';
  setNav('navLib');
  render();
  animView(document.querySelector('main.main'));
}
function setHomeKind(el){
  document.querySelectorAll('[data-k]').forEach(c=>c.classList.remove('on'));
  el.classList.add('on');
  homeKind = el.dataset.k;
  renderHome();
}
function kindOf(wf){
  const c=(typeof capsOf==='function')?(capsOf(wf||'')):null;
  return (c&&c.kind)||'';
}
// ---------- 首页 logo 光标交互：光标位置高亮（聚光）+ 点击脉冲 ----------
function initBrandFX(){
  const wrap=document.querySelector('.brand-big'), base=$('homeBrand'), hero=document.querySelector('.home-hero');
  if(!wrap||!base||!hero||wrap.dataset.fx) return;
  wrap.dataset.fx='1';
  let lit=wrap.querySelector('.brand-lit');
  if(!lit){
    lit=document.createElement('img');
    lit.className='brand-lit'; lit.alt='';
    wrap.appendChild(lit);
    lit.addEventListener('error',()=>{ lit.style.visibility='hidden'; });
  }
  const sync=()=>{ if(base.src && lit.src!==base.src) lit.src=base.src; };
  base.addEventListener('load',sync);
  sync();
  [0,200,600].forEach(ms=>setTimeout(sync,ms));

  hero.addEventListener('mousemove',e=>{
    const r=wrap.getBoundingClientRect();
    wrap.style.setProperty('--mx',(e.clientX-r.left)+'px');
    wrap.style.setProperty('--my',(e.clientY-r.top)+'px');
    wrap.classList.add('near');
  });
  hero.addEventListener('mouseleave',()=>{ wrap.classList.remove('near'); });

  wrap.addEventListener('click',()=>{
    wrap.classList.remove('pulse'); void wrap.offsetWidth; wrap.classList.add('pulse');
    setTimeout(()=>wrap.classList.remove('pulse'),600);
    const p=$('homePrompt'); if(p) p.focus();
  });
}

function renderHomeWfChips(){
  const chips=$('homeWfChips'); if(!chips) return;
  const list=modeWfs();
  const sig=(OUT_MODE==='video'?'V:':'I:')+list.map(w=>w.name+(w.ok?'1':'0')).join('|');
  if(chips.dataset.sig===sig){
    [...chips.children].forEach(c=>c.classList.toggle('on', c.dataset && c.dataset.n===WF_NAME));
    return;
  }
  chips.dataset.sig=sig;
  if(!list.length){
    chips.innerHTML=`<span style="font-size:12px;color:#7c7c85">这个模式下还没有可用流程</span>`;
    return;
  }
  chips.innerHTML = list.map(w=>{
    const need = w.can_image && w.has_prompt===false;   // 必须给参考图的流程
    const label = (w.label||w.name).replace('.json','').replace(/^[【▶▷\s]+/,'');
    const ic = (w.kind==='视频') ? '🎬 ' : '';
    return `<button class="wf-chip ${w.name===WF_NAME?'on':''}${w.ok?'':' dis'}${need?' needimg':''}"
    data-n="${escapeHtml(w.name)}" title="${escapeHtml((w.label||w.name)+(need?'（必须给参考图）':''))}"
    ${w.ok?`onclick="setWfFrom(this.dataset.n)"`:'disabled'}>${ic}${escapeHtml(label)}${need?' ·需图':''}</button>`;
  }).join('');
}

function renderHome(){
  const items0=STATE.items||[], wfs=STATE.workflows||[];
  const okN=wfs.filter(w=>w.ok).length;
  const brand=$('homeBrand');
  if(brand && !brand.src){
    const s=document.querySelector('.logo img');
    if(s) brand.src=s.src;
    const lit=document.querySelector('.brand-big .brand-lit');
    if(s && lit && lit.src!==s.src) lit.src=s.src;
  }
  if($('homeBrandSub')){
    $('homeBrandSub').textContent = `COMFYUI 工作台 · ${items0.length} 件作品 · ${okN}/${wfs.length} 个工作流可用`;
  }
  renderHomeWfChips();
  syncHomeWfUI();

  let items=items0.filter(it=>itemState(it)==='done');
  if(homeKind!=='全部') items=items.filter(it=>kindOf(it.workflow)===homeKind);
  const total=items.length;
  items=items.slice(0,12);
  if($('homeCount')) $('homeCount').textContent = `最近 ${items.length} 件 / 共 ${total} 件`;
  $('homeGrid').innerHTML = items.map(cardHtml).join('');
  $('homeEmpty').style.display = items.length?'none':'block';
}

function syncHomeWfUI(){
  const sel=$('homeWf');
  if(sel && sel.value!==WF_NAME && [...sel.options].some(o=>o.value===WF_NAME)) sel.value=WF_NAME;
  const chips=$('homeWfChips');
  if(chips) [...chips.children].forEach(c=>c.classList.toggle('on', c.dataset.n===WF_NAME));
  updateQuickEst();
  updateHomeRef();
  if(typeof syncGenUI==='function') syncGenUI();
}

/* 像素档位：MP_STEPS 14 档（0.2–2.0），输出像素按 multiple=32 反算，标签同时显示 MP 与实际像素 */
function arOf(aspect){
  const s=ratioShort(aspect||curRatio)||'16:9';
  const [a,b]=s.split(':').map(Number);
  return (a&&b)? a/b : 16/9;
}
function mpToPx(aspect, mp){
  const r=arOf(aspect), step=32;
  let w=Math.max(step, Math.round(Math.sqrt(mp*1e6*r)/step)*step);
  let h=Math.max(step, Math.round((w/r)/step)*step);
  // 面积没到目标就往上补一档，避免低于标称像素
  for(let i=0;i<8 && w*h < mp*1e6*0.995;i++){
    w+=step; h=Math.max(step, Math.round((w/r)/step)*step);
  }
  return [w,h];
}
function mpNum(mp){
  const v=Math.round((+mp||1)*100)/100;
  return (v%1===0) ? v.toFixed(1) : String(v);
}
function mpLabelText(mp){
  const [w,h]=mpToPx(curRatio, +mp||1);
  return `${mpNum(mp)} MP · ${w} × ${h}`;
}
function nearestMpIdx(v){
  const mp=+v||1;
  const idx=MP_STEPS.indexOf(mp);
  if(idx>=0) return idx;
  return MP_STEPS.reduce((best,x,i)=>Math.abs(x-mp)<Math.abs(MP_STEPS[best]-mp)?i:best,0);
}
function syncMpEverywhere(){
  const mpVal = +(($('mp')&&$('mp').value)||1);
  const idx = nearestMpIdx(mpVal);
  ['homeMp','gcMp'].forEach(id=>{ const el=$(id); if(el && +el.value!==idx) el.value=idx; });
  ['homeMpText','gcMpText'].forEach(id=>{ const el=$(id); if(el) el.textContent=mpLabelText(mpVal); });
  if($('mpLabel')) $('mpLabel').textContent=mpLabelText(mpVal);
  updateQuickEst();
  if(typeof syncSizeUI==='function') syncSizeUI();
}
function onHomeMp(idx){
  const v = MP_STEPS[+idx] ?? 1;
  $('mp').value = v;
  ['homeMp','gcMp'].forEach(id=>{ const el=$(id); if(el) el.value=+idx; });
  syncMpEverywhere();
}
function cycleMp(){
  const cur=+($('mp').value||1);
  const next=MP_STEPS[(nearestMpIdx(cur)+1)%MP_STEPS.length] ?? MP_STEPS[9];
  $('mp').value=next; syncMpEverywhere();
  toast(`像素 ${mpLabelText(next)}`);
}
function setRatioFrom(v){
  if(!v) return;
  curRatio=v;
  fillRatioSelects();
  syncMpEverywhere();
  updateQuickEst();
}
function cycleRatio(){
  const idx=RATIOS.findIndex(r=>r[0]===curRatio);
  setRatioFrom(RATIOS[(idx+1)%RATIOS.length][0]);
  toast('画幅 '+ratioShort(curRatio));
}

/* 当前流程是否支持调节画幅 / 像素；不支持就在控件下面给提示，并把控件置灰 */
function sizeSupported(){ const c=capsOf(WF_NAME); return (c&&c.size_mode&&c.size_mode!=='none'); }
function syncSizeUI(){
  if(!WF_NAME){ return; }
  const ok = sizeSupported();
  const msg = ok ? '' : `「${shortWf(WF_NAME)}」这张工作流的尺寸是固定的，画幅比例和像素大小都不支持调节`;
  ['homeRatioWrap','gcRatioWrap'].forEach(id=>{ const el=$(id); if(el) el.classList.toggle('off', !ok); });
  ['homeMp','gcMp'].forEach(id=>{ const el=$(id); if(el) el.disabled=!ok; });
  ['homeMpText','gcMpText'].forEach(id=>{ const el=$(id); if(el) el.style.opacity = ok?'':'0.45'; });
  ['homeSizeHint','gcSizeHint'].forEach(id=>{
    const el=$(id); if(!el) return;
    el.textContent = msg;
    el.style.display = ok ? 'none' : 'block';
  });
}
function homePickRef(){
  const c=capsOf(WF_NAME);
  if(!c.can_image) toast('这个流程没有图像输入位，参考图会作为「加载图像」节点挂进工作流；先在 ComfyUI 里把节点连到对应输入才起作用',true);
  $('homeFileInput').click();
}

/* ---------- 参考图（多张） ---------- */
// 第 i 张参考图对应工作流里的哪个图像位（首尾帧流程会显示 首帧 / 尾帧）
function slotLabel(i){
  const c=capsOf(WF_NAME), titles=c.image_titles||[];
  const t=(titles[i]||'').toString();
  if(/首|first|start/i.test(t)) return '首帧';
  if(/尾|last|end/i.test(t)) return '尾帧';
  if(/蒙版|mask/i.test(t)) return '蒙版';
  return titles.length>1 ? ('图 '+(i+1)) : '';
}
function refPillState(){
  const c=capsOf(WF_NAME);
  const slots=c.image_slots||0;
  const need = needsImage(WF_NAME) && refImages.length===0;
  const extra = Math.max(0, refImages.length - slots);
  const hint = !slots
    ? '该流程没有图像位，参考图会作为独立「加载图像」节点挂进工作流（需在 ComfyUI 里连线）'
    : (extra ? `已填满 ${slots} 个图像位，多出的 ${extra} 张会作为额外加载图像节点挂入` : `这个流程有 ${slots} 个图像位`);
  ['homeRefPill','gcRefPill'].forEach(id=>{
    const p=$(id); if(!p) return;
    p.classList.toggle('need', need);
    p.classList.toggle('on', refImages.length>0);
    p.classList.remove('off');
    p.title = need ? ('这个工作流必须给参考图 · ' + hint) : hint;
  });
  const txt = refImages.length
    ? `参考图 ${refImages.length}/${REF_MAX}${slots?` · 位 ${Math.min(refImages.length,slots)}/${slots}`:''}`
    : (needsImage(WF_NAME) ? '需要参考图' : (slots?`参考图 · ${slots} 位`:'参考图'));
  ['homeRefText','gcRefText'].forEach(id=>{ const el=$(id); if(el) el.textContent=txt; });
  ['homeRefIc','gcRefIc'].forEach(id=>{ const el=$(id); if(el) el.textContent='🖼'; });
}
function renderRefStrip(){
  const html = refImages.map((name,i)=>{
    const lb=slotLabel(i);
    return `<div class="ref-item" title="${escapeHtml(lb?lb+' · '+name:name)}">
      <img src="${refUrls[i]||''}" alt="">
      <span class="idx">${lb||(i+1)}</span>
      <button class="del" onclick="removeRef(${i})" title="移除">×</button>
    </div>`;
  }).join('') +
    (refImages.length<REF_MAX
      ? `<button class="ref-add" onclick="homePickRef()" title="再加一张">+</button>`
      : '');
  ['homeRefStrip','gcRefStrip'].forEach(id=>{
    const box=$(id); if(!box) return;
    box.innerHTML=html;
    box.classList.toggle('on', refImages.length>0);
  });
}
function updateHomeRef(){ refPillState(); renderRefStrip(); }
function removeRef(i){
  const url=refUrls[i];
  if(url && url.startsWith('blob:')) { try{ URL.revokeObjectURL(url); }catch(e){} }
  refImages.splice(i,1); refUrls.splice(i,1);
  updateHomeRef();
  toast('已移除这张参考图');
}
function clearRefs(){
  refUrls.forEach(u=>{ if(u&&u.startsWith('blob:')){ try{ URL.revokeObjectURL(u); }catch(e){} } });
  refImages=[]; refUrls=[]; updateHomeRef();
}

/* 批量上传参考图（最多 5 张），逐张写进 ComfyUI input */
async function addRefFiles(files){
  const c=capsOf(WF_NAME);
  const list=[...files].filter(f=>f.type.startsWith('image/'));
  if(!list.length) return;
  const room=REF_MAX-refImages.length;
  if(room<=0) return toast(`最多 ${REF_MAX} 张参考图`,true);
  const use=list.slice(0,room);
  if(list.length>room) toast(`最多 ${REF_MAX} 张，多余的已忽略`,true);
  if(!c.can_image) toast('这个流程没有图像位，图片会作为「加载图像」节点挂进工作流',true);
  for(const f of use){
    try{
      const fd=new FormData(); fd.append('image',f); fd.append('type','input'); fd.append('overwrite','true');
      const j=await (await fetch('/api/upload',{method:'POST',body:fd})).json();
      if(j.error) throw new Error(j.error);
      refImages.push(j.name);
      refUrls.push(URL.createObjectURL(f));
      updateHomeRef();
    }catch(err){ toast('上传失败：'+err.message,true); }
  }
  if(refImages.length) toast(`已添加 ${refImages.length} 张参考图`);
}
$('homeFileInput').addEventListener('change', async e=>{
  await addRefFiles(e.target.files);
  e.target.value='';
});

async function homeGenerate(){
  const t=(($('homePrompt')&&$('homePrompt').value)||'').trim();
  const c=capsOf(WF_NAME);
  if(!t && c.has_prompt!==false) return toast('先描述你想生成的画面',true);
  if(!t && !refImages.length) return toast('先写提示词，或至少加一张参考图',true);
  if(needsImage(WF_NAME) && !refImages.length){ refPillState(); return toast('「'+(c.label||WF_NAME)+'」必须给参考图，点「参考图」加上',true); }
  if(c.kind==='视频' && (c.image_slots||0)>refImages.length){
    refPillState();
    return toast(`「${c.label||WF_NAME}」要 ${c.image_slots} 张参考图${c.image_slots>1?'（首帧 / 尾帧）':''}，现在只有 ${refImages.length} 张`,true);
  }
  const btn=$('homeGen'); if(btn.disabled) return;
  btn.disabled=true; const old=btn.textContent; btn.textContent='提交中…';
  const payload={...payloadBase(), prompt:t};
  // 一开始生成，首页立即切换为生成过程页
  genPending={prompt:t, workflow:WF_NAME, aspect:curRatio, megapixels:+$('mp').value, duration:payload.duration, image:refImages[0]||null, images:refImages.slice(), video:(OUT_MODE==='video')};
  genPid=null;
  $('homePrompt').value='';
  openGenView();
  try{
    const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)});
    const j=await r.json();
    if(j.error) throw new Error(j.error);
    genPending=null; genPid=j.prompt_id;
    await load(); renderThread(true); startGenPoll();
    if(j.image_extra) toast(`已提交 · ${j.image_bound} 张进了图像位，另 ${j.image_extra} 张作为额外「加载图像」节点挂入`);
    else if(j.image_bound) toast(`已提交 · ${j.image_bound} 张参考图已填入工作流`);
    else toast((OUT_MODE==='video'?'视频任务已提交，排队中':'已提交，任务进入队列'));
  }catch(e){
    genPending=null; backFromGen();
    toast('提交失败：'+fmtErr(e),true);
  }finally{ btn.disabled=false; btn.textContent=old; }
}
$('homePrompt').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); homeGenerate(); }
});

// ---------- 生成过程页（创作会话）：历史图片+提示词 + 底部继续生成 ----------
let genPid=null, genTimer=null, genPending=null;

function fmtDur(s){
  s=Math.max(0,Math.round(s||0));
  if(s<60) return s+'s';
  const m=Math.floor(s/60), r=s%60;
  return r?`${m}m${r}s`:`${m}m`;
}

function genMediaHtml(it, st, f){
  if(st==='done' && f){
    const vid=isVideoFile(f);
    const body = mediaThumb(f, '', vid?'controls loop playsinline preload="metadata" muted':'');
    return `<div class="mediawrap" onclick="openViewer('${it.prompt_id}')">${body}${vid?'<div class="vbadge">🎬 视频</div>':''}</div>`;
  }
  const label = st==='error' ? '生成失败' : (st==='running' ? '生成中 ▶▶▶' : '排队中 ▶▶▶');
  const fail = st==='error' ? `<div class="gi-failbox">${escapeHtml(it.message||'工作流执行出错')}</div>` : '';
  return `<div class="gen-blobs"></div><div class="gi-badge">${label}</div>${fail}`;
}

function genItemHtml(it){
  const st=itemState(it), f=(it.files||[])[0];
  const ar=(ratioShort(it.aspect)||'16:9').replace(':','/');
  const mp=it.megapixels?(+it.megapixels).toFixed(1)+' MP':'';
  const stTxt = st==='done'?'完成':(st==='error'?'失败':(st==='running'?'生成中':'排队中'));
  return `<div class="gi ${st}" data-pid="${escapeHtml(it.prompt_id)}">
    <div class="gi-prompt">${escapeHtml(it.prompt||'(无提示词)')}</div>
    <div class="gi-media" style="aspect-ratio:${ar}">${genMediaHtml(it,st,f)}</div>
    <div class="gi-meta">
      <span class="st ${st==='error'?'err':(st==='done'?'done':'')}">${stTxt}</span>
      <span class="tm">${fmtTime(it.created||0)}</span>
      <span>${it.kind==='视频'?'🎬 ':''}${escapeHtml((it.workflow||'').replace('.json',''))}${mp?' · '+mp:''}${it.aspect?' · '+ratioShort(it.aspect):''}${it.duration?' · '+(+it.duration).toFixed(0)+'s':''}${it.origin?' · '+escapeHtml(it.origin):''}</span>
    </div>
    <div class="gi-bar"><i></i></div>
  </div>`;
}

function genPendingHtml(){
  const p=genPending, ar=(ratioShort(p.aspect)||'16:9').replace(':','/');
  return `<div class="gi queued pending" data-pid="__pending">
    <div class="gi-prompt">${escapeHtml(p.prompt)}</div>
    <div class="gi-media" style="aspect-ratio:${ar}"><div class="gen-blobs"></div><div class="gi-badge">提交中…</div></div>
    <div class="gi-meta"><span class="st">提交中</span><span>${escapeHtml((p.workflow||'').replace('.json',''))}${p.megapixels?' · '+(+p.megapixels).toFixed(1)+' MP':''}${p.duration?' · '+(+p.duration).toFixed(0)+'s':''}</span></div>
    <div class="gi-bar"><i></i></div>
  </div>`;
}

function renderThread(fresh){
  const el=$('genThread'); if(!el) return;
  const items=(STATE.items||[]).filter(i=>i&&i.prompt_id).slice(0,30).reverse();
  let html=items.map(genItemHtml).join('');
  if(genPending) html+=genPendingHtml();
  el.innerHTML = html || `<div class="gen-empty">还没有生成记录，在下面输入提示词开始</div>`;
  if($('genCount')) $('genCount').textContent=`共 ${(STATE.items||[]).length} 次生成`;
  syncGenUI();
  if(fresh) el.scrollTop=el.scrollHeight;
}

function syncGenUI(){
  const sel=$('gcWf');
  if(sel && [...sel.options].some(o=>o.value===WF_NAME) && sel.value!==WF_NAME) sel.value=WF_NAME;
  fillRatioSelects();
  syncMpEverywhere();
  updateHomeRef();
  if(typeof syncDurUI==='function') syncDurUI();
  if(typeof syncSizeUI==='function') syncSizeUI();
}

function openGenView(pid){
  if(pid) genPid=pid;
  $('docsView').classList.remove('show');
  $('genView').classList.add('show');
  $('flowView') && $('flowView').classList.remove('show');
  $('homeView').classList.remove('show');
  $('shareView').classList.remove('show');
  if(shareTimer){ clearInterval(shareTimer); shareTimer=null; }
  // 分栏状态跟着抽屉走：抽屉还开着就保留左右布局
  const split = $('drawer') && $('drawer').classList.contains('split');
  $('genView').classList.toggle('split', !!split);
  setNav('navGen');
  renderThread(true);
  animView($('genView'));
  // 不只盯着刚提交的那张：流里还有没跑完的卡，也要继续轮询
  const busy = [...document.querySelectorAll('#genThread .gi')]
    .some(c=>c.classList.contains('queued')||c.classList.contains('running'));
  if(genPid || busy) startGenPoll();
}

function startGenPoll(){
  if(genTimer) clearInterval(genTimer);
  genPoll();
  genTimer=setInterval(genPoll,1500);
}

async function genPoll(){
  // 会话页可能同时有多张在跑：逐张刷新所有未完成的卡片
  const cards=[...document.querySelectorAll('#genThread .gi')].filter(c=>{
    const p=c.dataset.pid;
    return p && p!=='__pending' && (c.classList.contains('queued')||c.classList.contains('running'));
  });
  if(!cards.length){ if(genTimer){ clearInterval(genTimer); genTimer=null; } return; }
  for(const c of cards) await pollCard(c.dataset.pid);
}

async function pollCard(pid){
  let j;
  try{ j=await (await fetch('/api/genstatus?pid='+encodeURIComponent(pid))).json(); }
  catch(e){ return; }
  if(j.error) return;
  const card=[...document.querySelectorAll('#genThread .gi')].find(c=>c.dataset.pid===pid);
  if(!card) return;
  const el=j.elapsed||0, est=j.est||30;
  const stEl=card.querySelector('.st'), bar=card.querySelector('.gi-bar i'), badge=card.querySelector('.gi-badge');
  if(j.status==='done'){
    const f=(j.files||[])[0];
    if(!f){ if(stEl) stEl.textContent='即将完成 · 取图中'; return; }
    card.className='gi done';
    const media=card.querySelector('.gi-media');
    if(media) media.innerHTML=`<img src="${imgUrl(f)}" alt="" onclick="openViewer('${pid}')">`;
    if(stEl){ stEl.textContent='完成'; stEl.classList.remove('err'); stEl.classList.add('done'); }
    const tm=card.querySelector('.tm'); if(tm) tm.textContent=fmtTime(Math.floor(Date.now()/1000));
    const bar=card.querySelector('.gi-bar'); if(bar) bar.remove();
    load();
    return;
  }
  if(j.status==='error'){
    card.className='gi error';
    const media=card.querySelector('.gi-media');
    if(media) media.innerHTML=`<div class="gen-blobs"></div><div class="gi-badge">生成失败</div><div class="gi-failbox">${escapeHtml(j.message||'执行失败')}</div>`;
    if(stEl){ stEl.textContent='失败'; stEl.classList.add('err'); stEl.classList.remove('done'); }
    return;
  }
  if(typeof j.ahead==='number' && j.ahead>0){
    card.className='gi queued';
    if(stEl) stEl.textContent=`排队中 · 前面 ${j.ahead} 个 · 约 ${fmtDur(j.ahead*est)}`;
    if(badge) badge.textContent='排队中 ▶▶▶';
  }else{
    card.className='gi running';
    if(stEl) stEl.textContent = el>est*1.15 ? `生成中 · 已用 ${fmtDur(el)}` : `生成中 · 预计 ${fmtDur(est)}`;
    if(badge) badge.textContent='生成中 ▶▶▶';
    if(bar) bar.style.width=Math.min(96,Math.round(el/Math.max(6,est)*100))+'%';
  }
}

async function genGenerate(){
  const t=(($('gcPrompt')&&$('gcPrompt').value)||'').trim();
  const c=capsOf(WF_NAME);
  if(!t && c.has_prompt!==false) return toast('先描述你想生成的画面',true);
  if(!t && !refImages.length) return toast('先写提示词，或至少加一张参考图',true);
  if(needsImage(WF_NAME) && !refImages.length){ refPillState(); return toast('「'+(c.label||WF_NAME)+'」必须给参考图，点「参考图」加上',true); }
  if(c.kind==='视频' && (c.image_slots||0)>refImages.length){
    refPillState();
    return toast(`「${c.label||WF_NAME}」要 ${c.image_slots} 张参考图${c.image_slots>1?'（首帧 / 尾帧）':''}，现在只有 ${refImages.length} 张`,true);
  }
  const btn=$('gcGen'); if(btn.disabled) return;
  btn.disabled=true; const old=btn.textContent; btn.textContent='提交中…';
  const payload={...payloadBase(), prompt:t};
  genPending={prompt:t, workflow:WF_NAME, aspect:curRatio, megapixels:+$('mp').value, duration:payload.duration, image:refImages[0]||null, images:refImages.slice()};
  genPid=null;
  renderThread(true);
  try{
    const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const j=await r.json(); if(j.error) throw new Error(j.error);
    $('gcPrompt').value='';
    genPending=null; genPid=j.prompt_id;
    await load(); renderThread(true); startGenPoll();
    if(j.image_extra) toast(`已提交 · ${j.image_bound} 张进了图像位，另 ${j.image_extra} 张作为额外节点挂入`);
    else toast('已提交，任务进入队列');
  }catch(e){
    genPending=null; renderThread(false);
    toast('提交失败：'+fmtErr(e),true);
  }finally{ btn.disabled=false; btn.textContent=old; }
}
$('gcPrompt') && $('gcPrompt').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); genGenerate(); }
});

function backFromGen(){
  if(genTimer){ clearInterval(genTimer); genTimer=null; }
  genPid=null; genPending=null;
  $('genView').classList.remove('show');
  openHome();
}

async function health(){
  try{
    const j=await (await fetch('/api/health')).json();
    if(j.error) throw new Error(j.error);
    const busy=(j.running+j.pending)>0;
    const cls='dot '+(busy?'busy':'ok');
    $('dot').className=cls;
    const txt=`空闲 ${j.vram_free_gb}G · 队列 ${j.running}/${j.pending}`;
    $('stText').textContent=txt;
    if($('homeDot')) $('homeDot').className=cls;
    if($('homeSt')) $('homeSt').textContent=txt;
  }catch(e){
    $('dot').className='dot'; $('stText').textContent='未连接';
    if($('homeDot')) $('homeDot').className='dot';
    if($('homeSt')) $('homeSt').textContent='ComfyUI 未连接';
  }
}
async function showHealth(){
  try{const j=await (await fetch('/api/health')).json();
    toast(j.error?('连接失败：'+j.error):`${j.gpu}｜显存 ${j.vram_free_gb}/${j.vram_total_gb}G｜队列 ${j.running} 运行 / ${j.pending} 等待`);
  }catch(e){toast('连接失败',true)}
}
async function refreshWorkflows(){
  toast('正在同步工作流库…');
  try{ const j=await (await fetch('/api/refresh-workflows',{method:'POST',body:'{}'})).json();
    STATE.workflows=j.workflows||[]; renderWorkflowSelect(); onWorkflowChange(true); toast('已同步 '+STATE.workflows.length+' 个工作流');
  }catch(e){toast('同步失败：'+e.message,true)}
}

// ---------- 抽屉 ----------
// 左右分栏：左=生成过程流，右=旧的生成抽屉（无遮罩、并排）
function setSplit(on){
  const g=$('genView'), d=$('drawer'), m=$('mask');
  if(g) g.classList.toggle('split', !!on);
  if(d){
    d.classList.toggle('split', !!on);
    if(on){ d.classList.add('show'); if(m) m.classList.remove('show'); }
    else if(!(m && m.classList.contains('show'))) d.classList.remove('show');
  }
  if(!on && m) m.classList.remove('show');
}
function openGenSplit(){
  openGenView();
  setSplit(true);
  if($('drawerTitle')) $('drawerTitle').textContent='新建生成';
}
function openDrawer(){
  setSplit(false);
  $('mask').classList.add('show'); $('drawer').classList.add('show');
}
function closeDrawer(){
  const split = $('drawer').classList.contains('split');
  if(split){
    // 分栏状态下关抽屉 = 收窄成整屏生成过程页，但不离开生成页
    setSplit(false);
    return;
  }
  $('mask').classList.remove('show'); $('drawer').classList.remove('show');
}
function setMode(){ mode='single'; $('drawerTitle').textContent='新建生成'; }
function syncMpLabel(){ syncMpEverywhere(); }

function pickFile(){ if($('fileInput').disabled) return toast('当前工作流不支持参考图',true); $('fileInput').click(); }
$('fileInput').onchange=async e=>{
  const f=e.target.files[0]; if(!f) return;
  dropImg.src=URL.createObjectURL(f); dropImg.style.display='block'; dropPh.style.display='none';
  dropName.textContent=f.name; dropHint.textContent='上传中…';
  const fd=new FormData(); fd.append('image',f); fd.append('type','input'); fd.append('overwrite','true');
  try{
    const j=await (await fetch('/api/upload',{method:'POST',body:fd})).json();
    if(j.error) throw new Error(j.error);
    if(!refImages.length){ refImages=[]; } refImages=[j.name]; refUrls=[dropImg.src]; dropHint.textContent='已上传到 ComfyUI input'; updateHomeRef();
  }catch(err){ dropHint.textContent='上传失败'; toast('上传失败：'+err.message,true); }
};

function payloadBase(){
  const c = capsOf(WF_NAME);
  return {
    workflow: WF_NAME,
    aspect: c.size_mode!=='none' ? curRatio : null,
    megapixels: c.size_mode!=='none' ? +$('mp').value : null,
    seed: $('seed').value.trim() || null,
    image: refImages[0]||null,
    images: refImages.slice(),
    lora: $('lora').value.trim() || null,
    duration: (OUT_MODE==='video' && c.can_duration && curDuration) ? curDuration : null
  };
}

// 该工作流是不是「必须给图才能跑」（抠图 / 纯图生图，没有提示词通道）
function needsImage(wf){
  const c=capsOf(wf||WF_NAME);
  return !!(c && c.can_image && c.has_prompt===false);
}
// 把后端长串 JSON 报错压成一句人话
function fmtErr(e){
  let raw=(e && e.message) ? e.message : String(e||'');
  if(/Required input is missing:\s*(mask|images)/.test(raw))
    return '这个工作流必须先给参考图，请点输入框下方的「参考图」添加图片，或换个文生图工作流';
  const m=raw.match(/"message":\s*"([^"]+)"/) || raw.match(/"error":\s*"([^"]+)"/);
  if(m) raw=m[1];
  return raw.length>160 ? raw.slice(0,157)+'…' : raw;
}

async function doSubmit(){
  const btn=$('genBtn'); btn.disabled=true;
  const old=btn.textContent; btn.textContent='提交中…'; $('progress').classList.add('on');
  try{
    const prompt=$('prompt').value.trim();
    if(!prompt) throw new Error('先写提示词');
    const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...payloadBase(), prompt})});
    const j=await r.json(); if(j.error) throw new Error(j.error);
    toast('已提交，任务进入队列');
    const wasSplit=$('drawer').classList.contains('split');
    if(wasSplit){ $('prompt').value=''; } else { closeDrawer(); }
    await load();
    openGenView(j.prompt_id);
    if(wasSplit) setSplit(true);
  }catch(e){ toast('提交失败：'+fmtErr(e),true); }
  finally{ btn.disabled=false; btn.textContent=old; $('progress').classList.remove('on'); }
}

// ---------- 大图 ----------
function findItem(pid){return STATE.items.find(i=>i.prompt_id===pid)}
function openViewer(pid){
  const it=findItem(pid); if(!it) return; current=it;
  const file=(it.files||[])[0], st=itemState(it), job=STATE.jobs[it.prompt_id]||{};
  const isVid = !!file && isVideoFile(file);
  if(file && isVid){
    vImg.style.display='none'; vImg.src='';
    vVid.style.display='block'; vVid.src=imgUrl(file);
  }else if(file){
    vVid.pause && vVid.pause(); vVid.style.display='none'; vVid.src='';
    vImg.src=imgUrl(file); vImg.style.display='block';
  }else{
    vVid.style.display='none'; vVid.src=''; vImg.style.display='none';
  }
  vPrompt.textContent=it.prompt||'';
  vMeta.innerHTML=[
    ['状态', st==='done'?'已完成':(st==='error'?'失败':(st==='running'?'生成中':'排队中'))],
    ['类型', isVid?'视频':'图片'],
    ['工作流', shortWf(it.workflow)],
    it.aspect?['比例', ratioShort(it.aspect)]:null,
    it.megapixels?['像素', (+it.megapixels).toFixed(1)+' MP']:null,
    ['种子', it.seed ?? '随机'],
    it.image?['参考图', it.image]:null,
    ['提交时间', new Date(it.created*1000).toLocaleString('zh-CN')],
    job.message?['错误', job.message]:null
  ].filter(Boolean).map(([k,v])=>`<div class="kv"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join('');
  $('viewer').classList.add('show');
}
function closeViewer(){
  try{ if(vVid && vVid.pause){ vVid.pause(); } }catch(e){}
  $('viewer').classList.remove('show'); current=null;
}
viewer.onclick=e=>{ if(e.target===viewer) closeViewer(); };
async function rerun(){
  if(!current) return;
  const seed=prompt('新的种子（留空=随机）','');
  const j=await (await fetch('/api/rerun',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prompt_id:current.prompt_id, seed: seed===''?null:seed})})).json();
  if(j.error) return toast('失败：'+j.error,true);
  toast('已重新提交'); closeViewer(); await load();
}
async function removeItem(){
  if(!current||!confirm('从资产库移除这条记录？')) return;
  await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prompt_id:current.prompt_id})});
  closeViewer(); await load();
}
function downloadImage(){
  if(!current||!(current.files||[])[0]) return;
  const a=document.createElement('a'); a.href=imgUrl(current.files[0]); a.download=current.files[0].filename; a.click();
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){closeViewer();closeDrawer();closeDoc();} });

/* ---------- 局域网共享 ---------- */
let shareData=null, shareTimer=null, shareTabCur='files';

function openShare(){
  if(typeof setSplit==='function') setSplit(false);
  $('docsView').classList.remove('show');
  $('flowView') && $('flowView').classList.remove('show');
  $('genView').classList.remove('show');
  if(genTimer){clearInterval(genTimer);genTimer=null;}
  $('homeView').classList.remove('show');
  $('shareView').classList.add('show');
  if($('composer')) $('composer').style.display='none';
  setNav('navShare');
  animView($('shareView'));
  loadShare();
  clearInterval(shareTimer);
  shareTimer=setInterval(loadShare, 3000);
}
function closeShare(){ showLibrary(); }
function setShareTab(t){
  shareTabCur=t;
  document.querySelectorAll('[data-s]').forEach(c=>c.classList.toggle('on', c.dataset.s===t));
  ['files','chat','clip'].forEach(k=>{
    const el=$('sv'+k.charAt(0).toUpperCase()+k.slice(1));
    if(el) el.style.display = (k===t)?'flex':'none';
  });
  if(t==='chat') scrollChat();
}
function scrollChat(){ const l=$('chatList'); if(l) l.scrollTop=l.scrollHeight; }

function fmtSize(b){
  if(!b) return '0 B';
  const u=['B','KB','MB','GB','TB']; let i=0,n=b;
  while(n>=1024&&i<u.length-1){n/=1024;i++}
  return (i===0?Math.round(n):n.toFixed(n>=10?0:1))+' '+u[i];
}
function fileLabel(kind){
  return ({image:'🖼 图片',video:'🎬 视频',audio:'🎵 音频',text:'📄 文本',archive:'🗜 压缩包'})[kind]||'📎 文件';
}

async function loadShare(){
  try{
    const j=await (await fetch('/api/share/state')).json();
    shareData=j; renderShare();
  }catch(e){
    const dot=$('shareDot'); if(dot){dot.className='dot'; $('shareNet').textContent='共享服务未连接';}
  }
}

function renderShare(){
  const d=shareData||{}, files=d.files||[], users=d.users||[], chat=d.chat||[], clip=d.clipboard||[];
  $('shareDot').className='dot '+(d.connected?'ok':'');
  $('shareSrv').textContent=d.connected?'已连接':'未连接';
  $('shareNet').textContent=d.lan?('局域网 '+d.lan):(d.server||'');
  $('shareStats').textContent=`· ${(d.stats&&d.stats.count)||files.length} 个文件 · ${fmtSize((d.stats&&d.stats.totalSize)||0)}`;

  $('shareUsers').innerHTML = users.slice(0,6).map(u=>
      `<div class="ava" style="background:${u.color||'#023a91'}" title="${escapeHtml(u.name)} · ${escapeHtml(u.device||'')}">${escapeHtml((u.name||'?').slice(0,1))}</div>`).join('')
    + (users.length?`<span style="font-size:11px;color:var(--dim);margin-left:10px">${users.length} 台在线</span>`:'');

  $('shareEmpty').style.display = files.length?'none':'block';
  $('shareFiles').innerHTML = files.map(f=>{
    const isImg = f.kind==='image' || (f.mime||'').startsWith('image/');
    const prev = isImg
      ? `<img src="${shareDl(f.id)}"  alt="">`
      : `<div class="ico">${fileLabel(f.kind)}<br>${fmtSize(f.size)}</div>`;
    return `<div class="fcard">
      <div class="prev" onclick="window.open(shareDl('${f.id}'))">${prev}</div>
      <div class="info">
        <div class="nm" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
        <div>${fmtSize(f.size)} · ${escapeHtml(f.uploader||'')}</div>
        <div>${fmtTime((f.time||0)/1000)}</div>
      </div>
      <div class="acts">
        <button class="btn" onclick="importRef('${f.id}')">用作参考图</button>
        <a class="btn" style="text-align:center;text-decoration:none;line-height:1.4" href="${shareDl(f.id)}" download="${escapeHtml(f.name)}">下载</a>
      </div></div>`;
  }).join('');

  const list=$('chatList');
  if(list){
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    list.innerHTML = chat.length ? chat.map(m=>{
      const mine = (m.fromId&&m.fromId===d.me) || (m.from===d.name);
      return `<div class="bub ${mine?'mine':''}">
        <div class="who">${escapeHtml(m.from||'匿名')}</div>${escapeHtml(m.text||'')}
        <div class="tm">${m.time?fmtTime(m.time/1000):''}</div></div>`;
    }).join('') : `<div class="tip">还没有消息，说点什么。</div>`;
    if(nearBottom) scrollChat();
  }

  $('clipList').innerHTML = clip.length ? clip.map(c=>`
      <div class="clip-item">
        <div class="clip-meta">
          <span>${escapeHtml(c.from||'')}</span>
          <span>${c.time?fmtTime(c.time/1000):''}</span>
          <span class="grow"></span>
          <button class="chip" onclick="useClip('${c.id}')">用作提示词</button>
          <button class="chip" onclick="copyClip('${c.id}')">复制</button>
        </div>
        ${escapeHtml(c.text||'')}
      </div>`).join('') : `<div class="tip">剪贴板是空的。</div>`;
}

async function sendChat(){
  const t=$('chatText').value.trim();
  if(!t) return toast('不能发送空消息',true);
  const j=await (await fetch('/api/share/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:t})})).json();
  if(j.error) return toast(j.error,true);
  $('chatText').value=''; $('chatCount').textContent='0/2000';
  await loadShare(); scrollChat();
}
async function clearChat(){
  if(!confirm('清空所有人的聊天记录？')) return;
  const j=await (await fetch('/api/share/chat/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  if(j.error) return toast(j.error,true);
  toast('聊天已清空'); await loadShare();
}
async function sendClip(){
  const t=$('clipText').value;
  if(!t.trim()) return toast('内容为空',true);
  const j=await (await fetch('/api/share/clip',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:t})})).json();
  if(j.error) return toast(j.error,true);
  $('clipText').value=''; toast('已发送到所有设备'); await loadShare();
}
async function clearClip(){
  if(!confirm('清空共享剪贴板？')) return;
  const j=await (await fetch('/api/share/clip/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  if(j.error) return toast(j.error,true);
  await loadShare();
}
function copyClip(id){
  const item=(shareData?.clipboard||[]).find(c=>c.id===id);
  if(!item) return;
  if(navigator.clipboard) navigator.clipboard.writeText(item.text||'').then(()=>toast('已复制到本机剪贴板'));
  else toast('复制失败，请手动选中');
}
function useClip(id){
  const item=(shareData?.clipboard||[]).find(c=>c.id===id);
  if(!item) return;
  $('prompt').value=item.text||'';
  openDrawer();
  toast('已填入提示词，可直接生成');
}
async function uploadShare(files){
  if(!files||!files.length) return;
  let ok=0;
  for(const f of files){
    const fd=new FormData(); fd.append('file', f, f.name);
    try{
      const j=await (await fetch('/api/share/upload',{method:'POST',body:fd})).json();
      if(j.ok===false||j.error) throw new Error(j.error||'服务端拒绝');
      ok++;
    }catch(e){ toast(`上传 ${f.name} 失败：${e.message}`,true); }
  }
  if(ok) toast(`已上传 ${ok} 个文件到共享`);
  const pick=$('sharePick'); if(pick) pick.value='';
  await loadShare();
}
async function importRef(id){
  toast('正在从共享导入到 ComfyUI…');
  const j=await (await fetch('/api/share/import',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id})})).json();
  if(j.error) return toast('导入失败：'+j.error,true);
  refImages.push(j.name);
  dropImg.src=shareDl(id); dropImg.style.display='block'; dropPh.style.display='none';
  dropName.textContent=j.filename||'共享文件'; dropHint.textContent='来自共享 · 已在 ComfyUI input';
  toast('已设为参考图，打开生成面板就能用');
  if(typeof updateHomeRef==='function') updateHomeRef();
}
async function pushShare(){
  if(!current||!(current.files||[])[0]) return toast('这张还没有可分享的图',true);
  const f=current.files[0]; toast('正在推送到共享…');
  const j=await (await fetch('/api/share/push',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({filename:f.filename,subfolder:f.subfolder,type:f.type})})).json();
  if(j.error) return toast('推送失败：'+j.error,true);
  toast('已分享到局域网共享'); if(shareTimer) loadShare();
}

setMode('single'); syncMpLabel(); updateQuickEst(); applyViewMode();
try{ const u=localStorage.getItem('cw_user'); if(u) $('lgUser').value=u; }catch(e){}
boot();
