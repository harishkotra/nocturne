/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b0d14",
        panel: "#121623",
        edge: "#1f2739",
        dim: "#8892a8",
        moon: "#c9b8ff",
        keep: "#5ec8a8",
        change: "#e0b352",
        gone: "#e06c75",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
