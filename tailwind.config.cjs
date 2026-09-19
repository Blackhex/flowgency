module.exports = {
  content: [
    './flowgency/templates/**/*.html',
    './flowgency/static/**/*.js',
    './flowgency/**/*.py',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      screens: { '3xl': '1920px' },
      colors: {
        flowgency: {
          50: '#f0f4ff', 100: '#dbe4ff', 200: '#bac8ff',
          500: '#5c7cfa', 600: '#4263eb', 700: '#3b5bdb',
          800: '#364fc7', 900: '#1e2a5e', 950: '#141b3d',
        },
      },
    },
  },
};
