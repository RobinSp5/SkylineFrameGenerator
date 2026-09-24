// Minimal three.js preview for the generated GLB.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export interface Viewer {
  load(url: string): Promise<void>;
  clear(): void;
  /** Pixels covered by floating UI; the model is centred in the rest of the canvas. */
  setInsets(insets: { right?: number; bottom?: number }): void;
}

export function createViewer(container: HTMLElement): Viewer {
  // No scene background: the canvas is transparent and the container's themed CSS ground shows
  // through, so light and dark mode need no WebGL colour.
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
  camera.position.set(120, 100, 120);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  container.append(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x888877, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(80, 150, 60);
  scene.add(sun);
  const grid = new THREE.GridHelper(300, 30, 0x8a8a90, 0x8a8a90);
  // A mid grey at low opacity reads as a hairline on both the light and the dark ground.
  for (const material of [grid.material].flat()) {
    material.transparent = true;
    material.opacity = 0.25;
  }
  scene.add(grid);

  let insets = { right: 0, bottom: 0 };
  const resize = () => {
    const { clientWidth: w, clientHeight: h } = container;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Shifting the view by half the covered strip puts the orbit target in the middle of what
    // stays visible beside (or above) the panel.
    camera.setViewOffset(w, h, insets.right / 2, insets.bottom / 2, w, h);
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  let model: THREE.Object3D | null = null;
  const loader = new GLTFLoader();

  const materialsOf = (mesh: THREE.Mesh): THREE.Material[] =>
    Array.isArray(mesh.material) ? mesh.material : [mesh.material];

  // three.js does not free GPU buffers when an object leaves the scene graph, so every generation
  // would leak a model's worth of geometries and materials.
  const clear = () => {
    if (model) {
      scene.remove(model);
      model.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          for (const material of materialsOf(obj)) material.dispose();
        }
      });
    }
    model = null;
  };

  return {
    clear,
    setInsets(next) {
      insets = { right: next.right ?? 0, bottom: next.bottom ?? 0 };
      resize();
    },
    async load(url) {
      clear();
      const gltf = await loader.loadAsync(url);
      model = gltf.scene;
      model.rotation.x = -Math.PI / 2; // model is z-up, three.js is y-up
      model.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          // The GLTF's own material is replaced and never used again; drop it before it is orphaned.
          for (const material of materialsOf(obj)) material.dispose();
          obj.material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85 });
        }
      });
      scene.add(model);
      const box = new THREE.Box3().setFromObject(model);
      const size = box.getSize(new THREE.Vector3()).length();
      const center = box.getCenter(new THREE.Vector3());
      controls.target.copy(center);
      // Back off in proportion to how much of the canvas the panel hides, so the model fits the rest.
      // The pane may still be hidden (zero size) while loading; it fills the window once shown.
      const w = container.clientWidth || window.innerWidth;
      const h = container.clientHeight || window.innerHeight;
      const hidden = Math.max(w / Math.max(1, w - insets.right), h / Math.max(1, h - insets.bottom));
      const distance = size * Math.min(3, hidden);
      camera.position.copy(center).add(new THREE.Vector3(distance * 0.7, distance * 0.6, distance * 0.7));
      camera.near = size / 100;
      camera.far = size * 10;
      camera.updateProjectionMatrix();
    },
  };
}
