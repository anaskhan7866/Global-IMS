/**
 * Tailwind build configuration.
 *
 * The site's CSS is pre-built ONCE into static/css/app.css (small, cached, instant) instead of
 * being generated in every visitor's browser by the Tailwind CDN script.
 *
 * After changing classes in any template, rebuild with:   npm run build:css
 * (or keep it rebuilding automatically while you work:   npm run watch:css)
 *
 * Light / dark mode
 * -----------------
 * The templates are written once with dark-theme classes (bg-slate-950, text-white, ...).
 * Every colour below is a CSS variable instead of a fixed value:
 *   - dark mode (default) uses Tailwind's normal colours, so nothing changes;
 *   - light mode (<html data-theme="light">) swaps the shades: dark backgrounds become light,
 *     light text becomes dark, and accent text is darkened for contrast on white.
 * So every page gets a light theme without duplicating classes in the templates.
 */
const plugin = require('tailwindcss/plugin');
const twColors = require('tailwindcss/colors');

const SHADES = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950'];
// Neutral greys: mirror the whole scale (950 <-> 50, 800 <-> 200, 400 <-> 600, ...).
const NEUTRALS = ['slate', 'gray'];
// Accent colours: tinted dark backgrounds become tinted light ones, pale text becomes deep text,
// and the solid button shades (500-700) stay as they are.
const ACCENTS = ['cyan', 'blue', 'emerald', 'rose', 'amber', 'red', 'purple', 'teal', 'green', 'indigo', 'orange', 'yellow', 'sky', 'violet', 'pink'];
const NEUTRAL_LIGHT = { 50: '950', 100: '900', 200: '800', 300: '700', 400: '600', 500: '500', 600: '400', 700: '300', 800: '200', 900: '100', 950: '50' };
const ACCENT_LIGHT = { 50: '950', 100: '900', 200: '800', 300: '800', 400: '700', 500: '500', 600: '600', 700: '700', 800: '200', 900: '100', 950: '50' };
const SOLID_SHADES = ['500', '600', '700'];

const rgb = (hex) => {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).join(' ');
};
const ref = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

const colors = { transparent: 'transparent', current: 'currentColor', black: '#000', white: ref('white') };
const darkVars = { '--c-white': '255 255 255' };
const lightVars = { '--c-white': rgb(twColors.slate[900]) };

for (const name of [...NEUTRALS, ...ACCENTS]) {
  const map = NEUTRALS.includes(name) ? NEUTRAL_LIGHT : ACCENT_LIGHT;
  colors[name] = {};
  for (const shade of SHADES) {
    colors[name][shade] = ref(`${name}-${shade}`);
    darkVars[`--c-${name}-${shade}`] = rgb(twColors[name][shade]);
    lightVars[`--c-${name}-${shade}`] = rgb(twColors[name][map[shade]]);
  }
}

// In light mode, "text-white" means dark text - except on solid coloured buttons/badges/gradients,
// where it must stay white. Exact class tokens only (bg-cyan-600, not bg-cyan-600/20 or hover:...).
const solidSelectors = [];
for (const name of ACCENTS) {
  for (const shade of SOLID_SHADES) {
    solidSelectors.push(`[class~="bg-${name}-${shade}"]`, `[class~="from-${name}-${shade}"]`);
  }
}

const themePlugin = plugin(({ addBase }) => {
  addBase({
    ':root': { ...darkVars, colorScheme: 'dark' },
    ':root[data-theme="light"]': { ...lightVars, colorScheme: 'light' },
    [`:root[data-theme="light"] :is(${solidSelectors.join(',')})`]: { '--c-white': '255 255 255' },
  });
});

module.exports = {
  content: [
    './templates/**/*.html',
    './dashboard/templates/**/*.html',
    './inventory/**/*.py',
    './transactions/**/*.py',
    './dashboard/**/*.py',
  ],
  theme: {
    colors,
    extend: {
      fontFamily: {
        // Built-in system fonts: nothing to download, text renders immediately.
        sans: ['system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', '"Cascadia Mono"', '"SF Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
    },
  },
  plugins: [themePlugin],
};
