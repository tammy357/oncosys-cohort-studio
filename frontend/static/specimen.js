// The background "specimen field": one dot per sample in the release, grouped by PAM50 subtype.
// The layout is illustrative (seeded clusters, not an embedding). When a cohort is built, its samples stay
// lit and the rest drift back and fade, so the background always shows who is in.
import * as THREE from 'three';

// Order matches the server's GROUPS: LumA, LumB, Basal, Her2, Normal-like, no call, normal tissue, metastatic.
export const PALETTES = {
  light: ['#3d348b', '#7b70d0', '#cf3f68', '#c98a1c', '#23806f', '#b3aa9b', '#2f6db3', '#18161d'],
  dark: ['#a49bff', '#cbc3ff', '#ff7ea3', '#f0b45e', '#4fc8ad', '#8a8494', '#7fb2f0', '#f0ebe2'],
};
const CENTERS = [[-1.45, 0.5, 0.25], [-0.25, 1.4, -0.35], [1.7, -0.5, 0.35], [1.2, 1.15, -0.55], [0.55, -1.5, 0.65],
  [-1.25, -1.15, -0.45], [0.2, 0.05, -1.35], [2.35, 1.55, 0.95]];

function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const VERT = /* glsl */`
  attribute vec3 aColor; attribute float aFrom; attribute float aTarget; attribute float aSeed;
  uniform float uTime; uniform float uMix; uniform float uSize; uniform float uPixelRatio;
  varying vec3 vColor; varying float vAlpha; varying float vOn;
  void main() {
    float on = mix(aFrom, aTarget, smoothstep(0.0, 1.0, uMix));
    vec3 p = position;
    float s = aSeed * 6.2831;
    p += 0.055 * vec3(sin(uTime * 0.61 + s), cos(uTime * 0.47 + s * 1.7), sin(uTime * 0.53 + s * 2.3));
    p *= 1.0 + (1.0 - on) * 0.32;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = uSize * uPixelRatio * (0.5 + 0.5 * on) * (10.0 / -mv.z);
    vColor = aColor; vAlpha = (0.08 + 0.92 * on) * 0.82; vOn = on;
  }`;
const FRAG = /* glsl */`
  varying vec3 vColor; varying float vAlpha; varying float vOn;
  void main() {
    vec2 c = gl_PointCoord - 0.5; float d = length(c);
    if (d > 0.5) discard;
    float edge = smoothstep(0.5, 0.42, d);
    float rim = smoothstep(0.22, 0.48, d);
    vec3 col = mix(vColor * 1.12, vColor * 0.78, rim * 0.6);
    gl_FragColor = vec4(col, vAlpha * edge * (0.78 + 0.22 * vOn));
  }`;

export function createSpecimen(canvas, specimen, { reduceMotion = false, theme = 'light', gsap = null } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(dpr);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  camera.position.set(0, 0, 10.5);

  const samples = specimen.samples;
  const n = samples.length;
  const counts = new Array(CENTERS.length).fill(0);
  samples.forEach(([, g]) => { counts[g] += 1; });
  const rand = mulberry32(20261114);
  const gauss = () => { const u = 1 - rand(); const v = rand(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const from = new Float32Array(n).fill(1);
  const target = new Float32Array(n).fill(1);
  const seed = new Float32Array(n);
  const groupOf = new Uint8Array(n);
  const index = new Map();
  samples.forEach(([id, g], i) => {
    const c = CENTERS[g];
    const sigma = 0.22 + 0.42 * Math.sqrt(counts[g] / 421);
    pos[i * 3] = c[0] + gauss() * sigma;
    pos[i * 3 + 1] = c[1] + gauss() * sigma * 0.9;
    pos[i * 3 + 2] = c[2] + gauss() * sigma * 0.75;
    seed[i] = rand();
    groupOf[i] = g;
    index.set(id, i);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const aColor = new THREE.BufferAttribute(col, 3);
  const aFrom = new THREE.BufferAttribute(from, 1);
  const aTarget = new THREE.BufferAttribute(target, 1);
  geometry.setAttribute('aColor', aColor);
  geometry.setAttribute('aFrom', aFrom);
  geometry.setAttribute('aTarget', aTarget);
  geometry.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
  const uniforms = { uTime: { value: 0 }, uMix: { value: 1 }, uSize: { value: 5.4 }, uPixelRatio: { value: dpr } };
  const material = new THREE.ShaderMaterial({ uniforms, vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false });
  const points = new THREE.Points(geometry, material);

  // A faint field-of-view reticle around the field, like a microscope eyepiece.
  const ringGeo = new THREE.BufferGeometry().setFromPoints(
    Array.from({ length: 181 }, (_, k) => new THREE.Vector3(Math.cos((k / 180) * Math.PI * 2) * 3.55, Math.sin((k / 180) * Math.PI * 2) * 3.55, 0)));
  const ringMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.16 });
  const ring = new THREE.Line(ringGeo, ringMat);
  const ticks = [];
  for (let k = 0; k < 48; k++) {
    const a = (k / 48) * Math.PI * 2;
    const r0 = k % 4 === 0 ? 3.42 : 3.49;
    ticks.push(new THREE.Vector3(Math.cos(a) * r0, Math.sin(a) * r0, 0), new THREE.Vector3(Math.cos(a) * 3.55, Math.sin(a) * 3.55, 0));
  }
  const tickLines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ticks), ringMat);

  const cloud = new THREE.Group();
  cloud.add(points);
  const field = new THREE.Group();
  field.add(cloud, ring, tickLines);
  scene.add(field);

  function paint(themeName) {
    const pal = (PALETTES[themeName] || PALETTES.light).map(hex => new THREE.Color(hex));
    for (let i = 0; i < n; i++) {
      const c = pal[groupOf[i]];
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    }
    aColor.needsUpdate = true;
    ringMat.color = new THREE.Color(themeName === 'dark' ? '#f0ebe2' : '#18161d');
  }
  paint(theme);

  let wide = true;
  function layout() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    wide = w > 1080;
    // Keep the field clear of the text column: place it by the viewport's real width in world units.
    const halfW = Math.tan((camera.fov * Math.PI) / 360) * camera.position.z * camera.aspect;
    const scale = wide ? Math.min(0.82, halfW / 6.6) : 0.62;
    field.position.set(wide ? halfW - 2.95 * scale - 0.15 : 0, wide ? 0.15 : 1.1, 0);
    field.scale.setScalar(scale);
    render();
  }

  let speed = 1;
  let time = 0;
  let last = performance.now();
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  window.addEventListener('pointermove', e => {
    pointer.tx = (e.clientX / window.innerWidth - 0.5) * 0.35;
    pointer.ty = (e.clientY / window.innerHeight - 0.5) * 0.2;
  }, { passive: true });

  function render() { renderer.render(scene, camera); }
  let raf = 0;
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    time += dt * speed;
    uniforms.uTime.value = time;
    pointer.x += (pointer.tx - pointer.x) * 0.04;
    pointer.y += (pointer.ty - pointer.y) * 0.04;
    cloud.rotation.y = time * 0.07 + pointer.x;
    cloud.rotation.x = 0.16 + pointer.y;
    ring.rotation.z = tickLines.rotation.z = time * 0.012;
    render();
    raf = requestAnimationFrame(frame);
  }
  if (!reduceMotion) raf = requestAnimationFrame(frame);
  document.addEventListener('visibilitychange', () => {
    if (reduceMotion) return;
    cancelAnimationFrame(raf);
    if (!document.hidden) { last = performance.now(); raf = requestAnimationFrame(frame); }
  });
  window.addEventListener('resize', layout);
  layout();

  // ---- reacting to the app ----
  let current = null;                 // Float32Array of the cohort's on/off state, or null = everything on
  function transition(next) {
    const m = uniforms.uMix.value;
    for (let i = 0; i < n; i++) from[i] = from[i] + (target[i] - from[i]) * Math.min(1, m);
    target.set(next);
    aFrom.needsUpdate = true;
    aTarget.needsUpdate = true;
    if (reduceMotion || !gsap) { uniforms.uMix.value = 1; render(); return; }
    uniforms.uMix.value = 0;
    gsap.to(uniforms.uMix, { value: 1, duration: 1.6, ease: 'power2.inOut', overwrite: true, onUpdate: () => { if (!raf) render(); } });
  }
  return {
    highlight(ids) {
      const next = new Float32Array(n);
      (ids || []).forEach(id => { const i = index.get(id); if (i !== undefined) next[i] = 1; });
      current = next;
      transition(next);
    },
    clear() { current = null; transition(new Float32Array(n).fill(1)); },
    focusGroup(g) {
      if (g === null || g === undefined) { transition(current || new Float32Array(n).fill(1)); return; }
      const next = new Float32Array(n);
      for (let i = 0; i < n; i++) next[i] = groupOf[i] === g ? 1 : 0.06;
      transition(next);
    },
    busy(on) {
      if (gsap && !reduceMotion) gsap.to({ v: speed }, { v: on ? 3.2 : 1, duration: 0.8, ease: 'power2.out', onUpdate() { speed = this.targets()[0].v; } });
      else speed = on ? 3.2 : 1;
    },
    setTheme(name) { paint(name); render(); },
  };
}
