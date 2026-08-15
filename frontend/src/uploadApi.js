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

export function requestMultipartCompletion(runId, uploadId, parts) {
  return apiRequest(`/api/v1/ingestion-runs/${runId}/completion-request`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({upload_id: uploadId, parts}),
  });
}

function xmlElementText(document, localName) {
  const element = Array.from(document.getElementsByTagName('*')).find(
    (candidate) => candidate.localName === localName,
  );
  return element?.textContent?.trim() || '';
}

export async function completeMultipartUpload(signedRequest) {
  const response = await fetch(signedRequest.url, {
    method: signedRequest.method,
    headers: signedRequest.headers,
    body: signedRequest.body,
  });
  const responseBody = await response.text();
  const document = new DOMParser().parseFromString(responseBody, 'application/xml');
  const parseFailed = document.querySelector('parsererror');
  const errorCode = xmlElementText(document, 'Code');
  if (!response.ok || parseFailed || errorCode) {
    const errorMessage = xmlElementText(document, 'Message');
    throw new Error(
      errorMessage || `S3 could not finalize the upload (status ${response.status})`,
    );
  }

  const objectEtag = xmlElementText(document, 'ETag');
  if (!objectEtag) {
    throw new Error('S3 completed the request without returning an object ETag');
  }
  return {
    objectEtag,
    objectVersionId:
      response.headers.get('x-amz-version-id') ||
      xmlElementText(document, 'VersionId') ||
      null,
  };
}

export function confirmMultipartUpload(runId, objectEtag, objectVersionId) {
  return apiRequest(`/api/v1/ingestion-runs/${runId}/confirm-upload`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      object_etag: objectEtag,
      object_version_id: objectVersionId,
    }),
  });
}
