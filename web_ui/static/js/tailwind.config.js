/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ["web_ui/templates/**/*.html"],
    theme: {
        extend: {
            colors: {
                primary: { 50: '#eff6ff', 100: '#dbeafe', 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8' },
                accent: { 400: '#e879f9', 500: '#d946ef', 600: '#c026d3' }
            }
        }
    }
}
