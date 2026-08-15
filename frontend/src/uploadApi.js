const API_BASE_URL = (
  process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000'
).replace(/\/$/, '');

async function apiRequest(path, options) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : message;
    } catch {
      // Keep the status message for non-JSON responses.
    }
    throw new Error(message);
  }
  return response.json();
}

export function createIngestionRun(file) {
  const supportedContentTypes = new Set([
    'application/csv',
    'application/vnd.ms-excel',
    'text/csv',
  ]);
  const contentType = supportedContentTypes.has(file.type)
    ? file.type
    : 'text/csv';
  return apiRequest('/api/v1/ingestion-runs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      filename: file.name,
      content_type: contentType,
      byte_size: file.size,
    }),
  });
}

export function requestPartUrls(endpoint, partNumbers) {
  return apiRequest(endpoint, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({part_numbers: partNumbers}),
  });
}

export function abortIngestionRun(runId) {
  return apiRequest(`/api/v1/ingestion-runs/${runId}/abort`, {method: 'POST'});
}
