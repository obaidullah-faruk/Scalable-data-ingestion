import {
  buildInitialParts,
  validateCsvFile,
  withConcurrency,
} from './multipartUpload';

test('builds the correct final partial chunk', () => {
  const parts = buildInitialParts(20, 8, 3);

  expect(parts[1].size).toBe(8);
  expect(parts[2].size).toBe(8);
  expect(parts[3].size).toBe(4);
});

test('rejects empty CSV files', () => {
  const file = new File([], 'empty.csv', {type: 'text/csv'});

  expect(validateCsvFile(file)).toMatch(/empty/i);
});

test('limits simultaneous operations', async () => {
  let active = 0;
  let maximumActive = 0;

  const results = await withConcurrency([1, 2, 3, 4, 5, 6], 3, async (item) => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await new Promise((resolve) => setTimeout(resolve, 2));
    active -= 1;
    return item * 2;
  });

  expect(maximumActive).toBe(3);
  expect(results).toEqual([2, 4, 6, 8, 10, 12]);
});
