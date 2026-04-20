#!/usr/bin/env bun
/**
 * Script to extract crop parameters from existing image URLs and populate the database.
 * Also updates image links to use standard format without inline crop params.
 * 
 * Run with: bun scripts/extract-crop-data.ts
 */

import { createDb, schema } from '../backend/schema';

interface CropData {
  top_left_x: number;
  top_left_y: number;
  width: number;
  height: number;
}

interface ImageRow {
  id: number;
  link: string;
  crop_top: number | null;
  crop_left: number | null;
  crop_width: number | null;
  crop_height: number | null;
}

function parseCropFromUrl(url: string): CropData | null {
  try {
    const urlObj = new URL(url);
    const height = urlObj.searchParams.get('height');
    const width = urlObj.searchParams.get('width');
    const top_left_y = urlObj.searchParams.get('top_left_y');
    const top_left_x = urlObj.searchParams.get('top_left_x');

    if (height && width && top_left_y && top_left_x) {
      return {
        top_left_x: parseInt(top_left_x, 10),
        top_left_y: parseInt(top_left_y, 10),
        width: parseInt(width, 10),
        height: parseInt(height, 10),
      };
    }
  } catch {
    // Invalid URL
  }
  return null;
}

function stripCropFromUrl(url: string): string {
  try {
    const urlObj = new URL(url);
    urlObj.searchParams.delete('height');
    urlObj.searchParams.delete('width');
    urlObj.searchParams.delete('top_left_y');
    urlObj.searchParams.delete('top_left_x');
    return urlObj.toString();
  } catch {
    return url;
  }
}

async function main() {
  const env = process.env as { DB: string };
  
  if (!env.DB) {
    console.error('DB environment variable not set');
    process.exit(1);
  }

  const db = createDb(env.DB);

  // Get all images
  const allImages = await db.select().from(schema.images).all() as ImageRow[];

  console.log(`Found ${allImages.length} images`);

  let updated = 0;
  let skipped = 0;

  for (const image of allImages) {
    const cropData = parseCropFromUrl(image.link);

    if (cropData) {
      // Strip crop params from URL and store them separately
      const cleanUrl = stripCropFromUrl(image.link);

      await db
        .update(schema.images)
        .set({
          link: cleanUrl,
          cropTop: cropData.top_left_y,
          cropLeft: cropData.top_left_x,
          cropWidth: cropData.width,
          cropHeight: cropData.height,
        })
        .where((row) => row.id.equals(image.id));

      console.log(`Image ${image.id}: Extracted crop data and cleaned URL`);
      updated++;
    } else {
      console.log(`Image ${image.id}: No crop data found in URL (${image.link})`);
      skipped++;
    }
  }

  console.log(`\nDone! Updated: ${updated}, Skipped (no crop data): ${skipped}`);
}

main().catch(console.error);
