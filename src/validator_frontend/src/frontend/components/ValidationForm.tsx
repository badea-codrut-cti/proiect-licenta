import { html } from 'hono/html';

interface CropData {
  cropTop: number | null;
  cropLeft: number | null;
  cropWidth: number | null;
  cropHeight: number | null;
}

interface ImageData {
  id: number;
  link: string;
  aiDescription: string;
  firstValidatorModifications: string | null;
  cropTop: number | null;
  cropLeft: number | null;
  cropWidth: number | null;
  cropHeight: number | null;
}

interface ValidationFormProps {
  image: ImageData;
}

export function ValidationForm({ image }: ValidationFormProps) {
  const description = image.firstValidatorModifications || image.aiDescription;

  // Use crop data from image props to reconstruct cropped URL
  const cropTop = image.cropTop;
  const cropLeft = image.cropLeft;
  const cropWidth = image.cropWidth;
  const cropHeight = image.cropHeight;

  let imageUrl = image.link;
  try {
    const baseUrl = new URL(image.link);
    // Strip existing Mathpix crop params
    baseUrl.searchParams.delete('height');
    baseUrl.searchParams.delete('width');
    baseUrl.searchParams.delete('top_left_y');
    baseUrl.searchParams.delete('top_left_x');
    
    // If we have stored crop data, apply it (Mathpix will crop to our specs)
    if (cropTop != null && cropLeft != null && cropWidth != null && cropHeight != null) {
      baseUrl.searchParams.set('top_left_y', String(cropTop));
      baseUrl.searchParams.set('top_left_x', String(cropLeft));
      baseUrl.searchParams.set('width', String(cropWidth));
      baseUrl.searchParams.set('height', String(cropHeight));
    }
    // Otherwise leave it as full original (no params)
    
    imageUrl = '/validate/image-proxy?url=' + encodeURIComponent(baseUrl.toString());
  } catch {
    imageUrl = image.link;
  }

  return html`
    <div class="grid md:grid-cols-2 gap-6">
      <div class="bg-white rounded-lg shadow p-4">
        <div class="flex justify-between items-center mb-4">
          <h3 class="font-bold">Imagine</h3>
          <button
            type="button"
            id="recropBtn"
            class="text-sm bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded"
          data-image-id="${image.id}"
          data-crop-top="${cropTop ?? ''}"
          data-crop-left="${cropLeft ?? ''}"
          data-crop-width="${cropWidth ?? ''}"
          data-crop-height="${cropHeight ?? ''}"
          data-original-src="${(() => {
            try {
              const u = new URL(image.link);
              u.searchParams.delete('height');
              u.searchParams.delete('width');
              u.searchParams.delete('top_left_y');
              u.searchParams.delete('top_left_x');
              return u.toString();
            } catch {
              return image.link;
            }
          })()}"
          >
            🔄 Recrop
          </button>
        </div>
        <div id="imageContainer">
          <img id="mainImage" src="${imageUrl}" alt="Diagrama" class="w-full border rounded-lg" />
        </div>
        <div id="cropInfo" class="mt-2 text-sm text-gray-500 hidden">
          Crop: <span id="cropData"></span>
        </div>
      </div>

      <div class="bg-white rounded-lg shadow p-4">
        <h3 class="font-bold mb-2">Descriere CDL</h3>
        <textarea
          id="descriptionEditor"
          name="modifications"
          rows="12"
          class="w-full px-4 py-2 border rounded-lg font-mono text-sm"
          autocomplete="off"
        >${description}</textarea>
      </div>
    </div>

    <div id="cropModal" class="fixed inset-0 bg-black/50 z-50 hidden flex items-center justify-center p-4">
      <div class="bg-white rounded-lg shadow-lg max-w-4xl w-full max-h-[90vh] overflow-auto">
        <div class="flex justify-between items-center p-4 border-b">
          <h3 class="font-bold">Recrop Imagine</h3>
          <button type="button" id="closeCropBtn" class="text-gray-500 hover:text-gray-700">✕</button>
        </div>
        <div class="p-4">
          <img id="cropTarget" src="" alt="Crop target" class="max-w-full block" />
        </div>
        <div class="p-4 border-t flex gap-4">
          <button type="button" id="saveCropBtn" class="flex-1 bg-blue-600 text-white py-2 rounded hover:bg-blue-700">
            Salvează crop
          </button>
          <button type="button" id="cancelCropBtn" class="flex-1 bg-gray-300 text-gray-700 py-2 rounded hover:bg-gray-400">
            Anulează
          </button>
        </div>
      </div>
    </div>
  `;
}
