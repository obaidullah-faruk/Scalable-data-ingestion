import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

afterEach(() => {
  jest.restoreAllMocks();
  localStorage.clear();
});

test('recovers a durable processing snapshot after a page refresh', async () => {
  localStorage.setItem('ingestion-run-ids', JSON.stringify(['durable-run']));
  const snapshot = {
    run_id: 'durable-run',
    status: 'PROCESSING',
    original_filename: 'durable.csv',
    size_bytes: 100,
    uploaded_bytes: 100,
    processing_progress_percent: 40,
    completed_task_count: 1,
    total_task_count: 3,
    error_details: null,
    upload_confirmed_at: null,
    processing_started_at: null,
    completed_at: null,
    tasks: [
      {
        task_id: 'task-a',
        task_type: 'VALIDATE_PROFILE',
        status: 'SUCCEEDED',
        progress_percent: 100,
        processed_rows: 20,
        retry_count: 0,
        celery_task_id: 'celery-a',
        error_details: null,
        started_at: null,
        completed_at: null,
      },
      {
        task_id: 'task-b',
        task_type: 'LOAD_OBSERVATIONS',
        status: 'PROCESSING',
        progress_percent: 20,
        processed_rows: 4,
        retry_count: 0,
        celery_task_id: 'celery-b',
        error_details: null,
        started_at: null,
        completed_at: null,
      },
    ],
    validation_profile: {
      row_count: 20,
      missing_data_value_count: 1,
      invalid_period_count: 0,
      invalid_data_value_count: 0,
      invalid_status_count: 0,
      invalid_units_count: 0,
      findings: {},
    },
    series_summaries: [],
  };
  const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue({
    ok: true,
    json: async () => snapshot,
  });
  const originalEventSource = global.EventSource;
  global.EventSource = class {
    addEventListener() {}
    close() {}
  };

  render(<App />);

  expect(await screen.findByText('durable.csv')).toBeInTheDocument();
  expect(screen.getByText('Processing progress')).toBeInTheDocument();
  expect(screen.getByText('40%')).toBeInTheDocument();
  expect(screen.getByText(/20 rows .* 1 missing values/i)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    'http://localhost:8000/api/v1/ingestion-runs/durable-run',
    undefined,
  );
  global.EventSource = originalEventSource;
});

test('validates that the selected file is a CSV', () => {
  render(<App />);
  const input = screen.getByLabelText(/select \.csv file/i);

  fireEvent.change(input, {
    target: {files: [new File(['hello'], 'notes.txt', {type: 'text/plain'})]},
  });

  expect(screen.getByRole('alert')).toHaveTextContent(/only files ending in \.csv/i);
  expect(screen.getByRole('button', {name: /start upload/i})).toBeDisabled();
});

test('uploads CSV bytes only through presigned Floci URLs', async () => {
  const fetchMock = jest.spyOn(global, 'fetch').mockImplementation((url) => {
    if (url === 'http://localhost:8000/api/v1/ingestion-runs') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          run_id: 'run-1',
          upload_id: 'upload-1',
          part_size_bytes: 3,
          total_parts: 2,
          part_url_batch_limit: 100,
          part_urls_endpoint: '/api/v1/ingestion-runs/run-1/part-urls',
        }),
      });
    }
    if (url === 'http://localhost:8000/api/v1/ingestion-runs/run-1/part-urls') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          parts: [
            {part_number: 1, url: 'http://localhost:4566/part-1'},
            {part_number: 2, url: 'http://localhost:4566/part-2'},
          ],
        }),
      });
    }
    if (url === 'http://localhost:8000/api/v1/ingestion-runs/run-1/completion-request') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          method: 'POST',
          url: 'http://localhost:4566/finalize',
          headers: {'Content-Type': 'application/xml'},
          body: '<CompleteMultipartUpload />',
        }),
      });
    }
    if (url === 'http://localhost:4566/finalize') {
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => (
          '<CompleteMultipartUploadResult>' +
          '<ETag>&quot;final-etag&quot;</ETag>' +
          '</CompleteMultipartUploadResult>'
        ),
        headers: {get: () => null},
      });
    }
    if (url === 'http://localhost:8000/api/v1/ingestion-runs/run-1/confirm-upload') {
      return Promise.resolve({
        ok: true,
        json: async () => ({status: 'AWAITING_CONFIRMATION'}),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: {get: (name) => name.toLowerCase() === 'etag' ? 'etag-value' : null},
    });
  });
  render(<App />);
  const input = screen.getByLabelText(/select \.csv file/i);
  const file = new File(['abcdef'], 'data.csv', {type: 'text/csv'});

  fireEvent.change(input, {target: {files: [file]}});
  fireEvent.click(screen.getByRole('button', {name: /start upload/i}));

  expect(await screen.findByText(/upload finalized and verified/i)).toBeInTheDocument();
  const createRequest = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(createRequest).toEqual({
    filename: 'data.csv',
    content_type: 'text/csv',
    byte_size: 6,
  });
  const directUploads = fetchMock.mock.calls.filter(([, options]) =>
    options.method === 'PUT',
  );
  expect(directUploads).toHaveLength(2);
  directUploads.forEach(([, options]) => {
    expect(options.method).toBe('PUT');
    expect(options.body).toBeInstanceOf(Blob);
  });
  const completionRequest = JSON.parse(fetchMock.mock.calls.find(
    ([url]) => url.endsWith('/completion-request'),
  )[1].body);
  expect(completionRequest).toEqual({
    upload_id: 'upload-1',
    parts: [
      {part_number: 1, etag: 'etag-value'},
      {part_number: 2, etag: 'etag-value'},
    ],
  });
  const directCompletion = fetchMock.mock.calls.find(
    ([url]) => url === 'http://localhost:4566/finalize',
  );
  expect(directCompletion[1]).toMatchObject({
    method: 'POST',
    body: '<CompleteMultipartUpload />',
  });
  const confirmationRequest = JSON.parse(fetchMock.mock.calls.find(
    ([url]) => url.endsWith('/confirm-upload'),
  )[1].body);
  expect(confirmationRequest).toEqual({
    object_etag: '"final-etag"',
    object_version_id: null,
  });
  await waitFor(() => expect(screen.getByText('100%')).toBeInTheDocument());
});
