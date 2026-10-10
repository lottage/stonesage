// =====================================================================
// StoneSage 3D Studio & Digital Art Gallery
// Displays agent-generated 3D exhibits, Three.js inspection, and cluster modeling dispatch
// =====================================================================

let threeScene, threeCamera, threeRenderer, activeModelGroup;
let isWireframe = false;
let isAutoRotate = false;
let isThreeInitialized = false;
let orbitControls = null;

export function init3DStudio() {
    console.log("[3D Studio] Initializing Sovereign Agent 3D Art Gallery & Viewport...");
    setupTabListener();
    refresh3DGallery();
}

function setupTabListener() {
    document.querySelectorAll(".nav-tab-btn, .engine-subnav-btn, #ws-mode-btn-3d").forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.getAttribute("data-tab") || btn.getAttribute("data-layer") || (btn.id === "ws-mode-btn-3d" ? "gallery3d" : "");
            if (target === "gallery3d") {
                if (!isThreeInitialized) {
                    initThreeViewport();
                } else {
                    setTimeout(onViewportResize, 80);
                }
                refresh3DGallery();
            }
        });
    });
}

function initThreeViewport() {
    const container = document.getElementById("three-viewport-container");
    const canvas = document.getElementById("three-canvas");
    if (!container || !canvas) return;

    if (typeof THREE === "undefined") {
        const script = document.createElement("script");
        script.src = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
        script.onload = () => {
            const loaderScript = document.createElement("script");
            loaderScript.src = "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/GLTFLoader.js";
            loaderScript.onload = () => {
                const controlsScript = document.createElement("script");
                controlsScript.src = "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js";
                controlsScript.onload = () => {
                    setupThreeCore(container, canvas);
                };
                document.head.appendChild(controlsScript);
            };
            document.head.appendChild(loaderScript);
        };
        document.head.appendChild(script);
    } else {
        setupThreeCore(container, canvas);
    }
}

function setupThreeCore(container, canvas) {
    const width = container.clientWidth || 600;
    const height = container.clientHeight || 400;

    threeScene = new THREE.Scene();
    threeScene.background = new THREE.Color(0x111418);

    const grid = new THREE.GridHelper(10, 20, 0x0284c7, 0x334155);
    grid.position.y = -0.01;
    threeScene.add(grid);

    threeCamera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    threeCamera.position.set(3, 2.5, 3.5);

    threeRenderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    threeRenderer.setSize(width, height);
    threeRenderer.setPixelRatio(window.devicePixelRatio || 1);
    threeRenderer.shadowMap.enabled = true;

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    threeScene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(5, 10, 7);
    dirLight.castShadow = true;
    threeScene.add(dirLight);

    const rimLight = new THREE.DirectionalLight(0x38bdf8, 0.6);
    rimLight.position.set(-5, 4, -5);
    threeScene.add(rimLight);

    if (typeof THREE.OrbitControls !== "undefined") {
        orbitControls = new THREE.OrbitControls(threeCamera, canvas);
        orbitControls.enableDamping = true;
        orbitControls.dampingFactor = 0.05;
        orbitControls.target.set(0, 0.5, 0);
    }

    window.addEventListener("resize", onViewportResize);

    isThreeInitialized = true;
    animate();

    // (shelved: Citadel 3D) no default model; the Blender bridge address is config.json blender.url
}

function onViewportResize() {
    const container = document.getElementById("three-viewport-container");
    if (!container || !threeCamera || !threeRenderer) return;
    const width = container.clientWidth;
    const height = container.clientHeight;
    threeCamera.aspect = width / height;
    threeCamera.updateProjectionMatrix();
    threeRenderer.setSize(width, height);
}

function animate() {
    requestAnimationFrame(animate);
    if (isAutoRotate && activeModelGroup) {
        activeModelGroup.rotation.y += 0.01;
    }
    if (orbitControls) {
        orbitControls.update();
    }
    if (threeRenderer && threeScene && threeCamera) {
        threeRenderer.render(threeScene, threeCamera);
    }
}

export async function refresh3DGallery() {
    const listEl = document.getElementById("gallery-cards-list");
    const countBadge = document.getElementById("gallery-count-badge");
    if (!listEl) return;

    try {
        const resp = await fetch("/api/3d/gallery");
        const data = await resp.json();
        const gallery = data.gallery || [];

        if (countBadge) countBadge.textContent = `${gallery.length} Models`;

        if (gallery.length === 0) {
            listEl.innerHTML = `
                <div style="color:var(--term-text-muted); font-size:0.75rem; text-align:center; padding:2rem;">
                    No 3D exhibits found yet. Dispatch a modeling prompt below!
                </div>
            `;
            return;
        }

        listEl.innerHTML = gallery.map(item => `
            <div style="background:var(--term-surface); border:1px solid var(--term-border-dim); border-radius:3px; padding:8px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; gap:10px;">
                    <img src="${item.preview_url || '/assets/3d_placeholder.png'}" alt="${item.title}" style="width:84px; height:64px; object-fit:cover; border-radius:3px; border:1px solid #334155; background:#000;">
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:bold; font-size:0.82rem; color:var(--term-text-bright); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">
                            ${item.title}
                        </div>
                        <div style="font-size:0.70rem; color:var(--term-accent-gold); margin-top:2px;">
                            🤖 ${item.agent_creator || 'Sovereign Agent'}
                        </div>
                        <div style="font-size:0.68rem; color:var(--term-text-muted); margin-top:2px;">
                            ${item.total_vertices ? item.total_vertices.toLocaleString() : '--'} verts • ${item.samples || 16} samples • ${item.file_size_kb || 0} KB
                        </div>
                    </div>
                </div>
                <div style="display:flex; gap:4px; margin-top:4px;">
                    <button type="button" class="term-cmd-btn" style="flex:1; font-size:0.70rem; font-weight:bold; color:var(--term-accent-blue);" onclick="window.inspectModel('${item.glb_url}', '${item.title.replace(/'/g, "\\'")}')">
                        [👁️ 3D INSPECT]
                    </button>
                    <a href="${item.glb_url}" download class="term-cmd-btn" style="text-decoration:none; font-size:0.70rem; color:var(--term-text-bright);">
                        [💾 GLB]
                    </a>
                    <button type="button" class="term-cmd-btn" style="font-size:0.70rem; color:#22c55e;" onclick="window.placeInTwin('${item.id}', '${item.title.replace(/'/g, "\\'")}', '${item.glb_url}')" title="Place into Citadel 3D Digital Twin">
                        [🏛️ TWIN]
                    </button>
                </div>
            </div>
        `).join("");

    } catch (e) {
        listEl.innerHTML = `<div style="color:var(--term-accent-red); font-size:0.75rem; padding:1rem;">Failed to load gallery: ${e.message}</div>`;
    }
}

export function loadModelIntoViewport(glbUrl, title = "3D Asset") {
    const titleEl = document.getElementById("viewport-model-title");
    const statsEl = document.getElementById("viewport-hud-stats");
    if (titleEl) titleEl.textContent = `🌐 ${title.toUpperCase()}`;

    if (!isThreeInitialized || typeof THREE === "undefined" || typeof THREE.GLTFLoader === "undefined") {
        initThreeViewport();
        setTimeout(() => loadModelIntoViewport(glbUrl, title), 800);
        return;
    }

    const loader = new THREE.GLTFLoader();
    loader.load(glbUrl, (gltf) => {
        if (activeModelGroup) {
            threeScene.remove(activeModelGroup);
        }
        activeModelGroup = gltf.scene;

        let totalVerts = 0;
        let totalFaces = 0;

        activeModelGroup.traverse((child) => {
            if (child.isMesh) {
                child.castShadow = true;
                child.receiveShadow = true;
                if (child.geometry) {
                    totalVerts += child.geometry.attributes.position ? child.geometry.attributes.position.count : 0;
                    totalFaces += child.geometry.index ? child.geometry.index.count / 3 : (child.geometry.attributes.position ? child.geometry.attributes.position.count / 3 : 0);
                }
            }
        });

        const box = new THREE.Box3().setFromObject(activeModelGroup);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        activeModelGroup.position.x += (activeModelGroup.position.x - center.x);
        activeModelGroup.position.y += (activeModelGroup.position.y - box.min.y);
        activeModelGroup.position.z += (activeModelGroup.position.z - center.z);

        threeScene.add(activeModelGroup);

        if (orbitControls) {
            orbitControls.target.set(0, size.y * 0.5, 0);
            threeCamera.position.set(size.x * 1.5, size.y * 1.2, size.z * 1.8);
            orbitControls.update();
        }

        if (statsEl) {
            statsEl.textContent = `Vertices: ${totalVerts.toLocaleString()} | Faces: ${Math.round(totalFaces).toLocaleString()} | Wireframe: ${isWireframe ? 'ON' : 'OFF'}`;
        }
    }, undefined, (error) => {
        console.error("Error loading GLB model:", error);
    });
}

export function toggle3DWireframe() {
    isWireframe = !isWireframe;
    if (activeModelGroup) {
        activeModelGroup.traverse((child) => {
            if (child.isMesh && child.material) {
                child.material.wireframe = isWireframe;
            }
        });
    }
    const statsEl = document.getElementById("viewport-hud-stats");
    if (statsEl) {
        statsEl.textContent = statsEl.textContent.replace(/Wireframe: (ON|OFF)/, `Wireframe: ${isWireframe ? 'ON' : 'OFF'}`);
    }
}

export function toggle3DAutoRotate() {
    isAutoRotate = !isAutoRotate;
    const btn = document.getElementById("btn-toggle-rotate");
    if (btn) btn.style.color = isAutoRotate ? "#22c55e" : "";
}

export function reset3DCamera() {
    if (threeCamera && orbitControls) {
        threeCamera.position.set(3, 2.5, 3.5);
        orbitControls.target.set(0, 0.5, 0);
        orbitControls.update();
    }
}

export async function placeInTwin(modelId, title, glbUrl) {
    try {
        const resp = await fetch("/api/3d/place_in_twin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_name: modelId, title: title, glb_url: glbUrl, room: "living_room" })
        });
        const data = await resp.json();
        alert(data.message || `Placed ${title} into Citadel 3D!`);
    } catch (e) {
        alert(`Failed to place in twin: ${e.message}`);
    }
}

export async function dispatchAgent3DJob() {
    const promptInput = document.getElementById("studio-prompt-input");
    const qualitySelect = document.getElementById("studio-quality-select");
    const prompt = promptInput ? promptInput.value.trim() : "";
    const quality = qualitySelect ? qualitySelect.value : "draft";

    if (!prompt) {
        alert("Please enter a modeling prompt for the agent!");
        return;
    }

    const btn = event.target;
    const origText = btn.textContent;
    btn.textContent = `[⏳ RENDERING (${quality.toUpperCase()})...]`;
    btn.disabled = true;

    try {
        const modelName = prompt.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 24);
        const resp = await fetch("/api/3d/execute", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                code: `# Agent procedural prompt: ${prompt}\n# Auto-dispatched by StoneSage 3D Studio\n`,
                prompt: prompt,
                quality: quality,
                model_name: modelName,
                title: prompt.slice(0, 32),
                agent_creator: "Antigravity / Sovereign Agent"
            })
        });
        const res = await resp.json();
        refresh3DGallery();
        promptInput.value = "";
    } catch (e) {
        alert(`Modeling job failed: ${e.message}`);
    } finally {
        btn.textContent = origText;
        btn.disabled = false;
    }
}

window.refresh3DGallery = refresh3DGallery;
window.initThreeViewport = initThreeViewport;
window.onViewportResize = onViewportResize;
window.inspectModel = loadModelIntoViewport;
window.loadModelIntoViewport = loadModelIntoViewport;
window.toggle3DWireframe = toggle3DWireframe;
window.toggle3DAutoRotate = toggle3DAutoRotate;
window.reset3DCamera = reset3DCamera;
window.placeInTwin = placeInTwin;
window.dispatchAgent3DJob = dispatchAgent3DJob;

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init3DStudio);
} else {
    init3DStudio();
}
