/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./control_center/templates/**/*.html",
    "./control_center/static/js/**/*.js"
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"]
      },
      boxShadow: {
        panel: "0 18px 50px rgba(2, 8, 23, 0.12)"
      }
    }
  },
  plugins: []
};
