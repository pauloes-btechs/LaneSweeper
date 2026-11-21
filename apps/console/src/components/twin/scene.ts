// High-fidelity digital-twin scene: physically based materials, sky-lit IBL, soft shadows, ACES tone mapping,
// ambient occlusion and bloom. Purely visual: it animates replayed telemetry and never sends commands.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Sky } from 'three/addons/objects/Sky.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
// The original LK-01 parametric model from the LaneKeeper site: one source of truth for geometry and dimensions.
import { buildRobot, DIMS } from '../../../../site/src/robot/model.js';

export type Quality = 'hd' | 'ultra';
export interface TwinTextures { asphalt?: string; lane?: string; concrete?: string; facade?: string; sidewalk?: string }
export interface TwinFrame { progress: number; sweeping: boolean; state: string }

const LANE_W = 2.4, ROAD_LEN = 90, Z0 = -14, Z1 = 14;
const rand = (() => { let s = 7; return () => ((s = (s * 16807) % 2147483647) / 2147483647); })();

// ---------------------------------------------------------------- procedural fallbacks (until AI textures exist)
function canvasTex(size: number, paint: (g: CanvasRenderingContext2D, n: number) => void, repeat: [number, number]) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d')!;
  paint(g, size);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(...repeat);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}
function speckle(base: string, tones: string[], density: number, maxR: number) {
  return (g: CanvasRenderingContext2D, n: number) => {
    g.fillStyle = base; g.fillRect(0, 0, n, n);
    for (let i = 0; i < n * n * density; i++) {
      g.fillStyle = tones[(rand() * tones.length) | 0];
      g.globalAlpha = 0.25 + rand() * 0.6;
      g.beginPath(); g.arc(rand() * n, rand() * n, rand() * maxR, 0, Math.PI * 2); g.fill();
    }
    g.globalAlpha = 1;
  };
}
const fallback = {
  asphalt: () => canvasTex(1024, speckle('#3b3d40', ['#2a2b2d', '#55575a', '#6b6c6e', '#1d1e20'], 0.06, 2.2), [6, 30]),
  lane: () => canvasTex(1024, (g, n) => {
    speckle('#2f8a56', ['#2a7a4c', '#38975f', '#256b43'], 0.03, 2)(g, n);
    for (let i = 0; i < 260; i++) { g.fillStyle = '#3a3c3f'; g.globalAlpha = 0.7; g.fillRect(rand() * n, rand() * n, rand() * 7, rand() * 5); }
    g.globalAlpha = 1;
  }, [1, 12]),
  concrete: () => canvasTex(512, speckle('#b9b6ae', ['#a9a69e', '#c8c5bd', '#8f8c85'], 0.08, 1.4), [1, 1]),
  sidewalk: () => canvasTex(1024, (g, n) => {
    speckle('#a7a6a2', ['#9a9995', '#b5b4b0', '#8c8b87'], 0.05, 1.6)(g, n);
    g.strokeStyle = '#7c7b77'; g.lineWidth = 3;
    for (let k = 0; k <= 4; k++) { g.beginPath(); g.moveTo(0, (k * n) / 4); g.lineTo(n, (k * n) / 4); g.moveTo((k * n) / 4, 0); g.lineTo((k * n) / 4, n); g.stroke(); }
  }, [1, 24]),
  facade: () => canvasTex(1024, (g, n) => {
    g.fillStyle = '#a07c5c'; g.fillRect(0, 0, n, n);
    for (let y = 0; y < n; y += 8) for (let x = (y / 8) % 2 ? 0 : 12; x < n; x += 24) { g.fillStyle = rand() > 0.5 ? '#94715a' : '#ad8866'; g.fillRect(x, y, 22, 7); }
    for (let r = 0; r < 6; r++) for (let c = 0; c < 5; c++) {
      const x = 40 + c * 196, y = 40 + r * 165;
      g.fillStyle = '#e9e4da'; g.fillRect(x - 6, y - 6, 112, 132);
      g.fillStyle = rand() > 0.3 ? '#1d2630' : '#c9a76a'; g.fillRect(x, y, 100, 120);
    }
  }, [1, 1]),
};

function loadTex(url: string | undefined, make: () => THREE.Texture, repeat?: [number, number]): THREE.Texture {
  const t = make();
  if (url) {
    new THREE.TextureLoader().setCrossOrigin('anonymous').load(url, (img) => {
      img.wrapS = img.wrapT = THREE.RepeatWrapping;
      img.repeat.copy(repeat ? new THREE.Vector2(...repeat) : t.repeat);
      img.colorSpace = THREE.SRGBColorSpace;
      img.anisotropy = 16;
      t.image = img.image; t.needsUpdate = true;
    });
  }
  return t;
}

// ---------------------------------------------------------------- scene pieces
function barrierGeometry() {
  // NJ/Jersey profile, 0.6 m wide at base, 0.81 m tall, extruded 3 m.
  const s = new THREE.Shape();
  s.moveTo(-0.3, 0); s.lineTo(-0.28, 0.08); s.lineTo(-0.17, 0.33); s.lineTo(-0.08, 0.81);
  s.lineTo(0.08, 0.81); s.lineTo(0.17, 0.33); s.lineTo(0.28, 0.08); s.lineTo(0.3, 0); s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth: 2.9, bevelEnabled: true, bevelSize: 0.015, bevelThickness: 0.015, bevelSegments: 2 });
  g.translate(0, 0, -1.45);
  return g;
}

function bicycle(mat: { frame: THREE.Material; tyre: THREE.Material; skin: THREE.Material; cloth: THREE.Material; helmet: THREE.Material }) {
  const root = new THREE.Group();
  const tyre = new THREE.TorusGeometry(0.34, 0.025, 12, 48);
  for (const z of [-0.52, 0.52]) {
    const w = new THREE.Mesh(tyre, mat.tyre); w.position.set(0, 0.36, z); w.rotation.y = Math.PI / 2; root.add(w);
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.1, 12), mat.frame); hub.rotation.z = Math.PI / 2; hub.position.set(0, 0.36, z); root.add(hub);
  }
  const tube = (a: THREE.Vector3, b: THREE.Vector3, r = 0.018) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, a.distanceTo(b), 10), mat.frame);
    m.position.copy(a).add(b).multiplyScalar(0.5); m.lookAt(b); m.rotateX(Math.PI / 2); root.add(m);
  };
  const V = (y: number, z: number) => new THREE.Vector3(0, y, z);
  const bb = V(0.33, -0.05), seat = V(0.82, -0.2), head = V(0.85, 0.38);
  tube(bb, seat); tube(seat, head); tube(bb, head, 0.022); tube(bb, V(0.36, -0.52)); tube(seat, V(0.36, -0.52)); tube(head, V(0.36, 0.52));
  // rider (capsules), helmet, high-vis
  const cap = (r: number, l: number, m: THREE.Material, p: THREE.Vector3, rx: number) => {
    const c = new THREE.Mesh(new THREE.CapsuleGeometry(r, l, 6, 14), m); c.position.copy(p); c.rotation.x = rx; c.castShadow = true; root.add(c);
  };
  cap(0.15, 0.42, mat.cloth, V(1.12, 0.02), -0.75);
  cap(0.065, 0.42, mat.cloth, V(0.62, -0.1), 0.35);
  cap(0.05, 0.42, mat.skin, V(1.05, 0.3), -1.2);
  const h = new THREE.Mesh(new THREE.SphereGeometry(0.11, 24, 16), mat.skin); h.position.set(0, 1.47, 0.2); root.add(h);
  const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.135, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), mat.helmet);
  helmet.position.set(0, 1.49, 0.19); helmet.rotation.x = -0.3; root.add(helmet);
  root.traverse((o) => { if ((o as THREE.Mesh).isMesh) o.castShadow = true; });
  return root;
}

// ---------------------------------------------------------------- the scene
export class TwinScene {
  private renderer: THREE.WebGLRenderer;
  private composer: EffectComposer;
  private scene = new THREE.Scene();
  private cam: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private robot: THREE.Object3D;
  private brooms: THREE.Object3D[] = [];
  private beacon: THREE.PointLight;
  private shards: THREE.InstancedMesh;
  private shardPos: THREE.Vector3[] = [];
  private shardMat = new THREE.Matrix4();
  private cyclist: THREE.Object3D;
  private bloom: UnrealBloomPass;
  private raf = 0;
  private t0 = performance.now();
  private follow = true;
  private frames = 0;
  frame: TwinFrame = { progress: 0, sweeping: false, state: 'queued' };

  constructor(private host: HTMLElement, tex: TwinTextures, quality: Quality) {
    const w = host.clientWidth || 800, h = host.clientHeight || 420;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, quality === 'ultra' ? 3 : 1.5));
    this.renderer.setSize(w, h);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.55;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(this.renderer.domElement);

    this.cam = new THREE.PerspectiveCamera(34, w / h, 0.05, 600);
    this.cam.position.set(3.6, 1.9, Z0 + 5);
    this.controls = new OrbitControls(this.cam, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 1.2; this.controls.maxDistance = 40;
    this.controls.addEventListener('start', () => { this.follow = false; });

    // Sky + image-based lighting from the same sky, so reflections match the light.
    const sky = new Sky();
    sky.scale.setScalar(4000);
    const u = sky.material.uniforms;
    u.turbidity.value = 6; u.rayleigh.value = 1.6; u.mieCoefficient.value = 0.004; u.mieDirectionalG.value = 0.82;
    const sunDir = new THREE.Vector3().setFromSphericalCoords(1, THREE.MathUtils.degToRad(80), THREE.MathUtils.degToRad(-35));
    u.sunPosition.value.copy(sunDir);
    this.scene.add(sky);
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const envScene = new THREE.Scene(); envScene.add(sky.clone());
    this.scene.environment = pmrem.fromScene(envScene, 0.02).texture;
    this.scene.environmentIntensity = 0.9;
    this.scene.fog = new THREE.FogExp2(0xc9b7a3, 0.012);

    const sun = new THREE.DirectionalLight(0xffd9b0, 3.2);
    sun.position.copy(sunDir).multiplyScalar(40);
    sun.castShadow = true;
    sun.shadow.mapSize.set(quality === 'ultra' ? 4096 : 2048, quality === 'ultra' ? 4096 : 2048);
    const sc = sun.shadow.camera; sc.left = -12; sc.right = 12; sc.top = 22; sc.bottom = -22; sc.near = 1; sc.far = 120;
    sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.02; sun.shadow.radius = 4;
    this.scene.add(sun, sun.target);

    this.buildStreet(tex);

    this.robot = buildRobot();
    this.robot.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; }
      if (/broom/i.test(o.name)) this.brooms.push(o);
    });
    this.robot.position.set(0, 0, Z0);
    this.scene.add(this.robot);
    this.beacon = new THREE.PointLight(0xffa13a, 0, 6, 2);
    this.beacon.position.set(0, (DIMS as { height: number }).height + 0.15, 0);
    this.robot.add(this.beacon);

    // Broken glass: transmissive shards in the robot's path; swept ones vanish as the brushes pass.
    const shardGeo = new THREE.TetrahedronGeometry(0.018, 0);
    shardGeo.scale(1.6, 0.25, 1);
    const glass = new THREE.MeshPhysicalMaterial({ color: 0xe6f4ec, roughness: 0.03, iridescence: 0.2, transmission: 1, thickness: 0.006, ior: 1.52,
      specularIntensity: 1, envMapIntensity: 2.5, transparent: true });
    const n = 900;
    this.shards = new THREE.InstancedMesh(shardGeo, glass, n);
    this.shards.castShadow = false;
    const q = new THREE.Quaternion(), e = new THREE.Euler(), s = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      const cluster = i % 3;
      const cz = [-4, 3.5, 9][cluster], r = Math.sqrt(rand()) * [0.9, 0.6, 1.1][cluster], a = rand() * Math.PI * 2;
      const p = new THREE.Vector3(Math.cos(a) * r * 0.8 - 0.1, 0.004, cz + Math.sin(a) * r * 1.6);
      this.shardPos.push(p);
      q.setFromEuler(e.set(rand() * 0.3, rand() * Math.PI * 2, rand() * 0.3));
      const k = 0.35 + rand() * 1.0;
      this.shards.setMatrixAt(i, this.shardMat.compose(p, q, s.set(k, k, k)));
      this.shards.setColorAt(i, new THREE.Color(rand() > 0.55 ? 0x3c8a4a : rand() > 0.5 ? 0xe8f2ec : 0x8a5a2a));
    }
    this.scene.add(this.shards);

    const mats = {
      frame: new THREE.MeshStandardMaterial({ color: 0x1f6fd1, metalness: 0.7, roughness: 0.3 }),
      tyre: new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.9 }),
      skin: new THREE.MeshStandardMaterial({ color: 0x9c6b4e, roughness: 0.6 }),
      cloth: new THREE.MeshStandardMaterial({ color: 0xd6f03a, roughness: 0.75, emissive: 0x1a2200 }),
      helmet: new THREE.MeshPhysicalMaterial({ color: 0xf2f2f2, roughness: 0.25, clearcoat: 1 }),
    };
    this.cyclist = bicycle(mats);
    this.cyclist.position.set(LANE_W / 2 + 1.4, 0, Z1 + 20);
    this.cyclist.rotation.y = Math.PI;
    this.scene.add(this.cyclist);

    // Post: MSAA HDR target → ground-truth AO → bloom (beacon, LEDs, lamps) → tone map + sRGB.
    const target = new THREE.WebGLRenderTarget(w, h, { type: THREE.HalfFloatType, samples: quality === 'ultra' ? 8 : 4 });
    this.composer = new EffectComposer(this.renderer, target);
    this.composer.addPass(new RenderPass(this.scene, this.cam));
    const ao = new GTAOPass(this.scene, this.cam, w, h);
    ao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.5, thickness: 1, scale: 1.1 });
    ao.blendIntensity = 0.85;
    this.composer.addPass(ao);
    this.bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.35, 0.5, 0.92);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
    this.composer.setPixelRatio(this.renderer.getPixelRatio());

    this.tick();
  }

  private buildStreet(tex: TwinTextures) {
    const add = (m: THREE.Mesh, shadow = true) => { m.receiveShadow = true; m.castShadow = shadow; this.scene.add(m); return m; };
    const asphalt = new THREE.MeshStandardMaterial({ map: loadTex(tex.asphalt, fallback.asphalt, [6, 30]), roughness: 0.88 });
    const road = add(new THREE.Mesh(new THREE.PlaneGeometry(16, ROAD_LEN), asphalt), false);
    road.rotation.x = -Math.PI / 2; road.position.x = -6.5;

    const paint = new THREE.MeshPhysicalMaterial({ map: loadTex(tex.lane, fallback.lane, [1, 12]), roughness: 0.55, clearcoat: 0.25, clearcoatRoughness: 0.6 });
    const lane = add(new THREE.Mesh(new THREE.PlaneGeometry(LANE_W, ROAD_LEN), paint), false);
    lane.rotation.x = -Math.PI / 2; lane.position.y = 0.002;

    const white = new THREE.MeshStandardMaterial({ color: 0xf0efe9, roughness: 0.5 });
    for (const x of [-LANE_W / 2 - 0.06, LANE_W / 2 + 0.06]) {
      const l = add(new THREE.Mesh(new THREE.PlaneGeometry(0.1, ROAD_LEN), white), false);
      l.rotation.x = -Math.PI / 2; l.position.set(x, 0.003, 0);
    }
    // Car lane dashes
    for (let z = -ROAD_LEN / 2; z < ROAD_LEN / 2; z += 6) {
      const d = add(new THREE.Mesh(new THREE.PlaneGeometry(0.12, 3), white), false);
      d.rotation.x = -Math.PI / 2; d.position.set(-6.2, 0.003, z);
    }
    // Bike symbols (simple painted chevrons)
    for (const z of [-10, 6]) {
      for (const k of [0, 0.35]) {
        const c = add(new THREE.Mesh(new THREE.RingGeometry(0.28, 0.36, 3, 1, Math.PI / 2 - 0.9, 1.8), white), false);
        c.rotation.x = -Math.PI / 2; c.position.set(0, 0.004, z + k);
      }
    }
    // Jersey barriers with gaps, between lane and traffic
    const concrete = new THREE.MeshStandardMaterial({ map: loadTex(tex.concrete, fallback.concrete, [0.6, 0.6]), roughness: 0.82 });
    const bg = barrierGeometry();
    const nb = Math.floor(ROAD_LEN / 3.6);
    const barriers = new THREE.InstancedMesh(bg, concrete, nb);
    barriers.castShadow = barriers.receiveShadow = true;
    const m = new THREE.Matrix4();
    for (let i = 0; i < nb; i++) barriers.setMatrixAt(i, m.makeTranslation(-LANE_W / 2 - 0.55, 0, -ROAD_LEN / 2 + i * 3.6 + 1.8));
    this.scene.add(barriers);
    // Curb + sidewalk on the right
    const curb = add(new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.15, ROAD_LEN), concrete));
    curb.position.set(LANE_W / 2 + 0.3, 0.075, 0);
    const walk = new THREE.MeshStandardMaterial({ map: loadTex(tex.sidewalk, fallback.sidewalk, [1, 24]), roughness: 0.85 });
    const sw = add(new THREE.Mesh(new THREE.BoxGeometry(4.2, 0.15, ROAD_LEN), walk), false);
    sw.position.set(LANE_W / 2 + 2.5, 0.075, 0);
    // Buildings both sides
    const facadeTex = loadTex(tex.facade, fallback.facade, [1, 1]);
    for (const side of [-1, 1]) {
      let z = -ROAD_LEN / 2;
      while (z < ROAD_LEN / 2) {
        const len = 10 + rand() * 12, hgt = 14 + rand() * 30;
        const t = facadeTex.clone(); t.needsUpdate = true; t.repeat.set(len / 9, hgt / 9);
        const mat = new THREE.MeshStandardMaterial({ map: t, roughness: 0.8, color: new THREE.Color().setHSL(0.07, 0.15 + rand() * 0.2, 0.55 + rand() * 0.25) });
        const b = add(new THREE.Mesh(new THREE.BoxGeometry(8, hgt, len), mat));
        b.position.set(side < 0 ? -18.5 : LANE_W / 2 + 8.6, hgt / 2, z + len / 2);
        z += len + 0.3;
      }
    }
    // Street lights
    const pole = new THREE.MeshStandardMaterial({ color: 0x2b2f33, metalness: 0.6, roughness: 0.45 });
    const lampMat = new THREE.MeshStandardMaterial({ color: 0xfff2d8, emissive: 0xffd9a0, emissiveIntensity: 4 });
    for (let z = -ROAD_LEN / 2 + 6; z < ROAD_LEN / 2; z += 18) {
      const p = add(new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.1, 7.5, 16), pole)); p.position.set(LANE_W / 2 + 0.9, 3.75, z);
      const arm = add(new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.6, 12), pole)); arm.rotation.z = Math.PI / 2; arm.position.set(LANE_W / 2 - 0.35, 7.4, z);
      const head = add(new THREE.Mesh(new RoundedBoxGeometry(0.7, 0.12, 0.32, 3, 0.04), lampMat), false); head.position.set(LANE_W / 2 - 1.55, 7.3, z);
    }
  }

  setQuality(q: Quality) { this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, q === 'ultra' ? 3 : 1.5)); this.resize(); }

  resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.cam.aspect = w / h; this.cam.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(w, h);
  }

  recenter() { this.follow = true; }

  /** Stop the render loop (tests and screenshots). */
  stop() { cancelAnimationFrame(this.raf); this.raf = -1; }

  private tick = () => {
    const t = (performance.now() - this.t0) / 1000;
    const { progress, sweeping, state } = this.frame;
    const z = Z0 + (Z1 - Z0) * Math.max(0, Math.min(1, progress));
    const first = this.frames++ === 0 || Math.abs(z - this.robot.position.z) > 3;   // load or seek: snap, don't glide
    this.robot.position.z += (z - this.robot.position.z) * (first ? 1 : 0.08);
    for (const b of this.brooms) b.rotation.y += sweeping ? 0.32 : 0;
    const paused = state === 'paused_safety';
    this.beacon.intensity = paused ? (Math.sin(t * 9) > 0 ? 18 : 0) : sweeping ? 2.2 : 0.6;
    this.bloom.strength = paused ? 0.6 : 0.35;
    // Remove shards the brushes have passed over (sweep path ≈ 0.9 m wide).
    if (sweeping) {
      const rz = this.robot.position.z + 0.45;
      let dirty = false;
      this.shardPos.forEach((p, i) => {
        if (p.y > -1 && p.z < rz && p.z > rz - 1.2 && Math.abs(p.x - this.robot.position.x) < 0.48) {
          p.y = -5; this.shards.setMatrixAt(i, this.shardMat.makeScale(0, 0, 0)); dirty = true;
        }
      });
      if (dirty) this.shards.instanceMatrix.needsUpdate = true;
    }
    // A cyclist rides by in the traffic-side lane every ~20 s.
    this.cyclist.position.z = Z1 + 25 - ((t * 5.5) % 70);
    if (this.follow) {
      const want = new THREE.Vector3(this.robot.position.x - 0.85, 1.25, this.robot.position.z + 3.1);
      const look = new THREE.Vector3(this.robot.position.x + 0.15, 0.35, this.robot.position.z + 0.2);
      this.cam.position.lerp(want, first ? 1 : 0.05);
      this.controls.target.lerp(look, first ? 1 : 0.1);
    }
    this.controls.update();
    this.composer.render();
    if (this.raf !== -1) this.raf = requestAnimationFrame(this.tick);
  };

  dispose() {
    cancelAnimationFrame(this.raf);
    this.controls.dispose();
    this.composer.dispose();
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose?.();
      const mat = m.material as THREE.Material | THREE.Material[] | undefined;
      (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
    });
    this.renderer.dispose();
    this.host.innerHTML = '';
  }
}
