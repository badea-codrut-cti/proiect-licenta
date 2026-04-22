import { jsxRenderer } from 'hono/jsx-renderer'

export const renderer = jsxRenderer(({ children }) => {
  return (
    <html lang="ro">
      <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <link href="/assets/style.css" rel="stylesheet" />
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cropperjs@2/dist/cropper.css" />
        <script src="https://cdn.jsdelivr.net/npm/cropperjs@2"></script>
      </head>
      <body>{children}</body>
    </html>
  )
})
