import { useState, useEffect, useRef, useCallback } from 'hono/jsx'
import Cropper from 'cropperjs'

interface ImageData {
  id: number;
  problemId: number;
  cerinta: string;
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
  const [cerinta, setCerinta] = useState(image.cerinta);
  const [isEditingCerinta, setIsEditingCerinta] = useState(false);
  
  const imgRef = useRef<HTMLImageElement | null>(null);
  const cropperRef = useRef<Cropper | null>(null);
  const cerintaRef = useRef<HTMLDivElement | null>(null);
  const previewContainerRef = useRef<HTMLDivElement | null>(null);

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
      preview: previewContainerRef.current,
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

  // Trigger MathJax render when not editing
  useEffect(() => {
    if (!isEditingCerinta && cerintaRef.current && (window as any).MathJax) {
      // Clear previous MathJax output if any
      if ((window as any).MathJax.typesetClear) {
        (window as any).MathJax.typesetClear([cerintaRef.current]);
      }
      (window as any).MathJax.typesetPromise([cerintaRef.current]).catch((err: any) => console.log('MathJax error:', err));
    }
  }, [cerinta, isEditingCerinta]);

  const submitValidation = useCallback(async () => {
    if (!description || !description.trim()) {
      alert('Trebuie să completezi descrierea CDL.');
      return;
    }

    const cropper = cropperRef.current;
    let cropData: Cropper.Data | null = null;
    let hasCropChanged = false;

    if (cropper) {
      cropData = cropper.getData(true);
      if (
        cropData.x !== image.cropLeft ||
        cropData.y !== image.cropTop ||
        cropData.width !== image.cropWidth ||
        cropData.height !== image.cropHeight
      ) {
        hasCropChanged = true;
      }
    }

    const hasCerintaChanged = cerinta !== image.cerinta;
    const hasDescriptionChanged = description !== image.aiDescription;

    // If anything changed, it counts as a correction (approved = false) internally.
    const isApproval = !(hasCropChanged || hasCerintaChanged || hasDescriptionChanged);

    try {
      const response = await fetch('/validate/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          imageId: image.id, 
          problemId: image.problemId,
          approved: isApproval, 
          modifications: description,
          cerinta,
          cropTop: cropData?.y ?? null,
          cropLeft: cropData?.x ?? null,
          cropWidth: cropData?.width ?? null,
          cropHeight: cropData?.height ?? null,
        }),
      });
      if (response.ok) {
        location.reload();
      } else {
        alert('Eroare la trimitere. Te rog să încerci din nou.');
      }
    } catch {
      alert('Eroare de conexiune.');
    }
  }, [image, description, cerinta]);

  return (
    <div class="flex flex-col gap-6">
      {/* Cerinta panel */}

      {/* Image panel */}
      <div class="bg-white rounded-lg shadow p-4 max-w-6xl mx-auto w-full">
        <h3 class="font-bold mb-4">Imagine</h3>
        <div class="flex flex-col lg:flex-row gap-4">
          <div style="height: 65vh;" class="bg-gray-50 flex items-center justify-center rounded flex-1">
            <img
              ref={imgRef}
              src={proxiedUrl}
              crossorigin="anonymous"
              alt="Diagrama"
              style="display: block; max-width: 100%; max-height: 100%;"
            />
          </div>
          {/* Cropper.js built-in preview */}
          <div class="flex flex-col items-center justify-end">
            <h4 class="text-sm font-semibold text-gray-600 mb-2">Previzualizare Selectare</h4>
            <div 
              ref={previewContainerRef}
              class="w-80 h-80 border-2 border-blue-400 rounded-lg overflow-hidden bg-gray-100 shadow-inner"
            />
          </div>
        </div>
      </div>

      <div class="bg-yellow-100 border border-yellow-400 rounded-lg p-4">
        <h3 class="font-bold mb-2">Cerinţă:</h3>
        
        {isEditingCerinta ? (
          <textarea
            autoFocus
            rows={4}
            class="w-full bg-white border border-yellow-400 rounded p-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-yellow-500"
            value={cerinta}
            onInput={(e: any) => setCerinta(e.target.value)}
            onBlur={() => setIsEditingCerinta(false)}
          />
        ) : (
          <div 
            ref={cerintaRef} 
            class="w-full min-h-[4rem] text-sm text-gray-800 cursor-text py-2" 
            onClick={() => setIsEditingCerinta(true)}
          >
            {cerinta}
          </div>
        )}
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
            onClick={submitValidation}
            class="w-full bg-blue-600 text-white py-3 px-6 rounded-lg hover:bg-blue-700 transition font-bold text-lg"
          >
            Salvează și Continuă
          </button>
        </div>
      </div>
    </div>
  );
}
