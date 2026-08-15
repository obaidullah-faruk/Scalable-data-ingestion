import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

afterEach(() => {
  jest.restoreAllMocks();
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

  expect(await screen.findByText(/all parts are stored/i)).toBeInTheDocument();
  const createRequest = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(createRequest).toEqual({
    filename: 'data.csv',
    content_type: 'text/csv',
    byte_size: 6,
  });
  const directUploads = fetchMock.mock.calls.filter(([url]) =>
    url.startsWith('http://localhost:4566/'),
  );
  expect(directUploads).toHaveLength(2);
  directUploads.forEach(([, options]) => {
    expect(options.method).toBe('PUT');
    expect(options.body).toBeInstanceOf(Blob);
  });
  await waitFor(() => expect(screen.getByText('100%')).toBeInTheDocument());
});
