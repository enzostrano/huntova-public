/* Smooth scroll */
function hvScroll(s){var e=document.querySelector(s);if(e)e.scrollIntoView({behavior:'smooth',block:'start'})}

/* Nav scroll effect */
window.addEventListener('scroll',function(){document.getElementById('nav').classList.toggle('scrolled',window.scrollY>40)});

/* Reveal on scroll */
var obs=new IntersectionObserver(function(entries){entries.forEach(function(e){if(e.isIntersecting)e.target.classList.add('v')})},{threshold:.1,rootMargin:'0px 0px -40px 0px'});
document.querySelectorAll('.reveal').forEach(function(el){obs.observe(el)});

/* Auth — 3D tilt */
(function(){
  var box=document.getElementById('authBox');if(!box||window.matchMedia('(pointer:coarse)').matches)return;
  box.addEventListener('mousemove',function(e){
    var r=this.getBoundingClientRect(),x=(e.clientX-r.left-r.width/2)/(r.width/2),y=(e.clientY-r.top-r.height/2)/(r.height/2);
    this.style.transform='perspective(1500px) rotateX('+(y*-8)+'deg) rotateY('+(x*8)+'deg)';
  });
  box.addEventListener('mouseleave',function(){this.style.transform=''});
})();
function togglePass(id,btn){
  var inp=document.getElementById(id);
  if(inp.type==='password'){inp.type='text';btn.querySelector('.fi-eye').style.display='';btn.querySelector('.fi-eye-off').style.display='none'}
  else{inp.type='password';btn.querySelector('.fi-eye').style.display='none';btn.querySelector('.fi-eye-off').style.display=''}
}
function switchAuth(mode){
  document.querySelectorAll('.auth-tab').forEach(function(t){t.classList.remove('on')});
  document.querySelectorAll('.auth-form').forEach(function(f){f.classList.remove('on')});
  document.querySelectorAll('.auth-err').forEach(function(e){e.classList.remove('on')});
  var forgotOk=document.getElementById('forgotOk');if(forgotOk)forgotOk.style.display='none';
  if(mode==='login'){document.querySelectorAll('.auth-tab')[0].classList.add('on');document.getElementById('loginForm').classList.add('on')}
  else if(mode==='signup'){document.querySelectorAll('.auth-tab')[1].classList.add('on');document.getElementById('signupForm').classList.add('on')}
  else if(mode==='forgot'){document.getElementById('forgotForm').classList.add('on')}
}
function showErr(id,msg){var el=document.getElementById(id);el.textContent=msg;el.classList.add('on')}
/* Safe: SP_DOTS is a hardcoded static SVG spinner, no user input */
var SP_DOTS='<svg class="spinner" width="20" height="20" viewBox="0 0 24 24"><circle cx="4" cy="12" r="2" fill="currentColor"><animate id="d1" begin="0;d3.end+0.25s" attributeName="cy" calcMode="spline" dur="0.6s" values="12;6;12" keySplines=".33,.66,.66,1;.33,0,.66,.33"/></circle><circle cx="12" cy="12" r="2" fill="currentColor"><animate begin="d1.begin+0.1s" attributeName="cy" calcMode="spline" dur="0.6s" values="12;6;12" keySplines=".33,.66,.66,1;.33,0,.66,.33"/></circle><circle cx="20" cy="12" r="2" fill="currentColor"><animate id="d3" begin="d1.begin+0.2s" attributeName="cy" calcMode="spline" dur="0.6s" values="12;6;12" keySplines=".33,.66,.66,1;.33,0,.66,.33"/></circle></svg>';
function btnLoad(btn,text){btn.disabled=true;btn.innerHTML=SP_DOTS+' '+text}
function btnReset(btn,text){btn.disabled=false;btn.textContent=text}
function doLogin(e){
  e.preventDefault();var btn=document.getElementById('loginBtn');btnLoad(btn,'Logging in...');document.getElementById('loginErr').classList.remove('on');
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('loginEmail').value,password:document.getElementById('loginPass').value})})
  .then(function(r){return r.json()}).then(function(d){if(d.ok){window.location.href='/'}else{showErr('loginErr',d.error||'Login failed');btnReset(btn,'Log In')}})
  .catch(function(){showErr('loginErr','Network error');btnReset(btn,'Log In')});return false
}
function doSignup(e){
  e.preventDefault();var btn=document.getElementById('signupBtn');btnLoad(btn,'Creating account...');document.getElementById('signupErr').classList.remove('on');
  fetch('/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('signupName').value,email:document.getElementById('signupEmail').value,password:document.getElementById('signupPass').value})})
  .then(function(r){return r.json()}).then(function(d){if(d.ok){window.location.href='/'}else{showErr('signupErr',d.error||'Signup failed');btnReset(btn,'Create free account')}})
  .catch(function(){showErr('signupErr','Network error');btnReset(btn,'Create free account')});return false
}
function doForgot(e){
  e.preventDefault();var btn=document.getElementById('forgotBtn');btnLoad(btn,'Sending...');
  document.getElementById('forgotErr').classList.remove('on');
  var okEl=document.getElementById('forgotOk');okEl.style.display='none';
  fetch('/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('forgotEmail').value})})
  .then(function(r){return r.json()}).then(function(d){
    okEl.textContent='If that email exists, a reset link has been sent. Check your inbox.';okEl.style.display='block';
    btnReset(btn,'Send reset link');
  }).catch(function(){showErr('forgotErr','Network error');btnReset(btn,'Send reset link')});
  return false
}
/* Google OAuth error handler */
(function(){
  var p=new URLSearchParams(window.location.search);
  var err=p.get('auth_error');
  if(err){
    var msgs={google_denied:'Google sign-in was cancelled.',invalid_state:'Security check failed. Try again.',token_failed:'Google authentication failed.',userinfo_failed:'Could not get Google profile.',google_error:'Google sign-in error. Try again.',no_email:'Google account has no email.',account_suspended:'Your account has been suspended. Contact support.'};
    setTimeout(function(){showErr('loginErr',msgs[err]||'Authentication error')},200);
    window.history.replaceState({},'','/landing');
  }
})();
