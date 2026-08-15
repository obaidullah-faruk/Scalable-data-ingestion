export const MAX_UPLOAD_SIZE_BYTES = Number(
  process.env.REACT_APP_MAX_UPLOAD_SIZE_BYTES || 5 * 1024 * 1024 * 1024,
);

export function validateCsvFile(file) {
  if (!file) {
    return 'Choose a CSV file before starting the upload.';
  }
  if (!file.name.toLowerCase().endsWith('.csv')) {
    return 'Only files ending in .csv are accepted.';
  }
  if (file.size === 0) {
    return 'The selected CSV file is empty.';
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return `The selected file exceeds the ${formatBytes(MAX_UPLOAD_SIZE_BYTES)} limit.`;
  }
  return '';
}

export function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const unitIndex = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** unitIndex;
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

export function buildInitialParts(fileSize, partSize, totalParts) {
  return Object.fromEntries(
    Array.from({length: totalParts}, (_, index) => {
      const number = index + 1;
      const start = index * partSize;
      return [
        number,
        {
          number,
          size: Math.min(partSize, fileSize - start),
          status: 'waiting',
          etag: '',
          error: '',
          url: '',
        },
      ];
    }),
  );
}

export async function withConcurrency(items, limit, operation) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function worker() {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      results[currentIndex] = await operation(items[currentIndex]);
    }
  }

  await Promise.all(
    Array.from({length: Math.min(limit, items.length)}, () => worker()),
  );
  return results;
}

export async function uploadPart({
  file,
  partNumber,
  partSize,
  url,
  onChange,
}) {
  const start = (partNumber - 1) * partSize;
  const body = file.slice(start, Math.min(start + partSize, file.size));
  onChange({status: 'uploading', error: ''});
  try {
    const response = await fetch(url, {method: 'PUT', body});
    if (!response.ok) {
      throw new Error(`Floci returned status ${response.status}`);
    }
    const etag = response.headers.get('ETag');
    if (!etag) {
      throw new Error('Floci did not expose the part ETag');
    }
    onChange({status: 'succeeded', etag, error: '', url});
    return true;
  } catch (error) {
    onChange({
      status: 'failed',
      error: error instanceof Error ? error.message : 'Part upload failed',
      url,
    });
    return false;
  }
}
