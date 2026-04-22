import { useState, useEffect, useRef, useCallback } from 'hono/jsx'
import Cropper from 'cropperjs'

interface ImageData {
  id: number;
  link: string;
  aiDescription: string;
  cropTop: number | null;
  cropLeft: number | null;
  cropWidth: number | null;
  cropHeight: number | null;
}

interface ValidationFormProps {
  image: ImageData;
}

function stripCropParams(url: string): string {
  try {
    const u = new URL(url);
    u.searchParams.delete('height');
    u.searchParams.delete('width');
    u.searchParams.delete('top_left_y');
    u.searchParams.delete('top_left_x');
    return u.toString();
  } catch {
    return url;
  }
}

export function ValidationForm({ image }: ValidationFormProps) {
  const [description, setDescription] = useState(image.aiDescription);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const cropperRef = useRef<Cropper | null>(null);

  const proxiedUrl = '/validate/image-proxy?url=' + encodeURIComponent(stripCropParams(image.link));
  const hasInitialCrop = image.cropWidth != null && image.cropHeight != null;

  // Initialize cropperjs on mount
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;

    const cropper = new Cropper(img, {
      viewMode: 1,
      dragMode: 'move',
      autoCropArea: hasInitialCrop ? 1 : 0.8,
      guides: true,
      background: true,
      data: hasInitialCrop ? {
        x: image.cropLeft!,
        y: image.cropTop!,
        width: image.cropWidth!,
        height: image.cropHeight!,
      } : undefined,
    });

    cropperRef.current = cropper;
    return () => {
      cropper.destroy();
      cropperRef.current = null;
    };
  }, [proxiedUrl, hasInitialCrop, image.cropLeft, image.cropTop, image.cropWidth, image.cropHeight]);

  const submitValidation = useCallback(async (isApproval: boolean) => {
    if (!isApproval && (!description || !description.trim())) {
      alert('Trebuie să completezi descrierea CDL.');
      return;
    }

    const cropper = cropperRef.current;
    if (cropper) {
      const data = cropper.getData(true);
      try {
        await fetch('/validate/crop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            imageId: image.id,
            cropTop: data.y,
            cropLeft: data.x,
            cropWidth: data.width,
            cropHeight: data.height,
          }),
        });
      } catch (e) {
        console.error('Failed to save crop:', e);
      }
    }

    try {
      const response = await fetch('/validate/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageId: image.id, approved: isApproval, modifications: description }),
      });
      if (response.ok) {
        location.reload();
      } else {
        alert('Eroare la trimitere. Te rog să încerci din nou.');
      }
    } catch {
      alert('Eroare de conexiune.');
    }
  }, [image.id, description]);

  return (
    <div class="flex flex-col gap-6">
      {/* Image panel */}
      <div class="bg-white rounded-lg shadow p-4 max-w-5xl mx-auto w-full">
        <h3 class="font-bold mb-4">Imagine</h3>
        <div style="height: 65vh;" class="bg-gray-50 flex items-center justify-center rounded">
          <img
            ref={imgRef}
            src={proxiedUrl}
            crossorigin="anonymous"
            alt="Diagrama"
            style="display: block; max-width: 100%; max-height: 100%;"
          />
        </div>
      </div>

      {/* Description panel */}
      <div class="bg-white rounded-lg shadow p-4 flex flex-col">
        <h3 class="font-bold mb-2">Descriere CDL</h3>
        <textarea
          id="descriptionEditor"
          rows={14}
          class="w-full flex-1 px-4 py-2 border rounded-lg font-mono text-sm"
          autocomplete="off"
          value={description}
          onInput={(e: any) => setDescription(e.target.value)}
        />
      </div>

      {/* Action buttons */}
      <div class="bg-white rounded-lg shadow p-6">
        <div class="flex flex-wrap gap-4">
          <button
            type="button"
            onClick={() => submitValidation(true)}
            class="flex-1 min-w-48 bg-green-600 text-white py-3 px-6 rounded-lg hover:bg-green-700 transition"
          >
            ✓ Aprobat
          </button>
          <button
            type="button"
            onClick={() => submitValidation(false)}
            class="flex-1 min-w-48 bg-yellow-600 text-white py-3 px-6 rounded-lg hover:bg-yellow-700 transition"
          >
            ⚠ Corectat
          </button>
        </div>
      </div>
    </div>
  );
}
