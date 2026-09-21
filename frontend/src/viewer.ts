// Minimal three.js preview for the generated GLB.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export interface Viewer {
  load(url: string): Promise<void>;
  clear(): void;
}

export function createViewer(container: HTMLElement): Viewer {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xe9e9e6);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
  camera.position.set(120, 100, 120);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  container.append(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x888877, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(80, 150, 60);
  scene.add(sun);
  scene.add(new THREE.GridHelper(300, 30, 0xbbbbbb, 0xdddddd));

  const resize = () => {
    const { clientWidth: w, clientHeight: h } = container;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
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

  const clear = () => {
    if (model) scene.remove(model);
    model = null;
  };

  return {
    clear,
    async load(url) {
      clear();
      const gltf = await loader.loadAsync(url);
      model = gltf.scene;
      model.rotation.x = -Math.PI / 2; // model is z-up, three.js is y-up
      model.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85 });
        }
      });
      scene.add(model);
      const box = new THREE.Box3().setFromObject(model);
      const size = box.getSize(new THREE.Vector3()).length();
      const center = box.getCenter(new THREE.Vector3());
      controls.target.copy(center);
      camera.position.copy(center).add(new THREE.Vector3(size * 0.7, size * 0.6, size * 0.7));
      camera.near = size / 100;
      camera.far = size * 10;
      camera.updateProjectionMatrix();
    },
  };
}
