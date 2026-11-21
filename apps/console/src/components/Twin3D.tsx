import { useEffect, useRef } from 'react';
import * as THREE from 'three';
// The original LK-01 parametric model from the LaneKeeper site: one source of truth for geometry and dimensions.
// @ts-expect-error - plain JS module without type declarations
import { buildRobot, DIMS } from '../../../site/src/robot/model.js';

interface Props { state: string; progress: number; sweeping: boolean; label?: string }

/** Small three.js twin: the LK-01 model on a lane strip, moving with replay progress. Purely visual. */
export function Twin3D({ state, progress, sweeping, label = 'LK-01 digital twin (simulated)' }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const robot = useRef<THREE.Object3D | null>(null);
  const brooms = useRef<THREE.Object3D[]>([]);
  const beacon = useRef<THREE.PointLight | null>(null);
  const live = useRef({ progress, sweeping, state });
  live.current = { progress, sweeping, state };

  useEffect(() => {
    const host = el.current;
    if (!host) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); } catch { host.textContent = '3D view needs WebGL.'; return; }
    const w = host.clientWidth || 480, h = 260;
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x101820);
    const cam = new THREE.PerspectiveCamera(38, w / h, 0.1, 100);
    cam.position.set(3.2, 2.2, 3.6);
    scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x20242a, 1.4));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(4, 6, 3);
    scene.add(sun);
    const lane = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 30), new THREE.MeshStandardMaterial({ color: 0x2f8f5b, roughness: 0.9 }));
    lane.rotation.x = -Math.PI / 2;
    scene.add(lane);
    for (const x of [-0.85, 0.85]) {
      const s = new THREE.Mesh(new THREE.PlaneGeometry(0.12, 30), new THREE.MeshStandardMaterial({ color: 0xe9e9e9 }));
      s.rotation.x = -Math.PI / 2; s.position.set(x, 0.001, 0); scene.add(s);
    }
    const r: THREE.Object3D = buildRobot();
    scene.add(r);
    robot.current = r;
    r.traverse((o) => { if (/broom/i.test(o.name)) brooms.current.push(o); });
    const bl = new THREE.PointLight(0xffa13a, 0, 3);
    bl.position.set(0, (DIMS as { height: number }).height + 0.15, 0);
    r.add(bl);
    beacon.current = bl;
    let raf = 0;
    const t0 = performance.now();
    const tick = () => {
      const t = (performance.now() - t0) / 1000;
      const { progress: p, sweeping: sw, state: st } = live.current;
      r.position.z = -12 + 24 * Math.max(0, Math.min(1, p));
      cam.position.z = r.position.z + 3.6;
      cam.lookAt(r.position.x, 0.3, r.position.z);
      for (const b of brooms.current) b.rotation.y += sw ? 0.25 : 0;
      if (beacon.current) beacon.current.intensity = st === 'paused_safety' ? 6 * (Math.sin(t * 8) > 0 ? 1 : 0) : sw ? 2 : 0.3;
      renderer.render(scene, cam);
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => { cancelAnimationFrame(raf); renderer.dispose(); host.innerHTML = ''; brooms.current = []; };
  }, []);

  return <div ref={el} role="img" aria-label={`${label}: ${state}`} style={{ width: '100%', height: 260, borderRadius: 8, overflow: 'hidden' }} />;
}
