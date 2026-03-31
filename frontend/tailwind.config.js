export default {
  content: ['./src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          950: '#0f1117',
          900: '#1a1b23',
          800: '#22232e',
          750: '#2a2b38',
          700: '#2e2f3e',
          600: '#3a3b4e',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      }
    }
  },
  plugins: []
}
