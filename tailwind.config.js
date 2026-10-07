/** Tailwind CSS v3 config — scans Django templates + panel JS only.
 *  No React/Vue/Vite: the CLI writes straight into backend/static/css/.
 *  Palette matches the approved RoboPanel reference screenshot. */
module.exports = {
  content: [
    "backend/templates/**/*.html",
    "backend/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        crimson: {
          DEFAULT: "#E9003F",
          hover: "#FF315C",
          light: "#FF315C",
          deep: "#B80033",
        },
        base: "#08090D",
        shell: "#0D0F16",
        surface: "#141720",
        raised: "#181B24",
        line: "#FFFFFF14",
        "line-strong": "#FFFFFF24",
        body: "#F5F7FA",
        muted: "#A1A7B3",
        faint: "#6E7684",
        ok: "#00D084",
        warn: "#F59E0B",
        bad: "#EF4444",
        info: "#3B82F6",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          '"SF Mono"',
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        panel: "0 10px 40px -12px rgba(0, 0, 0, 0.65)",
        "crimson-glow": "0 0 28px -6px rgba(233, 0, 63, 0.5)",
        "red-ambient":
          "0 24px 60px -28px rgba(233, 0, 63, 0.35), 0 18px 40px -24px rgba(0, 0, 0, 0.8)",
      },
    },
  },
  plugins: [],
};
