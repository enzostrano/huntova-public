
window.onerror=function(msg,src,line,col,err){
  console.error('JS Error:',msg,'at',src+':'+line+':'+col,err);
  var neo=document.getElementById('neoMsg');if(neo)neo.textContent='JS Error: '+String(msg);
  return false;
};

// ── 401 handler + CSRF token injection ──
(function(){
  function _getCsrf(){
    var m=document.cookie.match(/(?:^|;\s*)hv_csrf=([^;]+)/);
    return m?m[1]:'';
  }
  var _origFetch=window.fetch;
  window.fetch=function(url,opts){
    // Auto-inject CSRF header on POST/PUT/DELETE/PATCH
    if(opts&&opts.method&&opts.method!=='GET'&&opts.method!=='HEAD'){
      var csrf=_getCsrf();
      if(csrf){
        if(!opts.headers)opts.headers={};
        if(opts.headers instanceof Headers){opts.headers.set('X-CSRF-Token',csrf)}
        else{opts.headers['X-CSRF-Token']=csrf}
      }
    }
    return _origFetch.apply(this,arguments).then(function(r){
      if(r.status===401&&!r.url.includes('/auth/')){window.location.href='/landing'}
      return r;
    });
  };
})();
function hvLogout(){
  if(evtSrc){try{evtSrc.close()}catch(_){}}evtSrc=null;
  if(_pollStatusTimer)clearInterval(_pollStatusTimer);
  fetch('/auth/logout',{method:'POST'}).then(function(){window.location.href='/landing'}).catch(function(){window.location.href='/landing'});
}

// ── ACCOUNT + FEATURE GATING ──
var _hvAccount=null;
var _hvFeatures={};
// Runtime capabilities (Phase 1 of OpenClaw-style pivot). Resolved by
// /api/runtime on first paint. In local mode billing/auth/upgrade
// surfaces hide. Defaults to cloud shape so the page works even if the
// fetch lags.
var _hvRuntime={mode:'cloud',billing_enabled:true,auth_enabled:true,single_user_mode:false,hosted_mode:true,smtp_enabled:true,public_share_enabled:true,google_oauth_enabled:false};

function hvApplyRuntime(){
  // Hide credit pill / pricing access entirely when billing is off.
  if(!_hvRuntime.billing_enabled){
    document.querySelectorAll('#hvCreditsBtn, .topnav-credits, .acct-upgrade-btn, .hv-billing-only').forEach(function(el){el.style.display='none'});
    document.body.classList.add('hv-no-billing');
  }
  if(_hvRuntime.single_user_mode){
    document.body.classList.add('hv-single-user');
    // Local CLI: no user account, no email, no plan tiers, no
    // log-out. Hide every SaaS-y bit so the topnav reads as "your
    // dev tool" instead of "an enterprise SaaS".
    document.querySelectorAll('.hv-saas-only').forEach(function(el){el.style.display='none'});
  }
  if(!_hvRuntime.auth_enabled){
    document.body.classList.add('hv-no-auth');
    // Hide auth-only surfaces (logout button, login pages, etc.)
    document.querySelectorAll('.hv-auth-only').forEach(function(el){el.style.display='none'});
    // Skip the email-verification banner — there's no email to verify
    // in local mode.
    var vb=document.getElementById('verifyBanner');if(vb)vb.style.display='none';
  }
  // Hide local-only surfaces (Providers tab, BYOK helpers) in cloud mode.
  if(_hvRuntime.mode!=='local'){
    document.querySelectorAll('.hv-local-only').forEach(function(el){el.style.display='none'});
  }
}

function hvLoadRuntime(){
  return fetch('/api/runtime').then(function(r){return r.json()}).then(function(d){
    if(d&&d.runtime){_hvRuntime=d.runtime}
    hvApplyRuntime();
  }).catch(function(){hvApplyRuntime()});
}

function hvLoadAccount(){
  fetch('/api/account').then(function(r){return r.json()}).then(function(d){
    if(!d.ok)return;
    _hvAccount=d.user;
    _hvFeatures=d.features||{};
    // Show / hide the first-run empty-state banner based on whether
    // any provider is configured. Pulls from /api/setup/status which
    // already enumerates configured providers — same source the
    // /setup wizard uses to flag "ready" providers.
    fetch('/api/setup/status').then(function(r){return r.json()}).then(function(s){
      var banner=document.getElementById('hvOnboardBanner');
      if(!banner)return;
      var configured=(s&&s.providers_configured)||[];
      banner.style.display=(configured.length===0)?'flex':'none';
    }).catch(function(){});
    // Show credits in topbar + dashboard
    var cb=document.getElementById('hvCredits');
    if(cb)cb.textContent=_hvAccount.credits_remaining||0;
    var creditsBtn=document.getElementById('hvCreditsBtn');
    if(creditsBtn)creditsBtn.style.visibility='visible';
    var dc=document.getElementById('dCredits');
    if(dc)dc.textContent=_hvAccount.credits_remaining||0;
    // Apply feature locks
    hvApplyGating();
    // Populate user menu
    var umdName=document.getElementById('umdName');
    var umdEmail=document.getElementById('umdEmail');
    var umdTier=document.getElementById('umdTier');
    var umdCredits=document.getElementById('umdCredits2');
    var avatar=document.getElementById('userAvatar');
    var avatarLg=document.getElementById('umAvatarLg');
    var initial=(_hvAccount.display_name||_hvAccount.email||'U').charAt(0).toUpperCase();
    if(umdName){umdName.textContent=_hvAccount.display_name||(_hvAccount.email||'').split('@')[0]||'User';umdName.style.visibility='visible';}
    if(umdEmail){umdEmail.textContent=_hvAccount.email||'';umdEmail.style.visibility='visible';}
    if(umdTier){umdTier.textContent=(_hvAccount.tier||'free').toUpperCase();umdTier.style.visibility='visible';}
    if(umdCredits){umdCredits.textContent=_hvAccount.credits_remaining||0;
      var leadsRow=umdCredits.parentElement;
      if(leadsRow)leadsRow.style.visibility='visible';}
    // Avatars — only mutate when in SaaS mode. In local mode the avatar
    // button holds a settings-gear SVG (rendered by templates/index.html);
    // overwriting textContent here would replace the gear with a "U"
    // letter and silently regress the local-CLI UX.
    if(avatar && !_hvRuntime.single_user_mode){
      if(_hvAccount.avatar_url){var _ai=document.createElement('img');_ai.src=_hvAccount.avatar_url;_ai.alt='';avatar.textContent='';avatar.appendChild(_ai);}
      else{avatar.textContent=initial;}
    }
    if(avatarLg && !_hvRuntime.single_user_mode){
      if(_hvAccount.avatar_url){var _ai2=document.createElement('img');_ai2.src=_hvAccount.avatar_url;_ai2.alt='';avatarLg.textContent='';avatarLg.appendChild(_ai2);}
      else{avatarLg.textContent=initial;}
    }
    // Plan progress bar
    var tiers={free:3,growth:25,agency:50};
    var max=tiers[_hvAccount.tier]||5;
    var cr=_hvAccount.credits_remaining||0;
    var fill=document.getElementById('umdFill');
    if(fill)fill.style.width=Math.min(100,Math.round(cr/max*100))+'%';
    // Show verification banner ONLY in SaaS mode + when auth is on +
    // when the user actually has an email that's unverified. Local
    // CLI users get an auto-bootstrapped account with no email and
    // email_verified=false, which used to re-show this banner after
    // hvApplyRuntime() had hidden it.
    if(_hvRuntime.auth_enabled && !_hvRuntime.single_user_mode &&
       _hvAccount.email && _hvAccount.email_verified===false){
      var vb=document.getElementById('verifyBanner');if(vb)vb.style.display='flex';
    }
    // Skip the credit-warning popups in local/BYOK mode — there's no
    // credit system to warn about.
    if(_hvRuntime && _hvRuntime.billing_enabled){
      try{hvCheckTokens();}catch(_e){}
    }
    // Refresh dashboard credits now that account is loaded
    try{if(typeof loadDashboard==='function')loadDashboard();}catch(_e){}
    // Refresh dashboard greeting in case display_name just changed
    // (e.g. user saved Profile → Your Name in single-user mode).
    try{if(typeof hvUpdateGreeting==='function')hvUpdateGreeting();}catch(_e){}
  }).catch(function(){
    // H1 fix: retry account load after 10s on failure
    setTimeout(hvLoadAccount,10000);
  });
}
function toggleUserMenu(){
  var menu=document.getElementById('userMenu');
  if(!menu)return;
  var isOpen=menu.classList.contains('open');
  // Close if open
  if(isOpen){menu.classList.remove('open');return;}
  // Open
  menu.classList.add('open');
  // Close on any click outside or escape
  function close(){menu.classList.remove('open');document.removeEventListener('click',handler);document.removeEventListener('keydown',escHandler);}
  function handler(e){if(!menu.contains(e.target))close();}
  function escHandler(e){if(e.key==='Escape')close();}
  setTimeout(function(){document.addEventListener('click',handler);document.addEventListener('keydown',escHandler);},10);
}
function resendVerification(btn){
  if(btn){btn.disabled=true;btn.textContent='Sending...';}
  fetch('/auth/resend-verification',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    if(btn){btn.textContent=d.ok?'Sent!':'Failed';setTimeout(function(){btn.disabled=false;btn.textContent='Resend email'},3000);}
  }).catch(function(){if(btn){btn.textContent='Failed';btn.disabled=false;}});
}
function hvApplyGating(){
  // Local/BYOK mode: every feature is unlocked. Strip any prior
  // hv-locked classes that may have been applied before runtime
  // capabilities loaded (race against /api/account).
  if(_hvRuntime && !_hvRuntime.billing_enabled){
    document.querySelectorAll('.hv-locked').forEach(function(el){
      el.classList.remove('hv-locked');
      el._hvGated=false;
      el.removeAttribute('title');
    });
    document.body.classList.remove('hv-blur-drafts');
    return;
  }
  document.querySelectorAll('[data-feature]').forEach(function(el){
    var feat=el.getAttribute('data-feature');
    if(_hvFeatures[feat]===false && !el._hvGated){
      el._hvGated=true;
      el.classList.add('hv-locked');
      el.setAttribute('title','Upgrade to unlock this feature');
      el.addEventListener('click',function(e){
        e.preventDefault();e.stopPropagation();
        hvShowUpgrade(feat);
      },true);
    }
  });
  // Email draft blurring for free tier (contact details always visible)
  if(_hvFeatures.email_draft_visible===false){
    document.body.classList.add('hv-blur-drafts');
  } else {
    document.body.classList.remove('hv-blur-drafts');
  }
}
function hvShowUpgrade(feat){
  var names={ai_chat:'AI Chat',email_rewrite:'Email Rewrite',email_draft_visible:'AI Email Drafts',research:'Lead Research',smart_score:'Smart Score',export_json:'JSON Export'};
  var name=names[feat]||feat;
  closeMod();
  setTimeout(function(){hvOpenPricing()},100);
}
function hvOpenPricing(){
  var cr=_hvAccount?_hvAccount.credits_remaining:0;
  var tier=_hvAccount?_hvAccount.tier:'free';
  var h='<div class="up-hdr">';
  h+='<div class="up-title">Keep your pipeline running</div>';
  h+='<div class="up-sub">You have <span class="up-cr '+(cr<3?'up-cr-low':'')+'">'+cr+'</span> lead credits remaining</div>';
  h+='</div>';
  // Top-ups first
  h+='<div class="up-topup-label">ADD CREDITS INSTANTLY</div>';
  h+='<div class="up-topups">';
  h+='<button class="up-topup" onclick="hvBuyCredits(\'topup_10\')"><span class="up-topup-n">10</span> leads<span class="up-topup-price">€19</span></button>';
  h+='<button class="up-topup up-topup-hl" onclick="hvBuyCredits(\'topup_30\')"><span class="up-topup-n">30</span> leads<span class="up-topup-price">€49</span><span class="up-topup-save">BEST VALUE</span></button>';
  h+='<button class="up-topup" onclick="hvBuyCredits(\'topup_75\')"><span class="up-topup-n">75</span> leads<span class="up-topup-price">€99</span><span class="up-topup-save">SAVE 30%</span></button>';
  h+='</div>';
  h+='<div class="up-topup-note">Credits added instantly after payment. Top-up credits never expire.</div>';
  // Plans
  h+='<div class="up-topup-label" style="margin-top:20px">MONTHLY PLANS</div>';
  h+='<div class="up-plans">';
  // Growth
  h+='<div class="up-plan'+(tier==='growth'?' up-plan-active':'')+'">';
  if(tier==='growth')h+='<div class="up-badge up-badge-current">CURRENT</div>';
  h+='<div class="up-plan-name up-plan-name-org">GROWTH</div>';
  h+='<div class="up-price">€49<span>/mo</span></div>';
  h+='<div class="up-credits">25 leads/month</div>';
  h+='<div class="up-features">Gemini Flash AI · AI Chat<br>Full contacts · Email rewrite<br>Smart scoring · JSON export</div>';
  h+='<button class="up-btn" onclick="hvBuyCredits(\'growth_monthly\')">'+(tier==='growth'?'Current Plan':'Upgrade')+'</button>';
  h+='</div>';
  // Agency
  h+='<div class="up-plan up-plan-featured'+(tier==='agency'?' up-plan-active':'')+'">';
  if(tier==='agency')h+='<div class="up-badge up-badge-current">CURRENT</div>';
  else h+='<div class="up-badge">PREMIUM</div>';
  h+='<div class="up-plan-name up-plan-name-acc">AGENCY</div>';
  h+='<div class="up-price">€149<span>/mo</span></div>';
  h+='<div class="up-credits">50 leads/month</div>';
  h+='<div class="up-features"><b class="up-feat-hl">Gemini Pro AI</b><br>Deep research<br>API access · Premium support</div>';
  h+='<button class="up-btn up-btn-glow" onclick="hvBuyCredits(\'agency_monthly\')">'+(tier==='agency'?'Current Plan':'Go Agency')+'</button>';
  h+='</div>';
  h+='</div>';
  var bg=document.getElementById('mBg');
  if(bg){
    document.getElementById('mT').textContent='';
    document.getElementById('mM').innerHTML=h;
    bg.classList.add('on');
    document.getElementById('mC').style.display='none';
  }
}
var _checkoutInFlight=false;
window.addEventListener('pageshow',function(e){if(e.persisted)_checkoutInFlight=false});
function hvBuyCredits(productId){
  if(_checkoutInFlight)return;
  _checkoutInFlight=true;
  fetch('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:productId})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.url)window.location.href=d.url;
    else{_checkoutInFlight=false;toast(d.error||'Payment not available')}
  }).catch(function(){_checkoutInFlight=false;toast('Payment error')});
}
// ── LOW TOKEN WARNING ──
function hvCheckTokens(){
  if(!_hvAccount)return;
  // Skip in local/BYOK mode — there's no credit system to warn about.
  // The user pays their AI provider directly; no quota limits here.
  if(_hvRuntime && (_hvRuntime.single_user_mode || !_hvRuntime.billing_enabled))return;
  var cr=_hvAccount.credits_remaining||0;
  var tier=_hvAccount.tier||'free';
  if(cr<=0){
    hvTokenPopup('empty');
  } else if(cr<=2 && tier==='free'){
    hvTokenPopup('low_free');
  } else if(cr<=5 && tier!=='free'){
    hvTokenPopup('low_paid');
  }
}
// Credits-exhausted upgrade block (Perplexity round 61). Renders the
// inner HTML for the modal — caller wraps in <div class="up-hdr">…</div>.
function renderCreditsExhaustedUpgrade(){
  var tier=(_hvAccount&&_hvAccount.tier)||'free';
  var tierMax={free:3,growth:25,agency:50}[tier]||3;
  var tierName={free:'Free',growth:'Growth',agency:'Agency'}[tier]||'Free';
  var usedLine='You’ve used '+tierMax+' of '+tierMax+' '+tierName.toLowerCase()+' leads this month.';
  var h='';
  h+='<div class="up-icon">⚡</div>';
  if(tier==='free'){
    h+='<div class="up-title">You’re out of leads</div>';
    h+='<div class="up-desc">'+usedLine+' Upgrade to keep your pipeline running every month.</div>';
    h+='<div class="up-cx-block">';
    h+='<div class="up-cx-plan">';
    h+='<div class="up-cx-plan-name">GROWTH</div>';
    h+='<div class="up-cx-plan-price">€49<span>/mo</span></div>';
    h+='<div class="up-cx-plan-allow">25 leads / month · Gemini AI · Email rewrite</div>';
    h+='</div>';
    h+='<button class="up-btn up-btn-glow up-cx-cta" onclick="closeMod();hvCheckoutWithSource(\'growth_monthly\',\'credits_exhausted\')">Upgrade to Growth — €49/mo</button>';
    h+='<div class="up-cx-secondary">';
    h+='<button class="up-link-btn" onclick="closeMod();hvCheckoutWithSource(\'topup_10\',\'credits_exhausted\')">Top up 10 leads for €19 instead</button>';
    h+='<span class="up-cx-sep">·</span>';
    h+='<button class="up-link-btn" onclick="closeMod();hvOpenPricing()">See all plans</button>';
    h+='</div></div>';
  } else if(tier==='growth'){
    h+='<div class="up-title">Pipeline paused — you used all '+tierMax+' Growth leads</div>';
    h+='<div class="up-desc">Top up to keep going this month, or move to Agency for 50 leads + Gemini Pro.</div>';
    h+='<div class="up-cx-block">';
    h+='<button class="up-btn up-btn-org up-cx-cta" onclick="closeMod();hvCheckoutWithSource(\'topup_30\',\'credits_exhausted\')">Top up 30 leads — €49</button>';
    h+='<div class="up-cx-secondary">';
    h+='<button class="up-link-btn" onclick="closeMod();hvCheckoutWithSource(\'topup_10\',\'credits_exhausted\')">10 leads for €19</button>';
    h+='<span class="up-cx-sep">·</span>';
    h+='<button class="up-link-btn" onclick="closeMod();hvCheckoutWithSource(\'agency_monthly\',\'credits_exhausted\')">Upgrade to Agency</button>';
    h+='</div></div>';
  } else {
    // agency — only top-up makes sense
    h+='<div class="up-title">Pipeline paused — you used all '+tierMax+' Agency leads</div>';
    h+='<div class="up-desc">Top up to keep going this month — your Gemini Pro access stays.</div>';
    h+='<div class="up-cx-block">';
    h+='<button class="up-btn up-btn-org up-cx-cta" onclick="closeMod();hvCheckoutWithSource(\'topup_30\',\'credits_exhausted\')">Top up 30 leads — €49</button>';
    h+='<div class="up-cx-secondary">';
    h+='<button class="up-link-btn" onclick="closeMod();hvCheckoutWithSource(\'topup_10\',\'credits_exhausted\')">10 leads for €19</button>';
    h+='<span class="up-cx-sep">·</span>';
    h+='<button class="up-link-btn" onclick="closeMod();hvCheckoutWithSource(\'topup_75\',\'credits_exhausted\')">75 leads — €99</button>';
    h+='</div></div>';
  }
  return h;
}

// Wrapper around hvBuyCredits that tags an analytics source. The
// server route already accepts an optional source field; if it ever
// stops accepting it the call still goes through (server ignores
// unknown body keys).
function hvCheckoutWithSource(productId,source){
  if(_checkoutInFlight)return;
  _checkoutInFlight=true;
  fetch('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({product_id:productId,source:source||''})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.url)window.location.href=d.url;
    else{_checkoutInFlight=false;toast(d.error||'Payment not available')}
  }).catch(function(){_checkoutInFlight=false;toast('Payment error')});
}

function hvTokenPopup(type){
  var h='<div class="up-hdr">';
  if(type==='empty'){
    // Per Perplexity round 61: this is the highest-converting paywall
    // moment in a freemium SaaS. Branch by tier — free users see a
    // focused Growth upgrade as primary; paid users see a top-up to
    // resume continuity (don't push a sidegrade tier change).
    h+=renderCreditsExhaustedUpgrade();
  } else if(type==='low_free'){
    h+='<div class="up-icon">🔥</div>';
    h+='<div class="up-title">Only '+(_hvAccount.credits_remaining)+' credits left</div>';
    h+='<div class="up-desc">Free accounts get 3 leads to prove the product works. Upgrade to keep your pipeline running every month.</div>';
    h+='<button class="up-btn" style="max-width:300px;margin:0 auto" onclick="closeMod();hvBuyCredits(\'growth_monthly\')">Upgrade to Growth — €49/mo</button>';
    h+='<div class="up-link"><button class="up-link-btn" onclick="closeMod();hvBuyCredits(\'topup_10\')">or top up 10 leads for €19</button></div>';
  } else {
    h+='<div class="up-icon">⚠️</div>';
    h+='<div class="up-title">Running low — '+(_hvAccount.credits_remaining)+' leads left</div>';
    h+='<div class="up-desc">Keep your pipeline momentum. Top up before your next scan.</div>';
    h+='<div class="up-topups" style="flex-direction:column;align-items:center">';
    h+='<button class="up-btn up-btn-org" style="max-width:280px" onclick="closeMod();hvBuyCredits(\'topup_30\')">30 leads — €49</button>';
    h+='<button class="up-btn" style="max-width:280px" onclick="closeMod();hvBuyCredits(\'topup_75\')">75 leads — €99</button>';
    h+='</div>';
  }
  h+='</div>';
  var bg=document.getElementById('mBg');
  if(bg){
    document.getElementById('mT').textContent='';
    document.getElementById('mM').innerHTML=h;
    bg.classList.add('on');
    document.getElementById('mC').style.display='none';
  }
}

// ── PAYMENT SUCCESS HANDLER ──
(function(){
  var params=new URLSearchParams(window.location.search);
  if(params.get('payment')==='success'){
    // Clean URL immediately so refresh doesn't re-fire the toast.
    window.history.replaceState({},'','/');
    // Stripe webhook processing can take 1–5 seconds. Previously we
    // toasted '✓ Payment successful! Credits added.' on page load before
    // confirming anything — users saw the green check even when the
    // webhook silently failed. Now we fetch the actual account state
    // and only toast once we've confirmed the server view, showing the
    // true credit balance in the message. If /api/account fails, we
    // toast a 'processing' message so the user isn't lied to.
    fetch('/api/account').then(function(r){return r.json()}).then(function(d){
      if(d&&d.ok&&d.user){
        var _cr=d.user.credits_remaining||0;
        toast('✓ Payment successful! You now have '+_cr+' credits.');
      } else {
        toast('Payment processing — credits will appear in a moment.');
      }
      hvLoadAccount();
    }).catch(function(){
      toast('Payment processing — credits will appear in a moment.');
      hvLoadAccount();
    });
  } else if(params.get('payment')==='cancelled'){
    setTimeout(function(){toast('Payment cancelled');window.history.replaceState({},'','/')},500);
  }
  if(params.get('verified')==='1'){
    setTimeout(function(){
      toast('Email verified successfully!');
      var vb=document.getElementById('verifyBanner');if(vb)vb.style.display='none';
      window.history.replaceState({},'','/');
    },500);
  }
  // Handle /#pricing hash from account page
  if(window.location.hash==='#pricing'){
    setTimeout(function(){hvOpenPricing();history.replaceState({},'','/');},600);
  }
})();

// Load runtime caps first so UI hides billing surfaces immediately on
// local installs. Account load follows so credit pill etc. populate
// only when billing is on.
try{hvLoadRuntime().finally(function(){try{hvLoadAccount()}catch(_){}})}catch(e){try{hvLoadAccount()}catch(_){}}

const $=s=>document.getElementById(s);
function hasV(v){return v&&typeof v==='string'&&v.trim().length>2&&v!=='null'&&v!=='none'&&v!=='N/A'&&v!=='undefined'}
var _bulkSelected=new Set();

// ── START POPUP ──
// Country list — derived from wizard geography when available, falls back to defaults
var _regionToCountries={
  'Western Europe':['France','Germany','Italy','Spain','Netherlands','Belgium','Luxembourg','Austria','Switzerland','Ireland'],
  'Eastern Europe':['Poland','Czech Republic','Romania','Hungary','Bulgaria','Croatia','Slovakia','Slovenia','Estonia','Latvia','Lithuania','Greece','Cyprus','Malta'],
  'Scandinavia':['Sweden','Norway','Denmark','Finland'],
  'United Kingdom':['United Kingdom'],
  'United States':['USA'],
  'Canada':['Canada'],
  'Middle East':['UAE','Saudi Arabia','Qatar','Bahrain','Kuwait','Oman'],
  'Asia Pacific':['Australia','New Zealand','Singapore','Japan','South Korea','India'],
  'Latin America':['Brazil','Mexico','Colombia','Argentina','Chile'],
  'Global':['France','Germany','Italy','Spain','Netherlands','Belgium','Luxembourg','Austria','Switzerland','Sweden','Norway','Denmark','Finland','Ireland','Poland','Czech Republic','Romania','Greece','Portugal','Hungary','Bulgaria','Croatia','Slovakia','Slovenia','Estonia','Latvia','Lithuania','Cyprus','Malta','USA','UAE','United Kingdom']
};
var _defaultCountries=['France','Germany','Italy','Spain','Netherlands','Belgium','Luxembourg','Austria','Switzerland','Sweden','Norway','Denmark','Finland','Ireland','Poland','Czech Republic','Romania','Greece','Portugal','Hungary','Bulgaria','Croatia','Slovakia','Slovenia','Estonia','Latvia','Lithuania','Cyprus','Malta','USA','UAE'];

function _buildStartCountries(){
  var countries={};
  // Try to derive from wizard geography
  var wiz=null;
  try{
    if(_hvAccount&&_hvAccount._settings&&_hvAccount._settings.wizard)wiz=_hvAccount._settings.wizard;
  }catch(_){}
  // Also check cached settings
  var regions=wiz?wiz.regions:null;
  if(regions&&Array.isArray(regions)&&regions.length){
    regions.forEach(function(r){
      var mapped=_regionToCountries[r]||[];
      mapped.forEach(function(c){countries[c]=true});
    });
  }
  // Fall back to defaults if no wizard data
  if(!Object.keys(countries).length){
    _defaultCountries.forEach(function(c){countries[c]=true});
  }
  return countries;
}
var _startCountries=_buildStartCountries();
// Refresh when account loads
var _origHvLoadAccount=hvLoadAccount;
hvLoadAccount=function(){_origHvLoadAccount();setTimeout(function(){_startCountries=_buildStartCountries()},500)};
const SL={new:"New",email_sent:"Email Sent",followed_up:"Followed Up",replied:"Replied",meeting_booked:"Meeting Booked",won:"Won",lost:"Lost",ignored:"Ignored"};
const SC={new:"var(--blu)",email_sent:"var(--org)",followed_up:"var(--pur)",replied:"var(--cyn)",meeting_booked:"var(--acc)",won:"var(--acc)",lost:"var(--red)",ignored:"var(--t3)"};
function sbg(s){return s>=9?'var(--acc)':s>=7?'var(--amb)':s>=5?'var(--pur)':'var(--t3)'}
function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML}
function safeUrl(u){if(!u||typeof u!=='string')return'';u=u.trim();if(u.startsWith('http://')||u.startsWith('https://')||u.startsWith('mailto:'))return esc(u);return''}
function toast(m){const t=$('toast');if(!t)return;if(t.classList.contains('toast-undo-active'))return;t.textContent=m;t.classList.add('on');clearTimeout(t._t);var dur=m&&(m.includes('error')||m.includes('Error')||m.includes('failed')||m.includes('Failed'))?4000:2500;t._t=setTimeout(()=>t.classList.remove('on'),dur)}
function cp(t,l){navigator.clipboard.writeText(t).then(()=>toast('✓ '+l+' copied')).catch(()=>toast('Copy failed'))}
function showMod(t,m,fn){$('mT').textContent=t;$('mM').innerHTML=esc(m);$('mBg').classList.add('on');$('mC').style.display='';$('mC').onclick=()=>{closeMod();fn()}}
function closeMod(){$('mBg').classList.remove('on');var mc=$('mC');if(mc)mc.style.display=''}

// Share-hunt modal — Feature F1.
function openShareHuntModal(){
  var bg=$('shareHuntBg');if(!bg)return;
  var f=$('shareHuntForm');if(f)f.style.display='';
  var r=$('shareHuntResult');if(r)r.style.display='none';
  var t=$('shareHuntTitleInput');if(t)t.value='';
  var b=$('shareHuntBtns');if(b)b.style.display='';
  var sb=$('shareHuntSubmit');if(sb){sb.disabled=false;sb.textContent='Create share link'}
  bg.classList.add('on');
  setTimeout(function(){var ti=$('shareHuntTitleInput');if(ti)ti.focus()},50);
}
function closeShareHuntModal(){var bg=$('shareHuntBg');if(bg)bg.classList.remove('on')}

function submitShareHunt(){
  var btn=$('shareHuntSubmit');if(btn){btn.disabled=true;btn.textContent='Working…'}
  var title=($('shareHuntTitleInput')||{}).value||'';
  function _ship(ids){
    fetch('/api/hunts/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_ids:ids,title:title})})
      .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})})
      .then(function(res){
        if(!res.ok||!res.j||!res.j.url){throw new Error((res.j&&res.j.error)||'share failed')}
        var url=res.j.url;
        var f=$('shareHuntForm');if(f)f.style.display='none';
        var rb=$('shareHuntBtns');if(rb)rb.style.display='none';
        var rs=$('shareHuntResult');if(rs)rs.style.display='';
        var inp=$('shareHuntUrl');if(inp){inp.value=url;inp.select()}
        var op=$('shareHuntOpen');if(op)op.href=url;
      })
      .catch(function(e){toast('Share failed: '+(e.message||e));if(btn){btn.disabled=false;btn.textContent='Create share link'}});
  }
  function _pickTopIds(leads){
    var arr=(leads||[]).slice().sort(function(a,b){return (b.fit_score||0)-(a.fit_score||0)});
    return arr.slice(0,10).map(function(l){return l.lead_id}).filter(Boolean);
  }
  if(typeof allLeads!=='undefined'&&allLeads&&allLeads.length){
    var ids=_pickTopIds(allLeads);
    if(!ids.length){toast('No leads to share yet');if(btn){btn.disabled=false;btn.textContent='Create share link'}return}
    _ship(ids);
  } else {
    fetch('/api/leads').then(function(r){return r.json()}).then(function(j){
      var ids=_pickTopIds(j&&j.leads||[]);
      if(!ids.length){toast('No leads to share yet');if(btn){btn.disabled=false;btn.textContent='Create share link'}return}
      _ship(ids);
    }).catch(function(e){toast('Share failed: '+(e.message||e));if(btn){btn.disabled=false;btn.textContent='Create share link'}});
  }
}

function copyShareHuntUrl(){
  var inp=$('shareHuntUrl');if(!inp)return;
  var url=inp.value||'';
  if(!url)return;
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(url).then(function(){
      toast('✓ Link copied');
      var b=$('shareHuntCopyBtn');if(b){var o=b.textContent;b.textContent='Copied';setTimeout(function(){b.textContent=o},1400)}
    }).catch(function(){inp.select();toast('Copy failed — select + ⌘C')});
  } else {
    inp.select();document.execCommand&&document.execCommand('copy');toast('✓ Link copied');
  }
}

function dAgo(d){if(!d)return'';const ms=Date.now()-new Date(d).getTime(),dy=Math.floor(ms/864e5);return dy===0?'today':dy+'d ago'}
// Compact date formatter that respects the user's locale — avoids the
// UTC-vs-local off-by-one that bit the old raw-string renderers. Returns
// e.g. "21 Apr" in en-GB and "Apr 21" in en-US. Falls back to ISO date on
// environments without Intl.
function dFmt(d){
  if(!d)return'';
  try{
    return new Date(d).toLocaleDateString(undefined,{month:'short',day:'numeric'});
  }catch(_){
    try{return (new Date(d).toISOString()||'').slice(0,10)}catch(_){return ''}
  }
}

// ── Navigation ──
try{document.querySelectorAll('.nav-btn').forEach(b=>{b.addEventListener('click',()=>{
  document.querySelectorAll('.nav-btn').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');$('pg-'+b.dataset.page).classList.add('on');
  document.querySelector('.nav').classList.remove('nav-open');
  // Hide lead detail page when switching tabs
  var ldp=document.getElementById('pg-lead-detail');if(ldp){ldp.style.display='none';ldp.classList.remove('on');}
})});}catch(_e){}

// ── Client-side Router ──
function routeFromPath() {
  var path = location.pathname;
  var leadMatch = path.match(/^\/leads\/(.+)$/);
  if (leadMatch) {
    // Switch to leads tab, then open the lead
    document.querySelector('[data-page="crm"]').click();
    setTimeout(function() { openLeadPage(leadMatch[1]); }, 300);
  } else if (path === '/leads') {
    document.querySelector('[data-page="crm"]').click();
  } else if (path === '/agent' || path === '/hunts') {
    document.querySelector('[data-page="hunts"]').click();
  }
  // else: dashboard (default)
}
window.addEventListener('popstate', function() { routeFromPath(); });

// Agent tabs removed — Hunts page uses unified layout
try{document.querySelectorAll('.ag-tab').forEach(t=>{t.addEventListener('click',function(){
  document.querySelectorAll('.ag-tab').forEach(function(x){x.classList.remove('on')});
  document.querySelectorAll('.ag-tc').forEach(function(x){x.classList.remove('on')});
  t.classList.add('on');var tc=$('at-'+t.dataset.at);if(tc)tc.classList.add('on');
})})}catch(_){}

// ── Resize handles ──
function setupResize(handle,target,dir){
  let startPos,startSize;
  function onDown(e){
    const t=e.touches?e.touches[0]:e;
    startPos=dir==='v'?t.clientY:t.clientX;
    startSize=dir==='v'?target.offsetHeight:target.offsetWidth;
    function onMove(ev){const t2=ev.touches?ev.touches[0]:ev;const d=(dir==='v'?t2.clientY:t2.clientX)-startPos;
      const s=Math.max(80,Math.min(startSize+(dir==='v'?d:-d),dir==='v'?window.innerHeight*.6:window.innerWidth*.5));
      target.style[dir==='v'?'maxHeight':'width']=s+'px';ev.preventDefault()}
    function onUp(){document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);
      document.removeEventListener('touchmove',onMove);document.removeEventListener('touchend',onUp)}
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
    document.addEventListener('touchmove',onMove,{passive:false});document.addEventListener('touchend',onUp);
    e.preventDefault();
  }
  handle.addEventListener('mousedown',onDown);
  handle.addEventListener('touchstart',onDown,{passive:false});
}
// CRM resize removed — single list layout
try{setupResize($('agResize'),$('agRight'),'h');}catch(_e){}

// ═══ SESSION PERSISTENCE ═══
var _logBuffer=[];
var _lastProgress=null;
var _saveTimer2=null;
function saveSession(){
  try{
    localStorage.setItem('hv_session',JSON.stringify({
      liveLeads:liveLeads.slice(0,20),sessionLeadIds:Array.from(_sessionLeadIds).slice(0,50),agentRunning:agentRunning,paused:paused,
      logs:_logBuffer.slice(-100),progress:_lastProgress,savedAt:Date.now()
    }));
  }catch(e){
    // Quota errors used to be swallowed silently — user lost session
    // resume without ever knowing why. Try to recover by purging the
    // existing key (frees roughly the same amount we were trying to
    // write) and surface a one-time toast so they know the resume
    // path is degraded.
    if(e && (e.name==='QuotaExceededError' || /quota/i.test(String(e.message||'')))){
      try{localStorage.removeItem('hv_session')}catch(_){}
      if(!saveSession._warned){
        saveSession._warned=true;
        try{if(typeof toast==='function')toast('Browser storage full — session resume disabled')}catch(_){}
      }
    }
    try{console.warn('saveSession failed:',e)}catch(_){}
  }
}
function debounceSaveSession(){clearTimeout(_saveTimer2);_saveTimer2=setTimeout(saveSession,1000)}
function restoreSession(){
  try{
    var raw=localStorage.getItem('hv_session');if(!raw)return;
    var s=JSON.parse(raw);
    if(!s||!s.savedAt||Date.now()-s.savedAt>300000){localStorage.removeItem('hv_session');return}
    if(s.agentRunning){
      agentRunning=true;paused=s.paused||false;
      $('btnGo').style.display='none';$('btnPa').style.display='';$('btnSt').style.display='';
      var dot=$('dot');if(dot)dot.className='dot dot-on';
      var dl=$('dotLabel');if(dl){dl.textContent='ONLINE';dl.className='dot-label dot-label-on'}
    }
    if(s.liveLeads&&s.liveLeads.length)liveLeads=s.liveLeads;
    if(s.sessionLeadIds&&s.sessionLeadIds.length)s.sessionLeadIds.forEach(function(id){_sessionLeadIds.add(id)});
    if(s.logs&&s.logs.length)s.logs.forEach(function(e){if(e&&e.msg)addLog(e.msg,e.level)});
    if(s.progress)try{
      var d=s.progress;var pct=d.total?Math.round(100*d.current/d.total):0;
      var tpF=$('tpF');if(tpF)tpF.style.width=pct+'%';
      var tpPct=$('tpPct');if(tpPct)tpPct.textContent=pct+'%';
      var _tp=$('topProg');if(_tp)_tp.style.display='flex';
    }catch(e){}
  }catch(e){localStorage.removeItem('hv_session')}
}
function clearSession(){try{localStorage.removeItem('hv_session')}catch(e){}}

// ═══ CRM LOGIC ═══
function scoreCls(s,prefix){
  prefix=prefix||'sc-bg-';
  return s>=8?prefix+'hot':s>=6?prefix+'warm':s>=4?prefix+'mid':prefix+'low';
}

// Plain-English interpretation for each score dimension. Raw "7/10
// Buyability" told users nothing — now every bar gets a short adjective
// so the breakdown is readable without the rationale blob underneath.
var _scoreVerbs={
  fit:['Weak fit','Moderate fit','Good fit','Strong fit'],
  buyability:['No budget signal','Budget unclear','Budget likely','Budget strong'],
  reachability:['Unreachable','Hard to reach','Contactable','Easy to reach'],
  service_opportunity:['No gap','Minor opportunity','Clear opportunity','Perfect fit'],
  timing:['Cold','Watching','Timely','Act now']
};
function scoreLabel(dimKey,v){
  var arr=_scoreVerbs[dimKey]||_scoreVerbs.fit;
  var b=v>=8?3:v>=6?2:v>=4?1:0;
  return arr[b];
}
let ALL=[],filtered=[],activeSt='',liF='all',emF='all';
let liveLeads=[],agentRunning=false,paused=false;

// Warn user before closing/navigating away while a hunt is running.
// The agent lives in a server-side thread tied to the user session, but the
// SSE stream and progress UI depend on this tab staying open. Losing the tab
// doesn't kill the agent immediately but users have reported confusion — they
// close the tab, come back, and their hunt appears frozen. The banner + native
// beforeunload prompt together make the rule explicit.
window.addEventListener('beforeunload',function(e){
  if(!agentRunning)return;
  var msg='A hunt is running. Closing this tab will stop progress updates — keep Huntova open to watch it finish.';
  e.preventDefault();e.returnValue=msg;return msg;
});
var _sessionLeadIds=new Set();
let currentModalLead=null;

function renderStats(leads){
  const c={};Object.keys(SL).forEach(k=>c[k]=0);c._t=leads.length;
  leads.forEach(l=>{const s=l.email_status||'new';if(c[s]!==undefined)c[s]++});
  var _emailed=c.email_sent+c.followed_up+c.replied+c.meeting_booked+c.won;
  var _replied=c.replied+c.meeting_booked+c.won;
  const items=[{k:'',l:'ALL',n:c._t,cc:'cv-all',r:''},
    {k:'new',l:'NEW',n:c.new,cc:'cv-new',r:''},
    {k:'email_sent',l:'SENT',n:c.email_sent,cc:'cv-sent',r:c._t?Math.round(_emailed/c._t*100)+'% contacted':''},
    {k:'followed_up',l:'FOLLOW UP',n:c.followed_up,cc:'cv-followed',r:''},
    {k:'replied',l:'REPLIED',n:c.replied,cc:'cv-replied',r:_emailed?Math.round(_replied/_emailed*100)+'% reply rate':''},
    {k:'meeting_booked',l:'MEETING',n:c.meeting_booked,cc:'cv-meeting',r:_replied?Math.round((c.meeting_booked+c.won)/_replied*100)+'% converted':''},
    {k:'won',l:'WON',n:c.won,cc:'cv-won',r:c.meeting_booked+c.won?Math.round(c.won/Math.max(1,c.meeting_booked+c.won)*100)+'% closed':''},
    {k:'lost',l:'LOST',n:c.lost,cc:'cv-lost',r:''},{k:'ignored',l:'SKIPPED',n:c.ignored,cc:'cv-ignored',r:''}];
  $('crmStats').innerHTML=items.map(p=>`<div class="cs${activeSt===p.k?' on':''}" onclick="chipF('${p.k}')" data-tip="${p.r||'Click to filter'}"><div class="cv ${p.cc}">${p.n}</div><div class="cl">${p.l}</div>${p.r?'<div class="cr">'+p.r+'</div>':''}</div>`).join('');
}
function chipF(k){activeSt=activeSt===k?'':k;$('fSt').value=activeSt;applyFilters()}
function buildCountryOptions(leads){
  const countries=new Set(),cities=new Set();
  leads.forEach(l=>{if(l.country)countries.add(l.country);if(l.city)cities.add(l.city)});
  const pCo=$('fCo').value,pCi=$('fCi').value;
  $('fCo').innerHTML='<option value="">All Countries</option>'+[...countries].sort().map(c=>`<option${c===pCo?' selected':''}>${esc(c)}</option>`).join('');
  $('fCi').innerHTML='<option value="">All Cities</option>'+[...cities].sort().map(c=>`<option${c===pCi?' selected':''}>${esc(c)}</option>`).join('');
}

async function updateLead(lid,field,val){
  const body={lead_id:lid};body[field]=val;
  try{const r=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok&&d.lead){const i=ALL.findIndex(l=>l.lead_id===lid);if(i>=0){ALL[i]=d.lead}applyFilters();toast('✓ Updated')}
  else{toast('Update failed: '+(d.error||'server error'));applyFilters()}}
  catch(e){toast('Update failed — check your connection');applyFilters()}
}
async function saveNotes(lid){const ta=$('mn-'+lid);if(ta)await updateLead(lid,'notes',ta.value)}
var _undoTimer=null,_undoLid=null;
async function deleteLead(lid){
  const l=ALL.find(x=>x.lead_id===lid);
  var orgName=l?l.org_name||'Unknown':'Unknown';
  showMod('Delete Lead?','Delete '+orgName+'? You can undo within 10 seconds.',async()=>{
    try{
      // Soft delete first (archive) — verify server confirms before mutating UI
      var _dr=await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({lead_id:lid})});
      var _dd=await _dr.json();
      if(!_dr.ok||!_dd.ok){toast('Delete failed: '+(_dd.error||'server error'));return}
      ALL=ALL.filter(x=>x.lead_id!==lid);
      liveLeads=liveLeads.filter(x=>x.lead_id!==lid);
      applyFilters();
      if(currentModalLead&&currentModalLead.lead_id===lid)closeLeadModal();
      // Expire previous undo (can't undo two at once)
      if(_undoLid&&_undoLid!==lid){_undoLid=null}
      clearTimeout(_undoTimer);
      // Show undo toast
      _undoLid=lid;
      var t=$('toast');if(!t)return;
      t.textContent='';t.classList.add('toast-undo-active');var _txt=document.createTextNode(orgName+' deleted ');t.appendChild(_txt);var _btn=document.createElement('button');_btn.className='toast-undo';_btn.textContent='Undo';_btn.onclick=undoDelete;t.appendChild(_btn);
      t.classList.add('on');
      _undoTimer=setTimeout(function(){
        t.classList.remove('on','toast-undo-active');
        _undoLid=null;
      },10000);
    }catch(e){toast('Delete error: '+esc(e.message))}
  });
}
async function undoDelete(){
  if(!_undoLid)return;
  var _lid=_undoLid;
  _undoLid=null;
  clearTimeout(_undoTimer);
  var t=$('toast');if(t)t.classList.remove('on','toast-undo-active');
  try{
    var r=await fetch('/api/undo-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:_lid})});
    var d=await r.json();
    if(d.ok&&d.lead){ALL.unshift(d.lead);applyFilters();toast('Restored!')}
    else toast('Undo failed: '+(d.error||'unknown'));
  }catch(e){toast('Undo error')}
}
function stDotCls(s){
  return{new:'sd-new',email_sent:'sd-sent',followed_up:'sd-followed',
    replied:'sd-replied',meeting_booked:'sd-meeting',won:'sd-won',lost:'sd-lost',ignored:'sd-ignored'}[s]||'sd-new';
}
function toggleStMenu(btn){
  // Close any existing open menu
  var existing=document.querySelector('.st-menu-fixed');
  if(existing){existing.remove();return;}
  
  var menu=btn.nextElementSibling;
  if(!menu)return;
  
  // Clone menu content into a fixed-position overlay
  var fixed=document.createElement('div');
  fixed.className='st-menu-fixed';
  fixed.innerHTML=menu.innerHTML;
  
  // Position below the button
  var rect=btn.getBoundingClientRect();
  fixed.style.cssText='position:fixed;left:'+Math.max(4,rect.left-60)+'px;top:'+(rect.bottom+4)+'px;'+
    'min-width:150px;background:var(--s2);border:1px solid var(--bd2);'+
    'border-radius:8px;padding:4px;z-index:9000;box-shadow:0 16px 48px rgba(0,0,20,.5);'+
    'animation:stMenuIn .15s ease';
  
  document.body.appendChild(fixed);
  
  // Close on any outside click
  setTimeout(function(){
    document.addEventListener('click',function closer(e){
      if(!fixed.contains(e.target)){
        fixed.remove();
        document.removeEventListener('click',closer);
      }
    });
  },20);
}
async function chSt(ev,el,lid,val){
  ev.stopPropagation();
  // Close fixed menu immediately (visual feedback)
  var fm=document.querySelector('.st-menu-fixed');
  if(fm)fm.remove();
  // Server-confirmed update — no optimistic mutation
  await updateLead(lid,'email_status',val);
  // updateLead success path handles ALL[i]=d.lead, applyFilters(), toast
  // On failure, updateLead shows toast('Error') and ALL is unchanged — row stays as-is
  renderStats(ALL);
}

function _buildLeadRationale(l){
  // Build a compact "why this lead" explanation from real data
  var parts=[];
  // Top dimension
  var dims=[
    {k:'fit_score',l:'Strong fit'},
    {k:'timing_score',l:'Active timing'},
    {k:'service_opportunity_score',l:'Clear service gap'},
    {k:'buyability_score',l:'Reachable buyer'}
  ];
  dims.sort(function(a,b){return (l[b.k]||0)-(l[a.k]||0)});
  if((l[dims[0].k]||0)>=7)parts.push(dims[0].l+' ('+l[dims[0].k]+'/10)');
  if((l[dims[1].k]||0)>=6)parts.push(dims[1].l);
  // Evidence
  if(l.evidence_quote&&l._quote_verified&&l._quote_verified!=='unverified'&&l._quote_verified!=='missing'){
    parts.push('\u201c'+l.evidence_quote.substring(0,60)+'\u2026\u201d');
  }
  // Why now
  if((l.timing_score||0)>=7&&l.timing_rationale&&l.timing_rationale.length>15){
    parts.push(l.timing_rationale.substring(0,50));
  }
  // Profile match note
  if(_learningProfile&&_learningProfile.instruction_summary&&l._rank_score>=25){
    parts.push('Matches your preferences');
  }
  // Confidence caveat
  if(l._data_confidence<0.3)parts.push('Low confidence \u2014 verify before acting');
  return parts.length?parts.join(' \u00b7 '):'';
}

function renderRow(l,i,isFresh){
  const id=l.lead_id||'i'+i,s=l.fit_score||0,es=l.email_status||'new';
  const _eid=esc(id);
  const _bk=_bulkSelected.has(id)?' checked':'';
  var _scCls=scoreCls(s);
  var _isUnseen=isFresh||!_seenLeads.has(id);
  // Single urgency pill — precedence: overdue follow-up > aging stale lead > Urgent (ts>=8) > Timely (ts>=6).
  // Replaces the old three-red-signals-in-a-row mess (NOW pill + 'overdue' text + 'aging' text).
  var _tsRow=l.timing_score||0;
  var _nowMs=Date.now();
  var _fuOverdue=l.follow_up_date&&new Date(l.follow_up_date)<new Date();
  var _foundAging=(es==='new'&&_tsRow>=8&&l.found_date&&(_nowMs-new Date(l.found_date).getTime())>172800000);
  var _urgHtml='';
  if(_fuOverdue)_urgHtml='<span class="urg-pill urg-overdue" title="Follow-up date passed">⏰ Overdue</span>';
  else if(_foundAging)_urgHtml='<span class="urg-pill urg-aging" title="Strong timing signal is going stale — act soon">⏳ Aging</span>';
  else if(_tsRow>=8)_urgHtml='<span class="urg-pill urg-urgent" title="High timing score — act now">⚡ Urgent</span>';
  else if(_tsRow>=6)_urgHtml='<span class="urg-pill urg-timely" title="Timing looks right soon">⏱ Timely</span>';
  // Secondary meta line — date only; date-based warnings are now in the pill.
  var _metaBits=[];
  if(dAgo(l.found_date))_metaBits.push('found '+dAgo(l.found_date));
  if(l.follow_up_date&&!_fuOverdue)_metaBits.push('follow up '+dAgo(l.follow_up_date));
  var _metaHtml=_metaBits.length?_metaBits.join(' · '):'';
  return `<div class="row${_isUnseen?' fresh':''} st-${es}" id="r-${_eid}" role="row" tabindex="0" onclick="openLeadPage('${_eid}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openLeadPage('${_eid}')}"><div class="rm">
  <div class="rc"><input type="checkbox" class="bulk-cb" onclick="event.stopPropagation();toggleBulkOne('${_eid}',this.checked)"${_bk} aria-label="Select lead"><div class="sc ${_scCls}">${s}</div>${typeof l._data_confidence==='number'?'<span class="conf-dot" style="background:'+(l._data_confidence>=0.6?'var(--acc)':l._data_confidence>=0.4?'var(--org)':'var(--red)')+'" title="'+(l._data_confidence>=0.6?'High':l._data_confidence>=0.4?'Medium':'Low')+' confidence ('+(l._confidence_signals||0)+'/5 signals)"></span>':''}${l.smart_score&&l.smart_score!==s?`<span class="smart-badge">↑${l.smart_score}</span>`:''}</div>
  <div class="org-c"><div class="org-n">${esc(l.org_name||'Unknown')}</div><div class="org-w">${(function(){var _u=safeUrl(l.org_website);return _u?`<a href="${_u}" target="_blank" rel="noopener" onclick="event.stopPropagation()">website</a>`:''})()}</div></div>
  <div class="evt-c"><div class="evt-n">${_urgHtml?_urgHtml+' ':''}${esc(l.why_fit||l.event_name||'—')}</div>${_metaHtml?'<div class="evt-t">'+_metaHtml+'</div>':''}${(function(){var _r=_buildLeadRationale(l);return _r?'<div class="evt-rationale">'+esc(_r)+'</div>':''})()}</div>
  <div class="loc-c"><span class="loc-city">${esc(l.city||'')}</span>${l.city&&l.country?', ':''}${esc(l.country||'')}</div>

  <div><span class="tag ${l.is_recurring?'tag-r':''}">${l.is_recurring?'Ongoing':'Prospect'}</span>${_learningProfile&&_learningProfile.instruction_summary&&l._rank_score>=25?'<span class="tag tag-match" title="Matches your learned preferences">Match</span>':''}</div>
  <div class="st-wrap" onclick="event.stopPropagation()"><div class="st-btn st-btn-${es}" onclick="toggleStMenu(this)">${SL[es]||es}</div><div class="st-menu">${Object.entries(SL).map(([k,v])=>'<div class="st-opt" onclick="chSt(event,this,\''+_eid+'\',\''+k+'\')"><span class="st-dot '+stDotCls(k)+'"></span>'+v+'</div>').join('')}</div></div>
  <div class="acts"><button class="abtn" onclick="event.stopPropagation();openLeadPage('${_eid}')">▸</button><button class="abtn abtn-del" onclick="event.stopPropagation();deleteLead('${_eid}')" data-tip="Permanently delete and block this org">🗑</button></div>
</div></div>`;
}

function applyFilters(){
  const q=($('fQ').value||'').toLowerCase().trim();
  const st=activeSt||$('fSt').value,co=$('fCo').value,ci=$('fCi').value,so=$('fSo').value;
  filtered=ALL.filter(l=>{
    if(st&&(l.email_status||'new')!==st)return false;if(co&&l.country!==co)return false;if(ci&&l.city!==ci)return false;
    if(liF==='sent'&&l.linkedin_status!=='sent')return false;if(liF==='not_sent'&&l.linkedin_status==='sent')return false;
    if(emF==='yes'&&!l.contact_email)return false;if(emF==='no'&&l.contact_email)return false;
    if(q){const h=[l.org_name,l.why_fit,l.event_name,l.country,l.city,l.region,l.event_type,l.notes,l.platform_used,l.contact_name].join(' ').toLowerCase();if(!h.includes(q))return false}
    return true;
  });
  if(so==='sd')filtered.sort((a,b)=>(b.fit_score||0)-(a.fit_score||0));
  else if(so==='sa')filtered.sort((a,b)=>(a.fit_score||0)-(b.fit_score||0));
  else if(so==='nd')filtered.sort((a,b)=>(b.found_date||'').localeCompare(a.found_date||''));
  else if(so==='od')filtered.sort((a,b)=>(a.found_date||'').localeCompare(b.found_date||''));
  else if(so==='co')filtered.sort((a,b)=>(a.country||'').localeCompare(b.country||''));
  else if(so==='oa')filtered.sort((a,b)=>(a.org_name||'').localeCompare(b.org_name||''));
  else if(so==='oz')filtered.sort((a,b)=>(b.org_name||'').localeCompare(a.org_name||''));
  else if(so==='ea')filtered.sort((a,b)=>(b.contact_email?1:0)-(a.contact_email?1:0));
  else if(so==='ra')filtered.sort((a,b)=>(b.is_recurring?1:0)-(a.is_recurring?1:0));
  else if(so==='pr')filtered.sort((a,b)=>{var _pa=(b.priority_rank||0)||(b.fit_score||0)*2+(b.timing_score||0)*3+(b.buyability_score||0);var _pb=(a.priority_rank||0)||(a.fit_score||0)*2+(a.timing_score||0)*3+(a.buyability_score||0);return _pa-_pb});
  else if(so==='hot')filtered.sort((a,b)=>{var _ha=typeof b._rank_score==='number'?b._rank_score:(b.timing_score||0)*3+(b.fit_score||0)*2+(b.service_opportunity_score||0);var _hb=typeof a._rank_score==='number'?a._rank_score:(a.timing_score||0)*3+(a.fit_score||0)*2+(a.service_opportunity_score||0);return _ha-_hb});
  renderStats(ALL);$('cCnt').textContent=filtered.length;
  // Session badge — show count of leads discovered this session
  var _sb=$('sessionBadge'),_sc=$('sessionCnt');
  var _sessionCount=filtered.filter(function(l){return _sessionLeadIds.has(l.lead_id)}).length;
  if(_sb){if(_sessionCount>0&&agentRunning){_sb.style.display='';if(_sc)_sc.textContent=_sessionCount}else{_sb.style.display='none'}}
  $('allCnt').textContent=filtered.length+' lead'+(filtered.length!==1?'s':'');
  const rw=$('crmRows');
  if(!filtered.length){
    // Branch on whether we have ANY leads at all vs filter-scoped empty.
    // Different copy + CTA per branch so the user always knows what to do next.
    if(ALL.length){
      rw.innerHTML='<div class="empty"><h3>No leads match your filters</h3><p style="color:var(--t3);font-size:12px;margin-top:4px">Try clearing filters or change the status / country / search query above.</p><button class="crm-empty-btn" style="margin-top:10px" onclick="try{$(\'fRs\').click()}catch(_){}">Clear filters</button></div>';
    } else {
      rw.innerHTML='<div class="empty"><h3>Your pipeline is empty</h3><p style="color:var(--t3);font-size:12px;margin-top:4px;max-width:440px;margin-left:auto;margin-right:auto">Start a hunt and leads will stream in here as they\'re qualified. Typical hunt: 3–8 minutes, 5–15 leads.</p><button class="crm-empty-btn" style="margin-top:12px" onclick="openStartPopup()">Start Finding Leads</button></div>';
    }
    return;
  }
  rw.innerHTML=filtered.map(function(l,i){return renderRow(l,i,_sessionLeadIds.has(l.lead_id))}).join('');
}
var _searchTimer=null;
// Any filter change invalidates bulk selection — selected leads may no
// longer be in `filtered` and bulk actions would silently apply to rows
// the user can no longer see. Clear before re-rendering.
function _clearBulkOnFilterChange(){
  if(_bulkSelected&&_bulkSelected.size){
    try{clearBulkSelection()}catch(_){_bulkSelected.clear()}
  }
}
function _applyFiltersFresh(){_clearBulkOnFilterChange();applyFilters()}
// Debounce all filter inputs (not just the search box) so rapid
// dropdown changes don't rebuild 500+ DOM rows on each change.
var _filterChangeTimer=null;
function _debouncedFilter(){clearTimeout(_filterChangeTimer);_filterChangeTimer=setTimeout(_applyFiltersFresh,120)}
try{$('fQ').addEventListener('input',function(){clearTimeout(_searchTimer);_searchTimer=setTimeout(_applyFiltersFresh,150)});
$('fSt').addEventListener('change',()=>{activeSt=$('fSt').value;_debouncedFilter()});}catch(_e){}
try{$('fCo').addEventListener('change',_debouncedFilter);$('fCi').addEventListener('change',_debouncedFilter);$('fSo').addEventListener('change',_debouncedFilter);
$('fLi').addEventListener('click',function(){if(liF==='all'){liF='sent';this.textContent='LinkedIn: Sent';this.classList.add('on')}else if(liF==='sent'){liF='not_sent';this.textContent='LinkedIn: Not Sent';this.classList.remove('on')}else{liF='all';this.textContent='LinkedIn: All';this.classList.remove('on')}_applyFiltersFresh()});
$('fEm').addEventListener('click',function(){if(emF==='all'){emF='yes';this.textContent='Email: Has Email';this.classList.add('on')}else if(emF==='yes'){emF='no';this.textContent='Email: No Email';this.classList.remove('on')}else{emF='all';this.textContent='Email: All';this.classList.remove('on')}_applyFiltersFresh()});
$('fRs').addEventListener('click',()=>{$('fQ').value='';$('fSt').value='';$('fCo').value='';$('fCi').value='';$('fSo').value='hot';liF='all';emF='all';activeSt='';$('fLi').textContent='LinkedIn: All';$('fLi').classList.remove('on');$('fEm').textContent='Email: All';$('fEm').classList.remove('on');_applyFiltersFresh()});}catch(_e){}
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT')return;if(e.key==='/'&&!document.querySelector('.lead-modal-bg.on,.modal-bg.on,.settings-modal.on,.start-bg.on,.iwiz.on')){e.preventDefault();$('fQ').focus()}});

// ═══ AGENT SSE ═══

let evtSrc=null;const agLeads=[];var _sseRetry=1000;var _leadRenderTimer=null;var _sseConnecting=false;
const icons={info:'🔍',ok:'✅',warn:'⚠️',lead:'🎯',skip:'⏭️',fetch:'🌐',ai:'🧠',save:'💾',error:'❌'};
const moodEmoji={thinking:'🤔',search:'🔍',fetch:'📡',excited:'🎉',happy:'😊',skip:'💨',boot:'⚡',ready:'🚀',idle:'😴',done:'🏆'};

// ═══ HUNTS PAGE STATE ═══
var _huntHotCount=0;
var _huntLeadCount=0;
var _huntElapsedTimer=null;
var _huntStartTime=null;
var _huntStages=['initializing','briefing','planning','sourcing','scoring','live_results'];
var _huntMicrocopy={
  initializing:['Creating hunt workspace','This usually takes 5\u201315 seconds'],
  briefing:['Reading your target profile','Better targeting now means fewer junk leads later'],
  planning:['Designing the search strategy','Building queries from your business profile'],
  sourcing:['Checking the first sources','Scanning company sites, directories, and listings'],
  scoring:['Ranking the strongest matches','Scoring prospects across 5 dimensions'],
  live_results:['Leads are coming in','You\u2019ll see results as they\u2019re qualified']
};

function huntShowState(state){
  var idle=$('huntIdle'),active=$('huntActive'),done=$('huntDone');
  if(idle)idle.style.display=state==='idle'?'':'none';
  if(active)active.style.display=state==='active'?'':'none';
  if(done)done.style.display=state==='done'?'':'none';
  // Start/stop elapsed timer
  if(state==='active'&&!_huntElapsedTimer){
    _huntStartTime=Date.now();
    _huntElapsedTimer=setInterval(_updateElapsed,1000);
    _updateElapsed();
  }
  if(state!=='active'&&_huntElapsedTimer){
    clearInterval(_huntElapsedTimer);_huntElapsedTimer=null;
  }
}

function _updateElapsed(){
  var el=$('huntElapsed');if(!el||!_huntStartTime)return;
  var s=Math.floor((Date.now()-_huntStartTime)/1000);
  var m=Math.floor(s/60);s=s%60;
  el.textContent=m+':'+(s<10?'0':'')+s;
}

function huntSetStage(stage){
  var tl=$('huntTimeline');if(!tl)return;
  var dots=tl.querySelectorAll('.hunt-stage');
  var lines=tl.querySelectorAll('.hunt-stage-line');
  var idx=_huntStages.indexOf(stage);
  if(idx<0)return;
  dots.forEach(function(el,i){
    el.classList.remove('hunt-stage-active','hunt-stage-done');
    if(i<idx)el.classList.add('hunt-stage-done');
    else if(i===idx)el.classList.add('hunt-stage-active');
  });
  lines.forEach(function(el,i){
    el.classList.remove('hunt-line-done');
    if(i<idx)el.classList.add('hunt-line-done');
  });
  // Update microcopy
  var mc=_huntMicrocopy[stage];
  if(mc){
    var ct=$('huntCurrentText');if(ct)ct.textContent=mc[0];
    var cs=$('huntCurrentSub');if(cs)cs.textContent=mc[1];
  }
  // Replace skeleton with artifact on planning stage
  if(stage==='planning'||stage==='sourcing'||stage==='scoring'||stage==='live_results'){
    _huntShowArtifact(stage);
  }
}

function _huntShowArtifact(stage){
  var art=$('huntArtifact');if(!art)return;
  if(stage==='planning'){
    art.className='hunt-artifact hunt-artifact-visible';
    art.textContent='';
    var title=document.createElement('div');title.className='hunt-artifact-title';title.textContent='Search Plan';
    var body=document.createElement('div');body.className='hunt-artifact-body';body.textContent='Generating targeted queries from your business profile\u2026';
    art.appendChild(title);art.appendChild(body);
  } else if(stage==='sourcing'){
    art.className='hunt-artifact hunt-artifact-visible';
    art.textContent='';
    var title2=document.createElement('div');title2.className='hunt-artifact-title';title2.textContent='Source Sweep';
    var body2=document.createElement('div');body2.className='hunt-artifact-body';body2.textContent='Checking company websites, directories, and listings\u2026';
    art.appendChild(title2);art.appendChild(body2);
  } else if(stage==='live_results'){
    art.style.display='none'; // hide artifact once real leads show
  }
}

function huntSetCurrent(text){
  var el=$('huntCurrentText');if(el)el.textContent=text;
  var sub=$('huntCurrentSub');if(sub)sub.textContent='';
}

// ── Hunt narration: marketing-friendly activity ticker ──
// Dramatises events the backend already emits (thoughts, browsing_state,
// lead events, drafting progress). All strings are derived from real data —
// no fabricated progress or fake logs. The ticker sits in the existing
// .hunt-current surface; a fade class is toggled so text changes feel alive.
var _huntNarr={lastOrg:'',lastDomain:'',lastQuery:'',draftingN:0,draftingTotal:0,lastNarrTs:0,lastKey:''};
function _huntFade(text,sub){
  var el=$('huntCurrentText');var se=$('huntCurrentSub');var wrap=$('huntCurrent');
  if(!el)return;
  if(el.textContent===text&&(!se||se.textContent===(sub||'')))return;
  if(wrap)wrap.classList.remove('hunt-narr-in');
  el.textContent=text;
  if(se)se.textContent=sub||'';
  if(wrap){void wrap.offsetWidth;wrap.classList.add('hunt-narr-in')}
}
function huntNarrate(icon,text,sub,key){
  // Throttle to avoid flicker when many events fire in the same tick.
  // Case-insensitive dedup so "search:Foo" and "search:foo" don't both
  // narrate within the 350ms window when the agent emits queries with
  // varied casing.
  var now=Date.now();
  var k=(key||'').toLowerCase();
  if(k && _huntNarr.lastKey===k && (now-_huntNarr.lastNarrTs)<350)return;
  _huntNarr.lastKey=k;_huntNarr.lastNarrTs=now;
  _huntFade((icon?icon+' ':'')+text,sub||'');
}
function _domainFromUrl(u){try{return new URL(u).hostname.replace(/^www\./,'')}catch(_){return''}}

// ── Hot lead hero card ──
var _huntBestLead=null;

function huntCheckBestLead(lead){
  var sc=lead.fit_score||0;
  if(sc<7)return; // only show leads worth acting on
  if(_huntBestLead&&(_huntBestLead.fit_score||0)>=sc)return; // already have a better one
  _huntBestLead=lead;
  var hero=$('huntHero');if(!hero)return;
  hero.style.display='';
  // Score
  var scoreEl=$('huntHeroScore');
  if(scoreEl){scoreEl.textContent=sc;scoreEl.className='hunt-hero-score '+scoreCls(sc,'hunt-hero-sc-')}
  // Name
  var nameEl=$('huntHeroName');if(nameEl)nameEl.textContent=lead.org_name||'Unknown';
  // Why now
  var whyEl=$('huntHeroWhy');if(whyEl)whyEl.textContent=lead.why_fit||lead.event_name||'';
  // Evidence quote
  var evEl=$('huntHeroEvidence');
  if(evEl){
    if(lead.evidence_quote){evEl.textContent='\u201c'+lead.evidence_quote.substring(0,150)+'\u201d';evEl.style.display=''}
    else{evEl.style.display='none'}
  }
  // Meta line: confidence + email + country
  var metaEl=$('huntHeroMeta');
  if(metaEl){
    var parts=[];
    if(typeof lead._data_confidence==='number')parts.push((lead._data_confidence>=0.6?'High':lead._data_confidence>=0.4?'Medium':'Low')+' confidence');
    if(lead.contact_email)parts.push('Email found');
    else parts.push('No email yet');
    if(lead.country)parts.push(lead.country);
    metaEl.textContent=parts.join(' \u00b7 ');
  }
}

function huntCopyDraft(){
  if(!_huntBestLead)return;
  var text='';
  if(_huntBestLead.email_subject)text+='Subject: '+_huntBestLead.email_subject+'\n\n';
  if(_huntBestLead.email_body)text+=_huntBestLead.email_body;
  if(!text){toast('No email draft available');return}
  navigator.clipboard.writeText(text).then(function(){toast('Email draft copied');_trackAction(_huntBestLead.lead_id,'copy_hunt_hero_email')}).catch(function(){toast('Copy failed')});
}

function huntGenerateFollowups(){
  if(!_huntBestLead)return;
  openLeadPage(_huntBestLead.lead_id);
  // The lead detail page already shows follow-up emails 2-4 if they exist
  toast('Open the lead to see follow-up drafts');
}

function huntHeroFeedback(signal,btn){
  if(!_huntBestLead||!btn)return;
  btn.disabled=true;btn.textContent=signal==='good'?'\u2713 Good Fit':'\u2713 Bad Fit';
  fetch('/api/lead-feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lead_id:_huntBestLead.lead_id,signal:signal})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok)toast('Feedback saved');
  }).catch(function(){btn.disabled=false;btn.textContent=signal==='good'?'Good Fit':'Bad Fit';toast('Feedback failed')});
}

function huntAddLead(lead){
  var body=$('huntFeedBody');if(!body)return;
  var sc=lead.fit_score||0;
  var scCls=scoreCls(sc,'hunt-lead-');
  var row=document.createElement('div');
  row.className='hunt-lead-row '+scCls;
  row.onclick=function(){openLeadPage(lead.lead_id)};
  // Build row with DOM methods — score
  var scoreDiv=document.createElement('div');
  scoreDiv.className='hunt-lead-score';
  scoreDiv.textContent=sc;
  row.appendChild(scoreDiv);
  // Info
  var infoDiv=document.createElement('div');
  infoDiv.className='hunt-lead-info';
  var nameDiv=document.createElement('div');
  nameDiv.className='hunt-lead-name';
  nameDiv.textContent=lead.org_name||'Unknown';
  var whyDiv=document.createElement('div');
  whyDiv.className='hunt-lead-why';
  whyDiv.textContent=(lead.why_fit||'').substring(0,100);
  infoDiv.appendChild(nameDiv);
  infoDiv.appendChild(whyDiv);
  row.appendChild(infoDiv);
  // Meta
  var metaDiv=document.createElement('div');
  metaDiv.className='hunt-lead-meta';
  if(lead.contact_email){
    var emailSpan=document.createElement('span');
    emailSpan.className='hunt-lead-email';
    emailSpan.textContent='Email found';
    metaDiv.appendChild(emailSpan);
  }
  var locSpan=document.createElement('span');
  locSpan.className='hunt-lead-loc';
  locSpan.textContent=lead.country||'';
  metaDiv.appendChild(locSpan);
  row.appendChild(metaDiv);
  body.insertBefore(row,body.firstChild);
  while(body.children.length>50)body.removeChild(body.lastChild);
  _huntLeadCount++;
  var fc=$('huntFeedCount');if(fc)fc.textContent=_huntLeadCount;
  var hf=$('huntLeadsFound');if(hf)hf.textContent=_huntLeadCount;
  if(sc>=8){
    _huntHotCount++;
    var hh=$('huntHotLeads');if(hh)hh.textContent=_huntHotCount;
  }
}

function huntUpdateProgress(d){
  var pc=$('huntPagesChecked');if(pc)pc.textContent=d.urls||0;
  var qd=$('huntQueriesDone');if(qd)qd.textContent=(d.current||0)+'/'+(d.total||0);
  // Map progress to new stages
  if(d.current>0&&d.current<=1) huntSetStage('sourcing');
  else if(d.current>1) huntSetStage('scoring');
  if(d.leads>0) huntSetStage('live_results');
}

function huntShowDone(leadsCount,hotCount){
  huntShowState('done');
  var sum=$('huntDoneSummary');
  if(sum)sum.textContent='Found '+leadsCount+' qualified lead'+(leadsCount!==1?'s':'')+', including '+hotCount+' hot lead'+(hotCount!==1?'s':'')+'.';
  var stats=$('huntDoneStats');
  if(stats){
    stats.textContent='';
    var s1=document.createElement('div');s1.className='hunt-done-stat';
    var v1=document.createElement('span');v1.className='hunt-done-stat-val';v1.textContent=leadsCount;
    var l1=document.createElement('span');l1.className='hunt-done-stat-label';l1.textContent='Total Leads';
    s1.appendChild(v1);s1.appendChild(l1);stats.appendChild(s1);
    var s2=document.createElement('div');s2.className='hunt-done-stat';
    var v2=document.createElement('span');v2.className='hunt-done-stat-val hunt-done-hot';v2.textContent=hotCount;
    var l2=document.createElement('span');l2.className='hunt-done-stat-label';l2.textContent='Hot Leads';
    s2.appendChild(v2);s2.appendChild(l2);stats.appendChild(s2);
  }
}

function huntReset(){
  _huntHotCount=0;_huntLeadCount=0;_huntStartTime=Date.now();_huntBestLead=null;
  _huntNarr={lastOrg:'',lastDomain:'',lastQuery:'',draftingN:0,draftingTotal:0,lastNarrTs:0,lastKey:''};
  var hero=$('huntHero');if(hero)hero.style.display='none';
  var fb=$('huntFeedBody');if(fb)fb.textContent='';
  var fc=$('huntFeedCount');if(fc)fc.textContent='0';
  var hf=$('huntLeadsFound');if(hf)hf.textContent='0';
  var hh=$('huntHotLeads');if(hh)hh.textContent='0';
  var pc=$('huntPagesChecked');if(pc)pc.textContent='0';
  var cu=$('huntCreditsUsed');if(cu)cu.textContent='0';
  var qd=$('huntQueriesDone');if(qd)qd.textContent='0/0';
  var el=$('huntElapsed');if(el)el.textContent='0:00';
  // Reset artifact to skeleton
  var art=$('huntArtifact');if(art){art.className='hunt-artifact';art.innerHTML='<div class="hunt-artifact-skeleton"><div class="hunt-skel-line hunt-skel-w60"></div><div class="hunt-skel-line hunt-skel-w80"></div><div class="hunt-skel-line hunt-skel-w40"></div></div>'}
  huntSetStage('initializing');
}

function connectSSE(){
  if(_sseConnecting)return;
  if(evtSrc)evtSrc.close();
  _sseConnecting=true;
  // Auth preflight: check session is valid before opening SSE (EventSource can't read 401)
  fetch('/api/status').then(function(r){if(r.status===401){_sseConnecting=false;window.location.href='/landing';return}
    try{evtSrc=new EventSource('/agent/events');_sseConnecting=false}catch(e){_sseConnecting=false;_sseRetry=Math.min(_sseRetry*2,30000);setTimeout(connectSSE,_sseRetry);return}
  evtSrc.addEventListener('log',e=>{try{const d=JSON.parse(e.data);addLog(d.msg,d.level);
    var m=d.msg||'';
    // Map internal messages to hunt stages
    if(m.indexOf('Loading hunt brain')>=0||m.indexOf('Hunt memory loaded')>=0)huntSetStage('briefing');
    else if(m.indexOf('Building hunting strategy')>=0||m.indexOf('Generating search queries')>=0||m.indexOf('Refining strategy')>=0)huntSetStage('planning');
    else if(m.indexOf('Query 1/')>=0||m.indexOf('Checking the first')>=0)huntSetStage('sourcing');
    // Fetch-level logs — narrate the domain if a URL is present.
    if(d.level==='fetch'){
      var _cleaned=m.replace(/^(Fetching|Crawling|Visiting)\s*/i,'').trim();
      var _dom=_domainFromUrl(_cleaned)||_cleaned.substring(0,60);
      huntNarrate('🌐','Checking '+_dom,_huntNarr.lastQuery?'from "'+_huntNarr.lastQuery+'"':'','fetch:'+_dom);
    }
    else if(d.level==='ai'){
      var _who=_huntNarr.lastOrg||_huntNarr.lastDomain||'prospect';
      huntNarrate('🧠','Analysing '+_who,'Gemini is scoring it across 5 dimensions','ai:'+_who);
    }
    else if(d.level==='info'&&m.indexOf('Query')===0){
      huntNarrate('🔎',m.substring(0,80),'','q:'+m);
    }
  }catch(_){}});
  evtSrc.addEventListener('thought',e=>{try{
    const d=JSON.parse(e.data);addThought(d.msg,d.mood);updateNeoBubble(d.msg);$('neoStatus').textContent=d.mood||'thinking';
    // Surface structured thoughts on the hunt screen so the wait feels alive.
    // Parse the real strings the backend emits rather than inventing fake ones.
    var msg=d.msg||'';
    var mSearch=msg.match(/^Searching:\s*(.+)$/i);
    var mDraft=msg.match(/Drafting email\s+(\d+)\s*\/\s*(\d+)\s*[\u2014\-\u2013]*\s*(.+)?/i);
    var mGoodLead=msg.match(/(?:Good lead|New lead found|Hunting for contact)/i);
    if(mSearch){
      _huntNarr.lastQuery=mSearch[1].trim().substring(0,80);
      huntNarrate('🔎','Searching for "'+_huntNarr.lastQuery+'"','','search:'+_huntNarr.lastQuery);
    } else if(mDraft){
      _huntNarr.draftingN=parseInt(mDraft[1],10);
      _huntNarr.draftingTotal=parseInt(mDraft[2],10);
      var _dorg=(mDraft[3]||'').trim()||_huntNarr.lastOrg||'lead';
      huntNarrate('✍️','Drafting email '+_huntNarr.draftingN+'/'+_huntNarr.draftingTotal+' — '+_dorg,'Personalising based on their site','draft:'+_huntNarr.draftingN);
    } else if(mGoodLead){
      var _org2=_huntNarr.lastOrg||'this prospect';
      huntNarrate('📬','Hunting contact email for '+_org2,'Crawling team and contact pages','enr:'+_org2);
    } else if(d.mood==='excited'||d.mood==='happy'){
      // Surface mood-stamped highlights sparingly (rate-limited by the throttle).
      huntNarrate(moodEmoji[d.mood]||'✨',msg.substring(0,80),'','mood:'+msg);
    }
  }catch(_){}});
  evtSrc.addEventListener('progress',e=>{try{
    const d=JSON.parse(e.data);
    const pct=d.total?Math.round(100*d.current/d.total):0;
    // ETA formatting
    // Update topbar progress
    var tpF=$('tpF');if(tpF)tpF.style.width=pct+'%';
    var tpPct=$('tpPct');if(tpPct)tpPct.textContent=pct+'%';
    var tpDet=$('tpDetail');if(tpDet)tpDet.textContent=(d.current||0)+'/'+( d.total||0)+'q';
    var _tp=$('topProg');if(_tp)_tp.style.display='flex';
    // Update old elements safely
    try{$('pF').style.width=pct+'%'}catch(_){}
    try{$('pPct').textContent=pct+'%'}catch(_){}
    try{$('pP').textContent=(d.current||0)+' / '+(d.total||0)+' queries'}catch(_){}
    try{if($('ntsPct'))$('ntsPct').textContent=pct+'%'}catch(_){}
    try{if($('ntsProg'))$('ntsProg').style.width=pct+'%'}catch(_){}
    // Stats
    try{$('xQ').textContent=d.current||0}catch(_){}
    try{$('xU').textContent=d.urls||0}catch(_){}
    try{$('xL').textContent=d.leads||0}catch(_){}
    if($('tcL'))$('tcL').textContent=d.leads||0;
    try{$('xS').textContent=d.skipped||0}catch(_){}
    try{$('xE').textContent=d.with_email||0}catch(_){}
    try{$('xR').textContent=d.recurring||0}catch(_){}
    $('aLC').textContent=d.leads||0;
    try{$('tcU').textContent=d.urls||0}catch(_){}
    try{$('tcE').textContent=d.with_email||0}catch(_){}
    _lastProgress=d;debounceSaveSession();
    huntUpdateProgress(d);
  }catch(_){}});
  evtSrc.addEventListener('screenshot',function(e){
    try{
      var d=JSON.parse(e.data);if(!d.img)return;
      if(!window._lvCount)window._lvCount=0;window._lvCount++;
      var img=$('lvImg'),empty=$('lvEmpty'),url=$('lvUrl'),ts=$('lvTs'),dot=$('lvDot'),ctr=$('lvCounter');
      if(img){img.onload=function(){img.style.opacity='1'};img.style.opacity='.5';img.src='data:image/jpeg;base64,'+d.img;img.style.display='block'}
      if(empty)empty.style.display='none';
      if(url)url.textContent=d.url||'Browsing...';
      if(ts)ts.textContent=d.ts||'';
      if(dot){dot.classList.add('on');clearTimeout(dot._t);dot._t=setTimeout(function(){dot.classList.remove('on')},3000)}
      if(ctr){ctr.textContent=window._lvCount+' page'+(window._lvCount!==1?'s':'')+' viewed';ctr.style.display='block'}
    }catch(err){}
  });
  // Browsing state fallback — shows structured card when no screenshot available
  evtSrc.addEventListener('browsing_state',function(e){
    try{
      var d=JSON.parse(e.data);
      var urlBar=$('lvUrl'),dot=$('lvDot');
      if(urlBar)urlBar.textContent=d.url||'';
      if(dot){dot.classList.add('on');clearTimeout(dot._t);dot._t=setTimeout(function(){dot.classList.remove('on')},3000)}
      // Narrate domain + phase, using real status from the backend.
      var _bsDomain=_domainFromUrl(d.url||'');
      if(_bsDomain)_huntNarr.lastDomain=_bsDomain;
      var _st=d.status||'loading';
      if(_st==='loading'&&_bsDomain) huntNarrate('🔍','Scanning '+_bsDomain,_huntNarr.lastQuery?'from "'+_huntNarr.lastQuery+'"':'looking for buying intent','bs:load:'+_bsDomain);
      else if(_st==='analysing') huntNarrate('🧠','Analysing '+(_bsDomain||'prospect'),'Scoring fit, timing, budget, reach','bs:an:'+_bsDomain);
      else if(_st==='scored') huntNarrate('✓','Scored '+(_bsDomain||'prospect'),'','bs:sc:'+_bsDomain);
      else if(_st==='enriching') huntNarrate('📬','Hunting contact email — '+(_huntNarr.lastOrg||_bsDomain),'Crawling contact + team pages','bs:enr:'+_bsDomain);
      else if(_st==='skipped') huntNarrate('💨','Skipping '+(_bsDomain||'page'),'Not a fit — moving on','bs:sk:'+_bsDomain);
      // Show live preview card
      var preview=$('lvPreview'),empty=$('lvEmpty');
      if(preview&&d.url){
        if(empty)empty.style.display='none';
        preview.style.display='flex';
        var domain='';try{domain=new URL(d.url).hostname.replace('www.','')}catch(_){}
        var fav=$('lvFavicon');if(fav&&domain)fav.src='https://www.google.com/s2/favicons?domain='+domain+'&sz=32';
        var domEl=$('lvDomain');if(domEl)domEl.textContent=domain;
        var titleEl=$('lvTitle');if(titleEl)titleEl.textContent=d.title||d.url;
        var statusEl=$('lvStatus');
        if(statusEl){
          var st=d.status||'loading';
          var labels={loading:'Fetching...',analysing:'Analysing...',scored:'✓ Scored',skipped:'✗ Skipped'};
          var cls={loading:'lv-status-fetching',analysing:'lv-status-analysing',scored:'lv-status-scored',skipped:'lv-status-skipped'};
          statusEl.textContent=labels[st]||st;
          statusEl.className='lv-preview-status '+(cls[st]||'');
        }
      }
    }catch(_){}
  });
  evtSrc.addEventListener('lead',e=>{try{const l=JSON.parse(e.data);
    ['contact_email','contact_linkedin','org_linkedin','org_website'].forEach(function(f){
      var v=l[f];if(!v||typeof v!=='string')l[f]=null;
      else{v=v.trim();if(!v||v.length<3||v==='null'||v==='none'||v==='N/A')l[f]=null;else l[f]=v;}
    });
    agLeads.push(l);
    liveLeads.unshift(l);if(liveLeads.length>20)liveLeads.pop();debounceSaveSession();
    _sessionLeadIds.add(l.lead_id);
    huntAddLead(l);huntSetStage('live_results');
    huntCheckBestLead(l);
    // Narrate qualified leads — real event, real data.
    _huntNarr.lastOrg=l.org_name||_huntNarr.lastOrg;
    var _qsc=l.fit_score||0;
    huntNarrate('🎯','Qualified '+(l.org_name||'a prospect')+' — fit '+_qsc+'/10',l.why_fit?(''+l.why_fit).substring(0,90):'','lead:'+l.lead_id);
    if(!ALL.find(x=>x.lead_id===l.lead_id))ALL.unshift(l);
    // Debounce CRM re-render: batch leads instead of rebuilding on every single one
    clearTimeout(_leadRenderTimer);
    _leadRenderTimer=setTimeout(function(){applyFilters()},800);
  }catch(_){}});
  evtSrc.addEventListener('status',e=>{try{
    const d=JSON.parse(e.data);$('topSt').textContent=d.text;const dot=$('dot');const dl=$('dotLabel');
    if(d.state==='running'){
      /* Fresh start: clear previous run data on non-running → running transition */
      if(!agentRunning){agLeads.length=0;_sessionLeadIds.clear();var _atb=$('aLTb');if(_atb)_atb.textContent='';var _sd=$('sDist');if(_sd)_sd.textContent='';var _cd=$('cDist');if(_cd)_cd.textContent='';
        huntReset();huntShowState('active');
      }
      $('btnGo').style.display='none';
      $('btnPa').style.display='';$('btnSt').style.display='';agentRunning=true;paused=false;$('btnPa').textContent='⏸ PAUSE';$('btnPa').style.color='var(--org)';
      // Hunt page controls
      var hpb=$('huntPauseBtn');if(hpb){hpb.style.display='';hpb.textContent='Pause';}
      var hsb=$('huntStopBtn');if(hsb)hsb.style.display='';
      if(dot){dot.className='dot dot-on'}if(dl){dl.textContent='ONLINE';dl.className='dot-label dot-label-on'}}
    else if(d.state==='paused'){$('btnPa').textContent='▶ RESUME';$('btnPa').style.color='var(--acc)';$('btnPa').style.background='rgba(61,155,143,.06)';$('btnPa').style.borderColor='rgba(61,155,143,.16)';paused=true;
      var hpb2=$('huntPauseBtn');if(hpb2)hpb2.textContent='Resume';
      huntSetCurrent('Hunt paused');
      if(dot){dot.className='dot dot-pause'}if(dl){dl.textContent='PAUSED';dl.className='dot-label dot-label-pause'}}
    else{$('btnGo').style.display='';$('btnPa').style.display='none';$('btnSt').style.display='none';agentRunning=false;paused=false;
      var _tp=$('topProg');if(_tp)_tp.style.display='none';
      var _lp=$('lvPreview');if(_lp)_lp.style.display='none';
      var _le=$('lvEmpty');if(_le)_le.style.display='flex';
      if(dot){dot.className='dot dot-off'}if(dl){dl.textContent='OFFLINE';dl.className='dot-label dot-label-off'}
      if(d.state==='done'||d.state==='stopped'){liveLeads=[];clearSession();loadCRM();hvLoadAccount();
        huntShowDone(_huntLeadCount,_huntHotCount);
        try{if(typeof showResultsSummary==='function')showResultsSummary(ALL)}catch(_e){}}
      else{huntShowState('idle');}}
    saveSession();
  }catch(_){}});
  evtSrc.addEventListener('crm_refresh',()=>{try{loadCRM()}catch(_){}});
  evtSrc.addEventListener('credits_exhausted',()=>{
    if(!(_hvRuntime && _hvRuntime.billing_enabled)) return;
    hvLoadAccount();hvTokenPopup('empty');
  });
  evtSrc.addEventListener('research_progress',e=>{try{
    const d=JSON.parse(e.data);const prog=$('rp-'+d.lead_id);
    if(prog){prog.innerHTML='<span class="research-spinner"></span>'+esc(d.step);prog.classList.add('on')}}catch(_){}});
  evtSrc.addEventListener('research_done',e=>{try{
    const d=JSON.parse(e.data);showResearchResult(d.lead_id,d.results)}catch(_){}});
  /* rewrite_done SSE removed — backend never emits it, rewrite uses sync HTTP response */

  evtSrc.addEventListener('scan_report',function(e){try{
    var d=JSON.parse(e.data);var msg='Scan complete! '+d.total+' leads found';
    if(d.hot)msg+=', '+d.hot+' hot';toast(msg);
  }catch(_){}});
  // DNA regeneration terminal event. Backend fires these async, and without
  // this listener the UI had no way to know when the profile actually updated
  // (or failed). Surface a truthful toast so users trust the training loop.
  evtSrc.addEventListener('dna_updated',function(e){try{
    var d=JSON.parse(e.data);
    if(d.ok){
      var base=d.trigger==='feedback_refine'?'Hunt profile refined from your feedback':'Hunt profile generated';
      toast('✓ '+base+' — v'+(d.version||'?')+' ('+(d.queries_count||0)+' queries)');
    } else {
      toast('Hunt profile update failed — '+(d.error||'unknown error'));
    }
  }catch(_){}});

    evtSrc.onopen=function(){_sseRetry=1000};
    evtSrc.onerror=()=>{evtSrc.close();evtSrc=null;_sseRetry=Math.min(_sseRetry*2,30000);setTimeout(connectSSE,_sseRetry)};
  }).catch(function(){_sseConnecting=false;_sseRetry=Math.min(_sseRetry*2,30000);setTimeout(connectSSE,_sseRetry)});
}

// Fallback: poll status every 5s in case SSE drops (LAN stability)
// Also runs once immediately on page load so a refresh mid-hunt rehydrates
// counters from /api/status rather than showing 0 for up to 5s.
function _applyProgressSnapshot(p){
  if(!p)return;
  try{
    var pct=p.total?Math.round(100*p.current/p.total):0;
    var tpF=$('tpF');if(tpF)tpF.style.width=pct+'%';
    var tpPct=$('tpPct');if(tpPct)tpPct.textContent=pct+'%';
    var tpDet=$('tpDetail');if(tpDet)tpDet.textContent=(p.current||0)+'/'+(p.total||0)+'q';
    try{$('xQ').textContent=p.current||0}catch(_){}
    try{$('xU').textContent=p.urls||0}catch(_){}
    try{$('xL').textContent=p.leads||0}catch(_){}
    try{$('xS').textContent=p.skipped||0}catch(_){}
    try{$('xE').textContent=p.with_email||0}catch(_){}
    try{$('xR').textContent=p.recurring||0}catch(_){}
    try{$('aLC').textContent=p.leads||0}catch(_){}
    try{$('tcL').textContent=p.leads||0}catch(_){}
    try{if(typeof huntUpdateProgress==='function')huntUpdateProgress(p)}catch(_){}
  }catch(_){}
}
async function _pollStatus(){
  try{
    const r=await fetch('/api/status');if(r.status===401)return;const d=await r.json();
    if(d.running){
      if(!agentRunning){$('btnGo').style.display='none';$('btnPa').style.display='';$('btnSt').style.display='';agentRunning=true;
        huntShowState('active');
      }
      // Resync live counters from /api/status in case SSE dropped events
      if(d.progress)_applyProgressSnapshot(d.progress);
      if(typeof d.lead_count==='number'){try{$('tcL').textContent=d.lead_count}catch(_){}}
      // Persist "Stopping…" state across page reloads / tab switches
      if(d.stopping){
        var _btnStp=$('btnSt');if(_btnStp)_btnStp.disabled=true;
        try{huntSetCurrent('Stopping…')}catch(_){}
      }
    }else{
      if(agentRunning){$('btnGo').style.display='';$('btnPa').style.display='none';var _bsp=$('btnSt');if(_bsp){_bsp.style.display='none';_bsp.disabled=false}agentRunning=false;loadCRM();
        huntShowDone(_huntLeadCount,_huntHotCount);
      }
    }
  }catch(e){}
}
var _pollStatusTimer=setInterval(_pollStatus,5000);
_pollStatus();

var _skipCount=0,_lastSkipTimer=null;
// Translate internal telemetry to customer-friendly messages
var _logRewrites=[
  [/^WAL: Replayed \d+ operations?.*/,'Resuming from last checkpoint'],
  [/^History: \d+ URLs, \d+ fingerprints?/,'Hunt memory loaded'],
  [/^Master: \+\d+ = \d+ total/,null],
  [/^GDPR: Purged .*/,null],
  [/^GDPR purge error.*/,null],
  [/^Score validated: .*/,null],
  [/^Score validation skipped.*/,null],
  [/^PASS1 KILL dead site.*/,null],
  [/^PASS1 KILL vendor.*/,null],
  [/^PASS2 Deep investigation: (.+)/,'Deep-researching: $1'],
  [/^AI model .+ failed:.*/,'Retrying analysis...'],
  [/^Control received:.*/,null],
  [/^WAL replay error.*/,null],
  // Stage/DNA generation messages
  [/^Stage 1: .*/,'Building hunting strategy...'],
  [/^Stage 1 attempt \d+:.*/,'Refining strategy...'],
  [/^Stage 1 JSON.*/,'Refining strategy...'],
  [/^Stage 1 failed:.*/,'Strategy hit a snag — adjusting approach'],
  [/^Stage 2: .*/,'Generating search queries...'],
  [/^Stage 2 attempt \d+:.*/,'Refining queries...'],
  [/^Stage 2 JSON.*/,'Refining queries...'],
  [/^Stage 2 failed:.*/,'Using backup query approach'],
  // AI retry/error messages — strip exception details
  [/^AI email failed:.*/,'Email generation retrying...'],
  [/^AI query gen attempt \d+ failed:.*/,'Retrying query generation...'],
  [/^AI generated (\d+) queries \(attempt \d+\)/,'Generated $1 search queries'],
  [/^Search engine connection failed/,'Reconnecting to search...'],
  [/^Search error:.*/,'Search issue — retrying'],
  // Rewrite/strategy warnings
  [/^.+Strategy generation failed:.*/,'Strategy adjusted — using direct approach'],
  [/^.+Query generation failed:.*/,'Using backup query approach'],
  [/^.+Query gen: no JSON.*/,null],
  [/^.+Rewrite failed.*/,'Email rewrite retrying...'],
  [/^.+Rewrite error.*/,'Email rewrite retrying...'],
  // Query pipeline debug and score dimension shorthand
  [/^Generated (\d+) raw queries/,'Generated $1 search queries'],
  [/^Filtered to (\d+) clean queries \(.+\)/,'$1 search queries prepared'],
  [/^(.*Score: \d+\/10 — .+?) ?\| buy=\d+ reach=\d+/,'$1'],
  // Strip Python exceptions from "...failed: ..." messages
  [/^(.+ failed): .+/,'$1 — skipping'],
];
function _rewriteLog(msg){
  for(var i=0;i<_logRewrites.length;i++){
    var r=_logRewrites[i];
    if(r[0].test(msg))return r[1]===null?null:msg.replace(r[0],r[1]);
  }
  return msg;
}
function addLog(msg,level){
  _logBuffer.push({msg:msg,level:level});if(_logBuffer.length>200)_logBuffer.shift();
  const w=$('logW');if(!w)return;
  // Strip server-side timestamp+icon prefix (format: "HH:MM:SS <icon> <msg>")
  msg=msg.replace(/^\d{2}:\d{2}:\d{2}\s+\S+\s+/,'');
  // Translate internal messages for customers
  msg=_rewriteLog(msg);
  if(msg===null)return; // suppressed

  // Consolidate repeated skips into a counter
  if(level==='skip'){
    _skipCount++;
    clearTimeout(_lastSkipTimer);
    _lastSkipTimer=setTimeout(function(){
      if(_skipCount>0){
        var d2=document.createElement('div');d2.className='ll ll-skip-summary';
        var now2=new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
        d2.innerHTML='<span class="lt">'+now2+'</span><span class="li">⏭</span><span class="lm skip">Skipped '+_skipCount+' page'+ (_skipCount!==1?'s':'')+' (not qualified)</span>';
        w.appendChild(d2);w.scrollTop=w.scrollHeight;
        _skipCount=0;
      }
    },2000);
    return; // Don't render individual skips
  }
  _skipCount=0; // Reset on non-skip log

  const now=new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const d=document.createElement('div');d.className='ll'+(level==='detail'?' ll-detail':'');
  d.innerHTML=`<span class="lt">${now}</span><span class="li">${icons[level]||'·'}</span><span class="lm ${level||'info'}">${esc(msg)}</span>`;
  w.appendChild(d);if(w.children.length>500)w.removeChild(w.firstChild);w.scrollTop=w.scrollHeight;
}
function addThought(msg,mood){try{setOrbState(mood,msg.substring(0,80))}catch(_e){}
  const w=$('neoThoughts');if(!w)return;const now=new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const emoji=moodEmoji[mood]||'💭';
  const d=document.createElement('div');d.className='thought';
  d.innerHTML=`<div class="thought-time">${now}</div><div class="thought-bubble mood-${mood||'thinking'}"><div class="thought-mood mood-${mood||'thinking'}">${emoji} ${mood||'thinking'}</div>${esc(msg)}</div>`;
  w.appendChild(d);if(w.children.length>100)w.removeChild(w.children[1]);w.scrollTop=w.scrollHeight;
}
function updateNeoBubble(){}
const neoTips=[
  "Leads with ongoing needs are 3x more likely to convert — they need long-term partners.",
  "Score 9 leads with emails should be your first outreach every morning.",
  "Mentioning something specific from their website doubles the reply rate.",
  "The best cold email is under 120 words. Get to the value fast.",
  "LinkedIn connection + email on the same day = 40% higher response rate.",
  "Follow up exactly 5 days after the first email — not sooner, not later.",
  "Morning emails (9-10am recipient time) get 23% higher open rates.",
  "Use the Research button before emailing score 9 leads — personalisation wins deals.",
  "Setting the right tier helps forecast your pipeline — don't skip it.",
  "Your pipeline value updates live in the Dashboard — check it daily.",
  "One personalised email beats ten generic ones. Quality over quantity.",
  "The action queue shows your hottest leads first — start there every day.",
  "Top-up credits never expire. Keep your pipeline fueled for when you need it.",
];
const neoTipsLocal=[
  "Run `huntova benchmark` to compare cost and latency across providers.",
  "Anthropic Claude is the default — switch via Settings → Providers.",
  "Use Settings → Plugins to wire CSV export, Slack webhooks, or your own scripts.",
  "Run `huntova teach` to feed good-fit / bad-fit verdicts into the agent's DNA.",
  "Local mode keeps every lead in `~/.local/share/huntova/huntova.db` — back it up.",
];
let lastTipIdx=-1;
function showRandomTip(){
  var pool=(_hvRuntime && _hvRuntime.mode==='local')
    ?neoTips.filter(function(t){return !/credit|tier|forecast|pipeline value/i.test(t)}).concat(neoTipsLocal)
    :neoTips;
  let idx;do{idx=Math.floor(Math.random()*pool.length)}while(idx===lastTipIdx && pool.length>1);
  lastTipIdx=idx;var _nm=$('neoMsg');if(_nm)_nm.innerHTML='<b class="neo-tip">Tip:</b> '+esc(pool[idx]);
}
try{setInterval(showRandomTip,20000);setTimeout(showRandomTip,3000)}catch(_e){}
function addAgentLeadRow(l){
  const tb=$('aLTb'),tr=document.createElement('tr'),s=l.fit_score||0;
  var _agSc=scoreCls(s);
  tr.innerHTML=`<td><span class="sc ${_agSc}">${s}</span></td>
    <td class="ag-org-name">${esc(l.org_name)}</td><td>${esc(l.why_fit||l.event_name||'—')}</td>
    <td>${esc(l.country)}</td><td>${esc(l.city||'—')}</td><td>${esc(l.platform_used)}</td>
    <td>${l.contact_email?'<span class="ag-check">✓</span>':'—'}</td>
    <td>${(function(){var _u=safeUrl(l.org_linkedin);return _u?'<a href="'+_u+'" target="_blank" rel="noopener" class="ag-link">LinkedIn</a>':'—'})()}</td>`;
  tb.insertBefore(tr,tb.firstChild);
}
function updateDists(){
  const sc={},co={};agLeads.forEach(l=>{const s=l.fit_score||0;sc[s]=(sc[s]||0)+1;co[l.country||'?']=(co[l.country||'?']||0)+1});
  var sd=$('sDist');if(sd)sd.innerHTML=Object.entries(sc).sort((a,b)=>b[0]-a[0]).map(([k,v])=>{var _c=+k>=8?'cv-won':+k>=6?'cv-replied':+k>=4?'cv-sent':'cv-ignored';return `<span class="pm ${_c}">${esc(k)}:${v}</span>`}).join('');
  var cd=$('cDist');if(cd)cd.innerHTML=Object.entries(co).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([k,v])=>`<span class="pm">${esc(k)}:${v}</span>`).join('');
}
function togglePause(){
  var btn=$('btnPa');if(btn)btn.disabled=true;
  if(paused){agentCtrl('resume')}
  else{agentCtrl('pause')}
}
function confirmStop(){
  showMod('Stop Huntova?','All leads found so far are already saved.',function(){agentCtrl('stop')});
}
function agentCtrl(action){
  addLog('Sending '+action+' command...','info');addThought('User pressed '+action.toUpperCase()+'. On it!','ready');
  if(action==='stop'){
    // Optimistic feedback — backend may take up to ~one in-flight request
    // timeout before it emits the final "stopped" status event. Show
    // "Stopping…" immediately so the UI doesn't look frozen.
    var _btnSt=$('btnSt');if(_btnSt)_btnSt.disabled=true;
    try{huntSetCurrent('Stopping…')}catch(_){}
    addThought('Stopping — finishing the in-flight request then wrapping up.','idle');
  }
  fetch('/agent/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})})
    .then(r=>r.json()).then(d=>{if(d.ok){toast('✓ Hunt '+action);if(action==='pause'){$('btnPa').textContent='▶ RESUME';$('btnPa').className='tbtn tbtn-go';paused=true}else if(action==='resume'){$('btnPa').textContent='⏸ PAUSE';$('btnPa').style.color='var(--org)';$('btnPa').style.background='rgba(232,153,62,.06)';$('btnPa').style.borderColor='rgba(232,153,62,.12)';paused=false}var _bp=$('btnPa');if(_bp)_bp.disabled=false}else{toast('Failed: '+(d.error||'unknown'));var _bs=$('btnSt');if(_bs)_bs.disabled=false;var _bp2=$('btnPa');if(_bp2)_bp2.disabled=false}})
    .catch(e=>{toast('Connection error — try again');addLog('Control failed: '+e,'error');var _bs=$('btnSt');if(_bs)_bs.disabled=false;var _bp=$('btnPa');if(_bp)_bp.disabled=false});
}
var _loadCRMTimer=null;var _loadCRMPending=false;var _feedbackCache={};
async function loadCRM(){
  // Trailing-edge debounce: queue latest call instead of dropping it
  if(_loadCRMTimer){_loadCRMPending=true;return}
  _loadCRMTimer=setTimeout(function(){_loadCRMTimer=null;if(_loadCRMPending){_loadCRMPending=false;loadCRM()}},2000);
  try{const r=await fetch('/api/leads');if(!r.ok)throw new Error('HTTP '+r.status);var _data=await r.json();ALL=Array.isArray(_data)?_data:[];
    // Refresh currentModalLead reference after ALL replacement
    if(currentModalLead){var _refreshed=ALL.find(function(l){return l.lead_id===currentModalLead.lead_id});if(_refreshed)currentModalLead=_refreshed;else currentModalLead=null}
    // Refresh hunt best lead reference after ALL replacement
    if(_huntBestLead){var _hbr=ALL.find(function(l){return l.lead_id===_huntBestLead.lead_id});if(_hbr)_huntBestLead=_hbr;else _huntBestLead=null}
    ALL.forEach(function(l){
      ['contact_email','contact_linkedin','org_linkedin','org_website','contact_page_url'].forEach(function(f){
        var v=l[f];if(!v||typeof v!=='string')l[f]=null;
        else{v=v.trim();if(!v||v.length<3||v==='null'||v==='none'||v==='N/A'||v==='undefined'||v==='not found')l[f]=null;else l[f]=v;}
      });
      // Restore feedback state from cache
      if(_feedbackCache[l.lead_id])l._user_feedback=_feedbackCache[l.lead_id];
    });
    buildCountryOptions(ALL);applyFilters()}
  catch(e){$('crmRows').innerHTML='<div class="empty"><h3>Cannot load leads</h3></div>'}
}
// Parallelise initial page load
Promise.all([loadCRM()]).then(function(){ routeFromPath(); }).catch(function(){});
connectSSE();
restoreSession();
// Reconnect SSE when tab becomes visible again (handles backgrounded tabs)
// F6 fix: skip if connectSSE preflight is already in-flight
document.addEventListener('visibilitychange',function(){if(!document.hidden&&!_sseConnecting&&(!evtSrc||evtSrc.readyState===2||evtSrc.readyState===0)){_sseRetry=1000;if(evtSrc){try{evtSrc.close()}catch(_){}}evtSrc=null;connectSSE()}});
// Set greeting based on time of day. Late-night (22:00–04:59) reads
// "Working late" so it doesn't say "Good morning" at midnight.
function hvUpdateGreeting(){
  var h=new Date().getHours();
  var g;
  if(h>=22 || h<5)g='Working late';
  else if(h<12)g='Good morning';
  else if(h<17)g='Good afternoon';
  else g='Good evening';
  var hi=$('dashHi');
  if(!hi)return;
  if(_hvAccount&&_hvAccount.display_name){
    hi.textContent=g+', '+_hvAccount.display_name+'! 👋';
  } else {
    hi.textContent=g+'! 👋';
  }
}
(function(){
  hvUpdateGreeting();
  // Re-evaluate when account loads (race with /api/account fetch).
  var _gInt=setInterval(function(){
    if(_hvAccount&&_hvAccount.display_name){
      hvUpdateGreeting();
      clearInterval(_gInt);
    }
  },500);
  setTimeout(function(){clearInterval(_gInt)},5000);
})();
setInterval(function(){if(!agentRunning||!evtSrc||evtSrc.readyState!==1)loadCRM()},30000);

// ═══ RETENTION: Dashboard summary + action queue ═══
var _lastSummaryLoad=0;
function loadDashboardSummary(){
  if(Date.now()-_lastSummaryLoad<15000)return;
  _lastSummaryLoad=Date.now();
  fetch('/api/dashboard-summary').then(function(r){return r.json()}).then(function(d){
    if(!d.ok)return;
    // Since last visit banner
    var slv=d.since_last_visit;
    if(slv&&slv.new_leads_7d>0){
      var banner=document.getElementById('slvBanner');
      if(banner){
        banner.querySelector('.slv-count').textContent=slv.new_leads_7d;
        banner.style.display='flex';
      }
    }
    // Action queue removed — hot leads surfaced via main list sort + inline badges
  }).catch(function(){_lastSummaryLoad=0}); // Reset so next call retries
}
setTimeout(loadDashboardSummary,500);


// Legend popup positioning
function toggleLegend(btn){
  var pop=$('legPop');
  if(!pop)return;
  if(pop.classList.contains('on')){pop.classList.remove('on');pop.style.display='none';return}
  var r=btn.getBoundingClientRect();
  var x=r.right-320;if(x<10)x=10;
  var y=r.bottom+8;
  if(y+pop.scrollHeight>window.innerHeight-20)y=Math.max(10,window.innerHeight-pop.scrollHeight-20);
  pop.style.left=x+'px';pop.style.top=y+'px';
  pop.style.display='block';
  setTimeout(function(){pop.classList.add('on')},20);
}


// ── Status pill helpers ──
// Short labels for compact displays — derived from SL
var SL_SHORT={new:'New',email_sent:'Sent',followed_up:'Follow Up',replied:'Replied',meeting_booked:'Meeting',won:'Won',lost:'Lost',ignored:'Ignored'};
function esLabel(s){return SL_SHORT[s]||SL[s]||s}

// ── Huntova Chat ──
// ── Outcome instrumentation: track user actions on leads ──
var _actionLog=[];
var _actionFlushTimer=null;
function _trackAction(lid,action){
  _actionLog.push({lead_id:lid,action:action,at:new Date().toISOString()});
  if(_actionLog.length>500)_actionLog=_actionLog.slice(-200);
  // Auto-flush to server every 30s or when 10+ actions queued
  if(!_actionFlushTimer)_actionFlushTimer=setTimeout(_flushActions,30000);
  if(_actionLog.length>=10)_flushActions();
}
function _flushActions(){
  clearTimeout(_actionFlushTimer);_actionFlushTimer=null;
  if(!_actionLog.length)return;
  var batch=_actionLog.splice(0,50);
  fetch('/api/track-actions',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({actions:batch})}).catch(function(){
    _actionLog=batch.concat(_actionLog);
  });
}

// Send-tracking (Perplexity round 63). One-tap commit-to-action so we
// can measure the funnel end-to-end (draft → sent → replied) and so the
// learning profile gets explicit positive signal on "replied" outcomes.
// Reuses /api/update + the existing _outcome_signals path on the
// server, which automatically writes a good-fit feedback row when
// email_status transitions to "replied".
async function markLeadEmailOutcome(lid,outcome){
  var statusMap={sent:'email_sent',followed_up:'followed_up',replied:'replied'};
  var newStatus=statusMap[outcome];
  if(!newStatus){toast('Unknown outcome');return}
  _trackAction(lid,'email_'+outcome);
  await updateLead(lid,'email_status',newStatus);
  // If we're currently viewing this lead's detail page, re-render so
  // the status pill, badges, and any disabled-button states update
  // without a full page reload.
  try{
    var l=(typeof ALL!=='undefined'&&ALL)?ALL.find(function(x){return x.lead_id===lid}):null;
    if(l && location.pathname==='/leads/'+lid && typeof renderLeadPage==='function'){
      renderLeadPage(l);
    }
  }catch(_){}
}
// Flush on page unload so actions aren't lost
window.addEventListener('pagehide',function(){
  if(_actionLog.length){
    try{navigator.sendBeacon('/api/track-actions',JSON.stringify({actions:_actionLog}))}catch(_){}
  }
});

async function cpField(field,label){
  if(currentModalLead){
    cp(currentModalLead[field]||'',label);
    _trackAction(currentModalLead.lead_id,'copy_'+field);
  }
}
function sendMailTo(){
  if(!currentModalLead)return;
  var l=currentModalLead;
  var _n=(_hvAccount&&_hvAccount.display_name)||'';var _e=(_hvAccount&&_hvAccount.email)||'';
  var sig=_n?"\n\nBest,\n"+_n+(_e?"\n"+_e:""):"";
  var body=(l.email_body||'')+sig;
  var url='mailto:'+(l.contact_email||'')+'?subject='+encodeURIComponent(l.email_subject||'')+'&body='+encodeURIComponent(body);
  if(url.length>2000){cp(body,'Email body');toast('Email too long for mailto — body copied to clipboard instead');_trackAction(l.lead_id,'copy_email_overflow');return}
  window.open(url);
  _trackAction(l.lead_id,'send_mailto');
}

var _seenLeads=new Set();
try{var _sl=JSON.parse(localStorage.getItem('hv_seen')||'[]');_sl.forEach(function(id){_seenLeads.add(id)})}catch(_){}
function markLeadSeen(lid){
  _seenLeads.add(lid);
  try{localStorage.setItem('hv_seen',JSON.stringify([..._seenLeads].slice(-500)))}catch(_){}
  // Remove fresh highlight from the row
  var row=document.getElementById('r-'+lid);
  if(row)row.classList.remove('fresh');
}
function openLeadModal(lid){
  // Re-entry guard: rapid double-clicks would otherwise call into this
  // path twice synchronously, briefly thrashing the modal DOM. Bail if
  // a lead modal is already open — the second click is a no-op.
  var _bg=$('leadModalBg');
  if(_bg && _bg.classList.contains('on')) return;
  var l=ALL.find(function(x){return x.lead_id===lid});
  if(!l)return;
  markLeadSeen(lid);
  // Remember the element that opened us so closeLeadModal can restore focus.
  window._leadModalTrigger=document.activeElement;
  currentModalLead=l;
  var id=l.lead_id,sc=l.fit_score||0,es=l.email_status||'new';
  var _heroScCls=scoreCls(sc);
  var so=Object.entries(SL).map(function(e){return '<option value="'+e[0]+'"'+(e[0]===es?' selected':'')+'>'+e[1]+'</option>'}).join('');
  var tierV=l.deal_tier||'';
  var tiers=[['','Not set'],['small','Small (€500)'],['medium','Medium (€1K)'],['large','Large (€1.5K+)']];
  var tOpts=tiers.map(function(t){return '<option value="'+t[0]+'"'+(tierV===t[0]?' selected':'')+'>'+t[1]+'</option>'}).join('');
  var h='';

  // ── HEADER (centered premium) ──
  h+='<button class="lm-close-float" onclick="closeLeadModal()">✕</button>';
  h+='<div class="lm-hero">';
  h+='<div class="lm-hero-sc '+_heroScCls+'">'+sc+'</div>';
  h+='<h1 class="lm-hero-title">'+esc(l.org_name||'Unknown')+'</h1>';
  h+='<p class="lm-hero-sub">'+esc(l.event_type||'')+(l.found_date?' · '+dAgo(l.found_date):'')+'</p>';
  // Why this lead — the hero context line
  var _why=l.why_fit||'';var _gap=l.production_gap||'';
  if(_why||_gap){
    h+='<p class="lm-hero-why">';
    if(_why)h+=esc(_why);
    if(_why&&_gap)h+=' — ';
    if(_gap)h+='<em style="color:var(--acc)">'+esc(_gap)+'</em>';
    h+='</p>';
  }
  h+='<div class="lm-hero-tags">';
  if(l.country) h+='<span class="lm-pill lm-pill-loc">'+esc(l.country)+(l.city?' · '+esc(l.city):'')+'</span>';
  if(l.is_recurring) h+='<span class="lm-pill lm-pill-rec">Ongoing Need</span>';
  // Urgency badge based on timing score
  var _ts=l.timing_score||0;
  if(_ts>=8)h+='<span class="lm-pill lm-pill-hot">Urgent — Act Now</span>';
  else if(_ts>=6)h+='<span class="lm-pill lm-pill-warm">Timely</span>';
  // Hiring signal badge
  if(l._hiring_signal_detected)h+='<span class="lm-pill lm-pill-hiring">Hiring Signal</span>';
  // Data confidence badge with detailed explanation
  var _dc=l._data_confidence;var _cs=l._confidence_signals||0;
  if(typeof _dc==='number'){
    var _dcLbl=_dc>=0.6?'High confidence':_dc>=0.4?'Medium confidence':'Low confidence';
    var _dcDetails=[];
    if(l._quote_verified==='exact'||l._quote_verified==='close')_dcDetails.push('Quote verified');
    else if(l._quote_verified==='partial')_dcDetails.push('Quote partial');
    else _dcDetails.push('Quote unverified');
    _dcDetails.push(l.contact_email?'Email found':'No email');
    _dcDetails.push(l.contact_name?'Name found':'No name');
    _dcDetails.push((l.org_website||l.org_linkedin)?'Website/LinkedIn':'No web presence');
    var _tr2=l.timing_rationale||'';_dcDetails.push(_tr2.length>20?'Timing explained':'No timing signal');
    h+='<span class="lm-pill lm-pill-conf-'+(_dc>=0.6?'high':_dc>=0.4?'mid':'low')+'" title="'+_dcDetails.join(' · ')+'">'+_dcLbl+' ('+_cs+'/5)</span>';
  }
  // Quote verification badge — show all states
  var _qv=l._quote_verified||'';
  if(_qv==='exact')h+='<span class="lm-pill lm-pill-conf-high" title="Evidence quote found verbatim on page">✓ Quote verified</span>';
  else if(_qv==='close')h+='<span class="lm-pill lm-pill-conf-high" title="Evidence quote closely matches page text (70%+ word overlap)">✓ Quote matched</span>';
  else if(_qv==='partial')h+='<span class="lm-pill lm-pill-conf-mid" title="Evidence quote partially matches page text (40-70% word overlap)">~ Quote partial</span>';
  else if(_qv==='unverified')h+='<span class="lm-pill lm-pill-conf-low" title="Evidence quote could not be verified against page text">⚠ Quote unverified</span>';
  else if(_qv==='missing')h+='<span class="lm-pill" style="background:rgba(255,255,255,.03);color:var(--t3);border-color:var(--bd)" title="No evidence quote provided by AI">No quote</span>';
  h+='</div>';
  h+='<div class="lm-hero-ctrls">';
  h+='<div class="lm-ctrl-box"><span class="lm-ctrl-lbl">Status</span><select onchange="updateLeadModal(\''+id+'\',\'email_status\',this.value)">'+so+'</select></div>';
  h+='<div class="lm-ctrl-box"><span class="lm-ctrl-lbl">Tier</span><select onchange="updateLeadModal(\''+id+'\',\'deal_tier\',this.value)">'+tOpts+'</select></div>';
  var _fuVal=l.follow_up_date?l.follow_up_date.split('T')[0]:'';
  h+='<div class="lm-ctrl-box"><span class="lm-ctrl-lbl">Follow up</span><input type="date" class="lm-date-input" value="'+_fuVal+'" onchange="updateLeadModal(\''+id+'\',\'follow_up_date\',this.value?new Date(this.value).toISOString():\'\')"></div>';
  if(l.follow_up_date){var _fuD=new Date(l.follow_up_date);var _now=new Date();var _fuLabel=dFmt(l.follow_up_date);if(_fuD<_now)h+='<span class="lm-pill lm-pill-overdue" title="Was '+esc(_fuLabel)+'">Overdue</span>';else{var _dd=Math.ceil((_fuD-_now)/864e5);h+='<span class="lm-pill lm-pill-upcoming" title="'+esc(_fuLabel)+'">In '+_dd+'d · '+esc(_fuLabel)+'</span>'}}
  h+='<div class="lm-feedback" style="display:flex;gap:6px;margin-top:8px">';
  var _fb=l._user_feedback||'';
  h+='<button class="lm-fb-btn lm-fb-good'+(_fb==='good'?' lm-fb-selected':'')+'" '+(_fb?'disabled':'')+' onclick="sendLeadFeedback(\''+id+'\',\'good\',this)" title="This teaches Huntova what a strong prospect looks like for your business">'+(_fb==='good'?'✓ Good Fit':'👍 Good Fit')+'</button>';
  h+='<button class="lm-fb-btn lm-fb-bad'+(_fb==='bad'?' lm-fb-selected':'')+'" '+(_fb?'disabled':'')+' onclick="sendLeadFeedback(\''+id+'\',\'bad\',this)" title="This teaches Huntova what to avoid for your business">'+(_fb==='bad'?'✓ Bad Fit':'👎 Bad Fit')+'</button>';
  h+='</div>';
  h+='<div class="lm-fb-hint">Your ratings train Huntova to find better leads for your business</div>';
  h+='</div>';
  h+='</div>';

  h+='<div class="lm-body">';

  // ══ SECTION 1: SCRAPED DATA ══
  h+='<div class="lm-sec">';
  h+='<div class="lm-sec-t">Scraped Intelligence</div>';

  // Contact & Links card
  h+='<div class="si-card">';
  h+='<div class="si-card-hdr"><span class="si-card-icon">👤</span> Contact & Links</div>';
  h+='<div class="si-card-body si-grid">';
  var cn=l.contact_name?esc(l.contact_name):'<span class="emp">Unknown</span>';
  var cr=l.contact_role?' <span class="si-role">'+esc(l.contact_role)+'</span>':'';
  h+='<div class="si-field"><div class="si-label">Contact</div><div class="si-val">'+cn+cr+'</div></div>';
  // Email field with generic-address warning. _is_generic_email is stamped
  // by db.upsert_lead for info@/sales@/support@/etc. — mark those visually
  // so the user understands reach/response rates will be weaker.
  var _emailHtml;
  if(l.contact_email){
    _emailHtml='<a href="mailto:'+esc(l.contact_email)+'">'+esc(l.contact_email)+'</a>';
    if(l._is_generic_email)_emailHtml+=' <span class="generic-email-warn" title="Role-based address (info@, sales@, support@). Reach + reply rates are typically much lower than a named decision-maker\'s inbox.">⚠ generic</span>';
  } else {
    _emailHtml='<span class="emp">Not found</span>';
  }
  h+='<div class="si-field"><div class="si-label">Email</div><div class="si-val">'+_emailHtml+'</div></div>';
  h+='<div class="si-field"><div class="si-label">Website</div><div class="si-val">'+((function(){var _u=safeUrl(l.org_website);return _u?'<a href="'+_u+'" target="_blank" rel="noopener">'+esc(l.org_website).replace('https://','').replace('http://','')+'</a>':'<span class="emp">—</span>'})())+'</div></div>';
  h+='<div class="si-field"><div class="si-label">Current Tools</div><div class="si-val">'+(l.platform_used&&l.platform_used!=='unknown'?esc(l.platform_used):'<span class="emp">Not detected</span>')+'</div></div>';
  // Link pills
  h+='<div class="si-links">';
  if(l.org_linkedin&&safeUrl(l.org_linkedin)) h+='<a class="si-pill si-pill-li" href="'+safeUrl(l.org_linkedin)+'" target="_blank" rel="noopener">in Company</a>';
  if(l.contact_linkedin&&safeUrl(l.contact_linkedin)) h+='<a class="si-pill si-pill-at" href="'+safeUrl(l.contact_linkedin)+'" target="_blank" rel="noopener">@ Contact</a>';
  if(l.url&&safeUrl(l.url)) h+='<a class="si-pill si-pill-src" href="'+safeUrl(l.url)+'" target="_blank" rel="noopener">⌗ Source</a>';
  h+='</div>';
  h+='</div></div>';

  // Score Breakdown card — read from flat fields (AI response) or score_breakdown (legacy)
  var _sb=l.score_breakdown||{};
  // Build scores from flat fields if score_breakdown is empty
  var _scores=[
    {k:'fit_score',dk:'fit',l:'Business Fit',i:'🎯',v:l.fit_score||(_sb.event_fit||{}).score||0,hint:'How well their business matches your ICP'},
    {k:'buyability_score',dk:'buyability',l:'Buyability',i:'💰',v:l.buyability_score||(_sb.budget_signals||{}).score||0,hint:'Signs they can actually afford your service'},
    {k:'reachability_score',dk:'reachability',l:'Reachability',i:'🚪',v:l.reachability_score||(_sb.accessibility||{}).score||0,hint:'How easy it is to contact a decision-maker'},
    {k:'service_opportunity_score',dk:'service_opportunity',l:'Service Opportunity',i:'🔧',v:l.service_opportunity_score||(_sb.production_gap||{}).score||0,hint:'How much room there is for your service'},
    {k:'timing_score',dk:'timing',l:'Timing',i:'⏰',v:l.timing_score||(_sb.timing||{}).score||0,hint:'Are they in a buying moment right now'},
  ];
  var _hasSb=_scores.some(function(s){return s.v>0});
  if(_hasSb){
    h+='<div class="si-card">';
    h+='<div class="si-card-hdr"><span class="si-card-icon">📊</span> Score Breakdown</div>';
    h+='<div class="si-card-body" style="display:block">';
    _scores.forEach(function(d){
      var sv=d.v||0;var pct=sv*10;
      var col=sv>=8?'var(--acc)':sv>=6?'var(--cyn)':sv>=4?'var(--org)':'var(--red)';
      var verb=scoreLabel(d.dk,sv);
      h+='<div class="sb-row"><span class="sb-dim" title="'+esc(d.hint)+'">'+d.i+' '+d.l+'</span><div class="sb-track" role="progressbar" aria-valuenow="'+sv+'" aria-valuemin="0" aria-valuemax="10" aria-label="'+d.l+': '+sv+' out of 10"><div class="sb-fill" style="width:'+pct+'%;background:'+col+'"></div></div><span class="sb-verb" style="color:'+col+'">'+verb+'</span><span class="sb-sc" style="color:'+col+'">'+sv+'</span></div>';
    });
    // Rationale rows (from T1-7)
    if(l.fit_rationale)h+='<div class="sb-rationale"><span class="sb-rat-dim">Fit:</span> '+esc(l.fit_rationale)+'</div>';
    if(l.timing_rationale)h+='<div class="sb-rationale"><span class="sb-rat-dim">Timing:</span> '+esc(l.timing_rationale)+'</div>';
    if(l.buyability_rationale)h+='<div class="sb-rationale"><span class="sb-rat-dim">Buyability:</span> '+esc(l.buyability_rationale)+'</div>';
    h+='</div></div>';
  }

  // Analysis card (legacy + current tools)
  if(l.production_gap||l.why_fit||l.current_tools){
    h+='<div class="si-card">';
    h+='<div class="si-card-hdr"><span class="si-card-icon">🧠</span> Analysis</div>';
    h+='<div class="si-card-body">';
    if(l.why_fit) h+='<div class="si-block"><div class="si-label">Why They Fit</div><div class="si-val">'+esc(l.why_fit)+'</div></div>';
    if(l.production_gap) h+='<div class="si-block"><div class="si-label">Service Opportunity</div><div class="si-val">'+esc(l.production_gap)+'</div></div>';
    if(l.current_tools) h+='<div class="si-block"><div class="si-label">Current Tools</div><div class="si-val">'+esc(l.current_tools)+'</div></div>';
    if(l.tool_weaknesses) h+='<div class="si-block"><div class="si-label">Weaknesses</div><div class="si-val si-val-warn">'+esc(l.tool_weaknesses)+'</div></div>';
    h+='</div></div>';
  }

  // Evidence Dossier
  var _ed=l.evidence_dossier||[];
  if(_ed.length||l.evidence_quote){
    h+='<div class="si-card">';
    h+='<div class="si-card-hdr"><span class="si-card-icon">📋</span> Evidence ('+(_ed.length||1)+' points)</div>';
    h+='<div class="si-card-body" style="display:block">';
    if(_ed.length){_ed.forEach(function(e,i){
      h+='<div class="ev-item"><div class="ev-point">'+(i+1)+'. '+esc(e.point||'')+'</div>';
      if(e.quote)h+='<div class="ev-quote">"'+esc(e.quote)+'"</div>';
      h+='</div>';
    })}else if(l.evidence_quote){
      h+='<div class="ev-item"><div class="ev-quote">"'+esc(l.evidence_quote)+'"</div></div>';
    }
    h+='</div></div>';
  }

  // Deal Intelligence Briefing
  var _db=l.deal_briefing||{};
  if(_db.recommended_approach||_db.competitive_analysis||_db.objection_prep){
    h+='<div class="si-card di-card">';
    h+='<div class="si-card-hdr"><span class="si-card-icon">🎯</span> Deal Intelligence</div>';
    h+='<div class="si-card-body" style="display:block">';
    // Recommended approach
    var _ra=_db.recommended_approach||{};
    if(_ra.lead_with){
      h+='<div class="di-section">';
      h+='<div class="di-item di-green"><span class="di-tag">Lead with</span>'+esc(_ra.lead_with)+'</div>';
      if(_ra.mention)h+='<div class="di-item di-blue"><span class="di-tag">Mention</span>'+esc(_ra.mention)+'</div>';
      if(_ra.avoid)h+='<div class="di-item di-red"><span class="di-tag">Avoid</span>'+esc(_ra.avoid)+'</div>';
      h+='</div>';
    }
    // One-line pitch
    if(_db.one_line_pitch){
      h+='<div class="di-pitch">"'+esc(_db.one_line_pitch)+'"</div>';
    }
    // Competitive analysis
    var _ca=_db.competitive_analysis||{};
    if(_ca.current_platform||(_ca.weaknesses&&_ca.weaknesses.length)){
      h+='<div class="di-section"><div class="si-label">Competitive Intel</div>';
      if(_ca.current_platform)h+='<div class="ca-platform">Using: <b>'+esc(_ca.current_platform)+'</b></div>';
      if(_ca.weaknesses)_ca.weaknesses.forEach(function(w){h+='<div class="ca-weakness">'+esc(w)+'</div>'});
      if(_ca.switch_trigger)h+='<div class="ca-trigger">Switch trigger: '+esc(_ca.switch_trigger)+'</div>';
      h+='</div>';
    }
    // Objection prep
    var _op=_db.objection_prep||[];
    if(_op.length){
      h+='<div class="di-section"><div class="si-label">Objection Prep</div>';
      _op.forEach(function(o){
        h+='<div class="di-obj"><div class="di-obj-q">"'+esc(o.objection||'')+'"</div><div class="di-obj-a">→ '+esc(o.counter||'')+'</div></div>';
      });
      h+='</div>';
    }
    // Urgency
    var _ur=_db.urgency||{};
    if(_ur.level){
      var _uc={act_now:'var(--red)',soon:'var(--org)',watch:'var(--cyn)',wait:'var(--t3)'}[_ur.level]||'var(--t3)';
      var _ul={act_now:'ACT NOW',soon:'SOON',watch:'WATCHING',wait:'WAIT'}[_ur.level]||_ur.level;
      h+='<div class="di-urgency" style="border-left-color:'+_uc+'"><span class="di-urgency-badge" style="background:'+_uc+'">'+_ul+'</span> '+esc(_ur.reason||'')+'</div>';
    }
    h+='</div></div>';
  }
  h+='</div>';

  // ══ SECTION 2: OUTREACH EMAIL ══
  h+='<div class="lm-sec">';
  h+='<div class="lm-sec-t">Outreach Email</div>';

  // Tone selector row (always visible)
  h+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">';
  h+='<div class="lm-tones" style="margin-left:0">';
  ['friendly','consultative','broadcast'].forEach(function(t){
    var label=t==='friendly'?'Friendly':t==='consultative'?'Expert':'Pro';
    h+='<button class="lm-tp'+(t===_wizardTone?' on':'')+'" data-tone="'+t+'" onclick="selectTone(this)">'+label+'</button>';
  });
  h+='</div>';
  h+='<button class="btn btn-secondary lm-rw-btn" onclick="doRewrite(\''+id+'\')" data-feature="email_rewrite"><span class="lm-rw-icon">✨</span> Rewrite</button>';
  h+='</div>';

  var hasEmail=l.email_body&&l.email_body.length>50;
  if(hasEmail){
    h+='<div class="lm-ecard">';
    h+='<div class="lm-esubj" id="ms-'+id+'">'+esc(l.email_subject||'No subject')+'</div>';
    h+='<div class="lm-ebody" id="mb-'+id+'">'+esc(l.email_body)+'</div>';
    h+='<div class="lm-eacts">';
    h+='<button class="btn btn-primary btn-sm lm-eb lm-eb-send" onclick="sendMailTo()" data-feature="email_draft_visible">✉ Send</button>';
    h+='<button class="btn btn-secondary btn-sm lm-eb lm-eb-copy" onclick="cpField(\'email_body\',\'Email\')" data-feature="email_draft_visible">Copy Email</button>';
    h+='<button class="btn btn-secondary btn-sm lm-eb lm-eb-copy" onclick="cpField(\'email_subject\',\'Subject\')" data-feature="email_draft_visible">Copy Subject</button>';
    h+='<button class="btn btn-secondary btn-sm lm-eb lm-eb-copy" onclick="cpField(\'linkedin_note\',\'LinkedIn\')" data-feature="email_draft_visible">Copy LinkedIn</button>';
    h+='</div>';
    if(l.email_opens){h+='<div class="email-opens-bar">Opened '+l.email_opens+' time'+(l.email_opens>1?'s':'')+(l.last_opened?' · last: '+dAgo(l.last_opened):'')+'</div>'};

    // Follow-up sequence (Pass 3 top leads only)
    var _fu2=l.email_followup_2||'',_fu3=l.email_followup_3||'',_fu4=l.email_followup_4||'';
    if(_fu2||_fu3||_fu4){
      var _fuCount=[_fu2,_fu3,_fu4].filter(Boolean).length;
      h+='<details class="lm-followups" data-feature="email_draft_visible"><summary class="lm-fu-toggle">Follow-up Sequence ('+_fuCount+' emails)</summary>';
      var _fuLabels=_wizardTone==='broadcast'?['Day 3 — Value proposition','Day 7 — Case study','Day 14 — Final follow-up']:_wizardTone==='consultative'?['Day 3 — New insight','Day 7 — Helpful resource','Day 14 — Closing note']:['Day 3 — Different angle','Day 7 — Quick check-in','Day 14 — Last note'];
      if(_fu2)h+='<div class="lm-fu"><div class="lm-fu-label">'+_fuLabels[0]+'</div><div class="lm-fu-body">'+esc(_fu2)+'</div><button class="btn btn-secondary btn-sm lm-eb lm-eb-copy lm-fu-copy" onclick="event.stopPropagation();cp(\''+esc(_fu2).replace(/'/g,"\\'")+'\',\'Follow-up 2\')">Copy</button></div>';
      if(_fu3)h+='<div class="lm-fu"><div class="lm-fu-label">'+_fuLabels[1]+'</div><div class="lm-fu-body">'+esc(_fu3)+'</div><button class="btn btn-secondary btn-sm lm-eb lm-eb-copy lm-fu-copy" onclick="event.stopPropagation();cp(\''+esc(_fu3).replace(/'/g,"\\'")+'\',\'Follow-up 3\')">Copy</button></div>';
      if(_fu4)h+='<div class="lm-fu"><div class="lm-fu-label">'+_fuLabels[2]+'</div><div class="lm-fu-body">'+esc(_fu4)+'</div><button class="btn btn-secondary btn-sm lm-eb lm-eb-copy lm-fu-copy" onclick="event.stopPropagation();cp(\''+esc(_fu4).replace(/'/g,"\\'")+'\',\'Follow-up 4\')">Copy</button></div>';
      h+='</details>';
    }

    // Email history
    var rwH=l.rewrite_history||[];
    if(rwH.length>0){
      h+='<div class="lm-ehist"><div class="lm-ehist-t">Previous Versions ('+rwH.length+')</div>';
      rwH.slice().reverse().forEach(function(rh,ri){
        // Render reverse-chronological for UX, but pass the original
        // (non-reversed) array index to revertEmail() so the backend
        // restores the version the user actually clicked.
        var _origIdx=rwH.length-1-ri;
        h+='<div class="lm-eh-item" onclick="revertEmail(\''+id+'\','+_origIdx+')" title="Click to restore this version">';
        h+='<span class="lm-eh-date">'+new Date(rh.date).toLocaleDateString()+'</span>';
        h+='<span class="lm-eh-tone">'+esc(rh.tone||'?')+'</span>';
        h+='<span class="lm-eh-subj">'+esc(rh.subject||'')+'</span>';
        h+='<span class="lm-eh-rv">REVERT</span>';
        h+='</div>';
      });
      h+='</div>';
    }
    h+='</div>';
  } else {
    h+='<div class="lm-empty-email"><p>No email generated yet.</p><button class="btn btn-primary lm-rw-btn" onclick="doRewrite(\''+id+'\')"><span class="lm-rw-icon">✨</span> Generate Email</button></div>';
  }
  // ══ CHAT BUTTON (opens side panel) ══
  h+='<div style="padding:12px 20px;border-top:1px solid rgba(255,255,255,.04);text-align:center">';
  h+='<button class="neo-chat-toggle" onclick="openNeoWidget(\''+id+'\')" data-feature="ai_chat"><span class="nch-dot"></span> Chat with Huntova about this email</button>';
  h+='</div>';

  // ══ RESEARCH ══
  h+='<div style="padding:8px 20px;border-top:1px solid rgba(255,255,255,.04);display:flex;gap:8px;align-items:center">';
  var _rcost=(_hvRuntime && _hvRuntime.billing_enabled)?'(1 credit)':'(~$0.04 API spend)';
  h+='<button class="research-btn" id="rb-'+id+'" onclick="startResearch(\''+id+'\')" data-feature="research">🔬 Deep Research<span class="research-cost">'+_rcost+'</span></button>';
  h+='<span class="research-prog" id="rp-'+id+'"></span>';
  h+='</div>';
  h+='<div class="research-result" id="rr-'+id+'"></div>';

  // ══ SECTION 3: NOTES ══
  h+='</div>';
h+='<div class="lm-sec">';
  h+='<div class="lm-sec-t">Notes</div>';
  h+='<textarea id="mn-'+esc(id)+'" class="lm-notes" placeholder="Add notes about this lead...">'+esc(l.notes||'')+'</textarea>';
  // Defense-in-depth: lead IDs are currently 12-char hex hashes so
  // quotes can't appear, but escape anyway in case the ID generation
  // ever changes — otherwise an injected quote would break the
  // onclick string and silently disable Save Notes.
  h+='<button class="lm-nbtn" onclick="saveModalNotes(\''+esc(id)+'\')">Save Notes</button>';
  h+='</div>';

  h+='</div>';
  $('leadModal').innerHTML=h;
  $('leadModalBg').classList.add('on');
  document.body.style.overflow='hidden';
  hvApplyGating();
}

function getSelectedTone(){
  const btn=document.querySelector('.lm-tp.on');
  return btn?btn.dataset.tone:'friendly';
}
function selectTone(el){
  el.parentElement.querySelectorAll('.lm-tp').forEach(function(b){b.classList.remove('on')});
  el.classList.add('on');
}
async function doRewrite(lid){
  if(_hvFeatures.email_rewrite===false){hvShowUpgrade('email_rewrite');return}
  doRewriteInner(lid);
}
var _rewriteInFlight=false;
function _restoreRewriteUI(rwBtn){document.querySelectorAll('.lm-tp').forEach(function(b){b.disabled=false;b.style.opacity='1'});if(rwBtn){rwBtn.classList.remove('writing');rwBtn.innerHTML='<span class="lm-rw-icon">✨</span> Rewrite'}}
async function doRewriteInner(lid){
  if(_rewriteInFlight)return;
  _rewriteInFlight=true;
  var toneBtn=document.querySelector('.lm-tp.on');
  var tone=toneBtn?toneBtn.dataset.tone:'friendly';
  var rwBtn=document.querySelector('.lm-rw-btn');
  var ecard=document.querySelector('.lm-ecard');
  // Show writing state
  if(rwBtn){rwBtn.classList.add('writing');rwBtn.innerHTML='<span class="lm-rw-icon">✨</span> Writing...';}
  // Replace email content with typing animation
  var oldContent=ecard?ecard.innerHTML:'';
  if(ecard){ecard.innerHTML='<div class="lm-esubj" id="streamSubj">Writing subject...</div><div class="lm-stream" id="streamBody"><span class="lm-stream-cursor"></span></div>'}
  // Disable tone pills
  document.querySelectorAll('.lm-tp').forEach(function(b){b.disabled=true;b.style.opacity='.5'});
  var _streaming=false;
  try{
    var r=await fetch('/api/rewrite',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:lid,tone:tone})});
    var d=await r.json();
    if(d.ok){
      await loadCRM();
      var updated=ALL.find(function(x){return x.lead_id===lid});
      if(updated){
        currentModalLead=updated;
        // Stream the email text character by character
        var subEl=document.getElementById('streamSubj');
        var bodyEl=document.getElementById('streamBody');
        if(subEl&&updated.email_subject){subEl.textContent=updated.email_subject;}
        if(bodyEl&&updated.email_body){
          bodyEl.innerHTML='';
          _streaming=true;
          var chars=updated.email_body.split('');
          var ci=0;
          var streamInt=setInterval(function(){
            if(!currentModalLead||currentModalLead.lead_id!==lid||!bodyEl.isConnected){clearInterval(streamInt);_rewriteInFlight=false;_restoreRewriteUI(rwBtn);return}
            if(ci>=chars.length){clearInterval(streamInt);_rewriteInFlight=false;_restoreRewriteUI(rwBtn);
              setTimeout(function(){if(currentModalLead&&currentModalLead.lead_id===lid&&bodyEl.isConnected)refreshLeadView(lid)},600);return;}
            bodyEl.textContent+=chars[ci];ci++;
          },8);
        } else {refreshLeadView(lid);}
      }
      toast('\u2728 AI rewrote email — '+tone);
    } else {
      if(ecard) ecard.innerHTML=oldContent;
      toast('Rewrite failed');
    }
  }catch(e){
    if(ecard) ecard.innerHTML=oldContent;
    toast('Rewrite error: '+e.message);
  }
  if(!_streaming){
    _rewriteInFlight=false;
    _restoreRewriteUI(rwBtn);
  }
  // If streaming, button stays in "writing" state until interval completes and refreshLeadView re-renders
}

async function revertEmail(lid,histIdx){
  if(_hvFeatures.email_rewrite===false){hvShowUpgrade('email_rewrite');return}
  try{
    var r=await fetch('/api/revert-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:lid,history_index:histIdx})});
    var d=await r.json();
    if(d.ok){
      await loadCRM();
      var updated=ALL.find(function(x){return x.lead_id===lid});
      if(updated){currentModalLead=updated;refreshLeadView(lid)}
      toast('Reverted to previous email');
    } else {toast('Revert failed')}
  }catch(e){toast('Revert error')}
}


function closeLeadModal(){
  if(window._flushModalSave)window._flushModalSave();
  $('leadModalBg').classList.remove('on');
  document.body.style.overflow='';
  currentModalLead=null;
  window._flushModalSave=null;window._cancelModalSave=null;window._getPendingEdits=null;
  // Restore focus to whatever opened the modal so keyboard users don't get dumped on <body>.
  var _trg=window._leadModalTrigger;window._leadModalTrigger=null;
  if(_trg&&typeof _trg.focus==='function'){try{_trg.focus()}catch(_){}}
}

// Refresh the lead detail page if it's active for this lead.
// The page has contenteditable email fields wired up through a closure that
// tracks _pgPendSubj / _pgPendBody — re-rendering blows that closure away,
// so if the user is mid-edit we must flush before swapping the DOM AND
// preserve any unsaved edits the backend update didn't include.
function refreshLeadView(lid){
  var _upd=ALL.find(function(x){return x.lead_id===lid});
  if(!_upd)return;
  // Grab pending edits BEFORE swapping currentModalLead — otherwise the
  // in-flight edits get thrown away when the auto-save closure is replaced.
  var _pend=null;
  try{if(window._getPendingEdits)_pend=window._getPendingEdits()}catch(_){}
  // Merge the user's pending edits on top of the server update so the
  // re-rendered DOM shows their typing, not the pre-edit server state.
  if(_pend){
    if(_pend.subject!==null&&_pend.subject!==undefined)_upd.email_subject=_pend.subject;
    if(_pend.body!==null&&_pend.body!==undefined)_upd.email_body=_pend.body;
  }
  // Flush to DB so the merged edits are durable. _flushModalSave is idempotent.
  try{if(window._flushModalSave)window._flushModalSave()}catch(_){}
  currentModalLead=_upd;
  var _ldp=document.getElementById('pg-lead-detail');
  if(_ldp&&_ldp.style.display!=='none'){renderLeadPage(_upd)}
}

// ── LEAD DETAIL FULL PAGE ──
function openLeadPage(lid) {
  var l = ALL.find(function(x) { return x.lead_id === lid; });
  if (!l) {
    // Lead not loaded yet, wait for CRM then retry
    loadCRM().then(function() {
      l = ALL.find(function(x) { return x.lead_id === lid; });
      if (l) renderLeadPage(l);
      else { history.replaceState(null, '', '/leads'); routeFromPath(); }
    });
    return;
  }
  markLeadSeen(lid);
  // Save scroll position
  var crmBody = document.querySelector('.crm-body');
  if (crmBody) window._leadsScrollY = crmBody.scrollTop;
  // Push URL if not already there
  if (location.pathname !== '/leads/' + lid) {
    history.pushState({lid: lid}, '', '/leads/' + lid);
  }
  renderLeadPage(l);
}

function renderLeadPage(l) {
  // Hide other pages, show lead detail
  document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('on'); });
  document.querySelectorAll('.nav-btn').forEach(function(b) { b.classList.remove('on'); });
  var pg = document.getElementById('pg-lead-detail');
  pg.style.display = 'flex';
  pg.classList.add('on');

  currentModalLead = l;
  var id = l.lead_id, sc = l.fit_score || 0, es = l.email_status || 'new';
  var _scCls = scoreCls(sc);
  var so = Object.entries(SL).map(function(e) { return '<option value="' + e[0] + '"' + (e[0] === es ? ' selected' : '') + '>' + e[1] + '</option>'; }).join('');

  var h = '';

  // Back link
  h += '<div class="ldp-back"><a href="/leads" onclick="event.preventDefault();goBackToLeads()">&#8592; Back to Leads</a></div>';

  // First-email activation hint (Perplexity round 62) — shown only
  // when this page was opened via openBestLeadForEmail() right after
  // the user's first hunt completes. One-shot banner.
  if(window._firstEmailPrompt){
    h += '<div class="ldp-first-email" role="status">'
       + '<span class="ldp-first-email-icon">✉</span>'
       + '<div class="ldp-first-email-txt">'
       + '<strong>This is your highest-fit lead.</strong> '
       + 'Review the draft below, tweak the tone if you want, then copy it into your email client.'
       + '</div></div>';
    window._firstEmailPrompt=false;
  }

  // Above the fold
  h += '<div class="ldp-hero">';
  h += '<div class="ldp-hero-left">';
  h += '<div class="ldp-score ' + _scCls + '">' + sc + '</div>';
  h += '<div class="ldp-hero-text">';
  h += '<h1 class="ldp-title">' + esc(l.org_name || 'Unknown') + '</h1>';
  h += '<p class="ldp-sub">' + esc(l.event_type || '') + (l.found_date ? ' &middot; ' + dAgo(l.found_date) : '') + '</p>';
  h += '</div></div>';

  // Confidence badge with detailed explanation
  var _dc = l._data_confidence;
  if (typeof _dc === 'number') {
    var _dcCls = _dc >= 0.6 ? 'high' : _dc >= 0.4 ? 'mid' : 'low';
    var _dcLbl = _dc >= 0.6 ? 'High confidence' : _dc >= 0.4 ? 'Medium confidence' : 'Low confidence';
    var _cs = l._confidence_signals || 0;
    var _dcD2=[];
    if(l._quote_verified==='exact'||l._quote_verified==='close')_dcD2.push('Quote verified');
    else if(l._quote_verified==='partial')_dcD2.push('Quote partial');
    else _dcD2.push('Quote unverified');
    _dcD2.push(l.contact_email?'Email found':'No email');
    _dcD2.push(l.contact_name?'Name found':'No name');
    _dcD2.push((l.org_website||l.org_linkedin)?'Website/LinkedIn':'No web presence');
    var _tr3=l.timing_rationale||'';_dcD2.push(_tr3.length>20?'Timing explained':'No timing signal');
    h += '<span class="lm-pill lm-pill-conf-' + _dcCls + '" title="' + _dcD2.join(' · ') + '">' + _dcLbl + ' (' + _cs + '/5)</span>';
  }
  // Quote verification badge
  var _qv2=l._quote_verified||'';
  if(_qv2==='exact'||_qv2==='close')h+='<span class="lm-pill lm-pill-conf-high">✓ Quote verified</span>';
  else if(_qv2==='partial')h+='<span class="lm-pill lm-pill-conf-mid">~ Quote partial</span>';
  else if(_qv2==='unverified')h+='<span class="lm-pill lm-pill-conf-low">⚠ Quote unverified</span>';
  h += '</div>';

  // Why Fit (hero context)
  var _why = l.why_fit || '', _gap = l.production_gap || '';
  if (_why || _gap) {
    h += '<div class="ldp-why">';
    if (_why) h += esc(_why);
    if (_why && _gap) h += ' &mdash; ';
    if (_gap) h += '<em>' + esc(_gap) + '</em>';
    h += '</div>';
  }

  // Why this lead — rationale from data + profile
  var _rat=_buildLeadRationale(l);
  if(_rat){
    h+='<div class="ldp-learned-match"><span class="ldp-learned-icon">&#x1F50D;</span> <span class="ldp-learned-text">'+esc(_rat)+'</span></div>';
  }
  if(_learningProfile&&_learningProfile.instruction_summary){
    h+='<div class="ldp-learned-match"><span class="ldp-learned-icon">&#x1F9E0;</span> <span class="ldp-learned-text">Your learned preferences: '+esc(_learningProfile.instruction_summary.substring(0,150))+'</span></div>';
  }

  // Badges — vocabulary aligned with the CRM row pill and the modal
  // (Urgent / Timely, replacing the earlier Hot/Warm labels from 2658619).
  h += '<div class="ldp-badges">';
  if (l.country) h += '<span class="lm-pill lm-pill-loc">' + esc(l.country) + (l.city ? ' &middot; ' + esc(l.city) : '') + '</span>';
  if (l.is_recurring) h += '<span class="lm-pill lm-pill-rec">Ongoing Need</span>';
  var _ts = l.timing_score || 0;
  if (_ts >= 8) h += '<span class="lm-pill lm-pill-hot">Urgent &mdash; Act Now</span>';
  else if (_ts >= 6) h += '<span class="lm-pill lm-pill-warm">Timely</span>';
  if (l._hiring_signal_detected) h += '<span class="lm-pill lm-pill-hiring">Hiring Signal</span>';
  // Budget signal — matches the modal's outcome-learning label so users see the same vocabulary everywhere.
  var _bs = l.buyability_score || 0;
  if (_bs >= 6) h += '<span class="lm-pill lm-pill-budget" title="Budget signals detected">💰 Budget</span>';
  h += '</div>';

  // Ranking explanation
  if(l._rank_reasons&&l._rank_reasons.length){
    h+='<div class="ldp-rank-reasons">';
    h+='<span class="ldp-rank-label">Ranking factors:</span> ';
    h+=l._rank_reasons.map(function(r){
      var isPos=r.indexOf('+')>0;var isNeg=r.indexOf('-')>0;
      return '<span class="ldp-rank-tag'+(isPos?' ldp-rank-pos':'')+(isNeg?' ldp-rank-neg':'')+'">'+esc(r)+'</span>';
    }).join(' ');
    h+='</div>';
  }

  // Train-the-AI strip — prominent, above-the-fold so users can't miss it.
  // Every rating feeds the DNA refinement loop; burying it below the notes
  // (where it used to live) meant users rarely clicked, so the AI wasn't
  // learning what "good" meant for their business.
  var _fbP=l._user_feedback||'';
  h+='<div class="ldp-train-strip'+(_fbP?' ldp-train-done':'')+'">';
  h+='<div class="ldp-train-txt"><strong>Is this a good prospect for your business?</strong><span>Your rating trains Huntova to find better leads next time.</span></div>';
  h+='<div class="ldp-train-btns">';
  h+='<button class="ldp-train-btn ldp-train-good'+(_fbP==='good'?' on':'')+'" '+(_fbP?'disabled':'')+' onclick="sendLeadFeedback(\''+esc(id)+'\',\'good\',this)">'+(_fbP==='good'?'✓ Good Fit':'👍 Good Fit')+'</button>';
  h+='<button class="ldp-train-btn ldp-train-bad'+(_fbP==='bad'?' on':'')+'" '+(_fbP?'disabled':'')+' onclick="sendLeadFeedback(\''+esc(id)+'\',\'bad\',this)">'+(_fbP==='bad'?'✓ Bad Fit':'👎 Bad Fit')+'</button>';
  h+='</div>';
  h+='</div>';

  // Primary actions
  // Send-tracking buttons (Perplexity round 63) live INSIDE this action
  // strip so the funnel signal is captured at the same surface the user
  // already touches when they take outreach action. State drives which
  // outcome chip is highlighted: not-yet-sent → "Mark as sent" pulse;
  // sent → "Mark as replied" pulse.
  var _esLower=(es||'new');
  var _isSent=['email_sent','followed_up','replied','meeting_booked','won'].indexOf(_esLower)>=0;
  var _isReplied=['replied','meeting_booked','won'].indexOf(_esLower)>=0;
  h += '<div class="ldp-actions">';
  h += '<button class="btn btn-primary btn-sm" onclick="sendMailTo()" data-feature="email_draft_visible">Send Email</button>';
  h += '<button class="btn btn-secondary btn-sm" onclick="cpField(\'email_body\',\'Email\')" data-feature="email_draft_visible">Copy Email</button>';
  h += '<button class="btn btn-secondary btn-sm ldp-mark-sent'+(_isSent?' on':'')+'" '+(_isSent?'disabled':'')
     + ' onclick="markLeadEmailOutcome(\''+esc(id)+'\',\'sent\')">'
     + (_isSent?'✓ Sent':'Mark as sent')+'</button>';
  h += '<button class="btn btn-secondary btn-sm ldp-mark-replied'+(_isReplied?' on':'')+'" '+(_isReplied?'disabled':'')
     + ' onclick="markLeadEmailOutcome(\''+esc(id)+'\',\'replied\')">'
     + (_isReplied?'✓ Replied':'Mark as replied')+'</button>';
  h += '<button class="btn btn-ghost btn-sm" onclick="goBackToLeads()">Skip</button>';
  h += '<select class="ldp-status-select" onchange="updateLeadFromPage(\'' + esc(id) + '\',\'email_status\',this.value)">' + so + '</select>';
  h += '</div>';

  // Below the fold - two columns
  h += '<div class="ldp-body">';

  // Left column
  h += '<div class="ldp-left">';

  // Email section
  h += '<div class="ldp-section">';
  h += '<h3 class="ldp-section-title">Outreach Email</h3>';
  if (l.email_subject && l.email_body) {
    h += '<div class="lm-tones">';
    [['friendly','Friendly'],['consultative','Expert'],['broadcast','Pro']].forEach(function(p) {
      h += '<button class="lm-tp' + (p[0] === _wizardTone ? ' on' : '') + '" data-tone="' + p[0] + '" onclick="selectTone(this)">' + p[1] + '</button>';
    });
    h += '<button class="btn btn-secondary btn-sm lm-rw-btn" onclick="doRewrite(\'' + esc(id) + '\')" data-feature="email_rewrite">Rewrite</button>';
    h += '</div>';
    h += '<div class="lm-ecard"><div class="lm-esubj" id="ms-' + id + '">' + esc(l.email_subject) + '</div>';
    h += '<div class="lm-ebody" id="mb-' + id + '">' + esc(l.email_body) + '</div></div>';
  }
  h += '</div>';

  // Follow-ups
  var _fu2 = l.email_followup_2 || '', _fu3 = l.email_followup_3 || '', _fu4 = l.email_followup_4 || '';
  if (_fu2 || _fu3 || _fu4) {
    h += '<details class="ldp-section"><summary class="ldp-section-title">Follow-up Sequence</summary>';
    var _fuL2=_wizardTone==='broadcast'?['Day 3 \u2014 Value proposition','Day 7 \u2014 Case study','Day 14 \u2014 Final follow-up']:_wizardTone==='consultative'?['Day 3 \u2014 New insight','Day 7 \u2014 Helpful resource','Day 14 \u2014 Closing note']:['Day 3 \u2014 Different angle','Day 7 \u2014 Quick check-in','Day 14 \u2014 Last note'];
    if (_fu2) h += '<div class="lm-fu"><div class="lm-fu-label">' + _fuL2[0] + '</div><div class="lm-fu-body">' + esc(_fu2) + '</div></div>';
    if (_fu3) h += '<div class="lm-fu"><div class="lm-fu-label">' + _fuL2[1] + '</div><div class="lm-fu-body">' + esc(_fu3) + '</div></div>';
    if (_fu4) h += '<div class="lm-fu"><div class="lm-fu-label">' + _fuL2[2] + '</div><div class="lm-fu-body">' + esc(_fu4) + '</div></div>';
    h += '</details>';
  }

  // Notes
  h += '<div class="ldp-section">';
  h += '<h3 class="ldp-section-title">Notes</h3>';
  h += '<textarea class="lm-notes" id="mn-' + id + '" placeholder="Add notes about this lead...">' + esc(l.notes || '') + '</textarea>';
  h += '<button class="btn btn-secondary btn-sm" onclick="saveNotes(\'' + esc(id) + '\')">Save Notes</button>';
  h += '</div>';

  // Business context from wizard — shows what criteria were used for this lead
  (function(){
    try{
      var _wz=null;
      // Try to get wizard context from settings cache
      fetch('/api/settings').then(function(r){return r.json()}).then(function(s){
        if(!s||!s.wizard)return;
        _wz=s.wizard;
        var ctx=$('ldpBizCtx');if(!ctx)return;
        var parts=[];
        if(_wz.business_type)parts.push('<span class="ldp-biz-tag">'+esc(_wz.business_type)+'</span>');
        if(_wz.how_it_works)parts.push('<span class="ldp-biz-tag">'+esc(_wz.how_it_works)+'</span>');
        var svcs=Array.isArray(_wz.services)?_wz.services:typeof _wz.services==='string'?_wz.services.split(',').map(function(s2){return s2.trim()}).filter(Boolean):[];
        svcs.slice(0,4).forEach(function(s2){parts.push('<span class="ldp-biz-tag">'+esc(s2)+'</span>')});
        if(_wz.deal_size)parts.push('<span class="ldp-biz-tag">Deal: '+esc(_wz.deal_size)+'</span>');
        if(parts.length)ctx.innerHTML='<div class="ldp-biz-tags">'+parts.join('')+'</div>';
      }).catch(function(){});
    }catch(_){}
  })();

  h += '<div class="ldp-section ldp-biz-context">';
  h += '<h3 class="ldp-section-title">Your Business Context</h3>';
  h += '<div id="ldpBizCtx" style="font-size:12px;color:var(--t3)">Loading...</div>';
  h += '</div>';

  h += '</div>'; // end left

  // Right column
  h += '<div class="ldp-right">';

  // Contact
  h += '<div class="ldp-section">';
  h += '<h3 class="ldp-section-title">Contact & Links</h3>';
  if (l.contact_name) h += '<div class="ldp-field"><span class="ldp-field-label">Contact</span><span>' + esc(l.contact_name) + (l.contact_role ? ' &middot; ' + esc(l.contact_role) : '') + '</span></div>';
  if (l.contact_email) {
    var _genWarn = l._is_generic_email ? ' <span class="generic-email-warn" title="Role-based address — weaker reach + reply rates than a named inbox.">⚠ generic</span>' : '';
    h += '<div class="ldp-field"><span class="ldp-field-label">Email</span><span><a href="mailto:' + esc(l.contact_email) + '">' + esc(l.contact_email) + '</a>' + _genWarn + '</span></div>';
  }
  if (l.org_website) { var _wu = safeUrl(l.org_website); if (_wu) h += '<div class="ldp-field"><span class="ldp-field-label">Website</span><a href="' + _wu + '" target="_blank" rel="noopener">' + esc(l.org_website) + '</a></div>'; }
  var _lu = safeUrl(l.contact_linkedin || l.org_linkedin); if (_lu) h += '<div class="ldp-field"><span class="ldp-field-label">LinkedIn</span><a href="' + _lu + '" target="_blank" rel="noopener">Profile</a></div>';
  h += '</div>';

  // Evidence
  if (l.evidence_quote) {
    h += '<div class="ldp-section">';
    h += '<h3 class="ldp-section-title">Evidence</h3>';
    h += '<blockquote class="ldp-quote">"' + esc(l.evidence_quote) + '"</blockquote>';
    h += '</div>';
  }

  // Score breakdown (collapsible, default collapsed)
  h += '<details class="ldp-section">';
  h += '<summary class="ldp-section-title">Score Breakdown</summary>';
  var _scores = [
    {l: 'Business Fit', dk:'fit', v: l.fit_score || 0},
    {l: 'Buyability', dk:'buyability', v: l.buyability_score || 0},
    {l: 'Reachability', dk:'reachability', v: l.reachability_score || 0},
    {l: 'Service Opportunity', dk:'service_opportunity', v: l.service_opportunity_score || 0},
    {l: 'Timing', dk:'timing', v: l.timing_score || 0}
  ];
  // Contextual insight summary
  var _minDim = _scores.reduce(function(a, b) { return a.v <= b.v ? a : b; });
  var _maxDim = _scores.reduce(function(a, b) { return a.v >= b.v ? a : b; });
  var _avg = _scores.reduce(function(s, d) { return s + d.v; }, 0) / _scores.length;
  var _insight = '';
  if (_avg >= 8) _insight = 'Strong across all dimensions — top prospect';
  else if (_avg >= 6 && _minDim.v < 5) _insight = 'Good overall but ' + _minDim.l.toLowerCase() + ' is a gap — plan around it';
  else if (_avg >= 6) _insight = 'Solid prospect — ' + _maxDim.l.toLowerCase() + ' is the strongest signal';
  else if (_minDim.v < 4) _insight = _minDim.l + ' is weak — may not convert without a workaround';
  else _insight = 'Moderate fit — review the details before reaching out';
  h += '<div class="sb-insight">' + _insight + '</div>';
  _scores.forEach(function(d) {
    var col = d.v >= 8 ? 'var(--acc)' : d.v >= 6 ? 'var(--acc)' : d.v >= 4 ? 'var(--org)' : 'var(--red)';
    var verb = scoreLabel(d.dk, d.v);
    h += '<div class="sb-row"><span class="sb-dim">' + d.l + '</span><div class="sb-track"><div class="sb-fill" style="width:' + (d.v * 10) + '%;background:' + col + '"></div></div><span class="sb-verb" style="color:' + col + '">' + verb + '</span><span class="sb-sc" style="color:' + col + '">' + d.v + '</span></div>';
  });
  // Rationale
  if (l.fit_rationale) h += '<div class="sb-rationale"><span class="sb-rat-dim">Fit:</span> ' + esc(l.fit_rationale) + '</div>';
  if (l.timing_rationale) h += '<div class="sb-rationale"><span class="sb-rat-dim">Timing:</span> ' + esc(l.timing_rationale) + '</div>';
  if (l.buyability_rationale) h += '<div class="sb-rationale"><span class="sb-rat-dim">Buyability:</span> ' + esc(l.buyability_rationale) + '</div>';
  h += '</details>';

  // Deal intelligence (collapsible)
  if (l._pass2) {
    var p2 = l._pass2;
    h += '<details class="ldp-section">';
    h += '<summary class="ldp-section-title">Deal Intelligence</summary>';
    if (p2.recommended_approach) h += '<div class="ldp-field"><span class="ldp-field-label">Lead with</span><span>' + esc(p2.recommended_approach) + '</span></div>';
    if (p2.one_line_pitch) h += '<div class="ldp-field"><span class="ldp-field-label">Pitch</span><span>' + esc(p2.one_line_pitch) + '</span></div>';
    h += '</details>';
  }

  h += '</div>'; // end right
  h += '</div>'; // end body

  var _ldp=document.getElementById('leadDetailPage');
  if(!_ldp)return;
  _ldp.innerHTML = h;

  // Apply feature gating
  hvApplyGating();

  // Make email fields editable with auto-save
  (function(){
    var subj = document.getElementById('ms-' + id);
    var body = document.getElementById('mb-' + id);
    if (subj) { subj.setAttribute('contenteditable', 'true'); subj.setAttribute('spellcheck', 'true'); }
    if (body) { body.setAttribute('contenteditable', 'true'); body.setAttribute('spellcheck', 'true'); }
    var ecard = subj ? subj.closest('.lm-ecard') : null;
    if (ecard && !ecard.querySelector('.edit-hint')) {
      var hint = document.createElement('div');
      hint.className = 'edit-hint';
      hint.id = 'editHint-' + id;
      hint.textContent = 'Click to edit \u2014 changes saved automatically';
      ecard.insertBefore(hint, ecard.firstChild);
    }
    var _pgSaveTimer, _pgPendSubj=null, _pgPendBody=null;
    function _pgCapture(){
      // Only capture from nodes that are still attached to the document.
      // During the Rewrite flow the .lm-ecard innerHTML is replaced, which
      // detaches the subj/body references we closed over. textContent on a
      // detached node returns the stale pre-replace content — capturing
      // that and then flushing it to the server would revert the AI
      // rewrite the user just triggered.
      _pgPendSubj=(subj&&subj.isConnected)?subj.textContent:null;
      _pgPendBody=(body&&body.isConnected)?body.textContent:null;
    }
    function _pgFlush(){
      if(!currentModalLead||currentModalLead.lead_id!==id){_pgPendSubj=null;_pgPendBody=null;return}
      var ss=_pgPendSubj, sb=_pgPendBody;
      _pgPendSubj=null;_pgPendBody=null;
      if(ss===null&&sb===null)return;
      if(ss!==null&&ss!==currentModalLead.email_subject)currentModalLead.email_subject=ss;
      if(sb!==null&&sb!==currentModalLead.email_body)currentModalLead.email_body=sb;
      var h=document.getElementById('editHint-'+id);
      fetch('/api/update',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({lead_id:id,email_subject:(ss!==null&&ss!==undefined)?ss:currentModalLead.email_subject,email_body:(sb!==null&&sb!==undefined)?sb:currentModalLead.email_body})
      }).then(function(r){
        if(r.ok&&h){h.textContent='Changes saved';h.classList.add('edit-saved');clearTimeout(h._t);h._t=setTimeout(function(){h.textContent='Click to edit \u2014 changes saved automatically';h.classList.remove('edit-saved')},2000)}
      }).catch(function(){if(h){h.textContent='Save failed \u2014 will retry';h.classList.remove('edit-saved')}});
    }
    function _pgDebouncedSave(){_pgCapture();clearTimeout(_pgSaveTimer);_pgSaveTimer=setTimeout(function(){_pgSaveTimer=null;_pgFlush()},800)}
    window._flushModalSave=function(){if(_pgSaveTimer){clearTimeout(_pgSaveTimer);_pgSaveTimer=null}_pgCapture();_pgFlush()};
    window._cancelModalSave=function(){if(_pgSaveTimer){clearTimeout(_pgSaveTimer);_pgSaveTimer=null}_pgPendSubj=null;_pgPendBody=null};
    window._getPendingEdits=function(){_pgCapture();return{subject:_pgPendSubj,body:_pgPendBody}};
    if (subj) subj.addEventListener('input', _pgDebouncedSave);
    if (body) body.addEventListener('input', _pgDebouncedSave);
  })();

  // Scroll to top
  window.scrollTo(0, 0);
}

function goBackToLeads() {
  if(window._flushModalSave)window._flushModalSave();
  history.pushState(null, '', '/leads');
  document.getElementById('pg-lead-detail').style.display = 'none';
  document.getElementById('pg-lead-detail').classList.remove('on');
  document.querySelector('[data-page="crm"]').click();
  // Restore scroll position
  setTimeout(function() {
    var crmBody = document.querySelector('.crm-body');
    if (crmBody && window._leadsScrollY) crmBody.scrollTop = window._leadsScrollY;
  }, 100);
}

function updateLeadFromPage(lid, field, val) {
  updateLead(lid, field, val).then(function() {
    var l = ALL.find(function(x) { return x.lead_id === lid; });
    if (l) renderLeadPage(l);
  });
}

async function updateLeadModal(lid,field,val){
  // B4 fix: capture pending edits BEFORE cancelling timer or touching DOM
  var pendingEdits=window._getPendingEdits?window._getPendingEdits():{subject:null,body:null};
  // Cancel pending auto-save timer — we'll merge email edits into this request
  if(window._cancelModalSave)window._cancelModalSave();
  // Read pending email edits: prefer captured edits, fall back to DOM
  var mergeBody={lead_id:lid};
  mergeBody[field]=val;
  var _subj=document.getElementById('ms-'+lid);
  var _body=document.getElementById('mb-'+lid);
  // Use captured pending edits first (reliable), DOM second (may be stale)
  var sv=pendingEdits.subject||(_subj&&_subj.getAttribute('contenteditable')?_subj.textContent:null);
  var bv=pendingEdits.body||(_body&&_body.getAttribute('contenteditable')?_body.textContent:null);
  if(sv!==null&&currentModalLead&&sv!==currentModalLead.email_subject)mergeBody.email_subject=sv;
  if(bv!==null&&currentModalLead&&bv!==currentModalLead.email_body)mergeBody.email_body=bv;
  // Single request with all fields
  try{
    const r=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(mergeBody)});
    const d=await r.json();
    if(d.ok&&d.lead){const i=ALL.findIndex(l=>l.lead_id===lid);if(i>=0){ALL[i]=d.lead}applyFilters();toast('✓ Updated')}
    else{toast('Update failed: '+(d.error||'unknown'))}
  }catch(e){toast('Error')}
  // Refresh modal with updated data only if still open for this lead
  if(currentModalLead&&currentModalLead.lead_id===lid){
    const l=ALL.find(x=>x.lead_id===lid);
    if(l)refreshLeadView(lid);
  }
}
var _badFitReasons=['Wrong industry','Too small','No budget','Wrong geography','Unreachable','No service gap','Competitor','Other'];

function _showBadFitReasons(lid,btn){
  // Remove any existing dropdown
  var existing=document.querySelector('.bad-fit-reasons');if(existing)existing.remove();
  var container=document.createElement('div');
  container.className='bad-fit-reasons';
  var label=document.createElement('div');
  label.style.cssText='font-size:11px;color:var(--t3);margin-bottom:6px;font-weight:600';
  label.textContent='Why is this a bad fit?';
  container.appendChild(label);
  _badFitReasons.forEach(function(r){
    var opt=document.createElement('button');
    opt.className='bad-fit-opt';
    opt.textContent=r;
    opt.onclick=function(e){
      e.stopPropagation();
      btn._reasonSelected=true;
      btn._selectedReason=r;
      sendLeadFeedback(lid,'bad',btn);
    };
    container.appendChild(opt);
  });
  // Insert after the button. Accept all three feedback surfaces: the
  // modal's .lm-feedback block, the old .ldp-feedback section, and the
  // new above-the-fold .ldp-train-strip (added in 4c808ea).
  var parent=btn.closest('.lm-feedback,.ldp-feedback,.ldp-train-strip');
  if(parent)parent.appendChild(container);
  else btn.parentElement.appendChild(container);
}

function sendLeadFeedback(lid,signal,btn){
  if(!btn)return;
  // For bad fit, show reason dropdown first
  if(signal==='bad'&&!btn._reasonSelected){
    _showBadFitReasons(lid,btn);return;
  }
  var reason=btn._selectedReason||'';
  // Disable both buttons, highlight the selected one. Parent lookup needs to
  // cover the modal block (.lm-feedback) AND the two lead-detail-page variants
  // — legacy .ldp-feedback and the current .ldp-train-strip. Without matching
  // .ldp-train-strip the new above-the-fold feedback CTA silently did nothing.
  var parent=btn.closest('.lm-feedback,.ldp-feedback,.ldp-train-strip');
  if(parent){
    parent.querySelectorAll('.lm-fb-btn,.ldp-train-btn').forEach(function(b){
      b.disabled=true;b.classList.remove('lm-fb-selected','on');
    });
  }
  // Remove reason dropdown if showing
  var rd=parent?parent.querySelector('.bad-fit-reasons'):null;if(rd)rd.remove();
  // Apply the selected-state class that matches whichever surface owns the
  // button. .lm-fb-selected is for modal; .on is for the train-strip.
  if(btn.classList.contains('ldp-train-btn')){btn.classList.add('on')}
  else{btn.classList.add('lm-fb-selected')}
  btn.textContent=signal==='good'?'✓ Good Fit':'✓ Bad Fit'+(reason?' ('+reason+')':'');
  fetch('/api/lead-feedback',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lead_id:lid,signal:signal,reason:reason})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){
      var msg='Feedback saved — '+d.total_feedback+' total ratings';
      if(d.refining)msg+=' (AI is learning from your feedback...)';
      if(d.profile_updating)msg+='\nPreferences updating...';
      toast(msg);
      // Refresh learning profile after a short delay (profile builds async)
      if(d.profile_updating)setTimeout(_loadLearningProfile,3000);
      // Store feedback on lead object + persistent cache
      _feedbackCache[lid]=signal;
      var lead=ALL.find(function(l){return l.lead_id===lid});
      if(lead)lead._user_feedback=signal;
    }else{
      toast(d.error||'Feedback error');
      if(parent){
        parent.querySelectorAll('.lm-fb-btn,.ldp-train-btn').forEach(function(b){
          b.disabled=false;b.classList.remove('lm-fb-selected','on');
        });
      }
      btn.textContent=signal==='good'?'👍 Good Fit':'👎 Bad Fit';
    }
  }).catch(function(){
    toast('Feedback failed — check your connection');
    if(parent){
      parent.querySelectorAll('.lm-fb-btn,.ldp-train-btn').forEach(function(b){
        b.disabled=false;b.classList.remove('lm-fb-selected','on');
      });
    }
    btn.textContent=signal==='good'?'👍 Good Fit':'👎 Bad Fit';
  });
}
async function saveModalNotes(lid){const ta=$('mn-'+lid);if(ta)await updateLead(lid,'notes',ta.value)}

try{document.addEventListener('keydown',function(e){if(e.key==='Escape'&&$('leadModalBg').classList.contains('on'))closeLeadModal()});}catch(_e){}


// ═══ AI DEEP RESEARCH ═══
let researchCache={};var _researchActive=null;var _researchTimeout=null;
function startResearch(lid){
  if(_hvFeatures.research===false){hvShowUpgrade('research');return}
  // Pre-flight credit check — only meaningful in cloud/billing mode
  if(_hvRuntime && _hvRuntime.billing_enabled){
    var _cr=_hvAccount?(_hvAccount.credits_remaining||0):0;
    if(_cr<1){toast('Out of credits — top up or upgrade to run Deep Research');try{hvOpenPricing()}catch(_){}return}
  }
  if(_researchActive){toast('Research already in progress for another lead — please wait');return}
  const btn=$('rb-'+lid);const prog=$('rp-'+lid);const res=$('rr-'+lid);
  if(!btn||!prog)return;
  // Confirm credit spend
  var _confMsg=(_hvRuntime && _hvRuntime.billing_enabled)
    ?'Deep Research costs 1 credit. This will re-scrape the prospect\'s website, do an AI deep-analysis, and rewrite the email.\n\nProceed?'
    :'Deep Research will re-scrape the prospect\'s website, run an AI deep-analysis, and rewrite the email. Costs ~$0.04 of API spend on your provider.\n\nProceed?';
  if(!confirm(_confMsg))return;
  _researchActive=lid;
  // Safety timeout: 5 minutes (not 90s). The backend agent can legitimately
  // take 2–3 minutes. The old 90s timer re-enabled the button while the
  // agent was still running, so users double-clicked and burned a second
  // credit. The new timer does NOT re-enable the button — it only shows a
  // warning strip and tells the user to refresh if they want to retry.
  clearTimeout(_researchTimeout);
  _researchTimeout=setTimeout(function(){
    if(_researchActive!==lid)return;
    _researchTimeout=null;
    var _rp=$('rp-'+lid);
    if(_rp){
      _rp.textContent='';
      var warn=document.createElement('span');
      warn.style.color='var(--org)';
      warn.textContent='\u26a0 Still running \u2014 this usually resolves within 5 minutes. Refresh the page to retry.';
      _rp.appendChild(warn);
      _rp.classList.add('on');
    }
    // Deliberately do NOT clear _researchActive or re-enable the button —
    // that was the double-charge bug.
  },300000);
  btn.disabled=true;btn.textContent='⏳ Researching...';
  prog.classList.add('on');prog.textContent='Starting deep research...';
  if(res)res.classList.remove('on');
  fetch('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:lid})})
    .then(function(r){return r.json().then(function(d){return{status:r.status,data:d}})})
    .then(function(r){
      if(r.data.error){
        _researchActive=null;
        prog.textContent='';prog.classList.remove('on');
        btn.disabled=false;btn.textContent='🔬 Deep Research';
        if(r.status===402)toast('No credits remaining — top up to use Deep Research');
        else toast('Error: '+r.data.error);
      }
    })
    .catch(function(e){_researchActive=null;prog.textContent='';prog.classList.remove('on');btn.disabled=false;btn.textContent='🔬 Deep Research';toast('Network error')});
}
function showResearchResult(lid,results){
  _researchActive=null;clearTimeout(_researchTimeout);_researchTimeout=null;
  var btn=$('rb-'+lid);var prog=$('rp-'+lid);var res=$('rr-'+lid);
  if(btn){btn.disabled=false;btn.textContent='🔬 Deep Research'}
  if(prog){prog.textContent='';prog.classList.remove('on')}

  // Handle errors
  if(results.error){
    toast('Research error: '+results.error.slice(0,80));
    if(res){res.innerHTML='<div class="rr-err">Research failed: '+esc(results.error.slice(0,200))+'<br><span style="font-size:11px;color:var(--t3);margin-top:4px;display:block">Your credit has been used. The error may be temporary — try again later.</span></div>';res.classList.add('on')}
    return;
  }
  if(!results.new_email||!results.new_email.email_body){
    toast('Research finished but AI response was incomplete');
    if(res){res.innerHTML='<div class="rr-err">AI returned an incomplete response. Try again.</div>';res.classList.add('on')}
    return;
  }

  var ne=results.new_email;
  var uf=results.updated_fields||{};
  var an=results.analysis||{};
  var kf=results.key_findings||ne.key_findings||[];
  researchCache[lid]=ne;

  // Build the research report UI
  var h='<div class="rr-report">';

  // Verdict badge
  var vrd=an.verdict||'unknown';
  var vrdColor=vrd==='strong_fit'||vrd==='good_fit'?'var(--acc)':vrd==='possible_fit'?'var(--org)':'var(--red)';
  var vrdLabel={'strong_fit':'Strong Fit','good_fit':'Good Fit','possible_fit':'Possible Fit','weak_fit':'Weak Fit','bad_fit':'Not a Fit'}[vrd]||'Analysed';
  h+='<div class="rr-verdict" style="border-color:'+vrdColor+'">';
  h+='<span class="rr-verdict-badge" style="background:'+vrdColor+'">'+esc(vrdLabel)+'</span>';
  if(uf.fit_score!==undefined)h+=' <span class="rr-score" style="color:'+vrdColor+'">Score: '+uf.fit_score+'/10</span>';
  h+='</div>';

  // Why they fit
  if(an.why_fit){
    h+='<div class="rr-section"><div class="rr-label">Why This Prospect Fits</div><div class="rr-text">'+esc(an.why_fit)+'</div></div>';
  }
  // Service opportunity
  if(an.service_opportunity){
    h+='<div class="rr-section"><div class="rr-label">Service Opportunity</div><div class="rr-text" style="color:var(--acc)">'+esc(an.service_opportunity)+'</div></div>';
  }

  // Key findings
  if(kf.length){
    h+='<div class="rr-section"><div class="rr-label">Key Findings</div>';
    kf.forEach(function(f){h+='<div class="rr-finding">'+esc(f)+'</div>'});
    h+='</div>';
  }

  // What was updated
  var updates=[];
  if(ne.email_subject)updates.push('Email rewritten');
  if(uf.contact_email)updates.push('Email: '+uf.contact_email);
  if(uf.contact_name)updates.push('Contact: '+uf.contact_name+(uf.contact_role?' ('+uf.contact_role+')':''));
  if(uf.org_linkedin)updates.push('Company LinkedIn found');
  if(uf.contact_linkedin)updates.push('Contact LinkedIn found');
  if(uf.platform_used)updates.push('Tools: '+uf.platform_used);
  if(an.competitors_using)updates.push('Competitors: '+an.competitors_using);
  if(an.company_size)updates.push('Size: '+an.company_size);
  if(updates.length){
    h+='<div class="rr-section"><div class="rr-label">Updated Fields</div>';
    updates.forEach(function(u){h+='<div class="rr-update">'+esc(u)+'</div>'});
    h+='</div>';
  }
  h+='<div class="rr-saved">All changes auto-saved</div>';
  h+='</div>';

  if(res){res.innerHTML=h;res.classList.add('on')}

  // Update lead data in memory and refresh modal
  var payload={lead_id:lid};
  if(ne.email_subject)payload.email_subject=ne.email_subject;
  if(ne.email_body)payload.email_body=ne.email_body;
  if(ne.linkedin_note)payload.linkedin_note=ne.linkedin_note;
  for(var fk in uf){if(uf[fk]!==null&&uf[fk]!==undefined)payload[fk]=uf[fk]}

  fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){return r.json()}).then(function(d){
      if(d.ok&&d.lead){
        var i=ALL.findIndex(function(l){return l.lead_id===lid});if(i>=0)ALL[i]=d.lead;
        var li=liveLeads.findIndex(function(l){return l.lead_id===lid});if(li>=0)liveLeads[li]=d.lead;
        applyFilters();
        var _toastMsg='Research complete — '+updates.length+' fields updated';
        if(!res){var _vl={'strong_fit':'Strong Fit','good_fit':'Good Fit','possible_fit':'Possible','weak_fit':'Weak','bad_fit':'Not a Fit'}[vrd];if(_vl)_toastMsg+=' · '+_vl}
        toast(_toastMsg);
        // Refresh credits display
        try{hvLoadAccount()}catch(_){}
        // Refresh modal after brief delay so user reads the report
        setTimeout(function(){
          if(currentModalLead&&currentModalLead.lead_id===lid)refreshLeadView(lid);
        },3000);
      }
    }).catch(function(e){toast('Error saving: '+e)});
}

// Load dashboard when tab is clicked + auto-refresh every 60s if visible
document.querySelectorAll('.nav-btn').forEach(b=>{
  const orig=b.onclick;
  b.addEventListener('click',()=>{if(b.dataset.page==='dash')loadDashboard()});
});
setInterval(function(){var dp=$('pg-dash');if(dp&&dp.classList.contains('on'))loadDashboard()},60000);



function loadDashboard(){
  try{
    var leads=ALL||[];
    var total=leads.length;
    var c={email_sent:0,followed_up:0,replied:0,meeting_booked:0,won:0,lost:0,ignored:0};
    var withEmail=0,recurring=0;
    var countries={},scores=[];
    leads.forEach(function(l){
      var st=l.email_status||'new';
      if(c[st]!==undefined)c[st]++;
      if(l.contact_email)withEmail++;
      if(l.is_recurring)recurring++;
      var co=l.country||'Unknown';
      countries[co]=(countries[co]||0)+1;
      if(l.fit_score)scores.push(l.fit_score);
    });
    var emailed=c.email_sent+c.followed_up+c.replied+c.meeting_booked+c.won;
    var replied=c.replied+c.meeting_booked+c.won;
    var meetings=c.meeting_booked+c.won;
    var won=c.won;
    var lost=c.lost;
    // Update cards
    var s=function(id,v){var el=$(id);if(el)el.textContent=v};
    s('dTotal',total);s('dEmail',withEmail);s('dRecur',recurring);
    // Credits from account data (cloud only — gated by hv-billing-only on the card)
    if(_hvAccount){
      var cr=_hvAccount.credits_remaining||0;
      s('dCredits',cr);
      var tier=_hvAccount.tier||'free';
      var tierMax={free:3,growth:25,agency:50}[tier]||3;
      var barEl=$('dCreditsBar');if(barEl)barEl.style.width=Math.min(100,Math.round(cr/Math.max(tierMax,1)*100))+'%';
    }
    // Providers count (local-only stat replacing Credits Left card)
    if(_hvRuntime && _hvRuntime.mode==='local'){
      fetch('/api/setup/status').then(function(r){return r.json()}).then(function(d){
        var n=((d&&d.providers_configured)||[]).length;
        s('dProvidersCount',n);
        var sub=$('dProvidersSub');
        if(sub)sub.textContent=n===0?'None configured — open Settings':(n===1?'1 active':n+' active');
      }).catch(function(){s('dProvidersCount','—')});
    }
    s('dEmailed',emailed);s('dEmailRate',total?Math.round(emailed/total*100)+'%':'0%');
    s('dReplied',replied);
    s('dReplyRate',emailed?Math.round(replied/emailed*100)+'%':'0%');
    s('dReplyDays','—');
    s('dMeetings',meetings);
    s('dMeetRate',replied?Math.round(meetings/replied*100)+'%':'0%');
    s('dWon',won);s('dCloseRate',meetings?Math.round(won/meetings*100)+'%':'0%');
    s('dLost',lost);
    // Revenue — show deal count, not fabricated estimates
    s('dRevWon',won>0?won+' deal'+(won!==1?'s':''):'—');
    s('dRevPipe',(replied+meetings)>0?(replied+meetings)+' in pipeline':'—');
    // Funnel
    var fEl=$('dFunnel');
    if(fEl){
      var stages=[
        {l:'Total',n:total,co:'var(--t1)'},
        {l:'Emailed',n:emailed,co:'var(--org)'},
        {l:'Replied',n:replied,co:'var(--cyn)'},
        {l:'Meeting',n:meetings,co:'var(--pur)'},
        {l:'Won',n:won,co:'var(--acc)'}
      ];
      var maxN=Math.max(total,1);
      fEl.innerHTML=stages.map(function(st){
        var pct=Math.round(st.n/maxN*100);
        var rate=st.l==='Total'?'':Math.round(st.n/Math.max(total,1)*100)+'%';
        return '<div class="funnel-row"><div class="funnel-label">'+st.l+'</div>'+
          '<div class="funnel-bar-wrap"><div class="funnel-bar" style="width:'+pct+'%;background:'+st.co+'"><span>'+st.n+'</span></div></div>'+
          '<div class="funnel-rate">'+rate+'</div></div>';
      }).join('');
    }
    // Country bars
    var cEl=$('dCountries');
    if(cEl){
      var sorted=Object.entries(countries).sort(function(a,b){return b[1]-a[1]}).slice(0,12);
      var maxC=sorted.length?sorted[0][1]:1;
      cEl.innerHTML=sorted.map(function(pair){
        var pct=Math.round(pair[1]/maxC*100);
        return '<div style="display:flex;align-items:center;gap:8px;margin:4px 0">'+
          '<div class="country-name" style="width:80px;font-size:11px;font-weight:600;color:var(--t2);text-align:right">'+esc(pair[0])+'</div>'+
          '<div class="country-bar-wrap"><div class="country-bar" style="width:'+pct+'%;background:var(--acc)"></div></div>'+
          '<div style="width:30px;font-size:11px;color:var(--t3);text-align:center">'+pair[1]+'</div></div>';
      }).join('');
    }
    // Score distribution + outcome by score band
    var sEl=$('dScores');
    if(sEl){
      var bands=[{l:'0-4',min:0,max:4},{l:'5-6',min:5,max:6},{l:'7-8',min:7,max:8},{l:'9-10',min:9,max:10}];
      var bandColors=['var(--t3)','var(--org)','var(--cyn)','var(--acc)'];
      var bandData=bands.map(function(b){
        var inBand=leads.filter(function(l){var s=l.fit_score||0;return s>=b.min&&s<=b.max});
        var sent=inBand.filter(function(l){return ['email_sent','followed_up','replied','meeting_booked','won'].indexOf(l.email_status)>=0}).length;
        var replied2=inBand.filter(function(l){return ['replied','meeting_booked','won'].indexOf(l.email_status)>=0}).length;
        var met=inBand.filter(function(l){return ['meeting_booked','won'].indexOf(l.email_status)>=0}).length;
        var won2=inBand.filter(function(l){return l.email_status==='won'}).length;
        var goodFit=inBand.filter(function(l){return l._user_feedback==='good'}).length;
        var badFit=inBand.filter(function(l){return l._user_feedback==='bad'}).length;
        return{label:b.l,total:inBand.length,sent:sent,replied:replied2,meetings:met,won:won2,goodFit:goodFit,badFit:badFit};
      });
      var maxB=Math.max.apply(null,bandData.map(function(b){return b.total}))||1;
      var h2='<div class="dash-card-t" style="margin-bottom:8px">Score Band Performance</div>';
      if(total<5){
        h2+='<div style="font-size:12px;color:var(--t3);text-align:center;padding:10px">Not enough data yet — need at least 5 leads</div>';
      } else {
        h2+=bandData.map(function(b,i){
          var pct=Math.round(b.total/maxB*100);
          var sendRate=b.total?Math.round(b.sent/b.total*100)+'%':'—';
          var replyRate=b.sent?Math.round(b.replied/b.sent*100)+'%':'—';
          var gfRate=(b.goodFit+b.badFit)>0?Math.round(b.goodFit/(b.goodFit+b.badFit)*100)+'%':'—';
          return '<div style="display:flex;align-items:center;gap:6px;margin:4px 0">'+
            '<div style="width:30px;font:600 11px/1 var(--mono);color:'+bandColors[i]+';text-align:right">'+b.label+'</div>'+
            '<div class="country-bar-wrap"><div class="country-bar" style="width:'+pct+'%;background:'+bandColors[i]+'"></div></div>'+
            '<div style="width:24px;font-size:11px;color:var(--t3);text-align:center">'+b.total+'</div>'+
            '<div style="width:50px;font-size:10px;color:var(--t3);text-align:center" title="Send rate">'+sendRate+'</div>'+
            '<div style="width:50px;font-size:10px;color:var(--t3);text-align:center" title="Reply rate">'+replyRate+'</div>'+
            '<div style="width:50px;font-size:10px;color:var(--t3);text-align:center" title="Good fit %">'+gfRate+'</div></div>';
        }).join('');
        h2+='<div style="display:flex;gap:6px;margin-top:4px;padding-left:36px">'+
          '<div style="width:auto;flex:1"></div>'+
          '<div style="width:24px"></div>'+
          '<div style="width:50px;font-size:9px;color:var(--t3);text-align:center">Sent</div>'+
          '<div style="width:50px;font-size:9px;color:var(--t3);text-align:center">Reply</div>'+
          '<div style="width:50px;font-size:9px;color:var(--t3);text-align:center">Good Fit</div></div>';
      }
      sEl.innerHTML=h2;
    }
    // Feedback loop summary
    var fbEl=$('dFeedback');
    if(fbEl){
      var gf=leads.filter(function(l){return l._user_feedback==='good'}).length;
      var bf=leads.filter(function(l){return l._user_feedback==='bad'}).length;
      var fbTotal=gf+bf;
      var rated=leads.filter(function(l){return l._user_feedback}).length;
      var unrated=total-rated;
      var fbH='';
      if(fbTotal===0){
        fbH='<div style="color:var(--t3);padding:10px 0">No feedback yet. Rate leads as Good Fit or Bad Fit to train Huntova.</div>';
      } else {
        var gfPct=Math.round(gf/fbTotal*100);
        fbH+='<div style="margin:6px 0"><span style="font:700 18px/1 var(--mono);color:var(--acc)">'+gfPct+'%</span> <span style="color:var(--t3)">Good Fit rate</span></div>';
        fbH+='<div style="display:flex;gap:12px;margin:8px 0">';
        fbH+='<div><span style="font:600 14px/1 var(--mono);color:var(--acc)">'+gf+'</span> <span style="color:var(--t3);font-size:11px">good</span></div>';
        fbH+='<div><span style="font:600 14px/1 var(--mono);color:var(--red)">'+bf+'</span> <span style="color:var(--t3);font-size:11px">bad</span></div>';
        fbH+='<div><span style="font:600 14px/1 var(--mono);color:var(--t3)">'+unrated+'</span> <span style="color:var(--t3);font-size:11px">unrated</span></div>';
        fbH+='</div>';
        if(gfPct>=70)fbH+='<div style="font-size:11px;color:var(--acc);margin-top:4px">Huntova is finding leads you like</div>';
        else if(gfPct<50&&fbTotal>=5)fbH+='<div style="font-size:11px;color:var(--org);margin-top:4px">Keep rating — Huntova is still learning your preferences</div>';
      }
      fbEl.innerHTML=fbH;
    }
  }catch(e){console.error('loadDashboard error:',e)}
}



// ═══ NEO FLOATING CHAT WIDGET ═══
var _nwLid=null;
function openNeoWidget(lid){
  if(_hvFeatures.ai_chat===false){hvShowUpgrade('ai_chat');return}
  _nwLid=lid;
  var w=$('neoWidget');if(!w)return;
  // Show lead name
  var lead=ALL.find(function(l){return l.lead_id===lid});
  var nm=$('nwLead');if(nm)nm.textContent=lead?lead.org_name:'';
  // Reset messages
  $('nwMsgs').innerHTML='<div class="neo-chat-hint">Ask Huntova to edit the email, change tone, ask about the lead...</div>';
  w.classList.add('on');
  // Center widget if not dragged yet
  if(!w.dataset.dragged){
    w.style.right='24px';w.style.bottom='24px';w.style.left='auto';w.style.top='auto';
  }
  setTimeout(function(){$('nwIn').focus()},150);
}
function closeNeoWidget(){var w=$('neoWidget');if(w)w.classList.remove('on');_nwLid=null}
function sendNeoWidget(){
  if(!_nwLid)return;
  var inp=$('nwIn');if(!inp)return;var msg=inp.value.trim();if(!msg)return;inp.value='';
  var msgs=$('nwMsgs');if(!msgs)return;
  // Remove hint
  var hint=msgs.querySelector('.neo-chat-hint');if(hint)hint.remove();
  msgs.innerHTML+='<div class="neo-chat-msg user">'+esc(msg)+'</div>';
  msgs.scrollTop=msgs.scrollHeight;
  msgs.innerHTML+='<div class="neo-chat-msg neo" id="nwTyping"><span class="nw-dots"><span></span><span></span><span></span></span></div>';
  msgs.scrollTop=msgs.scrollHeight;
  fetch('/api/neo-chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({lead_id:_nwLid,message:msg})
  }).then(function(r){return r.json()}).then(function(d){
    var t=$('nwTyping');if(t)t.remove();
    msgs.innerHTML+='<div class="neo-chat-msg neo">'+esc(d.reply||d.error||'No response')+'</div>';
    msgs.scrollTop=msgs.scrollHeight;
    // Update email in modal if changed
    if(d.updated_email){
      var ue=d.updated_email;
      // Update ALL array first
      var lead=ALL.find(function(l){return l.lead_id===_nwLid});
      if(lead){
        if(ue.email_subject)lead.email_subject=ue.email_subject;
        if(ue.email_body)lead.email_body=ue.email_body;
        if(ue.linkedin_note)lead.linkedin_note=ue.linkedin_note;
      }
      // Update DOM directly
      var subEl=$('ms-'+_nwLid);if(subEl)subEl.textContent=ue.email_subject||subEl.textContent;
      var bodEl=$('mb-'+_nwLid);if(bodEl)bodEl.textContent=ue.email_body||bodEl.textContent;
      // Fallback: query by class if IDs missing
      if(!subEl){var sq=document.querySelector('.lm-esubj');if(sq&&ue.email_subject)sq.textContent=ue.email_subject}
      if(!bodEl){var bq=document.querySelector('.lm-ebody');if(bq&&ue.email_body)bq.textContent=ue.email_body}
      msgs.innerHTML+='<div class="neo-chat-msg neo" style="color:var(--acc);font-size:11px">✓ Email updated in modal</div>';
      msgs.scrollTop=msgs.scrollHeight;
    }
  }).catch(function(e){
    var t=$('nwTyping');if(t)t.remove();
    msgs.innerHTML+='<div class="neo-chat-msg neo" style="color:var(--red)">Error: '+esc(e.message)+'</div>';
  });
}
// Draggable
(function(){
  var hdr=$('nwHdr');if(!hdr)return;
  var w=$('neoWidget');var dx=0,dy=0,mx=0,my=0;
  hdr.onmousedown=function(e){
    if(e.target.tagName==='BUTTON')return;
    e.preventDefault();mx=e.clientX;my=e.clientY;
    document.onmousemove=function(ev){
      dx=mx-ev.clientX;dy=my-ev.clientY;mx=ev.clientX;my=ev.clientY;
      var newTop=Math.max(0,Math.min(window.innerHeight-60,w.offsetTop-dy));
      var newLeft=Math.max(-w.offsetWidth+80,Math.min(window.innerWidth-80,w.offsetLeft-dx));
      w.style.top=newTop+'px';w.style.left=newLeft+'px';
      w.style.right='auto';w.style.bottom='auto';w.dataset.dragged='1';
    };
    document.onmouseup=function(){document.onmousemove=null;document.onmouseup=null};
  };
})();

// ═══ SETTINGS ═══
var _wizardTone='friendly';
// Map wizard outreach_tone answers to tone pill keys
var _toneMap={'Direct & to the point':'broadcast','Consultative & helpful':'consultative','Warm & friendly':'friendly','Premium & exclusive':'broadcast','Casual & personal':'friendly'};
function _loadWizardTone(){
  fetch('/api/settings').then(function(r){return r.json()}).then(function(s){
    if(s&&s.wizard&&s.wizard.outreach_tone){
      _wizardTone=_toneMap[s.wizard.outreach_tone]||s.wizard.outreach_tone||'friendly';
    }
  }).catch(function(){});
}
_loadWizardTone();

// Cached settings snapshot for current modal session.
var _settingsCache={};
// Bundled plugin metadata mirrored from server.py — kept here so the UI
// can render even if the registry route is offline.
var _BUNDLED_PLUGINS=[
  {name:'csv-sink',desc:'Append every saved lead to a CSV file. Drop-in for spreadsheet workflows.',inline:'csv'},
  {name:'dedup-by-domain',desc:'Drop search results whose domain already appeared earlier in this hunt.'},
  {name:'slack-ping',desc:'POST to a Slack incoming webhook on each saved lead.',inline:'slack'},
  {name:'generic-webhook',desc:'POST a JSON payload to your webhook URL on each saved lead. HMAC-signed if a secret is set in Settings → Webhooks.'},
  {name:'recipe-adapter',desc:'Reads HV_RECIPE_ADAPTATION env, applies winning_terms / suppress_terms / added_queries to the query list.'},
  {name:'adaptation-rules',desc:'Applies AI-generated scoring_rules from the recipe adaptation card.'}
];

function settingsTab(name){
  var tabs=document.querySelectorAll('#settingsVTabs .vtab');
  tabs.forEach(function(t){var on=t.dataset.tab===name;t.classList.toggle('on',on);t.setAttribute('aria-selected',on?'true':'false')});
  var panels=document.querySelectorAll('.vtab-panel');
  panels.forEach(function(p){var on=p.dataset.panel===name;p.classList.toggle('on',on);if(on)p.removeAttribute('hidden');else p.setAttribute('hidden','')});
  if(name==='providers')hvLoadProvidersTab();
}

// Providers tab — local mode BYOK key management. Reads /api/setup/status
// for what's configured, posts to /api/setup/key to save + 1-shot test.
var _HV_PROVIDERS=[
  {slug:'anthropic',name:'Anthropic Claude',hint:'Default provider. console.anthropic.com → API Keys.',keyUrl:'https://console.anthropic.com/settings/keys',recommended:true},
  {slug:'openai',name:'OpenAI',hint:'platform.openai.com → API keys.',keyUrl:'https://platform.openai.com/api-keys'},
  {slug:'gemini',name:'Google Gemini',hint:'aistudio.google.com → Get API key.',keyUrl:'https://aistudio.google.com/apikey'},
  {slug:'openrouter',name:'OpenRouter',hint:'One key, many models. openrouter.ai/keys.',keyUrl:'https://openrouter.ai/keys'},
  {slug:'groq',name:'Groq',hint:'Fastest inference. console.groq.com/keys.',keyUrl:'https://console.groq.com/keys'},
  {slug:'deepseek',name:'DeepSeek',hint:'platform.deepseek.com → API keys.',keyUrl:'https://platform.deepseek.com/api_keys'},
  {slug:'together',name:'Together',hint:'api.together.ai → settings → API keys.',keyUrl:'https://api.together.ai/settings/api-keys'},
  {slug:'mistral',name:'Mistral',hint:'console.mistral.ai → API keys.',keyUrl:'https://console.mistral.ai/api-keys'},
  {slug:'perplexity',name:'Perplexity',hint:'perplexity.ai → settings → API.',keyUrl:'https://www.perplexity.ai/settings/api'},
  {slug:'ollama',name:'Ollama (local)',hint:'Runs on your machine. Auth optional.',localServer:true},
  {slug:'lmstudio',name:'LM Studio (local)',hint:'Runs on your machine. Auth optional.',localServer:true},
  {slug:'llamafile',name:'llamafile (local)',hint:'Single-file local inference. No key.',localServer:true}
];

function hvLoadProvidersTab(){
  var box=$('hvProvidersList');if(!box)return;
  box.textContent='Loading…';
  fetch('/api/setup/status').then(function(r){return r.json()}).then(function(s){
    var configured=(s&&s.providers_configured_set)||{};
    box.textContent='';
    _HV_PROVIDERS.forEach(function(p){
      var row=document.createElement('div');
      row.className='hv-prov-row';
      row.style.cssText='display:flex;flex-direction:column;gap:6px;padding:12px;background:var(--bg2);border:1px solid var(--bd);border-radius:10px';
      var top=document.createElement('div');
      top.style.cssText='display:flex;align-items:center;gap:10px;justify-content:space-between';
      var left=document.createElement('div');
      var nm=document.createElement('div');
      nm.style.cssText='font-weight:600;color:var(--t1);font-size:14px';
      nm.textContent=p.name;
      if(p.recommended){
        var b=document.createElement('span');
        b.textContent=' default';
        b.style.cssText='margin-left:8px;font-size:10px;padding:2px 6px;border:1px solid var(--ac);color:var(--ac);border-radius:4px;font-weight:500;letter-spacing:.5px;text-transform:uppercase';
        nm.appendChild(b);
      }
      left.appendChild(nm);
      var hint=document.createElement('div');
      hint.style.cssText='font-size:12px;color:var(--t2);margin-top:2px';
      hint.textContent=p.hint;
      left.appendChild(hint);
      top.appendChild(left);
      var status=document.createElement('span');
      status.style.cssText='font-size:11px;padding:3px 8px;border-radius:6px;letter-spacing:.5px;text-transform:uppercase;font-weight:600';
      if(configured[p.slug]){
        status.textContent='✓ Configured';
        status.style.background='rgba(20,184,166,.15)';
        status.style.color='#14b8a6';
      } else {
        status.textContent='Not set';
        status.style.background='rgba(120,120,120,.12)';
        status.style.color='var(--t3)';
      }
      top.appendChild(status);
      row.appendChild(top);
      var inp=document.createElement('input');
      inp.type=p.localServer?'text':'password';
      inp.placeholder=p.localServer?'Optional auth token (leave blank for no-auth)':(configured[p.slug]?'•••••• (saved — type a new key to replace)':'sk-...');
      inp.style.cssText='width:100%;padding:8px 10px;background:var(--bg);border:1px solid var(--bd);border-radius:8px;color:var(--t1);font-size:13px;font-family:ui-monospace,SFMono-Regular,monospace';
      inp.dataset.slug=p.slug;
      row.appendChild(inp);
      var btnRow=document.createElement('div');
      btnRow.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap';
      var save=document.createElement('button');
      save.className='btn btn-secondary cb cb-s';
      save.textContent='Save & test';
      save.onclick=function(){hvSaveProviderKey(p.slug,inp,save,result)};
      btnRow.appendChild(save);
      if(p.keyUrl){
        var lnk=document.createElement('a');
        lnk.href=p.keyUrl;lnk.target='_blank';lnk.rel='noopener';
        lnk.textContent='Get key ↗';
        lnk.style.cssText='font-size:12px;color:var(--ac);text-decoration:none';
        btnRow.appendChild(lnk);
      }
      var result=document.createElement('span');
      result.style.cssText='font-size:12px;color:var(--t2);margin-left:auto';
      btnRow.appendChild(result);
      row.appendChild(btnRow);
      box.appendChild(row);
    });
  }).catch(function(e){box.textContent='Could not load providers: '+e});
}

function hvSaveProviderKey(slug,inp,btn,resultEl){
  var key=(inp.value||'').trim();
  btn.disabled=true;btn.textContent='Saving…';
  resultEl.textContent='';resultEl.style.color='var(--t2)';
  fetch('/api/setup/key',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({provider:slug,key:key,test:true})
  }).then(function(r){return r.json()}).then(function(d){
    btn.disabled=false;btn.textContent='Save & test';
    if(d.ok){
      resultEl.textContent=d.test_passed?'✓ Saved & verified':'✓ Saved (test skipped)';
      resultEl.style.color='#14b8a6';
      inp.value='';
      setTimeout(hvLoadProvidersTab,400);
    } else {
      resultEl.textContent=d.message||d.error||'Save failed';
      resultEl.style.color='#f87171';
    }
  }).catch(function(e){
    btn.disabled=false;btn.textContent='Save & test';
    resultEl.textContent='Network error: '+e;
    resultEl.style.color='#f87171';
  });
}

function _renderPlugins(){
  var box=$('pluginsList');if(!box)return;
  var en=_settingsCache.plugins_enabled||{};
  box.textContent='';
  _BUNDLED_PLUGINS.forEach(function(p){
    var row=document.createElement('div');row.className='plug-row';row.dataset.name=p.name;
    var top=document.createElement('div');top.className='plug-row-top';
    var nm=document.createElement('div');nm.className='plug-row-name';nm.textContent=p.name;
    var tg=document.createElement('div');tg.className='plug-toggle'+(en[p.name]?' on':'');tg.setAttribute('role','switch');tg.setAttribute('tabindex','0');tg.setAttribute('aria-checked',en[p.name]?'true':'false');
    tg.addEventListener('click',function(){tg.classList.toggle('on');tg.setAttribute('aria-checked',tg.classList.contains('on')?'true':'false')});
    tg.addEventListener('keydown',function(e){if(e.key===' '||e.key==='Enter'){e.preventDefault();tg.click()}});
    top.appendChild(nm);top.appendChild(tg);row.appendChild(top);
    var desc=document.createElement('div');desc.className='plug-row-desc';desc.textContent=p.desc;row.appendChild(desc);
    if(p.inline==='csv'){
      var inp=document.createElement('input');inp.type='text';inp.placeholder='Output CSV path (e.g. ~/huntova-leads.csv)';inp.value=_settingsCache.plugin_csv_sink_path||'';inp.dataset.role='csv-path';row.appendChild(inp);
    } else if(p.inline==='slack'){
      var inp2=document.createElement('input');inp2.type='text';inp2.placeholder=_settingsCache.plugin_slack_webhook_url_set?'•••••• (saved in keychain — leave blank to keep)':'https://hooks.slack.com/services/...';inp2.dataset.role='slack-webhook';row.appendChild(inp2);
    }
    box.appendChild(row);
  });
}

function _applyTheme(theme){
  var html=document.documentElement;
  if(theme==='light'){html.classList.add('light');html.classList.remove('dark')}
  else if(theme==='dark'){html.classList.add('dark');html.classList.remove('light')}
  else { // system
    html.classList.remove('light');html.classList.remove('dark');
    try{var m=window.matchMedia('(prefers-color-scheme: light)');if(m.matches)html.classList.add('light')}catch(_){}
  }
}
function _applyReducedMotion(on){
  document.documentElement.classList.toggle('reduce-motion',!!on);
}

async function openSettings(){
  try{const r=await fetch('/api/settings');const s=await r.json();
    _settingsCache=s||{};
    $('sBooking').value=s.booking_url||'';$('sName').value=s.from_name||'';
    $('sEmail').value=s.from_email||'';$('sPhone').value=s.phone||'';$('sWebsite').value=s.website||'';
    if($('sWebhookUrl'))$('sWebhookUrl').value=s.webhook_url||'';
    if($('sWebhookSecret')){$('sWebhookSecret').value='';var h=$('sWebhookSecretHint');if(h)h.textContent=s.webhook_secret_set?'Saved in keychain. Leave blank to keep, type a new value to replace.':'Stored in OS keychain.'}
    if($('sSmtpHost'))$('sSmtpHost').value=s.smtp_host||'';
    if($('sSmtpPort'))$('sSmtpPort').value=s.smtp_port||587;
    if($('sSmtpUser'))$('sSmtpUser').value=s.smtp_user||'';
    if($('sSmtpPassword')){$('sSmtpPassword').value='';var ph=$('sSmtpPasswordHint');if(ph)ph.textContent=s.smtp_password_set?'Saved in keychain. Leave blank to keep, type a new value to replace.':'Stored in OS keychain.'}
    if($('sTheme'))$('sTheme').value=s.theme||'system';
    if($('sReducedMotion'))$('sReducedMotion').checked=!!s.reduced_motion;
    if($('sTelemetry'))$('sTelemetry').checked=s.telemetry_opt_in!==false;
    _renderPlugins();
    if(s&&s.wizard&&s.wizard.outreach_tone)_wizardTone=_toneMap[s.wizard.outreach_tone]||s.wizard.outreach_tone||'friendly';
  }catch(e){}
  $('settingsModal').classList.add('on');
  settingsTab('profile');
  // Render learning profile panel
  _loadLearningProfile();
  setTimeout(function(){renderLearnedPreferences($('learnedPrefsPanel'))},300);
}
function closeSettings(){$('settingsModal').classList.remove('on')}

async function testWebhook(btn){
  var res=$('sWebhookResult');if(res){res.className='settings-test-result';res.textContent=''}
  if(btn){btn.disabled=true;btn.textContent='Testing…'}
  try{
    // Save URL+secret first so the server probe sees the latest values.
    var body={webhook_url:($('sWebhookUrl').value||'').trim()};
    var sec=($('sWebhookSecret').value||'').trim();if(sec)body.webhook_secret=sec;
    if(body.webhook_url&&!/^https?:\/\//i.test(body.webhook_url)){
      var e=$('sWebhookUrlErr');if(e){e.style.display='block';e.textContent='URL must start with http:// or https://'}
      if(btn){btn.disabled=false;btn.textContent='Test webhook'}
      return;
    }
    await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var r=await fetch('/api/webhooks/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var d=await r.json();
    if(res){res.classList.add(d.ok?'ok':'err');res.textContent=d.ok?('OK · '+d.status):(d.message||d.error||'failed')}
  }catch(e){if(res){res.classList.add('err');res.textContent='request failed'}}
  finally{if(btn){btn.disabled=false;btn.textContent='Test webhook'}}
}

async function testSmtp(btn){
  var res=$('sSmtpResult');if(res){res.className='settings-test-result';res.textContent=''}
  if(btn){btn.disabled=true;btn.textContent='Testing…'}
  try{
    var port=parseInt($('sSmtpPort').value||'587',10);
    var pe=$('sSmtpPortErr');
    if(!port||port<1||port>65535){if(pe){pe.style.display='block';pe.textContent='Port must be 1–65535'}if(btn){btn.disabled=false;btn.textContent='Test SMTP'}return}
    if(pe)pe.style.display='none';
    var body={smtp_host:($('sSmtpHost').value||'').trim(),smtp_port:port,smtp_user:($('sSmtpUser').value||'').trim()};
    var pw=($('sSmtpPassword').value||'').trim();if(pw)body.smtp_password=pw;
    await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var r=await fetch('/api/smtp/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var d=await r.json();
    if(res){res.classList.add(d.ok?'ok':'err');res.textContent=d.ok?(d.message||'OK'):(d.message||d.error||'failed')}
  }catch(e){if(res){res.classList.add('err');res.textContent='request failed'}}
  finally{if(btn){btn.disabled=false;btn.textContent='Test SMTP'}}
}

async function downloadAccountBundle(btn){
  if(btn){btn.disabled=true;btn.textContent='Preparing…'}
  try{
    var r=await fetch('/api/account/export');
    if(!r.ok){var d=null;try{d=await r.json()}catch(_){};toast((d&&d.message)||'Export failed ('+r.status+')');return}
    var blob=await r.blob();
    var cd=r.headers.get('Content-Disposition')||'';var m=cd.match(/filename=([^;]+)/i);
    var name=m?m[1].trim():'huntova_account.json';
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href)},2000);
    toast('Bundle downloaded');
  }catch(e){toast('Download failed')}
  finally{if(btn){btn.disabled=false;btn.textContent='Download bundle (.json)'}}
}

function deleteAllMyData(btn){
  // Two-step confirm. We use native prompt so the modal is genuinely
  // blocking and the second step requires the user to type a phrase.
  if(!confirm('Delete every lead matching your account email? This cannot be undone.'))return;
  var phrase=prompt('Type DELETE to confirm permanent erasure of all your leads.');
  if((phrase||'').trim().toUpperCase()!=='DELETE'){toast('Cancelled — phrase did not match.');return}
  if(btn){btn.disabled=true;btn.textContent='Deleting…'}
  var email=(_settingsCache.from_email||'').trim();
  if(!email){toast('Set your account email in the Profile tab first.');if(btn){btn.disabled=false;btn.textContent='Delete all my data'}return}
  fetch('/api/gdpr/erasure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email})})
    .then(function(r){return r.json()}).then(function(d){
      if(d&&d.ok){toast('Deleted '+(d.deleted||0)+' lead(s).');if(typeof loadCRM==='function')loadCRM()}
      else toast((d&&d.error)||'Deletion failed');
    }).catch(function(){toast('Deletion failed')})
    .then(function(){if(btn){btn.disabled=false;btn.textContent='Delete all my data'}});
}

// ═══ LEARNING PROFILE ═══
var _learningProfile=null;
function _loadLearningProfile(){
  fetch('/api/learning-profile').then(function(r){return r.json()}).then(function(d){
    if(d.ok&&d.profile)_learningProfile=d.profile;
  }).catch(function(){});
}
_loadLearningProfile();

function renderLearnedPreferences(container){
  if(!container)return;
  container.textContent='';
  if(!_learningProfile||!_learningProfile.instruction_summary){
    var empty=document.createElement('div');
    empty.style.cssText='padding:16px;color:var(--t3);font-size:13px;text-align:center';
    empty.textContent='Not enough feedback yet. Rate a few leads as Good Fit or Bad Fit to start training.';
    container.appendChild(empty);return;
  }
  // Header
  var hdr=document.createElement('div');
  hdr.style.cssText='display:flex;align-items:center;justify-content:space-between;margin-bottom:12px';
  var title=document.createElement('div');
  title.style.cssText='font:700 13px/1 var(--font);color:var(--t1)';
  title.textContent='What Huntova Learned';
  hdr.appendChild(title);
  var ver=document.createElement('span');
  ver.style.cssText='font:500 11px/1 var(--mono);color:var(--t3)';
  var sig=_learningProfile.signals_processed||0;
  var strength=sig>=15?'Strong signal':sig>=8?'Emerging pattern':'Early learning';
  ver.textContent='v'+(_learningProfile.version||1)+' \u00b7 '+sig+' ratings \u00b7 '+strength;
  hdr.appendChild(ver);container.appendChild(hdr);
  // Instruction summary
  var inst=document.createElement('div');
  inst.style.cssText='font-size:13px;color:var(--t2);line-height:1.6;padding:12px;background:var(--s2);border:1px solid var(--bd);border-radius:var(--r-sm);margin-bottom:10px';
  inst.textContent=_learningProfile.instruction_summary;
  container.appendChild(inst);
  // Preferences
  try{
    var prefs=typeof _learningProfile.preferences==='string'?JSON.parse(_learningProfile.preferences):_learningProfile.preferences;
    if(prefs){
      var grid=document.createElement('div');
      grid.style.cssText='display:grid;grid-template-columns:1fr 1fr;gap:8px';
      var fields=[
        {k:'preferred_industries',l:'Preferred Industries',c:'var(--acc)'},
        {k:'avoided_industries',l:'Avoided Industries',c:'var(--red)'},
        {k:'valued_signals',l:'Positive Signals',c:'var(--acc)'},
        {k:'avoided_signals',l:'Red Flags',c:'var(--red)'},
        {k:'preferred_countries',l:'Preferred Countries',c:'var(--blu)'},
        {k:'preferred_company_sizes',l:'Company Sizes',c:'var(--pur)'}
      ];
      fields.forEach(function(f){
        var val=prefs[f.k];
        if(!val||!val.length)return;
        var card=document.createElement('div');
        card.style.cssText='padding:8px 10px;background:var(--s2);border:1px solid var(--bd);border-radius:var(--r-sm)';
        var lbl=document.createElement('div');
        lbl.style.cssText='font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px';
        lbl.textContent=f.l;card.appendChild(lbl);
        var tags=document.createElement('div');
        tags.style.cssText='display:flex;flex-wrap:wrap;gap:3px';
        val.slice(0,6).forEach(function(v){
          var tag=document.createElement('span');
          tag.style.cssText='font-size:11px;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,.04);color:'+f.c;
          tag.textContent=v;tags.appendChild(tag);
        });
        card.appendChild(tags);grid.appendChild(card);
      });
      container.appendChild(grid);
    }
  }catch(_){}
}

// ═══ AGENT DNA PANEL ═══
function toggleDnaPanel(){
  var b=$('dnaBody');if(!b)return;
  if(b.style.display==='none'){b.style.display='block';loadDnaPanel();}
  else b.style.display='none';
}
function loadDnaPanel(){
  fetch('/api/agent-dna').then(function(r){return r.json()}).then(function(d){
    if(!d.ok)return;
    var el=$('dnaContent'),vEl=$('dnaVersion'),fbEl=$('dnaFeedback');
    if(d.dna){
      var ctx=(d.dna.business_context||'').substring(0,500);
      if(ctx.length>=500)ctx+='...';
      if(el)el.textContent=ctx||'DNA generated but no context available.';
      if(vEl)vEl.textContent='v'+d.dna.version+' · '+(d.dna.search_queries?d.dna.search_queries.length:0)+' queries';
    }else{
      if(el)el.textContent='Hunt profile not set up yet. Complete the wizard or click Regenerate.';
      if(vEl)vEl.textContent='';
    }
    if(fbEl&&d.feedback)fbEl.textContent='Feedback: '+d.feedback.good+' good, '+d.feedback.bad+' bad';
  }).catch(function(){});
}
function regenerateDna(btn){
  if(btn){btn.disabled=true;btn.textContent='Generating...';}
  fetch('/api/agent-dna/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
  .then(function(r){return r.json()}).then(function(d){
    if(d.ok){toast('Hunt profile generated — v'+d.version+' with '+d.queries_count+' queries');loadDnaPanel();}
    else toast(d.error||'Generation failed');
    if(btn){btn.disabled=false;btn.textContent='Regenerate';}
  }).catch(function(){toast('Error generating DNA');if(btn){btn.disabled=false;btn.textContent='Regenerate';}});
}
/* DNA panel auto-load removed — no dnaBody element exists in DOM */
async function saveSettings(){
  try{
    // ── Profile fields (existing behavior preserved)
    var body={
      booking_url:$('sBooking').value.trim(),
      from_name:$('sName').value.trim(),
      from_email:$('sEmail').value.trim(),
      phone:$('sPhone').value.trim(),
      website:$('sWebsite').value.trim()
    };
    // ── Plugins tab
    if($('pluginsList')){
      var plug={};var rows=document.querySelectorAll('#pluginsList .plug-row');
      rows.forEach(function(r){
        var name=r.dataset.name;var on=!!r.querySelector('.plug-toggle.on');
        plug[name]=on;
        var csv=r.querySelector('input[data-role=csv-path]');if(csv)body.plugin_csv_sink_path=csv.value.trim();
        var sl=r.querySelector('input[data-role=slack-webhook]');
        if(sl){var v=sl.value.trim();if(v){
          if(!/^https?:\/\//i.test(v)){toast('Slack webhook URL must start with https://');return}
          body.plugin_slack_webhook_url=v;
        }}
      });
      body.plugins_enabled=plug;
    }
    // ── Webhooks tab (URL well-formed check)
    var wErr=$('sWebhookUrlErr');if(wErr)wErr.style.display='none';
    if($('sWebhookUrl')){
      var wu=$('sWebhookUrl').value.trim();
      if(wu&&!/^https?:\/\//i.test(wu)){if(wErr){wErr.style.display='block';wErr.textContent='URL must start with http:// or https://'};toast('Fix webhook URL');return}
      body.webhook_url=wu;
    }
    if($('sWebhookSecret')){var ws=$('sWebhookSecret').value.trim();if(ws)body.webhook_secret=ws}
    // ── Outreach (SMTP) tab — port int 1-65535
    var pErr=$('sSmtpPortErr');if(pErr)pErr.style.display='none';
    if($('sSmtpHost'))body.smtp_host=$('sSmtpHost').value.trim();
    if($('sSmtpPort')){
      var p=parseInt($('sSmtpPort').value||'',10);
      if($('sSmtpPort').value&&(!p||p<1||p>65535)){if(pErr){pErr.style.display='block';pErr.textContent='Port must be 1–65535'};toast('Fix SMTP port');return}
      if(p)body.smtp_port=p;
    }
    if($('sSmtpUser'))body.smtp_user=$('sSmtpUser').value.trim();
    if($('sSmtpPassword')){var sp=$('sSmtpPassword').value.trim();if(sp)body.smtp_password=sp}
    // ── Preferences tab
    if($('sTheme'))body.theme=$('sTheme').value;
    if($('sReducedMotion'))body.reduced_motion=$('sReducedMotion').checked;
    if($('sTelemetry'))body.telemetry_opt_in=$('sTelemetry').checked;
    var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d=await r.json();
    if(d.ok){
      // Apply preferences immediately so the user sees the change.
      if(body.theme)_applyTheme(body.theme);
      if('reduced_motion' in body)_applyReducedMotion(body.reduced_motion);
      // Local mode: from_name mirrors to display_name. Reload account
      // so the dashboard greeting + avatar pick up the new name without
      // a full page refresh.
      if(_hvRuntime && _hvRuntime.single_user_mode && body.from_name){
        try{hvLoadAccount()}catch(_){}
      }
      toast('✅ Settings saved');closeSettings()
    } else {
      toast('Error: '+(d.error||'unknown'))
    }
  }catch(e){toast('Error saving settings')}
}

// Apply persisted theme + reduced-motion early so the UI stops flashing.
(function(){fetch('/api/settings').then(function(r){return r.json()}).then(function(s){if(!s)return;if(s.theme)_applyTheme(s.theme);if(s.reduced_motion)_applyReducedMotion(true)}).catch(function(){})})();
try{$('settingsModal').addEventListener('click',function(e){if(e.target===this)closeSettings()});}catch(_e){}

// ═══ SMART SCORING ═══
async function runSmartScore(){
  if(_hvFeatures.smart_score===false){hvShowUpgrade('smart_score');return}
  toast('Re-scoring all leads with AI...');
  try{
    var r=await fetch('/api/smart-score',{method:'POST',headers:{'Content-Type':'application/json'}});
    var d=await r.json();
    if(r.ok&&d.ok){toast('Smart Score complete — '+( d.updated||0)+' leads updated');loadCRM()}
    else{toast('Smart Score failed: '+(d.detail||d.error||'unknown error'))}
  }catch(e){toast('Smart Score request failed')}
}



function openStartPopup(){
  if(agentRunning){toast('Hunt already running — pause or stop it first.');return;}
  try{
    // Remember the trigger so closeStartPopup can restore focus for keyboard users.
    window._startPopupTrigger=document.activeElement;
    var bg=$('startBg');
    if(!bg){console.error('startBg not found');return;}
    bg.style.display='';bg.classList.add('on');
    // Keyboard: Escape closes start popup
    bg._escHandler=function(e){if(e.key==='Escape')closeStartPopup()};
    document.addEventListener('keydown',bg._escHandler);
    var c=$('startCountries');
    if(!c){console.error('startCountries not found');return;}
    // Build country buttons
    var html='<button class="start-selall" onclick="toggleAllCountries(this)">Select All</button>';
    var countries=Object.keys(_startCountries).sort();
    countries.forEach(function(name){
      var isOn=_startCountries[name];
      html+='<button class="start-c'+(isOn?' on':'')+'" onclick="toggleCountry(this,\''+name.replace(/'/g,"\\'")+'\')">'+name+'</button>';
    });
    c.innerHTML=html;
    // Reset budget inputs each open so stale values from a prior
    // session never silently re-apply. Empty = unlimited (default).
    var _ml=$('startMaxLeads');if(_ml)_ml.value='';
    var _to=$('startTimeoutMin');if(_to)_to.value='';
    updateStartSummary();
  }catch(e){console.error('openStartPopup error:',e);}
}
function toggleCountry(btn,name){
  btn.classList.toggle('on');
  _startCountries[name]=btn.classList.contains('on');
  updateStartSummary();
}
function closeStartPopup(){
  var bg=$('startBg');
  if(bg){
    bg.classList.remove('on');bg.style.display='';
    if(bg._escHandler){document.removeEventListener('keydown',bg._escHandler);bg._escHandler=null}
  }
  var _trg=window._startPopupTrigger;window._startPopupTrigger=null;
  if(_trg&&typeof _trg.focus==='function'){try{_trg.focus()}catch(_){}}
}
function toggleAllCountries(btn){
  var btns=document.querySelectorAll('.start-c');
  var allOn=Array.from(btns).every(function(b){return b.classList.contains('on')});
  btns.forEach(function(b){
    if(allOn)b.classList.remove('on');else b.classList.add('on');
    var name=b.textContent;
    if(_startCountries.hasOwnProperty(name))_startCountries[name]=!allOn;
  });
  btn.textContent=allOn?'Select All':'Deselect All';
  updateStartSummary();
}
function updateStartSummary(){
  try{
    var sel=Object.values(_startCountries).filter(Boolean).length;
    var ss=$('startSummary');
    var goBtn=document.querySelector('.start-go');
    // Local / BYOK mode: no credits or tier UI — just countries + go.
    if(!_hvRuntime || !_hvRuntime.billing_enabled){
      if(ss)ss.innerHTML='<span><b>'+sel+'</b> countries selected</span><span>Runs until stopped. API spend is on your provider.</span>';
      if(goBtn){goBtn.textContent='▶ Start Hunting';goBtn.onclick=launchAgent;}
      return;
    }
    var cr=_hvAccount?_hvAccount.credits_remaining:0;
    var tier=_hvAccount?_hvAccount.tier:'free';
    var tierName={free:'Free',growth:'Growth',agency:'Agency'}[tier]||'Free';
    // Switch CTA when zero credits. Per Perplexity round 61: this is
    // the highest-converting paywall moment — go straight to checkout
    // instead of opening the generic pricing modal.
    if(cr<=0){
      if(tier==='free'){
        if(ss)ss.innerHTML='<span class="up-cr-low">0 of 3 free leads remaining</span><span>Upgrade to Growth for 25 leads / month</span>';
        if(goBtn){goBtn.textContent='Upgrade to Growth — €49/mo';goBtn.onclick=function(){closeStartPopup();hvCheckoutWithSource('growth_monthly','credits_exhausted_start_popup')};}
      } else {
        if(ss)ss.innerHTML='<span class="up-cr-low">0 leads remaining this month</span><span>Top up to keep going</span>';
        if(goBtn){goBtn.textContent='Top up 30 leads — €49';goBtn.onclick=function(){closeStartPopup();hvCheckoutWithSource('topup_30','credits_exhausted_start_popup')};}
      }
    } else {
      if(ss)ss.innerHTML='<span><b>'+sel+'</b> countries · <b>'+cr+'</b> leads remaining</span><span>'+tierName+' · Runs until stopped or credits run out</span>';
      if(goBtn){goBtn.textContent='▶ Start Hunting';goBtn.onclick=launchAgent;}
    }
  }catch(e){}
}
// Post-first-hunt activation wedge (Perplexity round 62). After a hunt
// completes the highest-leverage next step is "actually send outreach
// to your best lead", not "browse the CRM". This helper finds the
// top-fit lead that already has a generated draft, opens its detail
// page, and surfaces a one-time inline hint pointing at the draft.
function openBestLeadForEmail(){
  function _pickAndOpen(){
    var pool=(typeof ALL!=='undefined'&&ALL&&ALL.length)?ALL:[];
    if(!pool.length){
      toast('No leads yet — start a hunt first');
      return;
    }
    var withDrafts=pool.filter(function(l){
      return l && l.email_subject && l.email_body && l.email_body.length>30;
    });
    var target=null;
    if(withDrafts.length){
      withDrafts.sort(function(a,b){return (b.fit_score||0)-(a.fit_score||0)});
      target=withDrafts[0];
    } else {
      // No drafts available — fall back gracefully to the CRM list so the
      // user can still review what they got.
      toast('No email drafts yet — open a lead to generate one');
      var huntsBtn=document.querySelector('[data-page="crm"]');
      if(huntsBtn)huntsBtn.click();
      return;
    }
    window._firstEmailPrompt=true;
    openLeadPage(target.lead_id);
  }
  // ALL may not be hydrated yet (e.g. user came straight from the
  // hunt-done screen without visiting CRM). loadCRM populates it.
  if(typeof ALL!=='undefined'&&ALL&&ALL.length){
    _pickAndOpen();
  } else if(typeof loadCRM==='function'){
    loadCRM().then(_pickAndOpen).catch(function(){toast('Could not load leads')});
  } else {
    toast('Leads still loading — try again in a moment');
  }
}

// Zero-state activation wedge (Perplexity round 60). One-click start
// from the empty Hunts page using the user's wizard-derived (or default
// EU+UK+US) country set, skipping the picker so the first-session
// tester sees real progress within 5 seconds.
function launchHuntQuickStart(){
  if(agentRunning){toast('Hunt already running');return}
  // Make sure _startCountries is hydrated from the latest account
  // settings. _hvAccount may have arrived after this script first ran.
  try{_startCountries=_buildStartCountries()}catch(_){}
  var picked=Object.keys(_startCountries||{}).filter(function(c){return _startCountries[c]});
  if(!picked.length){
    // Fallback to the static defaults. Should never hit unless the
    // settings hydration failed entirely.
    picked=(_defaultCountries||['France','Germany','Italy','Spain','United Kingdom','USA']).slice(0,30);
    picked.forEach(function(c){_startCountries[c]=true});
  }
  // Credit gate, mirrors openStartPopup logic. Only enforced in cloud/billing mode.
  if(_hvRuntime && _hvRuntime.billing_enabled){
    var cr=_hvAccount?_hvAccount.credits_remaining:0;
    if(cr<=0){toast('No credits — top up to start a hunt');hvOpenPricing&&hvOpenPricing();return}
  }
  launchAgent();
}

var _launchInFlight=false;
function launchAgent(){
  if(_launchInFlight){return}
  try{
    var sel=[];
    Object.keys(_startCountries).forEach(function(c){if(_startCountries[c])sel.push(c)});
    if(!sel.length){toast('Select at least one country');return;}
    // Hunt budget caps — empty inputs mean unlimited (current default).
    // Client-side guards mirror server validation [1,500] / [1,120].
    var _mlEl=$('startMaxLeads'),_toEl=$('startTimeoutMin');
    var _mlRaw=_mlEl?(_mlEl.value||'').trim():'',_toRaw=_toEl?(_toEl.value||'').trim():'';
    var _maxLeads=null,_timeoutMin=null;
    if(_mlRaw){var _n=parseInt(_mlRaw,10);if(!isFinite(_n)||_n<1||_n>500){toast('Max leads must be 1–500');return}_maxLeads=_n}
    if(_toRaw){var _t=parseInt(_toRaw,10);if(!isFinite(_t)||_t<1||_t>120){toast('Timeout must be 1–120 minutes');return}_timeoutMin=_t}
    _launchInFlight=true;
    // F3 fix: disable start buttons during launch
    var _goBtn=$('btnGo');if(_goBtn)_goBtn.disabled=true;
    var _startGoBtn=document.querySelector('.start-go');if(_startGoBtn)_startGoBtn.disabled=true;
    closeStartPopup();
    // Navigate to Hunts page
    var huntsBtn=document.querySelector('[data-page="hunts"]');
    if(huntsBtn)huntsBtn.click();
    huntReset();huntShowState('active');
    var _budgetMsg='';
    if(_maxLeads&&_timeoutMin)_budgetMsg=' (cap '+_maxLeads+' leads / '+_timeoutMin+' min)';
    else if(_maxLeads)_budgetMsg=' (cap '+_maxLeads+' leads)';
    else if(_timeoutMin)_budgetMsg=' (cap '+_timeoutMin+' min)';
    huntSetCurrent('Launching hunt in '+sel.length+' countries...');
    var _runUntilMsg=(_hvRuntime && _hvRuntime.billing_enabled)?' — will run until stopped or credits exhausted':' — will run until stopped';
    addLog('Starting Huntova in '+sel.length+' countries'+_budgetMsg+_runUntilMsg,'info');
    addThought('Starting the hunt! '+sel.length+' countries selected.','ready');
    var _body={action:'start',countries:sel};
    if(_maxLeads)_body.max_leads=_maxLeads;
    if(_timeoutMin)_body.timeout_minutes=_timeoutMin;
    fetch('/agent/control',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(_body)})
      .then(function(r){return r.json()}).then(function(d){
        _launchInFlight=false;
        if(_goBtn)_goBtn.disabled=false;if(_startGoBtn)_startGoBtn.disabled=false;
        if(d.ok){toast('✓ Huntova is hunting');return}
        // Surface the actual server error instead of a generic 'Could not start'.
        // Hand the user the right next step:
        //   - cloud + credit/upgrade error → open pricing modal
        //   - local + no-provider error    → open Settings → Providers
        var msg=d.error||'Could not start';
        toast(msg);
        if(_hvRuntime && _hvRuntime.billing_enabled && /credit|upgrade/i.test(msg)){
          try{hvOpenPricing()}catch(_){}
        } else if(/no ai provider configured/i.test(msg)){
          // Close the start popup so the user sees the action.
          try{closeStartPopup()}catch(_){}
          openSettings();
          setTimeout(function(){settingsTab('providers')},150);
        }
        huntShowState('idle');
      })
      .catch(function(e){
        _launchInFlight=false;
        if(_goBtn)_goBtn.disabled=false;if(_startGoBtn)_startGoBtn.disabled=false;
        addLog('Launch failed: '+e,'error');
        // Previously the network-error branch only addLog'd — user saw no
        // feedback at all. Toast a real message.
        toast('Network error starting hunt — please try again');
        huntShowState('idle');
      });
  }catch(e){_launchInFlight=false;var _gb=$('btnGo');if(_gb)_gb.disabled=false;var _sg=document.querySelector('.start-go');if(_sg)_sg.disabled=false;console.error('launchAgent error:',e);toast('Launch error: '+(e&&e.message||e))}
}

// ── HUNTOVA ORB STATE MANAGEMENT ──
var _orbMoods = {
  idle: {cls:'',msgs:['Ready to hunt for leads.','Waiting for your command.','Standing by...']},
  search: {cls:'scanning',msgs:['Scanning the web...','Searching for prospects...','Hunting leads...']},
  fetch: {cls:'scanning',msgs:['Loading a page...','Analysing prospect...','Reading page content...']},
  thinking: {cls:'thinking',msgs:['Analysing with AI...','Thinking hard about this one...','Scoring this lead...']},
  ready: {cls:'',msgs:['Let\'s go!','Starting up!','On it!']},
  found: {cls:'found',msgs:['Found a lead!','Got one!','New lead discovered!']},
  skip: {cls:'',msgs:['Skipping — not a fit.','Filtered out.','Moving on...']},
};
function setOrbState(mood,msg){
  var orb=$('neoOrb');if(!orb)return;
  orb.className='neo-orb'+(mood&&_orbMoods[mood]?' '+_orbMoods[mood].cls:'');
  if(msg){$('neoBotMsg').textContent=msg;}
  else if(mood&&_orbMoods[mood]){
    var msgs=_orbMoods[mood].msgs;
    $('neoBotMsg').textContent=msgs[Math.floor(Math.random()*msgs.length)];
  }
  $('neoBotStatus').textContent=mood||'idle';
}


// ═══ STATUS COLOR MAP ═══
function stColor(s){
  var m={new:'rgba(255,255,255,.3)',email_sent:'var(--org)',followed_up:'var(--cyn)',
    replied:'var(--blu)',meeting_booked:'var(--pur)',won:'var(--acc)',lost:'var(--red)',ignored:'rgba(255,255,255,.1)'};
  return m[s]||'var(--t3)';
}


/* ── Responsive v2 ── */
(function(){
  var _rT;
  function onResize(){
    clearTimeout(_rT);
    _rT=setTimeout(function(){
      document.querySelectorAll('.st-menu').forEach(function(m){m.style.display='none'});
      document.querySelectorAll('.tip-box').forEach(function(t){t.remove()});
      /* Reset neo widget inline styles on mobile */
      var nw=document.querySelector('.neo-widget.on');
      if(nw && window.innerWidth<=500){
        nw.style.cssText='';
        nw.classList.add('on');
      }
    },250);
  }
  window.addEventListener('resize',onResize);
  window.addEventListener('orientationchange',function(){setTimeout(onResize,100)});
  
  /* iOS 100vh fix */
  function setVH(){
    var vh=window.innerHeight*0.01;
    document.documentElement.style.setProperty('--dvh',vh+'px');
  }
  setVH();
  window.addEventListener('resize',setVH);
  
  /* Prevent double-tap zoom on interactive elements */
  var lastTap=0;
  document.addEventListener('touchend',function(e){
    var now=Date.now();
    if(now-lastTap<300 && e.target.closest('button,.nav-btn,.fb,.abtn,.st-btn,.st-opt,.lm-tab,.ag-tab')){
      e.preventDefault();
    }
    lastTap=now;
  },{passive:false});
  
  /* Close lead modal on back button (mobile) */
  window.addEventListener('popstate',function(){
    var lmb=document.getElementById('leadModalBg');
    if(lmb && lmb.classList.contains('on')){
      if(typeof closeLeadModal==='function') closeLeadModal();
    }
    var nw=document.querySelector('.neo-widget.on');
    if(nw && typeof closeNeoWidget==='function') closeNeoWidget();
  });
  
  /* Push history state when opening modals (for back button support) */
  var origOpenLM=window.openLeadModal;
  if(origOpenLM){
    window.openLeadModal=function(id){
      if(!document.getElementById('leadModalBg').classList.contains('on'))
        history.pushState({modal:'lead'},'');
      return origOpenLM.call(this,id);
    };
  }
  var origOpenNW=window.openNeoWidget;
  if(origOpenNW){
    window.openNeoWidget=function(){
      history.pushState({modal:'neo'},'');
      return origOpenNW.apply(this,arguments);
    };
  }
})();

/* ════════════════════════════════════════════════════════════
   ONBOARDING WIZARD
   ════════════════════════════════════════════════════════════ */
var _wizData = {};
var _wizStep = 0;

/* ════════════════════════════════════════════════════════════
   HUNTOVA DEEP WIZARD v2 — Structured + AI Chat hybrid
   ════════════════════════════════════════════════════════════ */


function wizSkip() {
  var bg=$('wizBg');
  if(bg)bg.classList.remove('on');
  iwClose();
}




/* Hook into agent completion to show results summary */
/* showResultsSummary triggered by SSE status event handler directly */

/* ════════════════════════════════════════════════════════════
   EDIT-FIRST EMAIL FLOW + FRIENDLIER SCORE LABELS
   ════════════════════════════════════════════════════════════ */



/* Make email editable in lead modal — patch openLeadModal output */
try { var _origOpenLeadModal = typeof openLeadModal === 'function' ? openLeadModal : null;
if (_origOpenLeadModal) {
  var _patchedOpenLeadModal = function(id) {
    /* Call original */
    _origOpenLeadModal(id);
    
    /* After modal renders, make email fields editable */
    setTimeout(function() {
      var subj = document.getElementById('ms-' + id);
      var body = document.getElementById('mb-' + id);
      
      if (subj) {
        subj.setAttribute('contenteditable', 'true');
        subj.setAttribute('spellcheck', 'true');
      }
      if (body) {
        body.setAttribute('contenteditable', 'true');
        body.setAttribute('spellcheck', 'true');
      }
      
      /* Add edit hint */
      var ecard = subj ? subj.closest('.lm-ecard') : null;
      if (ecard && !ecard.querySelector('.edit-hint')) {
        var hint = document.createElement('div');
        hint.className = 'edit-hint';
        hint.id = 'editHint-' + id;
        hint.textContent = 'Click to edit — your changes are saved automatically';
        ecard.insertBefore(hint, ecard.querySelector('.lm-eacts'));
      }
      
      /* Auto-save on edit — B1 fix: capture content at debounce time, not fire time */
      var _pendingSubj=null, _pendingBody=null;

      function _captureEdits(){
        // Snapshot current DOM content at capture time, keyed to this lead.
        // Skip detached nodes — the Rewrite flow replaces .lm-ecard so the
        // subj/body refs here point at disconnected elements whose
        // textContent still reflects pre-rewrite text. Capturing that and
        // flushing would revert the rewrite.
        _pendingSubj=(subj&&subj.isConnected)?subj.textContent:null;
        _pendingBody=(body&&body.isConnected)?body.textContent:null;
      }

      function _flushPending(){
        // Guard: only save if this lead is still the active one
        if(!currentModalLead||currentModalLead.lead_id!==id){_pendingSubj=null;_pendingBody=null;return}
        var saveSubj=_pendingSubj, saveBody=_pendingBody;
        _pendingSubj=null;_pendingBody=null;
        if(saveSubj===null&&saveBody===null)return;
        // Update in-memory lead
        if(saveSubj!==null&&saveSubj!==currentModalLead.email_subject)currentModalLead.email_subject=saveSubj;
        if(saveBody!==null&&saveBody!==currentModalLead.email_body)currentModalLead.email_body=saveBody;
        // Save to server with captured values — never read from live currentModalLead at fetch time
        var h=document.getElementById('editHint-'+id);
        try{
          fetch('/api/update',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({lead_id:id,email_subject:saveSubj||currentModalLead.email_subject,email_body:saveBody||currentModalLead.email_body})
          }).then(function(r){
            if(r.ok&&h){h.textContent='Changes saved';h.classList.add('edit-saved');clearTimeout(h._t);h._t=setTimeout(function(){h.textContent='Click to edit — changes saved automatically';h.classList.remove('edit-saved')},2000)}
          }).catch(function(){if(h){h.textContent='Save failed — will retry';h.classList.remove('edit-saved')}});
        }catch(e){}
      }

      var _saveTimer;
      function debouncedSave(){
        _captureEdits(); // B1 fix: snapshot content NOW, not when timer fires
        clearTimeout(_saveTimer);
        _saveTimer=setTimeout(function(){_saveTimer=null;_flushPending()},800);
      }
      /* B2 fix: flush captures pending content AND fires save before modal close */
      window._flushModalSave=function(){
        if(_saveTimer){clearTimeout(_saveTimer);_saveTimer=null}
        _captureEdits(); // capture latest DOM state
        _flushPending();
      };
      /* Expose cancel for updateLeadModal to merge edits into its own request */
      window._cancelModalSave=function(){
        if(_saveTimer){clearTimeout(_saveTimer);_saveTimer=null}
        _pendingSubj=null;_pendingBody=null;
      };
      /* Expose read for updateLeadModal to get pending edits before re-render */
      window._getPendingEdits=function(){
        _captureEdits();
        return{subject:_pendingSubj,body:_pendingBody};
      };

      if (subj) subj.addEventListener('input', debouncedSave);
      if (body) body.addEventListener('input', debouncedSave);
      
    }, 150); /* Wait for modal to render */
  };
  openLeadModal = _patchedOpenLeadModal;
}} catch(_e) { console.warn('Lead modal patch error:', _e); }

/* ── Friendlier status labels ── */
(function(){
  /* Make status filter buttons friendlier */
  var friendlyStatus = {
    'new': '● New',
    'email_sent': '📤 Sent',
    'followed_up': '↩ Followed Up',
    'replied': '💬 Replied',
    'meeting_booked': '📅 Meeting',
    'won': '🎉 Won',
    'lost': '✗ Lost'
  };
  /* Patch renderStats to use friendlier labels */
  try { var _origRenderStats = typeof renderStats === 'function' ? renderStats : null;
  if (_origRenderStats) {
    var origRs = renderStats;
    renderStats = function(leads) {
      origRs(leads);
      /* After rendering, update labels to be friendlier */
      document.querySelectorAll('.cs .cl').forEach(function(el) {
        var text = el.textContent.trim();
        if (text === 'ALL') el.textContent = 'All Leads';
        else if (text === 'NEW') el.textContent = 'New';
        else if (text === 'SENT') el.textContent = 'Sent';
        else if (text === 'FOLLOW UP') el.textContent = 'Followed Up';
        else if (text === 'REPLIED') el.textContent = 'Replied';
        else if (text === 'MEETING') el.textContent = 'Meeting';
        else if (text === 'WON') el.textContent = 'Won!';
        else if (text === 'LOST') el.textContent = 'Lost';
      });
    };
  }
} catch(_e) { console.warn("Stats patch error:", _e); }})();

/* ════════════════════════════════════════════════════════════
   LIGHT THEME TOGGLE + CRM BADGES + MARKETING LANGUAGE
   ════════════════════════════════════════════════════════════ */

/* Light theme toggle — handled by the global toggleTheme() function below */



/* ── Marketing Language Swap ── */
(function(){
  /* Change title */
  document.title = 'Huntova · Find clients while you sleep';
  
  /* Change topbar text */
  setTimeout(function(){
    /* Nav tab labels */
    document.querySelectorAll('.nav-btn').forEach(function(btn){
      var page = btn.getAttribute('data-page');
      if(page === 'crm'){
        btn.setAttribute('data-tip','Your leads — view, filter and manage potential clients');
        /* Keep short label but make friendlier */
      }
      if(page === 'dash'){
        btn.setAttribute('data-tip','See your progress — how many leads, replies, and meetings');
      }
      if(page === 'hunts'){
        btn.setAttribute('data-tip','Your hunts — watch Huntova find and qualify prospects');
      }
    });
    
    /* Start button */
    var btnGo = document.getElementById('btnGo');
    if(btnGo){
      btnGo.setAttribute('data-tip','Start finding new clients');
      if(btnGo.textContent.indexOf('Start')>=0 || btnGo.textContent.indexOf('▶')>=0){
        /* Keep icon, update tooltip only */
      }
    }
    
    /* Status text */
    var topSt = document.getElementById('topSt');
    if(topSt && topSt.textContent === 'Idle'){
      topSt.textContent = 'Ready to find clients';
    }
    
    /* Start popup speech */
    var speech = document.querySelector('.start-speech');
    if(speech){
      speech.innerHTML = "Ready when you are! <b>Pick the countries</b> where your ideal clients are based, adjust the settings, and I'll start finding leads for you. Each run discovers fresh opportunities.";
    }
    
    /* Logo subtitle if exists */
    var dotLabel = document.getElementById('dotLabel');
    if(dotLabel && dotLabel.textContent === 'OFFLINE'){
      dotLabel.textContent = 'READY';
    }
  }, 300);
  
  /* Patch status updates to use friendlier language */
  /* Listen for status changes and translate */
  var statusObserver = new MutationObserver(function(mutations){
    mutations.forEach(function(m){
      if(m.target.id === 'topSt'){
        var t = m.target.textContent;
        if(t === 'Idle') m.target.textContent = 'Ready to find clients';
        else if(t.indexOf('Running')>=0) m.target.textContent = t.replace('Running','Finding clients');
        else if(t.indexOf('Done')>=0){
          var num = t.match(/\d+/);
          if(num) m.target.textContent = 'Found ' + num[0] + ' potential clients!';
        }
      }
    });
  });
  setTimeout(function(){
    var st = document.getElementById('topSt');
    if(st) statusObserver.observe(st, {childList:true,characterData:true,subtree:true});
  }, 500);
})();

/* ════════════════════════════════════════════════════════════
   ROW ENHANCER — Score labels, Top 10 badges, Intel badges
   Uses DOM observation instead of fragile string patching
   ════════════════════════════════════════════════════════════ */
(function(){
  try {
    function enhanceRow(rowEl, lead) {
      if (!rowEl || rowEl._enhanced) return;
      rowEl._enhanced = true;
      
      var s = lead ? (lead.fit_score || 0) : 0;
      var p1 = lead ? (lead._pass1 || {}) : {};
      var p2 = lead ? (lead._pass2 || {}) : {};
      
      /* ── Score label ── */
      var rc = rowEl.querySelector('.rc');
      if (rc && !rc.querySelector('.sc-label')) {
        rc.classList.add(s >= 9 ? 'sc-hot' : s >= 7 ? 'sc-warm' : 'sc-cold');
        var label = document.createElement('span');
        label.className = 'sc-label';
        label.textContent = s >= 9 ? 'Hot' : s >= 7 ? 'Warm' : s >= 5 ? 'Maybe' : 'Cold';
        label.style.color = s >= 9 ? 'var(--acc)' : s >= 7 ? 'var(--org)' : s >= 5 ? 'var(--pur)' : 'var(--t3)';
        rc.appendChild(label);
      }
      
      /* ── Top 10 badge ── */
      if (lead && lead.is_top10 && lead.priority_rank) {
        var orgN = rowEl.querySelector('.org-n');
        if (orgN && !orgN.querySelector('.top10-badge')) {
          var badge = document.createElement('span');
          badge.className = 'top10-badge';
          badge.style.cssText = 'background:var(--acc);color:var(--bg);font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;margin-right:4px;display:inline-block';
          badge.textContent = '#' + lead.priority_rank;
          orgN.insertBefore(badge, orgN.firstChild);
        }
      }
      
      /* ── Intel badges ── */
      var orgC = rowEl.querySelector('.org-c');
      if (orgC && !orgC.querySelector('.intel-badges') && lead) {
        var badges = [];
        
        if (p2.timing_urgency === 'critical')
          badges.push('<span class="intel-badge ib-timing-high">\u23f0 Urgent</span>');
        else if (p2.timing_urgency === 'high')
          badges.push('<span class="intel-badge ib-timing">\u23f0 Timely</span>');
        
        if (p2.budget_confidence === 'high')
          badges.push('<span class="intel-badge ib-budget">\ud83d\udcb0 Budget</span>');
        else if (p2.budget_confidence === 'medium')
          badges.push('<span class="intel-badge ib-budget">\ud83d\udcb0 Budget</span>');
        
        if (p2.decision_maker)
          badges.push('<span class="intel-badge ib-dm">\ud83d\udc64 '+esc((p2.decision_maker.name||'').split(' ')[0]||'Contact')+'</span>');

        if (p2.competitive_intel && p2.competitive_intel.length)
          badges.push('<span class="intel-badge ib-platform">\ud83d\udda5 '+esc(p2.competitive_intel[0])+'</span>');
        else if (p1.platform)
          badges.push('<span class="intel-badge ib-platform">\ud83d\udda5 '+esc(p1.platform)+'</span>');
        
        if (p2.video_quality === 'basic')
          badges.push('<span class="intel-badge ib-video">\ud83d\udcf9 Basic video</span>');
        else if (p2.video_quality === 'none_found')
          badges.push('<span class="intel-badge ib-video">\ud83d\udcf9 No video</span>');
        
        var gapCount = (p2.specific_gaps||[]).length;
        if (gapCount >= 2)
          badges.push('<span class="intel-badge ib-gaps">\ud83c\udfaf '+gapCount+' gaps</span>');
        
        if (badges.length) {
          var container = document.createElement('div');
          container.className = 'intel-badges';
          container.innerHTML = badges.join('');
          orgC.appendChild(container);
        }
      }
      
      /* ── Context tag: why this lead scored high ── */
      if (lead && orgC && !orgC.querySelector('.ctx-tag')) {
        var reason = '';
        var cls = 'ctx-why';
        
        /* Priority: Pass 2 gap > Pass 2 timing > why_fit > category */
        if (p2.specific_gaps && p2.specific_gaps.length) {
          reason = p2.specific_gaps[0];
          cls = 'ctx-gap';
        } else if (p2.timing_urgency === 'critical' || p2.timing_urgency === 'high') {
          var ts = p2.timing_signals || [];
          reason = ts.length ? ts[0] : 'Urgent timing detected';
        } else if (lead.why_fit) {
          reason = lead.why_fit;
        } else if (lead.production_gap) {
          reason = lead.production_gap;
          cls = 'ctx-gap';
        } else if (lead.event_type) {
          reason = lead.event_type;
          cls = 'ctx-event';
        }
        
        if (reason) {
          var tag = document.createElement('span');
          tag.className = 'ctx-tag ' + cls;
          tag.title = reason;
          tag.textContent = reason.length > 40 ? reason.substring(0, 38) + '...' : reason;
          orgC.appendChild(tag);
        }
      }
    }
    
    /* Find lead data for a row */
    function getLeadForRow(rowEl) {
      var id = (rowEl.id || '').replace('r-', '');
      if (!id) return null;
      /* Access the global leads array */
      var leads = typeof ALL !== 'undefined' ? ALL : null;
      if (!leads || !leads.length) return null;
      for (var i = 0; i < leads.length; i++) {
        if ((leads[i].lead_id || 'i'+i) === id) return leads[i];
      }
      return null;
    }
    
    /* Enhance all existing rows */
    function enhanceAllRows() {
      document.querySelectorAll('.row:not([data-enhanced])').forEach(function(row) {
        row.setAttribute('data-enhanced', '1');
        var lead = getLeadForRow(row);
        enhanceRow(row, lead);
      });
    }
    
    /* Run on initial load and whenever CRM updates */
    setTimeout(enhanceAllRows, 1000);
    setInterval(function(){var pg=document.getElementById('pg-crm');if(pg&&pg.classList.contains('on'))enhanceAllRows()}, 3000);
    
    /* Also observe DOM for new rows */
    var rowsContainer = document.querySelector('.rows') || document.querySelector('.crm-body');
    if (rowsContainer) {
      new MutationObserver(function() {
        setTimeout(enhanceAllRows, 100);
      }).observe(rowsContainer, { childList: true, subtree: true });
    }
    
    /* scan_report listener moved inside connectSSE() to survive reconnects */
  } catch(e) {
    console.warn('Row enhancer error:', e);
  }
})();



/* ── Responsive resize handler ── */

/* ── Theme toggle ── */
function toggleTheme() {
  var isLight = document.documentElement.classList.toggle('light');
  document.body.classList.toggle('light', isLight);
  try { localStorage.setItem('hv_theme', isLight ? 'light' : 'dark'); } catch(e) {}
  var btn = document.getElementById('themeBtn');
  if (btn) btn.textContent = isLight ? '🌙' : '☀️';
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', isLight ? '#f5f6f8' : '#07080c');
}
/* Restore theme on load */
(function() {
  try {
    var t = localStorage.getItem('hv_theme');
    if (t === 'light') {
      document.documentElement.classList.add('light');
      document.body.classList.add('light');
      var btn = document.getElementById('themeBtn');
      if (btn) btn.textContent = '🌙';
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', '#f5f6f8');
    }
  } catch(e) {}
})();

/* ── CSV Export ── */
function exportCSV() {
  var data=filtered&&filtered.length?filtered:ALL;
  if (!data || data.length === 0) { if (typeof toast === 'function') toast('No leads to export'); return; }
  var cols = ['contact_name','contact_email','org_name','org_website','country','city',
    'contact_linkedin','org_linkedin','fit_score','why_fit','production_gap',
    'event_name','event_type','platform_used','is_recurring'];
  // Email drafts + follow-up sequence
  if(_hvFeatures.email_draft_visible!==false){
    cols.push('email_subject','email_body','linkedin_note','email_followup_2','email_followup_3','email_followup_4');
  }
  cols.push('email_status','contact_phone','contact_role');
  // Include confidence/rationale if present
  cols.push('fit_rationale','timing_rationale','_data_confidence','_quote_verified','_contact_source','_contact_confidence');
  var rows = [cols.join(',')];
  data.forEach(function(l) {
    var row = cols.map(function(c) {
      var v = (l[c] == null ? '' : String(l[c]));
      v = v.replace(/\r\n?/g, '\n').replace(/"/g, '""');
      // CSV formula-injection defense: cells beginning with =, +, -, @
      // execute as formulas in Excel / Google Sheets / Numbers when
      // the file is opened. A lead.org_name like "=cmd|'/c calc'!A0"
      // could trigger RCE on a colleague's machine. Prefix a single
      // quote so the spreadsheet treats the value as text. Also @TAB
      // and @CR which Excel recognises.
      if (/^[=+\-@\t\r]/.test(v)) v = "'" + v;
      if (v.indexOf(',') >= 0 || v.indexOf('"') >= 0 || v.indexOf('\n') >= 0) v = '"' + v + '"';
      return v;
    });
    rows.push(row.join(','));
  });
  var blob = new Blob([rows.join('\n')], {type: 'text/csv'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'huntova_leads_' + new Date().toISOString().split('T')[0] + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  _trackAction('export','csv_export_'+data.length+'_leads');
  if (typeof toast === 'function') toast('Exported ' + data.length + ' leads to CSV'+(filtered&&filtered.length<ALL.length?' (filtered)':''));
}

/* ── CRM empty state toggle ── */
var _emptyObs = new MutationObserver(function() {
  var empty = document.getElementById('crmEmpty');
  var rows = document.getElementById('crmRows');
  if (empty && rows) empty.style.display = rows.children.length > 0 ? 'none' : 'flex';
});
try { _emptyObs.observe(document.getElementById('crmRows'), {childList: true}); } catch(e) {}

/* ════════════════════════════════════════════════════════════
   AI WIZARD v2 — Adaptive interview engine
   ════════════════════════════════════════════════════════════ */


/* ════════════════════════════════════════════════════════════
   WIZARD v6 — Card-based system with AI chat
   ════════════════════════════════════════════════════════════ */
var _iw={phase:0,qi:0,answers:{},confidence:0,phase5Qs:[],scanData:null,_answered:{},activeSection:1};

function iwOpen(){
  _iw={phase:0,qi:0,answers:{},confidence:0,phase5Qs:[],scanData:null,_answered:{},activeSection:1};
  fetch('/api/wizard/status').then(function(r){return r.json()}).then(function(d){
    if(d.has_answers){
      fetch('/api/settings').then(function(r2){return r2.json()}).then(function(s){
        var w=(s&&s.wizard)||{};
        if(w._wizard_answers)_iw.answers=w._wizard_answers;
        _iw.confidence=w._wizard_confidence||0;
        iwShow(!!w._wizard_answers);
      }).catch(function(){iwShow(false)});
    } else {iwShow(false)}
  }).catch(function(){iwShow(false)});
}
function iwShow(hasData){
  document.getElementById('iwiz').classList.add('on');
  document.body.style.overflow='hidden';
  if(hasData){
    // Has existing data — go straight to card workspace
    iwShowWorkspace();
  } else {
    // Fresh user — show briefing first
    iwShowBriefing();
  }
}
function iwClose(){
  document.getElementById('iwiz').classList.remove('on');
  document.body.style.overflow='';
}
// Typing effect (with cancel support)
var _iwTypeTimer=null;
function iwType(el,text,cb){
  // Cancel any running animation
  if(_iwTypeTimer){clearTimeout(_iwTypeTimer);_iwTypeTimer=null}
  var orb=document.getElementById('iwizOrb');
  if(orb)orb.className='iwiz-orb speaking';
  el.innerHTML='';
  var i=0;
  function tick(){
    if(i<text.length){el.innerHTML=text.substring(0,i+1)+'<span class="iwiz-cursor"></span>';i++;_iwTypeTimer=setTimeout(tick,25)}
    else{el.innerHTML=text;_iwTypeTimer=null;if(orb)orb.className='iwiz-orb';if(cb)cb()}
  }
  tick();
}
function iwTypeInstant(el,text){
  if(_iwTypeTimer){clearTimeout(_iwTypeTimer);_iwTypeTimer=null}
  el.innerHTML=text;
  var orb=document.getElementById('iwizOrb');
  if(orb)orb.className='iwiz-orb';
}
// Confidence
function iwUpdateConfidence(){
  var c=_iw.confidence;
  var pct=document.getElementById('iwizConfPct');
  var fill=document.getElementById('iwizConfFill');
  if(pct)pct.textContent=Math.round(c)+'%';
  if(fill){
    fill.style.height=c+'%';
    fill.style.background=c>=95?'var(--acc)':c>=85?'#7aecd8':c>=70?'var(--org)':c>=40?'var(--amb)':'var(--red)';
  }
  var prog=document.getElementById('iwizProg');
  if(prog)prog.style.width=c+'%';
}
function iwAddConfidence(amount){
  _iw.confidence=Math.min(100,_iw.confidence+amount);
  iwUpdateConfidence();
}
// Save progress
function iwSave(){
  fetch('/api/wizard/save-progress',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({answers:_iw.answers,phase:_iw.phase,confidence:_iw.confidence})}).catch(function(){});
}
var _iwOnboardStep=0;
function iwShowBriefing(){
  var brief=document.getElementById('iwizBrief');
  var ws=document.getElementById('iwizWorkspace');
  brief.style.display='flex';ws.style.display='none';
  _iwOnboardStep=0;
  _iwOnboardNext();
}

function _iwOnboardNext(){
  var brief=document.getElementById('iwizBrief');
  _iwOnboardStep++;

  if(_iwOnboardStep===1){
    // Step 1: Launch animation
    brief.innerHTML='<div style="text-align:center;max-width:400px">'
      +'<div class="iwiz-orb" style="margin:0 auto 24px;width:64px;height:64px"></div>'
      +'<div style="font:600 18px/1.4 var(--font);color:var(--t1);margin-bottom:8px">Launching Wizard</div>'
      +'<div style="font:400 13px/1.5 var(--font);color:var(--t3)">Preparing your AI training environment...</div>'
      +'</div>';
    setTimeout(_iwOnboardNext,2000);

  } else if(_iwOnboardStep===2){
    // Step 2: Welcome + explanation
    brief.innerHTML='<div style="text-align:center;max-width:480px">'
      +'<div style="font:700 24px/1.3 var(--font);color:var(--t1);margin-bottom:16px;letter-spacing:-.02em">Welcome to Huntova</div>'
      +'<div style="font:400 15px/1.7 var(--font);color:var(--t2);margin-bottom:24px">You\'re about to train an AI salesperson. It will search the web, evaluate companies, and write personalised outreach on your behalf.</div>'
      +'<div style="background:var(--s2);border:1px solid var(--bd);border-radius:10px;padding:16px 20px;text-align:left;margin-bottom:24px">'
      +'<div style="font:600 12px/1 var(--mono);color:var(--acc);text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">How this works</div>'
      +'<div style="font:400 13px/1.6 var(--font);color:var(--t2)">'
      +'1. You teach the agent about your business and ideal clients<br>'
      +'2. The agent builds a search strategy from your answers<br>'
      +'3. It hunts the web, scores prospects, and writes emails<br>'
      +'4. You get qualified leads in your CRM</div></div>'
      +'<div style="background:rgba(255,100,80,.06);border:1px solid rgba(255,100,80,.12);border-radius:8px;padding:12px 16px;font:500 13px/1.5 var(--font);color:rgba(255,160,140,.9);margin-bottom:24px">'
      +'The more specific your answers, the better your leads. Vague inputs = vague results.</div>'
      +'<button class="iwiz-btn" onclick="_iwOnboardNext()" style="width:100%;max-width:280px">Let\'s Begin \u2192</button>'
      +'</div>';

  } else if(_iwOnboardStep===3){
    // Step 3: Business name
    brief.innerHTML='<div style="text-align:center;max-width:440px">'
      +'<div style="font:600 18px/1.4 var(--font);color:var(--t1);margin-bottom:20px">What\'s the name of your business?</div>'
      +'<input class="iwiz-text" id="iwOnboardName" type="text" placeholder="Your company name" value="'+esc(_iw.answers.business_name||'')+'" style="text-align:center;font-size:18px;margin-bottom:24px">'
      +'<button class="iwiz-btn" onclick="iwOnboardSaveName()" style="width:100%;max-width:280px">Continue \u2192</button>'
      +'</div>';
    setTimeout(function(){var el=document.getElementById('iwOnboardName');if(el)el.focus()},100);

  } else if(_iwOnboardStep===4){
    // Step 4: Website URL + auto-scan
    var name=_iw.answers.business_name||'your business';
    brief.innerHTML='<div style="text-align:center;max-width:480px">'
      +'<div style="font:600 18px/1.4 var(--font);color:var(--t1);margin-bottom:8px">What\'s '+esc(name)+'\'s website?</div>'
      +'<div style="font:400 13px/1.5 var(--font);color:var(--t3);margin-bottom:20px">We\'ll scan it to pre-fill some answers and save you time.</div>'
      +'<input class="iwiz-text" id="iwOnboardUrl" type="text" placeholder="yourwebsite.com" value="'+esc(_iw.answers.website||'')+'" style="text-align:center;font-size:16px;margin-bottom:16px">'
      +'<button class="iwiz-btn" id="iwOnboardScanBtn" onclick="iwOnboardScan()" style="width:100%;max-width:280px">Scan & Continue \u2192</button>'
      +'<button class="iwiz-btn-ghost" onclick="_iwOnboardStep=4;_iwOnboardNext()" style="display:block;margin:12px auto 0">Skip \u2014 I\'ll fill it in manually</button>'
      +'</div>';
    setTimeout(function(){var el=document.getElementById('iwOnboardUrl');if(el)el.focus()},100);

  } else if(_iwOnboardStep===5){
    // Step 5: Scanning animation
    brief.innerHTML='<div style="text-align:center;max-width:400px">'
      +'<div class="iwiz-orb" id="iwOnboardOrb" style="margin:0 auto 24px;width:64px;height:64px;animation:orbThink 2s ease-in-out infinite"></div>'
      +'<div id="iwOnboardStatus" style="font:600 15px/1.4 var(--font);color:var(--t1);margin-bottom:8px">Scanning website...</div>'
      +'<div id="iwOnboardDetail" style="font:400 12px/1.4 var(--font);color:var(--t3)">Fetching pages and analysing content</div>'
      +'</div>';
    // The scan is already running from iwOnboardScan()

  } else {
    // Done with onboarding — go to workspace
    iwShowWorkspace();
  }
}

function iwOnboardSaveName(){
  var el=document.getElementById('iwOnboardName');
  if(el)_iw.answers.business_name=el.value.trim();
  _iwOnboardNext();
}

function iwOnboardScan(){
  var el=document.getElementById('iwOnboardUrl');
  if(!el||!el.value.trim()){_iwOnboardStep=4;_iwOnboardNext();return}
  var url=el.value.trim();
  if(!url.includes('.'))url+='.com';
  if(!url.startsWith('http'))url='https://'+url;
  _iw.answers.website=url;

  // Show scanning animation
  _iwOnboardStep=4; // Will be incremented to 5
  _iwOnboardNext();

  var statusEl,detailEl,orbEl;
  setTimeout(function(){
    statusEl=document.getElementById('iwOnboardStatus');
    detailEl=document.getElementById('iwOnboardDetail');
    orbEl=document.getElementById('iwOnboardOrb');
  },100);

  fetch('/api/wizard/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url})})
  .then(function(r){return r.json()}).then(function(d){
    if(d.error){
      if(statusEl)statusEl.textContent='Could not scan website';
      if(detailEl)detailEl.textContent='No worries \u2014 you can fill everything in manually';
      setTimeout(function(){iwShowWorkspace()},1500);
      return;
    }
    _iw.scanData=d;
    var _s=function(v){return(typeof v==='string'&&v&&v!=='undefined')?v.trim():''};
    if(_s(d.company_name)&&!_iw.answers.business_name)_iw.answers.business_name=_s(d.company_name);
    if(_s(d.summary))_iw.answers.what_you_do=_s(d.summary);
    if(d.industries_served)_iw.answers.industries=d.industries_served;
    if(d.target_clients)_iw.answers.ideal_customer=_s(d.target_clients);
    if(d.differentiators)_iw.answers._scan_diff=d.differentiators;
    if(d.regions)_iw.answers.geography=d.regions;
    if(d._site_text)_iw.answers._site_text=d._site_text;
    if(_s(d.business_type))_iw.answers.business_type=_s(d.business_type);
    if(d.delivery_method)_iw.answers.how_it_works=_s(d.delivery_method);
    if(d.buyer_roles)_iw.answers.decision_makers=d.buyer_roles;

    if(statusEl)statusEl.textContent='Website scanned successfully';
    if(detailEl){
      var count=Object.keys(_iw.answers).filter(function(k){return _iw.answers[k]&&k[0]!=='_'}).length;
      detailEl.textContent='Pre-filled '+count+' fields from your site';
    }
    if(orbEl)orbEl.style.animation='orbPulse 3s ease-in-out infinite';
    iwSave();
    setTimeout(function(){iwShowWorkspace()},1800);
  }).catch(function(){
    if(statusEl)statusEl.textContent='Scan failed';
    if(detailEl)detailEl.textContent='No worries \u2014 you can fill everything in manually';
    setTimeout(function(){iwShowWorkspace()},1500);
  });
}

function iwShowWorkspace(){
  var brief=document.getElementById('iwizBrief');
  var ws=document.getElementById('iwizWorkspace');
  brief.style.display='none';ws.style.display='flex';
  iwRenderTabs();
  iwRenderSection(_iw.activeSection);
  iwRenderBottom();
}

// Section definitions
var _iwSections={
  1:{name:'Business',icon:'B'},
  2:{name:'Customer',icon:'C'},
  3:{name:'Sales',icon:'S'},
  4:{name:'Market & Signals',icon:'M'}
};

function iwRenderTabs(){
  var tabs=document.getElementById('iwizTabs');
  var h='';
  for(var s in _iwSections){
    var sec=_iwSections[s];
    var isActive=parseInt(s)===_iw.activeSection;
    var hasData=_iwQuestions.filter(function(q){return q.phase===parseInt(s)&&_iw.answers[q.id]}).length>0;
    h+='<button class="iwiz-tab'+(isActive?' active':'')+(hasData?' has-data':'')+'" onclick="iwSwitchSection('+s+')">'
      +esc(sec.name)
      +'<span class="iwiz-tab-dot"></span>'
      +'</button>';
  }
  tabs.innerHTML=h;
}

function iwSwitchSection(s){
  _iw.activeSection=s;
  iwRenderTabs();
  iwRenderSection(s);
}

function iwRenderSection(sectionNum){
  var panel=document.getElementById('iwizCards');
  var questions=_iwQuestions.filter(function(q){return q.phase===sectionNum});
  var h='';

  questions.forEach(function(q){
    var val=_iw.answers[q.id]||'';
    var displayVal=typeof val==='string'?val:Array.isArray(val)?val.join(', '):'';
    h+='<div class="iwiz-field" id="field-'+q.id+'">';
    h+='<div class="iwiz-field-label">'+(q.critical?'<span class="req">*</span>':'')+esc(q.q)+'</div>';
    if(q.ph)h+='<div class="iwiz-field-hint">'+esc(q.ph)+'</div>';

    if(q.type==='text'||q.type==='url'){
      h+='<input class="iwiz-text" data-field="'+q.id+'" type="text" value="'+esc(displayVal)+'" placeholder="'+esc(q.ph||'')+'" onchange="iwFieldChanged(\''+q.id+'\')">';
      if(q.scan)h+='<button class="iwiz-scan-btn" style="margin-top:6px" onclick="iwDoCardScan(\''+q.id+'\')">Scan Website</button>';
    } else if(q.type==='long'){
      h+='<textarea class="iwiz-textarea" data-field="'+q.id+'" rows="3" placeholder="'+esc(q.ph||'')+'" onchange="iwFieldChanged(\''+q.id+'\')">'+esc(displayVal)+'</textarea>';
    } else if(q.type==='single'||q.type==='multi'){
      h+='<div class="iwiz-pills">';
      var sel=Array.isArray(val)?val:(val?[val]:[]);
      (q.opts||[]).forEach(function(opt){
        var isOn=sel.indexOf(opt)>=0;
        h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwCardPill(this,\''+q.id+'\',\''+opt.replace(/'/g,"\\'")+'\','+(q.type==='single'?'true':'false')+')">'+esc(opt)+'</button>';
      });
      h+='</div>';
    } else if(q.type==='multi_text'){
      h+='<div class="iwiz-pills">';
      var sel=Array.isArray(val)?val:[];
      (q.opts||[]).forEach(function(opt){
        var isOn=sel.indexOf(opt)>=0;
        h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwCardPill(this,\''+q.id+'\',\''+opt.replace(/'/g,"\\'")+'\',false)">'+esc(opt)+'</button>';
      });
      h+='</div>';
      h+='<input class="iwiz-text" data-field="'+q.id+'-custom" type="text" placeholder="Add your own (comma separated)" style="margin-top:6px" onchange="iwFieldCustom(\''+q.id+'\')">';
    }
    h+='</div>';
  });

  panel.innerHTML=h;
  panel.scrollTop=0;
}

function iwFieldChanged(key){
  var el=document.querySelector('[data-field="'+key+'"]');
  if(el)_iw.answers[key]=el.value.trim();
  iwAutoSave();
}

function iwCardPill(el,key,val,single){
  if(single){
    _iw.answers[key]=val;
    el.parentElement.querySelectorAll('.iwiz-pill').forEach(function(b){b.classList.remove('on')});
    el.classList.add('on');
  }else{
    if(!Array.isArray(_iw.answers[key]))_iw.answers[key]=[];
    var idx=_iw.answers[key].indexOf(val);
    if(idx>=0){_iw.answers[key].splice(idx,1);el.classList.remove('on')}
    else{_iw.answers[key].push(val);el.classList.add('on')}
  }
  iwAutoSave();
  iwRenderTabs(); // Update dot indicators
}

function iwFieldCustom(key){
  var el=document.querySelector('[data-field="'+key+'-custom"]');
  if(!el||!el.value.trim())return;
  var extras=el.value.split(',').map(function(s){return s.trim()}).filter(function(s){return s});
  if(!Array.isArray(_iw.answers[key]))_iw.answers[key]=[];
  extras.forEach(function(e){if(_iw.answers[key].indexOf(e)<0)_iw.answers[key].push(e)});
  el.value='';
  iwRenderSection(_iw.activeSection);
  iwAutoSave();
}

var _iwSaveTimer=null;
function iwAutoSave(){
  clearTimeout(_iwSaveTimer);
  _iwSaveTimer=setTimeout(function(){
    // Calculate confidence from answered fields
    var total=0,answered=0;
    _iwQuestions.forEach(function(q){
      total+=q.conf||3;
      var v=_iw.answers[q.id];
      if(v&&(typeof v==='string'?v.length>0:v.length>0)){
        answered+=q.conf||3;
        if(typeof v==='string'&&v.length>50)answered+=2;
      }
    });
    _iw.confidence=total>0?Math.min(100,Math.round(answered/total*100)):0;
    iwUpdateConfidence();
    iwRenderBottom();
    iwSave();
  },500);
}

function iwDoCardScan(fieldKey){
  var el=document.querySelector('[data-field="'+fieldKey+'"]');
  if(!el||!el.value.trim())return;
  var url=el.value.trim();
  // Add visual loading
  var field=document.getElementById('field-'+fieldKey);
  if(field)field.style.borderColor='var(--acc)';
  fetch('/api/wizard/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url})})
  .then(function(r){return r.json()}).then(function(d){
    if(field)field.style.borderColor='';
    if(d.error){toast('Could not read site');return}
    _iw.scanData=d;
    var _s=function(v){return(typeof v==='string'&&v&&v!=='undefined')?v.trim():''};
    if(_s(d.company_name)&&!_iw.answers.business_name)_iw.answers.business_name=_s(d.company_name);
    if(_s(d.summary))_iw.answers.what_you_do=_s(d.summary);
    if(d.industries_served)_iw.answers.industries=d.industries_served;
    if(d.target_clients)_iw.answers.ideal_customer=_s(d.target_clients);
    if(d.differentiators)_iw.answers._scan_diff=d.differentiators;
    if(d.regions)_iw.answers.geography=d.regions;
    if(d._site_text)_iw.answers._site_text=d._site_text;
    toast('Website scanned! Fields updated.');
    iwRenderSection(_iw.activeSection);
    iwRenderTabs();
    iwAutoSave();
  }).catch(function(){if(field)field.style.borderColor='';toast('Scan failed')});
}

function iwRenderBottom(){
  var bot=document.getElementById('iwizBottom');
  var totalQ=_iwQuestions.length;
  var answeredQ=_iwQuestions.filter(function(q){var v=_iw.answers[q.id];return v&&(typeof v==='string'?v.length>0:v.length>0)}).length;
  var pct=totalQ>0?Math.round(answeredQ/totalQ*100):0;
  var prog=document.getElementById('iwizProg');
  if(prog)prog.style.width=pct+'%';

  bot.innerHTML='<div style="display:flex;align-items:center;gap:12px">'
    +'<span style="font:500 13px/1 var(--font);color:var(--t3)">'+answeredQ+'/'+totalQ+' fields</span>'
    +'<span style="font:600 13px/1 var(--mono);color:var(--acc)">'+pct+'%</span>'
    +'</div>'
    +'<button class="iwiz-btn" onclick="iwComplete()" '+(pct<30?'disabled':'')+'>Complete Setup \u2192</button>';
}

// AI Chat — with conversation history
var _iwChatHistory=[];
var _iwSendingAssist=false;

function iwSendAssist(){
  // In-flight guard: rapid Enter + button clicks would otherwise fire
  // the same prompt twice (or more), spamming /api/wizard/assist and
  // doubling the assistant's response in the chat log.
  if(_iwSendingAssist)return;
  var input=document.getElementById('iwizChatIn');
  var msgs=document.getElementById('iwizChatMsgs');
  if(!input||!input.value.trim())return;
  _iwSendingAssist=true;
  var message=input.value.trim();
  input.value='';

  // Get context: active section + focused field
  var qContext=_iwSections[_iw.activeSection]?_iwSections[_iw.activeSection].name+' section':'General setup';
  var focusedField=document.activeElement;
  var currentAns='';
  if(focusedField&&focusedField.dataset&&focusedField.dataset.field){
    currentAns=focusedField.value||'';
    // Find the question for this field
    var fq=_iwQuestions.filter(function(q){return q.id===focusedField.dataset.field})[0];
    if(fq)qContext=fq.q;
  }

  // Add to history
  _iwChatHistory.push({role:'user',text:message});

  // Show user message
  msgs.innerHTML+='<div class="iwiz-chat-msg user">'+esc(message)+'</div>';
  msgs.scrollTop=msgs.scrollHeight;

  // Show loading
  var loadId='assist-load-'+Date.now();
  msgs.innerHTML+='<div class="iwiz-chat-msg bot" id="'+loadId+'"><span style="color:var(--t3)">Thinking…</span></div>';
  msgs.scrollTop=msgs.scrollHeight;

  fetch('/api/wizard/assist',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      message:message,
      question:qContext,
      current_answer:typeof currentAns==='string'?currentAns:'',
      answers:_iw.answers,
      history:_iwChatHistory.slice(-10)
    })})
  .then(function(r){return r.json()}).then(function(d){
    var loadEl=document.getElementById(loadId);
    if(d.ok&&d.reply){
      // Add bot reply to history
      _iwChatHistory.push({role:'bot',text:d.reply});
      // Render with markdown-like formatting
      var replyHtml=esc(d.reply)
        .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
        .replace(/\n\n/g,'<br><br>')
        .replace(/\n/g,'<br>');
      var copyBtn='<button class="copy-btn" onclick="iwCopyToField(this)">Copy to field</button>';
      if(loadEl)loadEl.innerHTML=replyHtml+'<br>'+copyBtn;
    }else{
      if(loadEl)loadEl.innerHTML='<span style="color:var(--red)">'+esc(d.error||'Try again')+'</span>';
    }
    msgs.scrollTop=msgs.scrollHeight;
  }).catch(function(err){
    var loadEl=document.getElementById(loadId);
    if(loadEl)loadEl.innerHTML='<span style="color:var(--red)">Network error \u2014 check your connection</span>';
  }).finally(function(){
    // Always clear the in-flight guard so a follow-up question after
    // a network error / 429 / empty reply can still be sent.
    _iwSendingAssist=false;
  });
}
function iwCopyToField(btn){
  var msgDiv=btn.parentElement;
  // Get text content without the button text
  var clone=msgDiv.cloneNode(true);
  clone.querySelectorAll('.copy-btn').forEach(function(b){b.remove()});
  var text=clone.textContent.trim();
  // Find the first visible textarea or text input in the card panel
  var fields=document.querySelectorAll('.iwiz-card-panel textarea, .iwiz-card-panel input[type="text"]');
  // Try to paste into focused field, or first empty critical field
  var target=document.activeElement;
  if(!target||!target.dataset||!target.dataset.field){
    // Find first empty field in current section
    for(var i=0;i<fields.length;i++){
      if(!fields[i].value.trim()){target=fields[i];break}
    }
    if(!target||!target.dataset)target=fields[0];
  }
  if(target){
    target.value=text;
    target.focus();
    target.style.borderColor='var(--acc)';
    setTimeout(function(){target.style.borderColor=''},1500);
    // Trigger save
    if(target.dataset&&target.dataset.field){
      _iw.answers[target.dataset.field]=text;
      iwAutoSave();
    }
  }
  btn.textContent='Copied!';
  setTimeout(function(){btn.textContent='Copy to field'},2000);
}

// Questions database
var _iwPhaseNames={1:'Your Business',2:'Your Customer',3:'Your Sales',4:'Your Market',5:'Deep Dive'};
function iwSetPhase(n,s,t){var el=document.getElementById('iwizPhase');if(el)el.textContent=n?(s?n+' \u00b7 '+s+'/'+t:n):''}

var _iwQuestions=[
  // Phase 1: Basics (0-20%)
  {id:'business_name',phase:1,q:"What's the name of your business?",type:'text',ph:'Your company name',conf:5},
  {id:'website',phase:1,q:"What's your website?",type:'url',ph:'yourwebsite.com',conf:5,scan:true},
  {id:'what_you_do',phase:1,q:"In one sentence, what does your business do?",type:'text',ph:'We help [who] to [what] by [how]',conf:5,critical:true,aiReply:true},
  {id:'services',phase:1,q:"What specific services or products do you offer?",type:'long',ph:'List your main services or products, separated by commas',conf:4,critical:true},
  {id:'how_it_works',phase:1,q:"How do you deliver your services?",type:'single',opts:['Fully remote / virtual','On-site / in-person only','Hybrid (remote + on-site)','Digital product (SaaS / app)','Physical product (shipped)','Local service area only'],ph:'',conf:5,critical:true},
  {id:'business_type',phase:1,q:"How would you describe your business?",type:'single',opts:['B2B Service','B2B Software/SaaS','Products/Manufacturing','Agency/Consultancy','Freelancer/Solo','Other'],conf:3},
  {id:'stage',phase:1,q:"Where are you in your journey?",type:'single',opts:['Just starting','Growing (1-10 clients)','Scaling (10-50)','Established (50+)'],conf:2},
  // Phase 2: Customer (20-50%)
  {id:'ideal_customer',phase:2,q:"Who is your ideal customer? Describe them.",type:'long',ph:'Describe the role, company type, and situation of your best buyer',conf:8,critical:true},
  {id:'customer_size',phase:2,q:"What size company do you usually sell to?",type:'multi',opts:['Solo/Freelancer','Small (2-20)','Medium (20-200)','Large (200-2000)','Enterprise (2000+)','Any size'],conf:4},
  {id:'industries',phase:2,q:"Which industries are your best customers in?",type:'multi',opts:['Technology','Healthcare','Finance','Marketing/Agencies','Manufacturing','Education','Retail/E-commerce','Professional Services','Real Estate','Media/Publishing','Non-profit','Legal','Hospitality','Construction'],conf:5},
  {id:'geography',phase:2,q:"Where are your target customers based?",type:'multi',opts:['Western Europe','Eastern Europe','United Kingdom','United States','Canada','Middle East','Asia Pacific','Latin America','Global'],conf:4},
  {id:'decision_makers',phase:2,q:"Who makes the buying decision?",type:'multi',opts:['CEO/Founder','CMO/Marketing','Sales Director','Operations','IT/CTO','HR Director','Finance/CFO','Procurement','Department Manager'],conf:4},
  {id:'pain_point',phase:2,q:"What's the biggest problem your customers have that you solve?",type:'long',ph:'They struggle with...',conf:8,critical:true,aiReply:true},
  {id:'triggers',phase:2,q:"What triggers a customer to need you right now?",type:'multi_text',opts:['Rapid growth','New product launch','Lost a client','Hiring surge','Budget approved','Competitor threat','Regulation change','Event/conference','Rebranding'],conf:5},
  // Phase 3: Sales (50-70%)
  {id:'deal_size',phase:3,q:"What's your typical deal value?",type:'single',opts:['Under \u20ac1k','\u20ac1k-5k','\u20ac5k-20k','\u20ac20k-100k','Over \u20ac100k'],conf:3},
  {id:'sales_cycle',phase:3,q:"How long does it take to close a deal?",type:'single',opts:['Same day','Days','Weeks','1-3 months','3-6 months','6+ months'],conf:2},
  {id:'lead_sources',phase:3,q:"How do you currently find new clients?",type:'multi',opts:['Referrals','Cold email','LinkedIn','Paid ads','SEO/Content','Events','Partnerships','Inbound/website','No consistent source'],conf:3},
  {id:'differentiator',phase:3,q:"Why do customers choose you over competitors?",type:'long',ph:'Unlike others, we...',conf:6,critical:true},
  {id:'outreach_tone',phase:3,q:"What tone should your outreach emails have?",type:'single',opts:['Direct & to the point','Consultative & helpful','Warm & friendly','Premium & exclusive','Casual & personal'],ph:'',conf:3},
  {id:'proof',phase:3,q:"Do you have a result or case study you can reference?",type:'long',ph:'Helped [client type] achieve [result] in [timeframe]',conf:4},
  // Phase 4: Market (70-85%)
  {id:'competitors',phase:4,q:"Who are your main competitors?",type:'text',ph:'Company A, Company B...',conf:3},
  {id:'comp_diff',phase:4,q:"What do you do that competitors don't?",type:'long',ph:"We're the only ones who...",conf:4},
  {id:'dream_client',phase:4,q:"Describe your dream client. Be specific.",type:'long',ph:'A [size] company in [industry] that [situation]...',conf:5,critical:true},
  {id:'past_clients',phase:4,q:"Name 2-3 companies you've worked with (or types of companies). This helps the AI find similar ones.",type:'long',ph:'Company A (industry, size), Company B (industry, size)...',conf:6,critical:true},
  {id:'buyer_search_terms',phase:4,q:"What would your ideal buyer type into Google when looking for your service?",type:'long',ph:'The exact phrases someone would search when they need what you offer',conf:7,critical:true},
  {id:'hiring_signals',phase:4,q:"What job titles does a company post when they actually need your service?",type:'long',ph:'Job titles that signal a company is building a team in your area of expertise',conf:5,critical:true},
  {id:'anti_customer',phase:4,q:"Who should Huntova NEVER target for you?",type:'long',ph:'Types of companies or situations that are a bad fit for your service',conf:5,critical:true},
  {id:'lookalikes',phase:4,q:"What companies look like good prospects but are actually wrong?",type:'long',ph:'Companies that seem relevant at first glance but are actually competitors or wrong fit',conf:5,critical:true},
  {id:'web_discovery_pages',phase:4,q:"Where do your best prospects show up online? What pages reveal them?",type:'long',ph:'Types of web pages where your ideal buyers are likely to appear',conf:6,critical:true},
  {id:'buying_signals',phase:4,q:"What visible online clues tell you a company needs your service right now?",type:'long',ph:'Observable signs on a company website or online presence that they need your help',conf:6,critical:true},
  {id:'disqualification_signals',phase:4,q:"What would you see on a company's website that means they are NOT a fit?",type:'long',ph:'Red flags that tell you this company already has what you offer or is wrong for another reason',conf:5,critical:true},
];

function iwRenderQuestion(){
  var msg=document.getElementById('iwizMsg');
  var inp=document.getElementById('iwizInput');
  var nav=document.getElementById('iwizNav');
  var isBack=_iw._goingBack;_iw._goingBack=false;

  // Find current question
  var phaseQs=_iwQuestions.filter(function(q){return q.phase===_iw.phase});
  if(_iw.qi>=phaseQs.length){
    _iw.phase++;_iw.qi=0;
    iwSave();
    if(_iw.phase===5){iwPhase5();return}
    if(_iw.phase>5){iwReview();return}
    phaseQs=_iwQuestions.filter(function(q){return q.phase===_iw.phase});
  }
  if(_iw.qi>=phaseQs.length){_iw.phase++;_iw.qi=0;iwRenderQuestion();return}

  var q=phaseQs[_iw.qi];
  var existing=_iw.answers[q.id]||'';
  iwSetPhase(_iwPhaseNames[_iw.phase]||'',_iw.qi+1,phaseQs.length);

  // Personalise question with business name
  var qText=q.q;
  if(_iw.answers.business_name&&qText.indexOf('your business')>=0){
    qText=qText.replace('your business',_iw.answers.business_name);
  }

  function showInput(){
    var h='';
    if(q.type==='text'||q.type==='long'){
      var cls=q.type==='long'?'iwiz-textarea':'iwiz-text';
      if(q.type==='long'){
        h='<textarea class="'+cls+'" id="iwAns" placeholder="'+esc(q.ph||'')+'" rows="3">'+esc(typeof existing==='string'?existing:'')+'</textarea>';
      }else{
        h='<input class="'+cls+'" id="iwAns" type="text" placeholder="'+esc(q.ph||'')+'" value="'+esc(typeof existing==='string'?existing:'')+'">';
      }
    } else if(q.type==='url'){
      h='<div class="iwiz-scan-row"><input class="iwiz-text" id="iwAns" type="text" placeholder="'+esc(q.ph||'')+'" value="'+esc(typeof existing==='string'?existing:'')+'">';
      if(q.scan)h+='<button class="iwiz-scan-btn" id="iwScanBtn" onclick="iwDoScan()">Scan</button>';
      h+='</div>';
    } else if(q.type==='single'||q.type==='multi'){
      h='<div class="iwiz-pills">';
      var sel=Array.isArray(existing)?existing:(existing?[existing]:[]);
      (q.opts||[]).forEach(function(opt){
        var isOn=sel.indexOf(opt)>=0;
        h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwTogglePill(this,\''+q.id+'\',\''+opt.replace(/'/g,"\\'")+'\','+(q.type==='single'?'true':'false')+')">'+esc(opt)+'</button>';
      });
      h+='</div>';
    } else if(q.type==='multi_text'){
      h='<div class="iwiz-pills">';
      var sel=Array.isArray(existing)?existing:[];
      (q.opts||[]).forEach(function(opt){
        var isOn=sel.indexOf(opt)>=0;
        h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwTogglePill(this,\''+q.id+'\',\''+opt.replace(/'/g,"\\'")+'\',false)">'+esc(opt)+'</button>';
      });
      h+='</div>';
      h+='<input class="iwiz-text" id="iwCustom" type="text" placeholder="Add your own (comma separated)" style="margin-top:8px">';
    }
    inp.innerHTML=h;
    // Navigation: Back + Skip (non-critical) + Continue
    var navH='<button class="iwiz-btn-ghost" onclick="iwBack()">\u2190 Back</button>';
    if(!q.critical) navH+='<button class="iwiz-btn-ghost" onclick="iwSkip()" style="margin-left:6px">Skip</button>';
    navH+='<button class="iwiz-btn" id="iwNext" onclick="iwAnswer()">Continue \u2192</button>';
    nav.innerHTML=navH;
    var ans=document.getElementById('iwAns');
    if(ans)setTimeout(function(){ans.focus()},50);
    if(ans)ans.addEventListener('keydown',function(e){if(e.key==='Enter'&&q.type!=='long')iwAnswer()});
  }

  if(isBack){
    // Going back: show instantly, no animation delay
    iwTypeInstant(msg,qText);
    showInput();
  } else {
    // Going forward: animate the question text
    var orb=document.getElementById('iwizOrb');
    if(orb)orb.className='iwiz-orb thinking';
    inp.innerHTML='';nav.innerHTML='';
    setTimeout(function(){
      iwType(msg,qText,showInput);
    },300);
  }
}

function iwTogglePill(el,key,val,single){
  if(!_iw.answers[key])_iw.answers[key]=[];
  if(single){
    _iw.answers[key]=val;
    el.parentElement.querySelectorAll('.iwiz-pill').forEach(function(b){b.classList.remove('on')});
    el.classList.add('on');
  }else{
    if(!Array.isArray(_iw.answers[key]))_iw.answers[key]=[];
    var idx=_iw.answers[key].indexOf(val);
    if(idx>=0){_iw.answers[key].splice(idx,1);el.classList.remove('on')}
    else{_iw.answers[key].push(val);el.classList.add('on')}
  }
}

function iwSkip(){
  _iw.qi++;
  iwSave();
  iwRenderQuestion();
}

function iwAnswer(){
  var phaseQs=_iwQuestions.filter(function(q){return q.phase===_iw.phase});
  var q=phaseQs[_iw.qi];
  if(!q)return;
  // Collect answer
  var ans=document.getElementById('iwAns');
  if(ans){
    var val=ans.value?ans.value.trim():'';
    if(q.type==='text'||q.type==='long'||q.type==='url')_iw.answers[q.id]=val;
  }
  // Collect custom text for multi_text
  var custom=document.getElementById('iwCustom');
  if(custom&&custom.value.trim()){
    var extras=custom.value.split(',').map(function(s){return s.trim()}).filter(function(s){return s});
    if(!Array.isArray(_iw.answers[q.id]))_iw.answers[q.id]=[];
    extras.forEach(function(e){if(_iw.answers[q.id].indexOf(e)<0)_iw.answers[q.id].push(e)});
  }
  // Calculate confidence — only add if this question wasn't already answered
  if(!_iw._answered)_iw._answered={};
  var val=_iw.answers[q.id];
  var wasAnswered=_iw._answered[q.id];
  if(val&&(typeof val==='string'?val.length>0:val.length>0)){
    if(!wasAnswered){
      iwAddConfidence(q.conf||3);
      if(typeof val==='string'&&val.length>50)iwAddConfidence(3);
      _iw._answered[q.id]=true;
    }
  }else if(q.critical&&!wasAnswered){
    _iw.confidence=Math.max(0,_iw.confidence-5);
    iwUpdateConfidence();
  }
  // Save and advance
  _iw.qi++;
  iwSave();
  iwRenderQuestion();
}

function iwBack(){
  // Cancel any running animation
  if(_iwTypeTimer){clearTimeout(_iwTypeTimer);_iwTypeTimer=null}
  _iw._goingBack=true;
  if(_iw.phase>5){
    // From review → go to last phase
    _iw.phase=4;var pq=_iwQuestions.filter(function(q){return q.phase===4});_iw.qi=pq.length-1;iwRenderQuestion();
  }else if(_iw.qi>0){_iw.qi--;iwRenderQuestion()}
  else if(_iw.phase>1){_iw.phase--;var pq=_iwQuestions.filter(function(q){return q.phase===_iw.phase});_iw.qi=Math.max(0,pq.length-1);iwRenderQuestion()}
  else{iwShowBriefing()}
}

function iwDoScan(){
  var ans=document.getElementById('iwAns');
  if(!ans||!ans.value.trim())return;
  var btn=document.getElementById('iwScanBtn');
  if(btn){btn.disabled=true;btn.textContent='Scanning...';btn.classList.add('btn-loading')}
  var orb=document.getElementById('iwizOrb');
  if(orb)orb.className='iwiz-orb thinking';
  fetch('/api/wizard/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:ans.value.trim()})})
  .then(function(r){return r.json()}).then(function(d){
    if(btn){btn.disabled=false;btn.textContent='Scan';btn.classList.remove('btn-loading')}
    if(orb)orb.className='iwiz-orb';
    if(d.error){toast('Could not read site \u2014 continue manually');return}
    _iw.scanData=d;
    var _s=function(v){return(typeof v==='string'&&v&&v!=='undefined')?v.trim():''};
    if(_s(d.company_name)&&!_iw.answers.business_name)_iw.answers.business_name=_s(d.company_name);
    if(_s(d.summary))_iw.answers.what_you_do=_s(d.summary);
    else if(_s(d.business_type))_iw.answers.what_you_do=_s(d.business_type);
    if(d.industries_served)_iw.answers.industries=d.industries_served;
    if(d.target_clients)_iw.answers.ideal_customer=_s(d.target_clients);
    if(d.differentiators)_iw.answers._scan_diff=d.differentiators;
    if(d.regions)_iw.answers.geography=d.regions;
    if(d._site_text)_iw.answers._site_text=d._site_text;
    iwAddConfidence(5);
    var msg=document.getElementById('iwizMsg');
    if(msg){
      var name=_s(d.company_name)||'your site';
      iwType(msg,"I scanned "+name+". I already have a head start. Let's keep going.",function(){
        setTimeout(function(){_iw.qi++;iwRenderQuestion()},800);
      });
    }
    iwSave();
  }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Scan';btn.classList.remove('btn-loading')}toast('Scan failed')});
}

// Phase 5: AI-generated questions
function iwPhase5(){
  var msg=document.getElementById('iwizMsg');
  var inp=document.getElementById('iwizInput');
  var nav=document.getElementById('iwizNav');
  inp.innerHTML='';nav.innerHTML='';
  iwSetPhase('Deep Dive');
  var orb=document.getElementById('iwizOrb');
  if(orb)orb.className='iwiz-orb thinking';
  iwType(msg,"Let me think about what else I need to know...",function(){
    fetch('/api/wizard/generate-phase5',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers:_iw.answers})})
    .then(function(r){return r.json()}).then(function(d){
      if(d.ok&&d.questions&&d.questions.length){
        _iw.phase5Qs=d.questions;
        _iw.qi=0;
        iwRenderPhase5Q();
      }else{
        // Skip to review if AI can't generate
        _iw.phase=6;iwReview();
      }
    }).catch(function(){_iw.phase=6;iwReview()});
  });
}
function iwRenderPhase5Q(){
  if(_iw.qi>=_iw.phase5Qs.length){_iw.phase=6;iwReview();return}
  var q=_iw.phase5Qs[_iw.qi];
  var msg=document.getElementById('iwizMsg');
  var inp=document.getElementById('iwizInput');
  var nav=document.getElementById('iwizNav');
  iwType(msg,q.question||'Tell me more.',function(){
    var h='';
    var qid='phase5_'+_iw.qi;
    if(q.type==='text'){
      h='<textarea class="iwiz-textarea" id="iwAns" placeholder="'+(q.placeholder||'')+'" rows="3"></textarea>';
    }else if(q.type==='single_select'||q.type==='multi_select'){
      h='<div class="iwiz-pills">';
      (q.options||[]).forEach(function(opt){
        h+='<button class="iwiz-pill" onclick="iwTogglePill(this,\''+qid+'\',\''+opt.replace(/'/g,"\\'")+'\','+(q.type==='single_select'?'true':'false')+')">'+esc(opt)+'</button>';
      });
      h+='</div>';
    }else{
      h='<textarea class="iwiz-textarea" id="iwAns" placeholder="Type your answer..." rows="3"></textarea>';
    }
    inp.innerHTML=h;
    nav.innerHTML='<button class="iwiz-btn" onclick="iwAnswerPhase5()">Continue \u2192</button>';
  });
}
function iwAnswerPhase5(){
  var qid='phase5_'+_iw.qi;
  var ans=document.getElementById('iwAns');
  if(ans)_iw.answers[qid]=ans.value.trim();
  iwAddConfidence(4);
  _iw.qi++;
  iwSave();
  iwRenderPhase5Q();
}

// Review
function iwReview(){
  var msg=document.getElementById('iwizMsg');
  var inp=document.getElementById('iwizInput');
  var nav=document.getElementById('iwizNav');
  _iw.phase=6;
  iwSetPhase('Review');
  iwTypeInstant(msg,"Review your answers. Click any field to edit it.");

  var a=_iw.answers;
  var _v=function(k){var v=a[k];if(!v)return'<span style="color:var(--t4);font-style:italic">Not set</span>';return typeof v==='string'?esc(v):Array.isArray(v)?esc(v.join(', ')):esc(String(v))};
  // Editable row: click to inline-edit
  var _r=function(label,key){
    var q=_iwQuestions.filter(function(x){return x.id===key})[0];
    var isLong=q&&(q.type==='long');
    return '<div class="iwiz-review-row" style="cursor:pointer;padding:6px 8px;border-radius:6px;transition:background .15s" onmouseenter="this.style.background=\'rgba(61,155,143,0.06)\'" onmouseleave="this.style.background=\'transparent\'" onclick="iwEditField(\''+key+'\')"><b style="color:var(--t3);font-size:12px;text-transform:uppercase;letter-spacing:.3px">'+esc(label)+'</b><div style="margin-top:2px;color:var(--t1);font-size:14px;line-height:1.4">'+_v(key)+'</div></div>';
  };

  var h='<div class="iwiz-review" style="max-height:55vh;overflow-y:auto;padding-right:8px">';

  h+='<div class="iwiz-review-sec" style="margin-bottom:16px"><h3 style="color:var(--acc);font-size:13px;text-transform:uppercase;letter-spacing:.5px;margin:0 0 8px">Your Business</h3>';
  h+=_r('Name','business_name');h+=_r('Website','website');h+=_r('What you do','what_you_do');
  h+=_r('Services','services');h+=_r('Delivery model','how_it_works');h+=_r('Type','business_type');
  h+='</div>';

  h+='<div class="iwiz-review-sec" style="margin-bottom:16px"><h3 style="color:var(--acc);font-size:13px;text-transform:uppercase;letter-spacing:.5px;margin:0 0 8px">Your Customer</h3>';
  h+=_r('Ideal customer','ideal_customer');h+=_r('Company size','customer_size');h+=_r('Industries','industries');
  h+=_r('Regions','geography');h+=_r('Decision makers','decision_makers');h+=_r('Pain point','pain_point');h+=_r('Triggers','triggers');
  h+='</div>';

  h+='<div class="iwiz-review-sec" style="margin-bottom:16px"><h3 style="color:var(--acc);font-size:13px;text-transform:uppercase;letter-spacing:.5px;margin:0 0 8px">Your Sales</h3>';
  h+=_r('Deal size','deal_size');h+=_r('Differentiator','differentiator');h+=_r('Outreach tone','outreach_tone');h+=_r('Proof','proof');
  h+='</div>';

  h+='<div class="iwiz-review-sec" style="margin-bottom:16px"><h3 style="color:var(--acc);font-size:13px;text-transform:uppercase;letter-spacing:.5px;margin:0 0 8px">Your Market & Signals</h3>';
  h+=_r('Competitors','competitors');h+=_r('Dream client','dream_client');h+=_r('Never target','anti_customer');
  h+=_r('Similar but wrong','lookalikes');h+=_r('Where prospects show up','web_discovery_pages');
  h+=_r('Buying signals','buying_signals');h+=_r('Disqualification signals','disqualification_signals');
  h+='</div>';

  h+='</div>';
  inp.innerHTML=h;
  nav.innerHTML='<button class="iwiz-btn-ghost" onclick="iwBack()">\u2190 Back</button><button class="iwiz-btn" onclick="iwComplete()">Complete Setup \u2192</button>';
}

function iwEditField(key){
  // Find the question definition
  var q=_iwQuestions.filter(function(x){return x.id===key})[0];
  if(!q)return;
  var msg=document.getElementById('iwizMsg');
  var inp=document.getElementById('iwizInput');
  var nav=document.getElementById('iwizNav');
  var existing=_iw.answers[key]||'';

  iwTypeInstant(msg,q.q);

  var h='';
  if(q.type==='text'||q.type==='long'||q.type==='url'){
    var cls=q.type==='long'?'iwiz-textarea':'iwiz-text';
    if(q.type==='long'){
      h='<textarea class="'+cls+'" id="iwAns" placeholder="'+esc(q.ph||'')+'" rows="3">'+esc(typeof existing==='string'?existing:'')+'</textarea>';
    }else{
      h='<input class="'+cls+'" id="iwAns" type="text" placeholder="'+esc(q.ph||'')+'" value="'+esc(typeof existing==='string'?existing:'')+'">';
    }
  } else if(q.type==='single'||q.type==='multi'){
    h='<div class="iwiz-pills">';
    var sel=Array.isArray(existing)?existing:(existing?[existing]:[]);
    (q.opts||[]).forEach(function(opt){
      var isOn=sel.indexOf(opt)>=0;
      h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwTogglePill(this,\''+key+'\',\''+opt.replace(/'/g,"\\'")+'\','+(q.type==='single'?'true':'false')+')">'+esc(opt)+'</button>';
    });
    h+='</div>';
  } else if(q.type==='multi_text'){
    h='<div class="iwiz-pills">';
    var sel=Array.isArray(existing)?existing:[];
    (q.opts||[]).forEach(function(opt){
      var isOn=sel.indexOf(opt)>=0;
      h+='<button class="iwiz-pill'+(isOn?' on':'')+'" onclick="iwTogglePill(this,\''+key+'\',\''+opt.replace(/'/g,"\\'")+'\',false)">'+esc(opt)+'</button>';
    });
    h+='</div>';
    h+='<input class="iwiz-text" id="iwCustom" type="text" placeholder="Add your own" style="margin-top:8px">';
  }
  inp.innerHTML=h;
  nav.innerHTML='<button class="iwiz-btn-ghost" onclick="iwReview()">\u2190 Cancel</button><button class="iwiz-btn" onclick="iwSaveField(\''+key+'\')">Save</button>';
  var ans=document.getElementById('iwAns');
  if(ans)setTimeout(function(){ans.focus()},50);
}

function iwSaveField(key){
  var q=_iwQuestions.filter(function(x){return x.id===key})[0];
  var ans=document.getElementById('iwAns');
  if(ans&&q){
    var val=ans.value?ans.value.trim():'';
    if(q.type==='text'||q.type==='long'||q.type==='url')_iw.answers[key]=val;
  }
  var custom=document.getElementById('iwCustom');
  if(custom&&custom.value.trim()){
    var extras=custom.value.split(',').map(function(s){return s.trim()}).filter(function(s){return s});
    if(!Array.isArray(_iw.answers[key]))_iw.answers[key]=[];
    extras.forEach(function(e){if(_iw.answers[key].indexOf(e)<0)_iw.answers[key].push(e)});
  }
  iwSave();
  iwReview();
}

function iwEditSection(phase){
  _iw.phase=phase;
  _iw.qi=0;
  iwRenderQuestion();
}

// Complete
function iwComplete(){
  // Collect all current field values before submitting
  document.querySelectorAll('[data-field]').forEach(function(el){
    var key=el.dataset.field;
    if(key&&!key.endsWith('-custom'))_iw.answers[key]=el.value?el.value.trim():'';
  });

  var bot=document.getElementById('iwizBottom');
  if(bot)bot.innerHTML='<div style="color:var(--t2);font:500 14px/1 var(--font)">Building your intelligence profile...</div>';
  var a=_iw.answers;
  // Parse services from comma-separated text + merge with scan data
  var scanServices=(_iw.scanData||{}).services||[];
  var manualServices=a.services?a.services.split(',').map(function(s){return s.trim()}).filter(function(s){return s}):[];
  var allServices=scanServices.slice();
  manualServices.forEach(function(s){if(allServices.indexOf(s)<0)allServices.push(s)});
  var profile={
    company_name:a.business_name||'',
    company_website:a.website||'',
    business_description:a.what_you_do||'',
    business_type:typeof a.business_type==='string'?a.business_type:'',
    services:allServices,
    how_it_works:typeof a.how_it_works==='string'?a.how_it_works:'',
    differentiators:a._scan_diff||(a.differentiator?[a.differentiator]:[]),
    target_clients:a.ideal_customer||'',
    icp_size:Array.isArray(a.customer_size)?a.customer_size:[],
    icp_industries:Array.isArray(a.industries)?a.industries:[],
    regions:Array.isArray(a.geography)?a.geography:[],
    buyer_roles:Array.isArray(a.decision_makers)?a.decision_makers:[],
    triggers:Array.isArray(a.triggers)?a.triggers:[],
    exclusions:a.anti_customer?[a.anti_customer]:[],
    lookalikes:a.lookalikes||'',
    web_discovery_pages:a.web_discovery_pages||'',
    buying_signals:a.buying_signals||'',
    disqualification_signals:a.disqualification_signals||'',
    past_clients:a.past_clients||'',
    buyer_search_terms:a.buyer_search_terms||'',
    hiring_signals:a.hiring_signals||'',
    competitors:a.competitors||'',
    dream_client:a.dream_client||'',
    comp_diff:a.comp_diff||'',
    pain_point:a.pain_point||'',
    proof:a.proof||'',
    outreach_tone:typeof a.outreach_tone==='string'?a.outreach_tone:'consultative',
    price_range:typeof a.deal_size==='string'?a.deal_size:'',
    sales_cycle:typeof a.sales_cycle==='string'?a.sales_cycle:'',
    stage:typeof a.stage==='string'?a.stage:'',
    lead_sources:Array.isArray(a.lead_sources)?a.lead_sources:[],
    _site_text:a._site_text||'',
    _interview_complete:true,
    _wizard_answers:a,
    _wizard_confidence:_iw.confidence,
  };
  fetch('/api/wizard/complete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({profile:profile,history:[]})})
  .then(function(r){return r.json().then(function(d){return {status:r.status,data:d}})})
  .then(function(res){
    var d=res.data;
    if(res.status===400 && d.vague_issues){
      var issues=d.vague_issues.map(function(i){return '<li style="margin:6px 0;color:var(--t2)">'+esc(i)+'</li>'}).join('');
      if(bot)bot.innerHTML='<div style="text-align:left"><div style="color:var(--red);font:600 14px/1.4 var(--font);margin-bottom:8px">Needs more detail:</div><ul style="padding-left:16px;font-size:13px;line-height:1.5;list-style:disc">'+issues+'</ul></div>';
    }else if(d.ok){
      if(bot)bot.innerHTML='<div style="display:flex;align-items:center;gap:12px"><span style="color:var(--acc);font:600 14px/1 var(--font)">Agent trained successfully!</span></div>'
        +'<button class="iwiz-btn" onclick="iwClose();openStartPopup()">Start Hunting \u2192</button>';
    }else{
      if(bot)bot.innerHTML='<div style="color:var(--t2)">Saved. '+esc(d.error||'')+'</div><button class="iwiz-btn" onclick="iwClose()">Close</button>';
    }
  }).catch(function(){
    if(bot)bot.innerHTML='<div style="color:var(--t2)">Saved!</div><button class="iwiz-btn" onclick="iwClose()">Close</button>';
  });
}

// Override old wizard open to use new immersive wizard
function wizOpen(loadExisting){iwOpen()}
function wizReopen(){iwOpen()}

















/* ═══ WIZARD — Multi-step guided interview ═══ */
var _wiz = {step:1, data:{}, scanData:null};

function wizClose(){
  // Don't fire partial save if final save just completed (avoid race)
  if(!_wiz._finalSaved){
    try{wizCollectStep()}catch(_){}
  }
  var bg=document.getElementById('wizBg');
  if(bg)bg.classList.remove('on');
  // Fallback: close immersive wizard if open
  iwClose();
}
function _wizAutoSave(){
  clearTimeout(_wizAutoSave._t);
  _wizAutoSave._t=setTimeout(function(){
    fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({wizard:_wiz.data})}).catch(function(){});
  },1500);
}

/* Legacy wizard removed — immersive wizard v4 (iwOpen/iwShow/iwShowWorkspace) is the active wizard */

// Wizard is OPT-IN now (Enzo's killer-feature directive 2026-04-30):
// the dashboard shows an empty-state banner with "🪄 Auto Wizard"
// button instead of force-popping. Pro users skip the wizard entirely
// — they configure via Settings or `huntova config set` / env vars.
// The wizard remains accessible via wizReopen() (user-menu →
// Retrain AI, dashboard "Run the wizard first" link, etc.).

/* Legacy wizard block deleted — 322 lines removed. See git history for original.
/* Legacy wizard deleted — see git history */


/* ═══════════════════════════════════════
   RESULTS SUMMARY — shown when agent completes
   ═══════════════════════════════════════ */
function showResultsSummary(leads){
  if(!leads||!leads.length)return;
  var panel=$('resPanel');if(!panel)return;
  var thisRun=leads.filter(function(l){return l.run_id&&l.run_id===leads[0].run_id});
  var data=thisRun.length>3?thisRun:leads;
  var total=data.length;
  var withEmail=data.filter(function(l){return l.contact_email}).length;
  var recurring=data.filter(function(l){return l.is_recurring}).length;
  var avgScore=total?Math.round(data.reduce(function(s,l){return s+(l.fit_score||0)},0)/total*10)/10:0;
  var top=data.slice().sort(function(a,b){return(b.fit_score||0)-(a.fit_score||0)}).slice(0,5);
  var countries={};data.forEach(function(l){var c=l.country||'?';countries[c]=(countries[c]||0)+1});
  var topCountries=Object.entries(countries).sort(function(a,b){return b[1]-a[1]}).slice(0,3);

  $('resTitle').textContent='Scan Complete — '+total+' leads found';
  var statsH='<div class="res-stat"><b>'+total+'</b> leads</div>';
  statsH+='<div class="res-stat"><b>'+withEmail+'</b> with email</div>';
  statsH+='<div class="res-stat"><b>'+recurring+'</b> ongoing need</div>';
  statsH+='<div class="res-stat"><b>'+avgScore+'</b> avg score</div>';
  if(topCountries.length)statsH+='<div class="res-stat">'+topCountries.map(function(c){return esc(c[0])+': '+c[1]}).join(' · ')+'</div>';
  $('resStats').innerHTML=statsH;

  var leadsH='';
  top.forEach(function(l){
    var bg=l.fit_score>=8?'var(--acc)':l.fit_score>=6?'var(--cyn)':'var(--org)';
    var _safeId=esc(l.lead_id||'');
    leadsH+='<div class="res-lead" onclick="openLeadPage(\''+_safeId+'\')" style="cursor:pointer;display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--bd)">';
    leadsH+='<span class="sc" style="background:'+bg+';width:26px;height:26px;font-size:10px">'+l.fit_score+'</span>';
    leadsH+='<span style="font-weight:600;font-size:12px;color:var(--t1)">'+esc(l.org_name||'?')+'</span>';
    leadsH+='<span style="font-size:10px;color:var(--t3);margin-left:auto">'+esc(l.country||'')+'</span>';
    leadsH+='</div>';
  });
  $('resLeads').innerHTML=leadsH;
  panel.classList.add('on');
  // Auto-hide progress bar
  $('topProg').style.display='none';
}

/* ═══════════════════════════════════════
   BULK SELECTION
   ═══════════════════════════════════════ */
function toggleBulkAll(checked){
  _bulkSelected.clear();
  if(checked){filtered.forEach(function(l){_bulkSelected.add(l.lead_id)})}
  document.querySelectorAll('.bulk-cb').forEach(function(cb){cb.checked=checked});
  updateBulkBar();
}
function toggleBulkOne(lid,checked){
  if(checked)_bulkSelected.add(lid);else _bulkSelected.delete(lid);
  var allCb=$('bulkAll');if(allCb)allCb.checked=_bulkSelected.size===filtered.length&&filtered.length>0;
  updateBulkBar();
}
function updateBulkBar(){
  var bar=$('bulkBar');if(!bar)return;
  bar.style.display=_bulkSelected.size>0?'flex':'none';
  var cnt=$('bulkCnt');if(cnt)cnt.textContent=_bulkSelected.size+' selected';
}
function clearBulkSelection(){
  _bulkSelected.clear();
  document.querySelectorAll('.bulk-cb').forEach(function(cb){cb.checked=false});
  var allCb=$('bulkAll');if(allCb)allCb.checked=false;
  updateBulkBar();
}
var _bulkInFlight=false;
async function applyBulkStatus(){
  if(_bulkInFlight)return;
  var st=$('bulkSt');if(!st||!st.value)return toast('Select a status first');
  var ids=Array.from(_bulkSelected);
  if(!ids.length)return;
  _bulkInFlight=true;
  try{
    var r=await fetch('/api/bulk-update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_ids:ids,email_status:st.value})});
    var d=await r.json();
    if(d.ok){toast(d.updated+' leads updated');clearBulkSelection();loadCRM()}
    else toast('Error: '+(d.error||'unknown'));
  }catch(e){toast('Bulk update error')}
  _bulkInFlight=false;
}

/* ═══════════════════════════════════════
   ACCESSIBILITY: Focus Trap + Escape Keys
   ═══════════════════════════════════════ */
(function() {
  var FOCUSABLE = 'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';

  function trapFocus(container) {
    if (!container) return;
    var els = container.querySelectorAll(FOCUSABLE);
    if (!els.length) return;
    var first = els[0], last = els[els.length - 1];
    container._focusTrap = function(e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last.focus(); }
      } else {
        if (document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    container.addEventListener('keydown', container._focusTrap);
    first.focus();
  }

  function releaseFocus(container) {
    if (!container || !container._focusTrap) return;
    container.removeEventListener('keydown', container._focusTrap);
    delete container._focusTrap;
  }

  /* Observe modal open/close via class changes */
  var modalConfigs = [
    { id: 'leadModalBg', cls: 'on', onClose: function() { if (typeof closeLeadModal === 'function') closeLeadModal(); } },
    { id: 'settingsModal', cls: 'on', onClose: function() { if (typeof closeSettings === 'function') closeSettings(); } },
    { id: 'startBg', cls: 'on', onClose: function() { if (typeof closeStartPopup === 'function') closeStartPopup(); } },
    { id: 'wizBg', cls: 'on', onClose: function() { var el = document.getElementById('wizBg'); if (el) el.classList.remove('on'); } },
    { id: 'mBg', cls: 'on', onClose: function() { if (typeof closeMod === 'function') closeMod(); } },
  ];

  modalConfigs.forEach(function(cfg) {
    var el = document.getElementById(cfg.id);
    if (!el) return;
    var obs = new MutationObserver(function() {
      if (el.classList.contains(cfg.cls)) {
        setTimeout(function() { trapFocus(el); }, 100);
      } else {
        releaseFocus(el);
      }
    });
    obs.observe(el, { attributes: true, attributeFilter: ['class'] });
  });

  /* Global Escape key handler for all modals */
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    /* Close topmost modal */
    for (var i = modalConfigs.length - 1; i >= 0; i--) {
      var el = document.getElementById(modalConfigs[i].id);
      if (el && el.classList.contains(modalConfigs[i].cls)) {
        modalConfigs[i].onClose();
        e.preventDefault();
        return;
      }
    }
    /* Close neo widget */
    var nw = document.querySelector('.neo-widget.on');
    if (nw && typeof closeNeoWidget === 'function') {
      closeNeoWidget();
      e.preventDefault();
    }
  });
})();
