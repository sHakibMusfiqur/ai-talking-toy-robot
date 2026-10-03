/** Tailwind CSS v3 config — scans Django templates + panel JS only.
 *  No React/Vue/Vite: the CLI writes straight into backend/static/css/. */
module.exports = {
  content: [
    "backend/templates/**/*.html",
    "backend/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        crimson: {
          DEFAULT: "#C41E3A",
          hover: "#A81831",
        },
        base: "#0B0B0D",
        surface: "#141417",
        raised: "#1B1B20",
        line: "#2A2A31",
        "line-strong": "#3A3A44",
        body: "#F5F5F7",
        muted: "#A1A1AA",
        faint: "#71717A",
        ok: "#22C55E",
        warn: "#F59E0B",
        bad: "#EF4444",
        info: "#38BDF8",
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
        "crimson-glow": "0 0 28px -6px rgba(196, 30, 58, 0.45)",
      },
    },
  },
  plugins: [],
};
