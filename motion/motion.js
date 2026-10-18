/* Responsive Shaniu scene wrapper. Easing and captions are vendored Adu code. */
const video = document.getElementById('base'), card = document.getElementById('card');
const portrait = CONFIG.height > CONFIG.width;
const root = document.documentElement;
for (const [name, value] of Object.entries({ w:CONFIG.width+'px',h:CONFIG.height+'px',accent:CONFIG.style==='ink'?'#a6e851':'#3869c8',panel:CONFIG.style==='ink'?'#131923eb':'#fffffff0',text:CONFIG.style==='ink'?'#fff':'#23344b',title:(portrait?36:40)+'px',small:'14px'})) root.style.setProperty('--'+name,value);
const ready = new Promise((resolve,reject)=>{if(video.readyState>=2)resolve();else{video.addEventListener('loadeddata',resolve,{once:true});video.addEventListener('error',()=>reject(Error('Base video failed')), {once:true});}});
window.renderAt = async t => {
  await ready;
  const target = Math.max(0,Math.min(t,video.duration-.001));
  if(Math.abs(video.currentTime-target)>.0001) await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{video.removeEventListener('seeked',done);reject(Error('Video seek timed out'));},10000);function done(){clearTimeout(timer);resolve();}video.addEventListener('seeked',done,{once:true});video.currentTime=target;});
  const current=CARDS.find(c=>t>=c.t0&&t<Math.min(CONFIG.end,c.t1));
  if(current){const enter=pr(t,current.t0-.12,current.t0+.45),out=1-pr(t,current.t1-.35,current.t1);card.style.opacity=String(clamp(enter*4)*out);card.style.transform=`translateY(${(1-EZ.expo(enter))*30}px) scale(${.96+.04*EZ.spring(enter)})`;document.getElementById('copy').textContent=current.text;document.getElementById('label').textContent=current.kind==='title'?'主题 · SHANIU':'要点 · '+String(CARDS.indexOf(current)).padStart(2,'0');}
  else card.style.opacity='0';
  if(window.OVERLAY)window.OVERLAY(t);
  document.getElementById('timeline').style.width=(100*t/CONFIG.end)+'%';
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const subtitle=document.querySelector('.native-subtitles');
  const rect=card.getBoundingClientRect();
  if(current&&(rect.right>CONFIG.width||rect.bottom>CONFIG.height*.7))throw Error('Title layout overflow');
  if(subtitle?.dataset.fit==='overflow')throw Error('Subtitle layout overflow');
  return true;
};
window.READY=Promise.all([ready,document.fonts.ready]);
