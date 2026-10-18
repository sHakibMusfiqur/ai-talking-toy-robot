"use strict";

/* Interactive 3D robot face - mood provenance (frontend-only mapping).
 *
 * REAL DEVICE TELEMETRY (mapped from live controller summary / command API;
 * not fabricated):
 *   IDLE           <- state IDLE, BOOT, "" or unknown
 *   LISTENING      <- state CAPTURING or LISTENING_RETRY
 *   THINKING       <- state PROCESSING
 *   PLAYING        <- state DANCE
 *   HAPPY          <- state WAVE, NOD, TILT_HEAD or POINT
 *   OFFLINE        <- online !== true (dimmed presence, not claimed as a mood)
 *   Antenna LED    <- real online flag only
 *
 * INFERRED (UI-only emphasis on top of real data; confirmed inputs):
 *   SAD            <- battery_percent <= 20 while the base state mood is IDLE
 *   THINKING       <- transient while a command POST is in flight (<= 60 s)
 *   <action mood>  <- per-action transient for 5 s after a confirmed 200
 *   CONFUSED       <- 4 s after a confirmed command failure
 *
 * Precedence: transient > OFFLINE > named state > low-battery > IDLE. */

var container = document.getElementById("robot-face");
if (container) initFace();

function setFaceState(state, message) {
  container.setAttribute("data-face-state", state);
  var loading = document.getElementById("face-loading");
  var fallback = document.getElementById("face-fallback");
  var text = document.getElementById("face-fallback-text");
  if (state === "live") {
    if (loading) loading.hidden = true;
    if (fallback) fallback.hidden = true;
  } else if (state === "fallback") {
    if (loading) loading.hidden = true;
    if (fallback) fallback.hidden = false;
    if (text && message) text.textContent = message;
    var status = document.getElementById("face-status");
    if (status) status.textContent = "Robot face preview unavailable.";
  }
}

function initFace() {
  var statusEl = document.getElementById("face-status");
  var state = { online: null, state: null, battery: null };
  var transient = { mood: null, until: 0 };
  var lastMood = "";

  document.addEventListener("robotface:state", function (event) {
    state.online = event.detail.online;
    state.state = event.detail.state;
    state.battery = event.detail.battery;
    wake();
  });
  document.addEventListener("robotface:command", function (event) {
    var detail = event.detail || {};
    if (detail.phase === "pending") {
      transient.mood = "THINKING";
      transient.until = Date.now() + 60000;
    } else if (detail.phase === "done") {
      if (detail.ok === false) {
        transient.mood = "CONFUSED";
        transient.until = Date.now() + 4000;
      } else {
        transient.mood = moodForAction(detail.action);
        transient.until = Date.now() + 5000;
      }
    }
    wake();
  });

  var LOW_BATTERY = 20;
  var ACTION_MOODS = {
    DANCE: "PLAYING",
    LISTENING_RETRY: "LISTENING",
    WAVE: "HAPPY",
    NOD: "HAPPY",
    TILT_HEAD: "HAPPY",
    POINT: "HAPPY",
    IDLE: "IDLE"
  };

  function moodForAction(action) {
    return ACTION_MOODS[String(action || "").toUpperCase()] || "IDLE";
  }

  function moodFromData() {
    var online = state.online;
    if (online !== true) return "OFFLINE";
    var name = String(state.state || "").toUpperCase();
    if (name === "CAPTURING" || name === "LISTENING_RETRY") return "LISTENING";
    if (name === "PROCESSING") return "THINKING";
    if (name === "DANCE") return "PLAYING";
    if (name === "WAVE" || name === "NOD" || name === "TILT_HEAD" || name === "POINT") {
      return "HAPPY";
    }
    if (typeof state.battery === "number" && state.battery <= LOW_BATTERY) {
      return "SAD";
    }
    return "IDLE";
  }

  function resolveMood(now) {
    if (transient.mood && now < transient.until) return transient.mood;
    transient.mood = null;
    return moodFromData();
  }

  /* Mood -> animation targets. eyesOpen/eyeOpenR scale lids, mouthCurve is
     -1 frown .. +1 smile, energy drives the dancing oscillation. */
  function moodTargets(mood) {
    var base = {
      browLY: 0, browRY: 0, browLRot: 0, browRRot: 0,
      eyeOpen: 1, eyeOpenR: 1, eyeX: 0, eyeY: 0,
      mouthCurve: 0.1, mouthOpen: 0,
      headYaw: 0, headPitch: 0, headRoll: 0,
      sway: 0.04, energy: 0, restless: 1, blink: 1, dim: 0
    };
    switch (mood) {
      case "LISTENING":
        base.browLY = 0.05; base.browRY = 0.05;
        base.eyeOpen = 1.12; base.eyeOpenR = 1.12;
        base.mouthCurve = 0.05; base.headPitch = 0.05;
        base.sway = 0.015; base.restless = 0.35; base.blink = 0.7;
        break;
      case "THINKING":
        base.browLY = 0.06; base.browLRot = 0.12; base.browRRot = -0.02;
        base.eyeOpenR = 0.95; base.mouthCurve = -0.08;
        base.sway = 0.16; base.restless = 2.6;
        break;
      case "HAPPY":
        base.browLY = 0.07; base.browRY = 0.07;
        base.eyeOpen = 0.82; base.eyeOpenR = 0.82;
        base.mouthCurve = 1; base.mouthOpen = 0.35;
        base.headPitch = -0.03; base.sway = 0.05;
        base.energy = 0.35; base.blink = 1.4; base.restless = 0.9;
        break;
      case "SAD":
        base.browLRot = 0.38; base.browRRot = -0.38;
        base.browLY = -0.045; base.browRY = -0.045;
        base.eyeOpen = 0.7; base.eyeOpenR = 0.7; base.eyeY = -0.35;
        base.mouthCurve = -1; base.headPitch = 0.12;
        base.sway = 0.02; base.energy = 0; base.restless = 0.3; base.blink = 0.6;
        break;
      case "CONFUSED":
        base.browLRot = -0.45; base.browLY = 0.06;
        base.browRRot = 0.08; base.browRY = -0.01;
        base.eyeOpenR = 0.8; base.mouthCurve = -0.3;
        base.headRoll = 0.1; base.sway = 0.06; base.restless = 1.8; base.blink = 0.9;
        break;
      case "PLAYING":
        base.browLY = 0.08; base.browRY = 0.08;
        base.eyeOpen = 0.95; base.eyeOpenR = 0.95;
        base.mouthCurve = 0.85; base.mouthOpen = 0.5;
        base.sway = 0.1; base.energy = 1; base.restless = 1.4; base.blink = 1.6;
        break;
      case "OFFLINE":
        base.dim = 1; base.sway = 0.008;
        base.eyeOpen = 0.9; base.eyeOpenR = 0.9;
        base.mouthCurve = -0.1; base.restless = 0.2; base.blink = 0.3;
        break;
      default: /* IDLE */ break;
    }
    return base;
  }

  function hasWebGL() {
    try {
      var probe = document.createElement("canvas");
      return !!(
        probe.getContext("webgl2") ||
        probe.getContext("webgl") ||
        probe.getContext("experimental-webgl")
      );
    } catch (error) {
      return false;
    }
  }

  var wake = function () {};

  import("three")
    .then(function (THREE) {
      start(THREE);
    })
    .catch(function () {
      setFaceState("fallback", "3D preview unavailable - the 3D library could not load.");
    });

  function start(THREE) {
    if (!hasWebGL()) {
      setFaceState("fallback", "3D preview unavailable - WebGL is not supported in this browser.");
      return;
    }
    var canvas = document.createElement("canvas");
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    container.appendChild(canvas);

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        antialias: true,
        alpha: true,
        powerPreference: "low-power"
      });
    } catch (error) {
      canvas.remove();
      setFaceState("fallback", "3D preview unavailable - WebGL could not start.");
      return;
    }
    renderer.setClearColor(0x000000, 0);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(36, 1, 0.1, 60);
    camera.position.set(0, 0.1, 4.6);
    camera.lookAt(0, -0.05, 0);

    /* Lights (procedural only - no environment files). */
    scene.add(new THREE.HemisphereLight(0xffffff, 0x1a1c22, 0.6));
    scene.add(new THREE.AmbientLight(0xffffff, 0.18));
    var key = new THREE.DirectionalLight(0xfff6e8, 1.6);
    key.position.set(3, 5, 4);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0xdfe8ff, 0.5);
    fill.position.set(-4, 1, 3);
    scene.add(fill);
    var rim = new THREE.DirectionalLight(0xffffff, 0.9);
    rim.position.set(0, 3, -5);
    scene.add(rim);

    var shellMat = new THREE.MeshPhysicalMaterial({
      color: 0xf6f5f1,
      roughness: 0.3,
      metalness: 0,
      clearcoat: 0.6,
      clearcoatRoughness: 0.25
    });
    var plateMat = new THREE.MeshPhysicalMaterial({
      color: 0x14161c,
      roughness: 0.35,
      metalness: 0.1,
      clearcoat: 0.8,
      clearcoatRoughness: 0.2
    });
    var eyeMat = new THREE.MeshBasicMaterial({ color: 0xe9f5ff });
    var irisMat = new THREE.MeshBasicMaterial({ color: 0x0d1118 });
    var browMat = new THREE.MeshStandardMaterial({ color: 0xd8dee7, roughness: 0.5 });
    var mouthMat = new THREE.MeshBasicMaterial({ color: 0xe9f5ff });
    var tipMat = new THREE.MeshBasicMaterial({ color: 0x00d084 });

    var head = new THREE.Group();
    scene.add(head);

    /* Glossy white shell. */
    var shell = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), shellMat);
    shell.scale.set(1, 0.96, 0.92);
    head.add(shell);

    /* Dark face display (slightly proud of the shell). */
    var plate = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), plateMat);
    plate.scale.set(0.6, 0.4, 0.1);
    plate.position.set(0, 0, 0.84);
    head.add(plate);

    /* Eyes: glowing lids + dark irises that can wander. */
    function makeEye(sign) {
      var group = new THREE.Group();
      group.position.set(0.24 * sign, 0.09, 0.93);
      var lid = new THREE.Mesh(new THREE.SphereGeometry(0.11, 24, 16), eyeMat);
      lid.scale.set(0.85, 1, 0.3);
      group.add(lid);
      var iris = new THREE.Mesh(new THREE.SphereGeometry(0.05, 20, 12), irisMat);
      iris.position.z = 0.045;
      group.add(iris);
      head.add(group);
      return { group: group, iris: iris };
    }
    var eyeL = makeEye(-1);
    var eyeR = makeEye(1);

    /* Eyebrows on the display. */
    function makeBrow(sign) {
      var brow = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.036, 0.05), browMat);
      brow.position.set(0.24 * sign, 0.27, 0.9);
      head.add(brow);
      return brow;
    }
    var browL = makeBrow(-1);
    var browR = makeBrow(1);

    /* Mouth: light arc always dipping below its anchor; the curve parameter
       flattens it toward neutral and mirrors it (scale.y sign) into a frown,
       so mid-range moods never rotate the arc sideways. */
    var MOUTH_ARC = Math.PI * 0.9;
    var mouth = new THREE.Mesh(
      new THREE.TorusGeometry(0.15, 0.024, 12, 48, MOUTH_ARC),
      mouthMat
    );
    mouth.position.set(0, -0.16, 0.92);
    head.add(mouth);
    var smileRot = -Math.PI / 2 - MOUTH_ARC / 2;

    /* Ear pods + antenna. */
    [-1, 1].forEach(function (sign) {
      var pod = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.1, 8, 20), shellMat);
      pod.rotation.z = Math.PI / 2;
      pod.position.set(1.0 * sign, 0.02, 0);
      head.add(pod);
    });
    var stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.26, 12), plateMat);
    stalk.position.set(0, 1.06, 0);
    head.add(stalk);
    var tip = new THREE.Mesh(new THREE.SphereGeometry(0.075, 20, 14), tipMat);
    tip.position.set(0, 1.24, 0);
    head.add(tip);

    /* Neck + base. */
    var neck = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.44, 0.3, 32), shellMat);
    neck.position.set(0, -1.06, 0);
    head.add(neck);
    var base = new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.72, 0.14, 40), shellMat);
    base.position.set(0, -1.27, 0);
    head.add(base);

    /* ---------- animation state ---------- */
    var reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    var reduced = reducedQuery.matches;
    var current = moodTargets("IDLE");
    var blinkTimer = 1.4;
    var blinkT = -1;
    var saccTimer = 1;
    var gaze = { x: 0, y: 0 };
    var running = false;
    var disposed = false;

    function damped(key, target, dt, speed) {
      current[key] += (target[key] - current[key]) * (1 - Math.exp(-speed * dt));
    }

    function applyMood(mood) {
      if (mood === lastMood) return;
      lastMood = mood;
      var label = mood.toLowerCase();
      if (statusEl) statusEl.textContent = "Robot face: " + label;
      if (reduced) {
        var instant = moodTargets(mood);
        Object.keys(instant).forEach(function (key) {
          current[key] = instant[key];
        });
        if (running) renderer.render(scene, camera);
      }
    }

    function resize() {
      var width = Math.max(1, container.clientWidth);
      var height = Math.max(1, container.clientHeight);
      var aspect = width / height;
      renderer.setPixelRatio(
        Math.min(window.devicePixelRatio || 1, width <= 768 ? 1.5 : 2)
      );
      renderer.setSize(width, height, false);
      camera.aspect = aspect;
      camera.position.z = 4.6 + Math.max(0, 1.1 - aspect) * 2.6;
      camera.updateProjectionMatrix();
      if (reduced) renderer.render(scene, camera);
    }

    var resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
    resize();

    var eyeBright = new THREE.Color(0xe9f5ff);
    var eyeDim = new THREE.Color(0x3a4654);
    var onlineColor = new THREE.Color(0x00d084);
    var offlineColor = new THREE.Color(0xf59e0b);

    function frame(dt, t) {
      var mood = resolveMood(Date.now());
      applyMood(mood);
      var targets = moodTargets(mood);

      ["browLY", "browRY", "browLRot", "browRRot", "eyeOpen", "eyeOpenR",
       "mouthCurve", "mouthOpen", "headYaw", "headPitch", "headRoll",
       "sway", "energy", "restless", "blink", "dim"].forEach(function (key) {
        damped(key, targets, dt, 6);
      });

      /* Blinking. */
      if (blinkT >= 0) {
        blinkT += dt;
        if (blinkT > 0.15) blinkT = -1;
      } else if (!reduced) {
        blinkTimer -= dt;
        if (blinkTimer <= 0) {
          blinkT = 0;
          blinkTimer = (2.2 + Math.random() * 2.4) / Math.max(0.2, current.blink);
        }
      }
      var blinkAmount = blinkT >= 0 ? Math.sin((blinkT / 0.15) * Math.PI) : 0;

      /* Eye saccades (searching eyes when restless). */
      if (!reduced) {
        saccTimer -= dt;
        if (saccTimer <= 0) {
          var ampX = Math.min(0.05, 0.022 * current.restless);
          var ampY = Math.min(0.04, 0.018 * current.restless);
          gaze.x = (Math.random() - 0.5) * 2 * ampX;
          gaze.y = (Math.random() - 0.5) * 2 * ampY;
          saccTimer = (0.7 + Math.random() * 1.9) / Math.max(0.3, current.restless);
        }
      } else {
        gaze.x = 0;
        gaze.y = 0;
      }

      var openL = current.eyeOpen * (1 - 0.94 * blinkAmount);
      var openR = current.eyeOpenR * (1 - 0.94 * blinkAmount);
      eyeL.group.scale.y = Math.max(0.04, openL);
      eyeR.group.scale.y = Math.max(0.04, openR);
      eyeL.iris.position.x = gaze.x;
      eyeL.iris.position.y = gaze.y + current.eyeY * 0.1;
      eyeR.iris.position.x = gaze.x;
      eyeR.iris.position.y = gaze.y + current.eyeY * 0.1;
      eyeMat.color.copy(eyeBright).lerp(eyeDim, current.dim);

      browL.position.y = 0.27 + current.browLY;
      browR.position.y = 0.27 + current.browRY;
      browL.rotation.z = current.browLRot;
      browR.rotation.z = current.browRRot;

      mouth.rotation.z = smileRot;
      var curveY = Math.max(-1, Math.min(1, current.mouthCurve));
      mouth.scale.set(
        1 + current.mouthOpen * 0.35,
        curveY * (1 + current.mouthOpen * 0.3),
        1
      );

      /* Head motion: mood pose + idle sway + energetic dance. */
      var sway = reduced ? 0 : current.sway;
      var energy = reduced ? 0 : current.energy;
      head.rotation.y =
        current.headYaw + Math.sin(t * 0.55) * sway * 0.6 + Math.sin(t * 5.4) * 0.24 * energy;
      head.rotation.x =
        current.headPitch + Math.sin(t * 0.79 + 1.3) * sway * 0.35;
      head.rotation.z =
        current.headRoll + Math.sin(t * 0.47 + 2.1) * sway * 0.3 + Math.sin(t * 8.1) * 0.1 * energy;
      head.position.y = Math.sin(t * 7) * 0.045 * energy;

      /* Antenna tip reports the REAL connectivity state. */
      tipMat.color.copy(state.online === true ? onlineColor : offlineColor);
      var pulse =
        state.online === true || reduced ? 1 : 0.55 + 0.45 * Math.sin(t * 2.4);
      tip.scale.setScalar(pulse);

      renderer.render(scene, camera);
    }

    var last = performance.now();
    function loop(now) {
      var dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      frame(dt, now / 1000);
    }

    function startLoop() {
      if (disposed || running) return;
      running = true;
      last = performance.now();
      if (reduced) {
        frame(0.016, last / 1000);
      } else {
        renderer.setAnimationLoop(loop);
      }
      setFaceState("live");
    }

    function applyReduced() {
      reduced = reducedQuery.matches;
      if (reduced) {
        renderer.setAnimationLoop(null);
        running = true;
        frame(0.016, performance.now() / 1000);
      } else if (running) {
        last = performance.now();
        renderer.setAnimationLoop(loop);
      }
    }
    if (reducedQuery.addEventListener) {
      reducedQuery.addEventListener("change", applyReduced);
    }

    function onVisibility() {
      if (document.hidden) {
        if (!reduced) renderer.setAnimationLoop(null);
      } else if (running && !reduced) {
        last = performance.now();
        renderer.setAnimationLoop(loop);
      }
    }
    document.addEventListener("visibilitychange", onVisibility);

    /* Assign (do not redeclare) the outer wake binding so the document
       listeners registered in initFace call this implementation - a nested
       wake declaration would shadow the stub and reduced-motion users would
       never see event-driven mood updates. */
    wake = function () {
      if (!running && !disposed) startLoop();
      if (reduced) frame(0.016, performance.now() / 1000);
    };
    window.addEventListener("pagehide", function () {
      disposed = true;
      renderer.setAnimationLoop(null);
      resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      renderer.dispose();
    });

    startLoop();
    wake();
  }
}
