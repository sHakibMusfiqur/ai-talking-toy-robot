module.exports = {
  content: [
    "backend/templates/**/*.html",
    "backend/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        /* Champagne gold — the single restrained accent */
        gold: {
          DEFAULT: "#C9A96A",
          hover: "#D9BD84",
          light: "#E8D5A9",
          deep: "#A88B4C",
          muted: "#8A7449",
        },
        /* Deep graphite / charcoal surfaces */
        base: "#0C0D10",
        shell: "#131419",
        surface: "#1A1C22",
        raised: "#23252D",
        line: "#FFFFFF12",
        "line-strong": "#FFFFFF28",
        /* Warm ivory text + warm neutral grays */
        body: "#F4F0E7",
        muted: "#ABA79C",
        faint: "#77746C",
        /* Functional status (kept) */
        ok: "#00D084",
        warn: "#F59E0B",
        bad: "#EF4444",
        info: "#3B82F6",
        /* Warm ivory for light-on-dark contrast surfaces */
        ivory: {
          DEFAULT: "#F7F3EA",
          deep: "#EDE7DA",
        },
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
        /* Elegant display serif for headings (local stack, offline-safe) */
        display: [
          '"Iowan Old Style"',
          '"Palatino Linotype"',
          "Palatino",
          '"Book Antiqua"',
          "Georgia",
          "serif",
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
        "gold-glow": "0 0 26px -8px rgba(201, 169, 106, 0.4)",
        "gold-ambient":
          "0 24px 60px -28px rgba(201, 169, 106, 0.28), 0 18px 40px -24px rgba(0, 0, 0, 0.8)",
      },
    },
  },
  plugins: [],
};
