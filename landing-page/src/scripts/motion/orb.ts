/**
 * The orb: one fragment shader on a full-screen quad (raw WebGL, no 3D library). A dotted wireframe
 * sphere with orbital rings, over a slow diagonal aurora, sits on a fixed canvas behind dark-band
 * content. Each band names a state in `data-orb`; entering it tweens position, size, ring wobble,
 * aurora strength and opacity.
 * Paused while the tab is hidden or the orb is invisible. No WebGL → nothing happens and the
 * bands' CSS glow carries the look.
 */
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

type State = { x: number; y: number; scale: number; distort: number; alpha: number; aurora: number };

const STATES: Record<string, State> = {
  hero: { x: 0.5, y: 0.02, scale: 0.36, distort: 0.5, alpha: 1, aurora: 1 },
  leak: { x: 0.72, y: -0.4, scale: 0.28, distort: 0.8, alpha: 0.8, aurora: 0.45 },
  demo: { x: -0.74, y: 0.46, scale: 0.24, distort: 1, alpha: 0.7, aurora: 0.25 },
  guard: { x: 0.68, y: -0.06, scale: 0.34, distort: 0.35, alpha: 0.9, aurora: 0.5 },
  light: { x: 0.5, y: -0.8, scale: 0.2, distort: 0.3, alpha: 0, aurora: 0 },
  cta: { x: 0, y: 0.04, scale: 0.4, distort: 0.6, alpha: 0.85, aurora: 0.8 },
};

const VERT = `attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}`;

const FRAG = `
precision highp float;
uniform vec2 uRes;uniform float uTime;uniform vec2 uPos;uniform float uScale;uniform float uDistort;uniform float uAlpha;uniform float uAurora;
const float PI=3.14159265;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
mat3 rotY(float a){float c=cos(a),s=sin(a);return mat3(c,0.,-s,0.,1.,0.,s,0.,c);}
mat3 rotX(float a){float c=cos(a),s=sin(a);return mat3(1.,0.,0.,0.,c,s,0.,-s,c);}
// Dots on a lat/long lattice, thinned at random, round at every latitude.
float dots(vec3 p,float t){
  float th=atan(p.z,p.x);float ph=acos(clamp(p.y,-1.,1.));
  vec2 g=vec2((th/(2.*PI)+.5)*72.,ph/PI*36.);
  vec2 id=floor(g);vec2 f=fract(g)-.5;
  float h=hash(id);
  float keep=step(.62,h)*step(.12,sin(ph));
  vec2 ff=vec2(f.x*sin(ph),f.y);
  float tw=.55+.45*sin(t*1.7+h*40.);
  return keep*tw*smoothstep(.2,.06,length(ff));
}
// Thin ellipse: a tilted orbit.
float ring(vec2 q,float ang,float a,float b,float px){
  float c=cos(ang),s=sin(ang);vec2 r=vec2(c*q.x+s*q.y,-s*q.x+c*q.y);
  float f=length(vec2(r.x/a,r.y/b));
  float g=length(vec2(r.x/(a*a),r.y/(b*b)))/max(f,1e-4);
  return smoothstep(px*1.6,0.,abs(f-1.)/g);
}
void main(){
  vec2 uv=(gl_FragCoord.xy-.5*uRes)/uRes.y;
  float t=uTime;
  vec3 deep=vec3(.031,.447,.349);vec3 mint=vec3(.204,.91,.651);vec3 light=vec3(.369,1.,.776);

  // Aurora: a slow diagonal sweep of mint light, lower left to upper right.
  float band=uv.y-(.42*uv.x+.08)-.05*sin(uv.x*2.6+t*.22);
  float core=exp(-band*band/.018);
  float haze=exp(-band*band/.14);
  float side=.35+.65*smoothstep(-.9,.9,uv.x);
  float aur=(core*.42+haze*.22)*side*uAurora;
  vec3 ac=mix(deep,mint,.35+.4*core)*aur;

  // Orb
  vec2 q=(uv-uPos*vec2(uRes.x/uRes.y*.5,.5))/uScale;
  float px=1./(uScale*uRes.y);
  float r=length(q);
  float inside=smoothstep(1.,1.-px*2.,r);
  float z=sqrt(max(0.,1.-r*r));
  mat3 R=rotY(t*.12)*rotX(.42);
  float front=dots(R*vec3(q,z),t)*inside;
  float back=dots(R*vec3(q,-z),t)*inside*.35;
  float fres=pow(1.-z,3.)*inside;
  float wob=uDistort*.08;
  float rings=ring(q,1.45+.05*sin(t*.3),.26+.12*sin(t*.17),1.02,px)*.7
             +ring(q,-.38+wob*sin(t*.4),1.28,.62+.05*sin(t*.21),px)*.55
             +ring(q,.62,1.04,.98,px)*.35;
  float halo=exp(-max(r-1.,0.)*6.)*(1.-inside)*.12+inside*(.05+fres*.22);
  float oa=clamp(front+back+rings*.8+halo,0.,1.);
  vec3 oc=light*front+mint*back*.8+mint*rings*.8+mix(deep,mint,.6)*halo;

  vec3 c=ac*(1.-oa)+oc;
  float a=aur*(1.-oa)+oa;
  gl_FragColor=vec4(c,a)*uAlpha;
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const s = gl.createShader(type);
  if (!s) return null;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
}

export function initOrb() {
  const canvas = document.querySelector<HTMLCanvasElement>('canvas.orb');
  if (!canvas) return;
  const gl = canvas.getContext('webgl', { premultipliedAlpha: true, antialias: false, alpha: true, powerPreference: 'low-power' });
  if (!gl) return;
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;
  const prog = gl.createProgram();
  if (!prog) return;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const u = (n: string) => gl.getUniformLocation(prog, n);
  const uRes = u('uRes');
  const uTime = u('uTime');
  const uPos = u('uPos');
  const uScale = u('uScale');
  const uDistort = u('uDistort');
  const uAlpha = u('uAlpha');
  const uAurora = u('uAurora');
  gl.clearColor(0, 0, 0, 0);

  const narrow = () => window.innerWidth < 1025;
  const s: State = { ...STATES.hero, alpha: 0 };

  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(uRes, canvas.width, canvas.height);
  };
  resize();
  window.addEventListener('resize', resize, { passive: true });

  const footerEl = document.querySelector<HTMLElement>('.site-footer');
  let footerH = footerEl?.offsetHeight ?? 0;
  if (footerEl) new ResizeObserver(() => (footerH = footerEl.offsetHeight)).observe(footerEl);

  let raf = 0;
  const t0 = performance.now();
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    // Fade out as the page scrolls off the footer.
    const remain = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
    const fade = footerH ? Math.min(1, Math.max(0, remain / footerH)) : 1;
    if (s.alpha * fade < 0.005) {
      gl.clear(gl.COLOR_BUFFER_BIT);
      return;
    }
    const n = narrow();
    gl.uniform1f(uTime, (now - t0) / 1000);
    gl.uniform2f(uPos, n ? s.x * 0.35 : s.x, n ? s.y * 0.6 + 0.12 : s.y);
    gl.uniform1f(uScale, n ? s.scale * 0.85 : s.scale);
    gl.uniform1f(uDistort, s.distort);
    gl.uniform1f(uAurora, n ? s.aurora * 0.7 : s.aurora);
    gl.uniform1f(uAlpha, (n ? s.alpha * 0.42 : s.alpha) * fade);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const start = () => {
    if (!raf) raf = requestAnimationFrame(frame);
  };
  const stop = () => {
    cancelAnimationFrame(raf);
    raf = 0;
  };
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  start();

  const goTo = (name: string) => {
    const target = STATES[name] ?? STATES.light;
    gsap.to(s, { ...target, duration: 1.6, ease: 'expo.out', overwrite: true });
  };
  document.querySelectorAll<HTMLElement>('[data-orb]').forEach((band) => {
    ScrollTrigger.create({
      trigger: band,
      start: 'top 55%',
      end: 'bottom 55%',
      onToggle: (self) => self.isActive && goTo(band.dataset.orb ?? 'light'),
    });
  });

  document.documentElement.classList.add('orb-on');
  goTo('hero');
}
