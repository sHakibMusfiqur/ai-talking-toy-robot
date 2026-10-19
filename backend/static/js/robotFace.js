"use strict";

/* Interactive 3D robot face - mood provenance (frontend-only mapping).
 *
 * Visual style: procedural "RoboPanel" head recreated from the owner's
 * design reference - rounded casing, recessed dot-matrix display with
 * glowing pixel eyes and mouth, antenna orb and side pods. All geometry is
 * generated at runtime; no image, video or model asset is ever loaded.
 *
 * REAL DEVICE TELEMETRY (mapped from live controller summary / command API;
 * not fabricated):
 *   IDLE           <- state IDLE, BOOT, "" or unknown
 *   LISTENING      <- state CAPTURING or LISTENING_RETRY
 *   THINKING       <- state PROCESSING
 *   PLAYING        <- state DANCE
 *   HAPPY          <- state WAVE, NOD, TILT_HEAD or POINT
 *   OFFLINE        <- online !== true (dimmed presence, not claimed as a mood)
 *   Antenna LED    <- real online flag only (hue = the user's glow color)
 *
 * INFERRED (UI-only emphasis on top of real data; confirmed inputs):
 *   SAD            <- battery_percent <= 20 while the base state mood is IDLE
 *   THINKING       <- transient while a command POST is in flight (<= 60 s)
 *   <action mood>  <- per-action transient for 5 s after a confirmed 200
 *   CONFUSED       <- 4 s after a confirmed command failure
 *   EXCITED        <- 1.5 s after the user taps the face (local interaction)
 *
 * Precedence: transient > OFFLINE > named state > low-battery > IDLE.
 *
 * Face colors (casing/display/eyes/mouth/glow) are session-only UI state;
 * they are never persisted or transmitted anywhere. */

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
    var palettePanel = document.getElementById("face-palette");
    if (palettePanel) palettePanel.hidden = true;
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

  /* Mood -> head animation targets. The face itself is driven by the
     pixel expressions below; these only steer head pose and ambience. */
  function moodTargets(mood) {
    var base = {
      headYaw: 0, headPitch: 0, headRoll: 0,
      sway: 0.04, energy: 0, dim: 0, blink: 1, restless: 1
    };
    switch (mood) {
      case "LISTENING":
        base.headPitch = 0.05;
        base.sway = 0.015; base.restless = 0.35; base.blink = 0.7;
        break;
      case "THINKING":
        base.headYaw = 0.06;
        base.sway = 0.16; base.restless = 2.6;
        break;
      case "HAPPY":
        base.headPitch = -0.03;
        base.sway = 0.05; base.energy = 0.35; base.blink = 1.4; base.restless = 0.9;
        break;
      case "SAD":
        base.headPitch = 0.12;
        base.sway = 0.02; base.energy = 0; base.restless = 0.3; base.blink = 0.6;
        break;
      case "CONFUSED":
        base.headRoll = 0.1;
        base.sway = 0.06; base.restless = 1.8; base.blink = 0.9;
        break;
      case "PLAYING":
        base.sway = 0.1; base.energy = 1; base.restless = 1.4; base.blink = 1.6;
        break;
      case "EXCITED":
        base.headPitch = -0.05;
        base.sway = 0.07; base.energy = 0.6; base.blink = 1.8; base.restless = 1.6;
        break;
      case "OFFLINE":
        base.dim = 1;
        base.sway = 0.008; base.restless = 0.2; base.blink = 0.3;
        break;
      default: /* IDLE */ break;
    }
    return base;
  }

  /* ---------- dot-matrix expressions ------------------------------------
     Patterns are ASCII pixel rows: "X" lights a pixel, "." leaves it dark.
     Eye patterns are 4 rows x 6 columns per eye (the right eye mirrors the
     left unless a mood defines an asymmetric pair); mouth patterns are
     4 rows x 11 columns. Unknown moods fall back to IDLE. */

  var EYE_IDLE = [".XXXX.", "XXXXXX", "XXXXXX", ".XXXX."];
  var EYE_CHEVRON = ["..XX..", ".X..X.", "X....X", "......"];
  var EYE_DROOP = ["......", ".XXXX.", "XXXXXX", ".XXXX."];
  var EYE_WIDE = ["XXXXXX", "XXXXXX", "XXXXXX", "XXXXXX"];
  var EYE_RING = [".XXXX.", "X....X", "X....X", ".XXXX."];
  var EYE_OPEN_U = ["X....X", "X....X", ".X..X.", "..XX.."];
  var EYE_SQUINT = ["......", "......", "XXXXXX", "XXXXXX"];
  var EYE_CLOSED = ["......", "......", "XXXXXX", "......"];

  var MOUTH_IDLE = ["...........", "...........", "..XXXXXXX..", "..........."];
  var MOUTH_HAPPY = ["...........", ".X.......X.", "..XX...XX..", "...XXXXX..."];
  var MOUTH_SAD = ["...XXXXX...", "..XX...XX..", ".X.......X.", "..........."];
  var MOUTH_THINKING = ["...........", "...........", ".....XXXX..", "..........."];
  var MOUTH_LISTENING = ["...........", "...XXXXX...", "..XX...XX..", "...XXXXX..."];
  var MOUTH_EXCITED = [".X.......X.", ".X.......X.", "..XX...XX..", "...XXXXX..."];
  var MOUTH_CONFUSED = ["...........", ".......XX..", "..XXXXXX...", "..........."];
  var MOUTH_PLAYING = ["...........", "..XXXXXXX..", "..XX...XX..", "...XXXXX..."];
  var MOUTH_OFFLINE = ["...........", "...........", "...XXXXX...", "..........."];

  function mirrorRows(rows) {
    var mirrored = [];
    rows.forEach(function (row) {
      mirrored.push(row.split("").reverse().join(""));
    });
    return mirrored;
  }

  function makeExpression(eyesLeft, eyesRight, mouth) {
    return {
      L: eyesLeft,
      R: eyesRight || mirrorRows(eyesLeft),
      mouth: mouth
    };
  }

  var EXPRESSIONS = {
    IDLE: makeExpression(EYE_IDLE, null, MOUTH_IDLE),
    HAPPY: makeExpression(EYE_CHEVRON, null, MOUTH_HAPPY),
    SAD: makeExpression(EYE_DROOP, null, MOUTH_SAD),
    THINKING: makeExpression(EYE_IDLE, EYE_SQUINT, MOUTH_THINKING),
    LISTENING: makeExpression(EYE_WIDE, null, MOUTH_LISTENING),
    EXCITED: makeExpression(EYE_RING, null, MOUTH_EXCITED),
    CONFUSED: makeExpression(EYE_SQUINT, EYE_IDLE, MOUTH_CONFUSED),
    PLAYING: makeExpression(EYE_OPEN_U, null, MOUTH_PLAYING),
    OFFLINE: makeExpression(EYE_CLOSED, EYE_CLOSED, MOUTH_OFFLINE),
    BLINK: makeExpression(EYE_CLOSED, EYE_CLOSED, null)
  };

  function expressionFor(mood) {
    return EXPRESSIONS[mood] || EXPRESSIONS.IDLE;
  }

  /* ---------- display pixel grid ----------------------------------------
     17 x 10 cells: eyes occupy rows 1-4 (left cols 1-6, right cols 10-15),
     the mouth occupies rows 6-9, cols 3-13; every remaining cell renders as
     a faint background dot so the whole panel reads as a dot matrix. */

  var GRID_COLS = 17;
  var GRID_ROWS = 10;
  var DOT_PITCH = 0.145;
  var EYE_ROWS = [1, 4];
  var EYE_COLS_LEFT = [1, 6];
  var EYE_COLS_RIGHT = [10, 15];
  var MOUTH_ROWS = [6, 9];
  var MOUTH_COLS = [3, 13];
  var FACE_CELLS = [];

  function buildFaceCells() {
    for (var r = 0; r < GRID_ROWS; r++) {
      for (var c = 0; c < GRID_COLS; c++) {
        var group = "grid";
        var side = "";
        var pr = -1;
        var pc = -1;
        if (r >= EYE_ROWS[0] && r <= EYE_ROWS[1]) {
          if (c >= EYE_COLS_LEFT[0] && c <= EYE_COLS_LEFT[1]) {
            group = "eye"; side = "L"; pr = r - EYE_ROWS[0]; pc = c - EYE_COLS_LEFT[0];
          } else if (c >= EYE_COLS_RIGHT[0] && c <= EYE_COLS_RIGHT[1]) {
            group = "eye"; side = "R"; pr = r - EYE_ROWS[0]; pc = c - EYE_COLS_RIGHT[0];
          }
        } else if (r >= MOUTH_ROWS[0] && r <= MOUTH_ROWS[1] &&
                   c >= MOUTH_COLS[0] && c <= MOUTH_COLS[1]) {
          group = "mouth"; pr = r - MOUTH_ROWS[0]; pc = c - MOUTH_COLS[0];
        }
        FACE_CELLS.push({
          x: (c - (GRID_COLS - 1) / 2) * DOT_PITCH,
          y: ((GRID_ROWS - 1) / 2 - r) * DOT_PITCH,
          group: group,
          side: side,
          pr: pr,
          pc: pc,
          lit: 0
        });
      }
    }
  }
  buildFaceCells();

  /* ---------- face color palette (session-only, never persisted) -------- */

  var PALETTE_KEYS = ["casing", "display", "eyes", "mouth", "glow"];
  var PALETTE_DEFAULT = {
    casing: "#f5a623",
    display: "#101013",
    eyes: "#ffc24d",
    mouth: "#ff9e2c",
    glow: "#ffd98a"
  };
  var PALETTE_PRESETS = {
    amber: { casing: "#f5a623", display: "#101013", eyes: "#ffc24d", mouth: "#ff9e2c", glow: "#ffd98a" },
    blue: { casing: "#3b82f6", display: "#0a1020", eyes: "#7dd3fc", mouth: "#38bdf8", glow: "#bae6fd" },
    pink: { casing: "#ec4899", display: "#180a12", eyes: "#f9a8d4", mouth: "#f472b6", glow: "#fbcfe8" },
    green: { casing: "#34d399", display: "#07140f", eyes: "#6ee7b7", mouth: "#34d399", glow: "#a7f3d0" },
    purple: { casing: "#8b5cf6", display: "#120a1e", eyes: "#c4b5fd", mouth: "#a78bfa", glow: "#ddd6fe" },
    mono: { casing: "#e5e7eb", display: "#0b0b0c", eyes: "#f9fafb", mouth: "#d1d5db", glow: "#ffffff" }
  };
  var HEX_PATTERN = /^#?[0-9a-fA-F]{6}$/;
  var HEX_SHORT_PATTERN = /^#?[0-9a-fA-F]{3}$/;

  var palette = {};
  PALETTE_KEYS.forEach(function (key) {
    palette[key] = PALETTE_DEFAULT[key];
  });
  var paletteListener = null;

  function normalizeHex(value) {
    var raw = String(value || "").trim();
    if (HEX_PATTERN.test(raw)) {
      return "#" + raw.replace("#", "").toLowerCase();
    }
    if (HEX_SHORT_PATTERN.test(raw)) {
      var short = raw.replace("#", "").toLowerCase();
      return "#" + short.charAt(0) + short.charAt(0) +
        short.charAt(1) + short.charAt(1) +
        short.charAt(2) + short.charAt(2);
    }
    return null;
  }

  function notifyPalette() {
    if (paletteListener) paletteListener(palette);
  }

  function syncPaletteInputs() {
    PALETTE_KEYS.forEach(function (key) {
      var colorInput = document.getElementById("face-color-" + key);
      var hexInput = document.getElementById("face-hex-" + key);
      if (colorInput) colorInput.value = palette[key];
      if (hexInput) {
        hexInput.value = palette[key].toUpperCase();
        hexInput.removeAttribute("aria-invalid");
      }
    });
  }

  function setPaletteKey(key, hex, sourceInput) {
    palette[key] = hex;
    var colorInput = document.getElementById("face-color-" + key);
    var hexInput = document.getElementById("face-hex-" + key);
    if (colorInput && sourceInput !== colorInput) colorInput.value = hex;
    if (hexInput) {
      if (sourceInput !== hexInput) hexInput.value = hex.toUpperCase();
      hexInput.removeAttribute("aria-invalid");
    }
    notifyPalette();
  }

  function applyPaletteValues(values) {
    if (!values) return;
    PALETTE_KEYS.forEach(function (key) {
      palette[key] = values[key];
    });
    syncPaletteInputs();
    notifyPalette();
  }

  function restoreHexInput(key, hexInput) {
    if (normalizeHex(hexInput.value)) return;
    hexInput.value = palette[key].toUpperCase();
    hexInput.removeAttribute("aria-invalid");
  }

  function wirePalette() {
    var panel = document.getElementById("face-palette");
    if (!panel) return;
    PALETTE_KEYS.forEach(function (key) {
      var colorInput = document.getElementById("face-color-" + key);
      if (colorInput) {
        colorInput.addEventListener("input", function () {
          var hex = normalizeHex(colorInput.value);
          if (hex) setPaletteKey(key, hex, colorInput);
        });
      }
      var hexInput = document.getElementById("face-hex-" + key);
      if (hexInput) {
        hexInput.addEventListener("input", function () {
          var hex = normalizeHex(hexInput.value);
          if (hex) {
            setPaletteKey(key, hex, hexInput);
          } else {
            hexInput.setAttribute("aria-invalid", "true");
          }
        });
        hexInput.addEventListener("change", function () {
          restoreHexInput(key, hexInput);
        });
        hexInput.addEventListener("blur", function () {
          restoreHexInput(key, hexInput);
        });
      }
    });
    Array.prototype.forEach.call(
      panel.querySelectorAll("[data-face-preset]"),
      function (button) {
        button.addEventListener("click", function () {
          applyPaletteValues(PALETTE_PRESETS[button.getAttribute("data-face-preset")]);
        });
      }
    );
    var reset = document.getElementById("face-palette-reset");
    if (reset) {
      reset.addEventListener("click", function () {
        applyPaletteValues(PALETTE_DEFAULT);
      });
    }
    syncPaletteInputs();
  }
  wirePalette();

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
    /* Absolutely positioned: an in-flow canvas would leak its intrinsic
       size into the flex layout and inflate the preview card on every
       renderer.setSize() call. */
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.display = "block";
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
    renderer.toneMappingExposure = 1.15;

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(36, 1, 0.1, 60);
    camera.position.set(0, 0.1, 8);
    camera.lookAt(0, 0.1, 0);

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

    var head = new THREE.Group();
    scene.add(head);

    /* Rounded-rectangle helper used for the casing, bezel and side pods. */
    function roundedRect(w, h, r) {
      var shape = new THREE.Shape();
      var x = -w / 2;
      var y = -h / 2;
      shape.moveTo(x + r, y);
      shape.lineTo(x + w - r, y);
      shape.quadraticCurveTo(x + w, y, x + w, y + r);
      shape.lineTo(x + w, y + h - r);
      shape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
      shape.lineTo(x + r, y + h);
      shape.quadraticCurveTo(x, y + h, x, y + h - r);
      shape.lineTo(x, y + r);
      shape.quadraticCurveTo(x, y, x + r, y);
      return shape;
    }

    function makeGlowTexture() {
      var glowCanvas = document.createElement("canvas");
      glowCanvas.width = 64;
      glowCanvas.height = 64;
      var context = glowCanvas.getContext("2d");
      var gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
      gradient.addColorStop(0, "rgba(255,255,255,1)");
      gradient.addColorStop(0.35, "rgba(255,255,255,0.5)");
      gradient.addColorStop(1, "rgba(255,255,255,0)");
      context.fillStyle = gradient;
      context.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(glowCanvas);
    }

    var casingMat = new THREE.MeshPhysicalMaterial({
      color: palette.casing, roughness: 0.34, metalness: 0.04,
      clearcoat: 0.55, clearcoatRoughness: 0.3
    });
    var screwMat = new THREE.MeshPhysicalMaterial({
      color: palette.casing, roughness: 0.4, metalness: 0.5, clearcoat: 0.2
    });
    var screenMat = new THREE.MeshPhysicalMaterial({
      color: palette.display, roughness: 0.3, metalness: 0,
      clearcoat: 0.85, clearcoatRoughness: 0.2
    });
    var podMat = new THREE.MeshPhysicalMaterial({
      color: 0xede7da, roughness: 0.45, metalness: 0,
      clearcoat: 0.35, clearcoatRoughness: 0.3
    });
    var ringMat = new THREE.MeshBasicMaterial({ color: palette.glow });
    var orbMat = new THREE.MeshBasicMaterial({ color: palette.glow });

    var eyeColor = new THREE.Color(palette.eyes);
    var mouthColor = new THREE.Color(palette.mouth);
    var inactiveColor = new THREE.Color(palette.display);
    var whiteColor = new THREE.Color(0xffffff);

    function applyMaterials(values) {
      casingMat.color.set(values.casing);
      screwMat.color.set(values.casing).multiplyScalar(0.65);
      screenMat.color.set(values.display);
      ringMat.color.set(values.glow);
      eyeColor.set(values.eyes);
      mouthColor.set(values.mouth);
      inactiveColor.set(values.display).lerp(whiteColor, 0.12);
    }

    /* Casing body (rounded shell, flat front under the bezel). */
    var bodyGeo = new THREE.ExtrudeGeometry(roundedRect(3.3, 2.3, 0.5), {
      depth: 0.6, bevelEnabled: true, bevelThickness: 0.06,
      bevelSize: 0.06, bevelSegments: 3, curveSegments: 24
    });
    bodyGeo.center();
    head.add(new THREE.Mesh(bodyGeo, casingMat));

    /* Bezel frame with a rounded opening - its depth recesses the display. */
    var frameShape = roundedRect(3.3, 2.3, 0.5);
    frameShape.holes.push(roundedRect(2.6, 1.7, 0.3));
    var frameGeo = new THREE.ExtrudeGeometry(frameShape, {
      depth: 0.14, bevelEnabled: true, bevelThickness: 0.05,
      bevelSize: 0.05, bevelSegments: 3, curveSegments: 24
    });
    frameGeo.center();
    var bezel = new THREE.Mesh(frameGeo, casingMat);
    bezel.position.z = 0.34;
    head.add(bezel);

    /* Dark display plate (wider than the opening so no edge can show). */
    var screen = new THREE.Mesh(new THREE.PlaneGeometry(2.85, 1.95), screenMat);
    screen.position.z = 0.38;
    head.add(screen);

    /* Four corner screws on the bezel face. */
    [[1.38, 0.92], [-1.38, 0.92], [1.38, -0.92], [-1.38, -0.92]].forEach(function (pos) {
      var screw = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.05, 18), screwMat);
      screw.rotation.x = Math.PI / 2;
      screw.position.set(pos[0], pos[1], 0.475);
      head.add(screw);
    });

    /* Side pods with glowing rings. */
    [-1, 1].forEach(function (sign) {
      var podGeo = new THREE.ExtrudeGeometry(roundedRect(0.62, 0.86, 0.2), {
        depth: 0.3, bevelEnabled: true, bevelThickness: 0.05,
        bevelSize: 0.05, bevelSegments: 3, curveSegments: 16
      });
      podGeo.center();
      var pod = new THREE.Mesh(podGeo, podMat);
      pod.position.set(1.9 * sign, 0, 0);
      head.add(pod);
      var ring = new THREE.Mesh(new THREE.TorusGeometry(0.26, 0.04, 12, 48), ringMat);
      ring.position.set(1.9 * sign, 0, 0.205);
      head.add(ring);
    });

    /* Antenna: collar, stalk and a glowing orb that reports connectivity. */
    var collar = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.2, 0.12, 24), casingMat);
    collar.position.y = 1.13;
    head.add(collar);
    var stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.3, 12), screwMat);
    stalk.position.y = 1.32;
    head.add(stalk);
    var orb = new THREE.Mesh(new THREE.SphereGeometry(0.115, 24, 16), orbMat);
    orb.position.y = 1.52;
    head.add(orb);

    /* Dot-matrix pixels: instanced spheres plus additive glow halos.
       Every grid cell always renders as a faint dot; mood pixels lerp the
       eye/mouth cells toward their lit color and grow slightly. */
    var DOT_Z = 0.39;
    var HALO_Z = 0.405;
    var glowTexture = makeGlowTexture();

    var dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(0.055, 10, 8),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
      FACE_CELLS.length
    );
    dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    head.add(dots);

    var halos = new THREE.InstancedMesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({
        map: glowTexture, transparent: true, depthWrite: false,
        blending: THREE.AdditiveBlending
      }),
      FACE_CELLS.length
    );
    halos.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    head.add(halos);

    paletteListener = function (values) {
      applyMaterials(values);
    };
    applyMaterials(palette);

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
    var lastMouthPattern = EXPRESSIONS.IDLE.mouth;
    var gesture = {
      yaw: 0, pitch: 0, hoverYaw: 0, hoverPitch: 0,
      active: false, pointerId: null,
      x0: 0, y0: 0, t0: 0, moved: 0
    };
    var lookYaw = 0;
    var lookPitch = 0;
    var hoverYaw = 0;
    var hoverPitch = 0;
    var tapPulse = 0;

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
      /* Keep the whole head (incl. side pods) in frame: base framing for
         square-ish previews, width-fit for narrow portrait canvases, and a
         floor so wide layouts never crop the antenna vertically. */
      camera.position.z = Math.max(
        7.9 + Math.max(0, 1 - aspect) * 3.4,
        2.45 / (0.325 * Math.max(0.4, aspect)),
        5.9
      );
      camera.updateProjectionMatrix();
      if (reduced) renderer.render(scene, camera);
    }

    var resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
    resize();

    var dummy = new THREE.Object3D();
    var haloDummy = new THREE.Object3D();
    var cellColor = new THREE.Color();
    var groupColor = new THREE.Color();

    function frame(dt, t) {
      var mood = resolveMood(Date.now());
      applyMood(mood);
      var targets = moodTargets(mood);

      ["headYaw", "headPitch", "headRoll", "sway", "energy", "dim",
       "blink", "restless"].forEach(function (key) {
        damped(key, targets, dt, 6);
      });

      /* Blinking - closed eyes override the mood pattern mid-blink. */
      var blinkAmount = 0;
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
      if (blinkT >= 0) blinkAmount = Math.sin((blinkT / 0.15) * Math.PI);
      var eyesClosed = blinkAmount > 0.5;

      /* Eye saccades (searching pixels when restless). */
      if (!reduced) {
        saccTimer -= dt;
        if (saccTimer <= 0) {
          var ampX = Math.min(0.03, 0.016 * current.restless);
          var ampY = Math.min(0.026, 0.013 * current.restless);
          gaze.x = (Math.random() - 0.5) * 2 * ampX;
          gaze.y = (Math.random() - 0.5) * 2 * ampY;
          saccTimer = (0.7 + Math.random() * 1.9) / Math.max(0.3, current.restless);
        }
      } else {
        gaze.x = 0;
        gaze.y = 0;
      }

      /* Gesture easing - released drags settle back to the mood pose. */
      var ease = reduced ? 1 : 1 - Math.exp(-9 * dt);
      lookYaw += (gesture.yaw - lookYaw) * ease;
      lookPitch += (gesture.pitch - lookPitch) * ease;
      hoverYaw += (gesture.hoverYaw - hoverYaw) * ease;
      hoverPitch += (gesture.hoverPitch - hoverPitch) * ease;
      if (!reduced) tapPulse = Math.max(0, tapPulse - dt * 2);

      /* Head motion: mood pose + gesture + idle sway + dance + tap bounce. */
      var sway = reduced ? 0 : current.sway;
      var energy = reduced ? 0 : current.energy;
      var bounce = reduced ? 0 : Math.sin(tapPulse * Math.PI) * 0.07;
      head.rotation.y =
        current.headYaw + lookYaw + hoverYaw +
        Math.sin(t * 0.55) * sway * 0.6 + Math.sin(t * 5.4) * 0.24 * energy;
      head.rotation.x =
        current.headPitch + lookPitch + hoverPitch +
        Math.sin(t * 0.79 + 1.3) * sway * 0.35;
      head.rotation.z =
        current.headRoll + Math.sin(t * 0.47 + 2.1) * sway * 0.3 +
        Math.sin(t * 8.1) * 0.1 * energy;
      head.position.y = Math.sin(t * 7) * 0.045 * energy + bounce;
      head.scale.setScalar(1 + Math.sin(tapPulse * Math.PI) * 0.03);

      /* Dot-matrix pixels follow the mood expression, damped per pixel
         (instant under reduced motion, which renders on demand only). */
      var expr = expressionFor(mood);
      if (expr.mouth) lastMouthPattern = expr.mouth;
      var litEase = reduced ? 1 : 1 - Math.exp(-11 * dt);
      for (var i = 0; i < FACE_CELLS.length; i++) {
        var cell = FACE_CELLS[i];
        var lit = 0;
        if (cell.group === "eye") {
          var pattern = eyesClosed ? EXPRESSIONS.BLINK.L :
            (cell.side === "L" ? expr.L : expr.R);
          lit = pattern[cell.pr].charAt(cell.pc) === "X" ? 1 : 0;
        } else if (cell.group === "mouth") {
          lit = lastMouthPattern[cell.pr].charAt(cell.pc) === "X" ? 1 : 0;
        }
        cell.lit += (lit - cell.lit) * litEase;
        var offsetX = cell.group === "eye" ? gaze.x : 0;
        var offsetY = cell.group === "eye" ? gaze.y : 0;

        dummy.position.set(cell.x + offsetX, cell.y + offsetY, DOT_Z);
        dummy.scale.setScalar(1 + 0.22 * cell.lit);
        dummy.updateMatrix();
        dots.setMatrixAt(i, dummy.matrix);

        groupColor.copy(inactiveColor);
        if (cell.group === "eye") groupColor.lerp(eyeColor, cell.lit);
        else if (cell.group === "mouth") groupColor.lerp(mouthColor, cell.lit);
        cellColor.copy(groupColor).multiplyScalar(1 - 0.72 * current.dim);
        dots.setColorAt(i, cellColor);

        haloDummy.position.set(cell.x + offsetX, cell.y + offsetY, HALO_Z);
        haloDummy.scale.setScalar(cell.lit * 0.36 * (1 - 0.7 * current.dim));
        haloDummy.updateMatrix();
        halos.setMatrixAt(i, haloDummy.matrix);
        cellColor.copy(cell.group === "eye" ? eyeColor : mouthColor)
          .multiplyScalar(cell.lit * (1 - 0.65 * current.dim));
        halos.setColorAt(i, cellColor);
      }
      dots.instanceMatrix.needsUpdate = true;
      if (dots.instanceColor) dots.instanceColor.needsUpdate = true;
      halos.instanceMatrix.needsUpdate = true;
      if (halos.instanceColor) halos.instanceColor.needsUpdate = true;

      /* Antenna orb reports the REAL connectivity state (hue = glow color). */
      var online = state.online === true;
      var pulse = reduced ? 1 : 0.55 + 0.45 * Math.sin(t * 2.4);
      orbMat.color.set(palette.glow).multiplyScalar(online ? 1 : pulse);
      orb.scale.setScalar(online ? 1 : 0.9 + 0.1 * pulse);

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

    /* ---------- pointer gestures: drag to turn, tap to react ---------- */
    var MAX_YAW = 0.55;
    var MAX_PITCH = 0.35;
    var HOVER_YAW = 0.14;
    var HOVER_PITCH = 0.1;
    var TAP_MOVE_LIMIT = 8;
    var TAP_TIME_LIMIT = 400;

    function clamp(value, min, max) {
      return value < min ? min : value > max ? max : value;
    }

    function onPointerDown(event) {
      if (!event.isPrimary || disposed) return;
      gesture.active = true;
      gesture.pointerId = event.pointerId;
      gesture.x0 = event.clientX;
      gesture.y0 = event.clientY;
      gesture.t0 = Date.now();
      gesture.moved = 0;
      gesture.hoverYaw = 0;
      gesture.hoverPitch = 0;
      try {
        container.setPointerCapture(event.pointerId);
      } catch (error) {
        /* Pointer capture is an enhancement - dragging still works. */
      }
    }

    function onPointerMove(event) {
      if (disposed) return;
      if (gesture.active && event.pointerId === gesture.pointerId) {
        var dx = event.clientX - gesture.x0;
        var dy = event.clientY - gesture.y0;
        gesture.moved = Math.max(gesture.moved, Math.abs(dx) + Math.abs(dy));
        gesture.yaw = clamp(dx * 0.004, -MAX_YAW, MAX_YAW);
        gesture.pitch = clamp(-dy * 0.004, -MAX_PITCH, MAX_PITCH);
        wake();
      } else if (!gesture.active && event.pointerType === "mouse") {
        var rect = container.getBoundingClientRect();
        var nx = (event.clientX - rect.left) / Math.max(1, rect.width) - 0.5;
        var ny = (event.clientY - rect.top) / Math.max(1, rect.height) - 0.5;
        gesture.hoverYaw = clamp(nx * 2 * HOVER_YAW, -HOVER_YAW, HOVER_YAW);
        gesture.hoverPitch = clamp(-ny * 2 * HOVER_PITCH, -HOVER_PITCH, HOVER_PITCH);
        wake();
      }
    }

    function triggerExcited() {
      transient.mood = "EXCITED";
      transient.until = Date.now() + 1500;
      if (!reduced) tapPulse = 1;
      wake();
    }

    function onPointerEnd(event) {
      if (!gesture.active || disposed) return;
      if (event && event.pointerId !== gesture.pointerId) return;
      gesture.active = false;
      var quick = Date.now() - gesture.t0 < TAP_TIME_LIMIT;
      var still = gesture.moved < TAP_MOVE_LIMIT;
      gesture.yaw = 0;
      gesture.pitch = 0;
      if (quick && still) triggerExcited();
      wake();
    }

    /* A cancelled gesture (browser takeover, palm rejection, etc.) only
       settles the head back - it must never count as a tap reaction. */
    function onPointerCancel(event) {
      if (!gesture.active || disposed) return;
      if (event && event.pointerId !== gesture.pointerId) return;
      gesture.active = false;
      gesture.yaw = 0;
      gesture.pitch = 0;
      wake();
    }

    function onPointerLeave() {
      gesture.hoverYaw = 0;
      gesture.hoverPitch = 0;
      wake();
    }

    /* Assign (do not redeclare) the outer wake binding so the document
       listeners registered in initFace call this implementation - a nested
       wake declaration would shadow the stub and reduced-motion users would
       never see event-driven mood updates. */
    wake = function () {
      if (!running && !disposed) startLoop();
      if (reduced) frame(0.016, performance.now() / 1000);
    };

    container.addEventListener("pointerdown", onPointerDown);
    container.addEventListener("pointermove", onPointerMove);
    container.addEventListener("pointerup", onPointerEnd);
    container.addEventListener("pointercancel", onPointerCancel);
    container.addEventListener("pointerleave", onPointerLeave);

    window.addEventListener("pagehide", function () {
      disposed = true;
      renderer.setAnimationLoop(null);
      resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      container.removeEventListener("pointerdown", onPointerDown);
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerup", onPointerEnd);
      container.removeEventListener("pointercancel", onPointerCancel);
      container.removeEventListener("pointerleave", onPointerLeave);
      renderer.dispose();
    });

    startLoop();
    wake();
  }
}
