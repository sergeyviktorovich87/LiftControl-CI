// Resize on selection so the displayed attachment size is the transmitted size.
// Sequential files keep memory bounded on phones; one encode at most per image.
export async function compressImage(file) {
  if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) return file;
  if (file.size <= 300 * 1024) return file;
  if (file.size > 50 * 1024 * 1024) throw Error('Фотография слишком большая. Выберите файл до 50 МБ.');
  let bitmap, url;
  try {
    if (typeof createImageBitmap === 'function') {
      bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
    } else {
      url = URL.createObjectURL(file);
      bitmap = new Image();
      bitmap.src = url;
      await bitmap.decode();
    }
    const width = bitmap.width || bitmap.naturalWidth, height = bitmap.height || bitmap.naturalHeight;
    if (!width || !height || width * height > 100 * 1000 * 1000) throw Error('IMAGE_DIMENSIONS');
    const scale = Math.min(1, 1920 / Math.max(width, height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d', {alpha: false});
    if (!context) throw Error('IMAGE_CANVAS');
    context.fillStyle = '#fff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .78));
    // Never inflate an already compact image.
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', {type:'image/jpeg', lastModified:file.lastModified});
  } catch {
    throw Error('Не удалось подготовить фотографию. Выберите её повторно.');
  } finally {
    bitmap?.close?.();
    if (url) URL.revokeObjectURL(url);
  }
}

export async function prepareImages(files) {
  const result = [];
  for (const file of files) result.push(await compressImage(file));
  return result;
}
