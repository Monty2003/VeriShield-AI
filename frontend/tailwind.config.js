/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        // Named by MEANING, not by hue. A signal should never get the wrong
        // colour because someone reached for "green" out of habit.
        verdict: {
          accept: "#00ff9f", // Cyber neon green
          review: "#ffb800", // Cyber amber
          reject: "#ff003c", // Cyber neon red
          unknown: "#64748b",
        },
        ink: {
          900: "#050b14", // Deep void
          800: "#0b1320",
          700: "#132035",
          600: "#1e334f",
          500: "#2a4a73",
        },
        cyber: {
          // Reads a CSS variable so the accent can actually be changed at
          // runtime. <alpha-value> keeps every "/30" opacity modifier working.
          cyan: "rgb(var(--accent) / <alpha-value>)",
          magenta: "#ff00ff",
          purple: "#7a04eb",
        }
      },
      fontFamily: {
        mono: ["Fira Code", "JetBrains Mono", "ui-monospace", "monospace"],
        sans: ["Rajdhani", "Inter", "system-ui", "sans-serif"],
        display: ["Orbitron", "sans-serif"],
      },
      animation: {
        "fade-up": "fadeUp 0.4s ease-out",
        "pulse-ring": "pulseRing 2s cubic-bezier(0.4,0,0.6,1) infinite",
        "glitch": "glitch 2.5s infinite",
        "scanline": "scanline 8s linear infinite",
        "pulse-glow": "pulseGlow 2s ease-in-out infinite",
        "spin-slow": "spin 6s linear infinite",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseRing: {
          "0%,100%": { opacity: "1", transform: "scale(1)", filter: "drop-shadow(0 0 4px rgba(0, 240, 255, 0.5))" },
          "50%": { opacity: "0.4", transform: "scale(1.05)", filter: "drop-shadow(0 0 10px rgba(0, 240, 255, 0.8))" },
        },
        glitch: {
          "0%, 100%": { clipPath: "inset(0 0 0 0)" },
          "20%": { clipPath: "inset(20% 0 80% 0)", transform: "translate(-2px, 2px)" },
          "40%": { clipPath: "inset(60% 0 10% 0)", transform: "translate(2px, -2px)" },
          "60%": { clipPath: "inset(40% 0 50% 0)", transform: "translate(-2px, -2px)" },
          "80%": { clipPath: "inset(80% 0 5% 0)", transform: "translate(2px, 2px)" },
        },
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100%)" },
        },
        pulseGlow: {
          "0%, 100%": { opacity: "0.5", filter: "brightness(1)" },
          "50%": { opacity: "1", filter: "brightness(1.5)" },
        },
      },
    },
  },
  plugins: [],
};
