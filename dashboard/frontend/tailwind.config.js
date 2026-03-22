/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-base': '#0a0a0b',
        'bg-surface': '#111113',
        'bg-elevated': '#18181b',
        'border-subtle': '#27272a',
        'border-active': '#3f3f46',
        'text-primary': '#fafafa',
        'text-secondary': '#a1a1aa',
        'text-tertiary': '#52525b',
        'accent-green': '#22c55e',
        'accent-red': '#ef4444',
        'accent-amber': '#f59e0b',
        'accent-blue': '#3b82f6',
        'accent-purple': '#a855f7',
      },
      fontFamily: {
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
