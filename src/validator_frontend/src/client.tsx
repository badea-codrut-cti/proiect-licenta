import { render } from 'hono/jsx/dom'
import { ValidationForm } from './frontend/components/ValidationForm'
import './style.css'
import 'cropperjs/dist/cropper.css'

const root = document.getElementById('validation-root')
if (root) {
  const imageData = JSON.parse(root.dataset.image || '{}')
  render(<ValidationForm image={imageData} />, root)
}
