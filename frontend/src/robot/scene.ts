import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { COLORS } from '../lib/format';

export type RobotGeometry = {
  linearUnit: 'meter'; angularUnit: 'radian'; sourceUpAxis: 'Z';
  joints: { name: string; channelId: string; originXYZ: [number, number, number]; originRPY: [number, number, number]; axis: [number, number, number]; limitsRadians: [number, number] }[];
  illustrativePoseRadians: number[];
};

export function validateGeometry(value: unknown): asserts value is RobotGeometry {
  const v = value as RobotGeometry;
  const vector = (a: unknown) => Array.isArray(a) && a.length === 3 && a.every(n => typeof n === 'number' && Number.isFinite(n));
  if (!v || v.linearUnit !== 'meter' || v.angularUnit !== 'radian' || v.sourceUpAxis !== 'Z' ||
      !Array.isArray(v.joints) || v.joints.length !== 7 || !Array.isArray(v.illustrativePoseRadians) ||
      v.illustrativePoseRadians.length !== 7 || v.illustrativePoseRadians.some(n => !Number.isFinite(n)) ||
      v.joints.some((j, i) => !j || typeof j.name !== 'string' || j.channelId !== `joint_${i + 1}` || !vector(j.originXYZ) || !vector(j.originRPY) || !vector(j.axis) || Math.abs(Math.hypot(...j.axis) - 1) > 1e-6 ||
        !Array.isArray(j.limitsRadians) || j.limitsRadians.length !== 2 || j.limitsRadians.some(limit => !Number.isFinite(limit)) || j.limitsRadians[0] >= j.limitsRadians[1])) {
    throw new Error('The robot reference geometry is invalid.');
  }
}

export function validPose(description: RobotGeometry, radians: number[]) {
  return Array.isArray(radians) && radians.length === description.joints.length && radians.every((angle, i) =>
    Number.isFinite(angle) && angle >= description.joints[i].limitsRadians[0] - 1e-5 && angle <= description.joints[i].limitsRadians[1] + 1e-5);
}

/** Original schematic geometry, using documented joint transforms, not CAD meshes. */
export function createRobotScene(host: HTMLDivElement, description: RobotGeometry, onPick: (id: string) => void, onLost: () => void, initialPose = description.illustrativePoseRadians) {
  validateGeometry(description);
  if (!validPose(description, initialPose)) throw new Error('Robot pose exceeds the validated joint mapping.');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x161d21, 1);
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, .01, 30);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;
  controls.enablePan = false;
  controls.enableZoom = false;
  controls.minDistance = .7;
  controls.maxDistance = 5;
  controls.maxPolarAngle = Math.PI * .49;
  scene.add(new THREE.HemisphereLight(0xe5f1fa, 0x52636b, 2.8));
  const key = new THREE.DirectionalLight(0xffeee0, 3.4);
  key.position.set(2, 4, 3); scene.add(key);
  const rim = new THREE.DirectionalLight(0xabcde6, 2);
  rim.position.set(-3, 2, -1); scene.add(rim);
  const grid = new THREE.GridHelper(2.2, 22, 0x465860, 0x29383f);
  grid.position.y = -.015; scene.add(grid);

  // URDF Z-up → Three.js Y-up, exactly once for the full mechanism.
  const robot = new THREE.Group();
  robot.rotation.x = -Math.PI / 2;
  scene.add(robot);
  const metal = new THREE.MeshStandardMaterial({ color: 0xb9c3c7, roughness: .4, metalness: .62 });
  const graphite = new THREE.MeshStandardMaterial({ color: 0x35434d, roughness: .46, metalness: .5 });
  const selectable: THREE.Mesh[] = [];
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  const geometry = new Set<THREE.BufferGeometry>();
  const allMaterials = new Set<THREE.Material>([metal, graphite]);
  const textures = new Set<THREE.Texture>();
  const frames: THREE.Group[] = [];
  const origins: THREE.Quaternion[] = [];
  const axes: THREE.Vector3[] = [];

  function mesh(shape: THREE.BufferGeometry, material: THREE.Material, parent: THREE.Object3D) {
    geometry.add(shape); const m = new THREE.Mesh(shape, material); parent.add(m); return m;
  }
  function cylinderBetween(parent: THREE.Object3D, end: THREE.Vector3, radius: number, material: THREE.Material) {
    const m = mesh(new THREE.CylinderGeometry(radius * .9, radius, end.length(), 24), material, parent);
    m.position.copy(end).multiplyScalar(.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), end.clone().normalize());
    return m;
  }
  const base = mesh(new THREE.CylinderGeometry(.105, .14, .055, 40), graphite, robot);
  base.rotation.x = Math.PI / 2; base.position.z = .015;
  let parent: THREE.Object3D = robot;
  description.joints.forEach((joint, i) => {
    const origin = new THREE.Vector3(...joint.originXYZ);
    cylinderBetween(parent, origin, i < 4 ? .045 : .034, i % 2 ? metal : graphite);
    const frame = new THREE.Group(); frame.position.copy(origin);
    const originRotation = new THREE.Quaternion().setFromEuler(new THREE.Euler(...joint.originRPY, 'ZYX'));
    const axis = new THREE.Vector3(...joint.axis).normalize();
    frame.quaternion.copy(originRotation).multiply(new THREE.Quaternion().setFromAxisAngle(axis, initialPose[i]));
    frames.push(frame); origins.push(originRotation); axes.push(axis);
    parent.add(frame);
    const material = new THREE.MeshStandardMaterial({ color: COLORS[i], roughness: .42, metalness: .35 });
    materials.set(joint.channelId, material); allMaterials.add(material);
    const pivot = mesh(new THREE.SphereGeometry(i < 4 ? .058 : .046, 24, 16), material, frame);
    pivot.userData.channelId = joint.channelId; selectable.push(pivot);
    const cap = mesh(new THREE.CylinderGeometry(.035, .035, .115, 24), graphite, frame);
    cap.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(...joint.axis));
    cap.userData.channelId = joint.channelId; selectable.push(cap);
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 96;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.fillStyle = '#172126'; ctx.beginPath(); ctx.arc(48, 48, 35, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = 3; ctx.strokeStyle = COLORS[i]; ctx.stroke();
      ctx.fillStyle = '#edf4f6'; ctx.font = '500 42px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(i + 1), 48, 50);
      const texture = new THREE.CanvasTexture(canvas); textures.add(texture);
      const labelMaterial = new THREE.SpriteMaterial({ map: texture, depthTest: false }); allMaterials.add(labelMaterial);
      const label = new THREE.Sprite(labelMaterial); label.position.set(.10, 0, .01); label.scale.set(.095, .095, 1); frame.add(label);
    }
    parent = frame;
  });
  cylinderBetween(parent, new THREE.Vector3(0, 0, .06), .035, metal);
  robot.updateMatrixWorld(true);
  const reach = description.joints.reduce((sum, joint) => sum + Math.hypot(...joint.originXYZ), .06);
  // Fit once around the initial measured pose, with room for articulation. Playback never moves the camera.
  const bounds = new THREE.Box3().setFromObject(robot).expandByScalar(reach * .04);
  const target = bounds.getCenter(new THREE.Vector3());
  const distance = bounds.getSize(new THREE.Vector3()).length() * 1.7;
  function reset() {
    controls.target.copy(target);
    camera.position.copy(target).add(new THREE.Vector3(1.1, .6, 1.7).normalize().multiplyScalar(distance));
    controls.update(); render();
  }
  let disposed = false;
  function render() { if (!disposed && !document.hidden) renderer.render(scene, camera); }
  function resize() {
    const { width, height } = host.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); render();
  }
  function zoom(factor: number) {
    const offset = camera.position.clone().sub(controls.target);
    const next = Math.max(.7, Math.min(5, offset.length() * factor));
    camera.position.copy(controls.target).add(offset.setLength(next)); controls.update(); render();
  }
  function rotate(direction: number) {
    const offset = camera.position.clone().sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), direction * Math.PI / 12);
    camera.position.copy(controls.target).add(offset); controls.update(); render();
  }
  let start = [0, 0];
  const down = (e: PointerEvent) => { start = [e.clientX, e.clientY]; };
  const up = (e: PointerEvent) => {
    if (Math.hypot(e.clientX - start[0], e.clientY - start[1]) > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    const point = new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
    const ray = new THREE.Raycaster(); ray.setFromCamera(point, camera);
    const hit = ray.intersectObjects(selectable)[0];
    if (hit) onPick(hit.object.userData.channelId);
  };
  function contextLost(e: Event) { e.preventDefault(); onLost(); }
  renderer.domElement.addEventListener('webglcontextlost', contextLost);
  renderer.domElement.addEventListener('pointerdown', down);
  renderer.domElement.addEventListener('pointerup', up);
  controls.addEventListener('change', render);
  document.addEventListener('visibilitychange', render);
  const observer = new ResizeObserver(resize); observer.observe(host);
  reset(); resize();
  return {
    reset, zoom, rotate,
    pose(radians: number[]) {
      if (!validPose(description, radians)) return false;
      frames.forEach((frame, i) => frame.quaternion.copy(origins[i]).multiply(new THREE.Quaternion().setFromAxisAngle(axes[i], radians[i])));
      robot.updateMatrixWorld(true);
      render();
      return true;
    },
    highlight(ids: string[]) {
      materials.forEach((material, id) => {
        material.emissive.set(ids.includes(id) ? material.color : 0x000000);
        material.emissiveIntensity = ids.includes(id) ? .3 : 0;
        material.opacity = ids.length && !ids.includes(id) ? .45 : 1;
        material.transparent = material.opacity < 1;
      }); render();
    },
    dispose() {
      disposed = true; observer.disconnect(); controls.dispose();
      document.removeEventListener('visibilitychange', render);
      renderer.domElement.removeEventListener('webglcontextlost', contextLost);
      renderer.domElement.removeEventListener('pointerdown', down);
      renderer.domElement.removeEventListener('pointerup', up);
      geometry.forEach(g => g.dispose()); allMaterials.forEach(m => m.dispose()); textures.forEach(t => t.dispose());
      grid.geometry.dispose(); (grid.material as THREE.Material).dispose();
      renderer.dispose(); renderer.domElement.remove();
    },
  };
}
