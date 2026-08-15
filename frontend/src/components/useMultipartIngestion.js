import { useState } from 'react';

import {
  abortIngestionRun,
  completeMultipartUpload,
  confirmMultipartUpload,
  createIngestionRun,
  requestMultipartCompletion,
  requestPartUrls,
} from '../uploadApi';
import {
  buildInitialParts,
  uploadPart,
  validateCsvFile,
  withConcurrency,
} from '../multipartUpload';

const UPLOAD_CONCURRENCY = 3;

function useMultipartIngestion({setUploads, updateUpload, rememberRun, refreshRunSnapshot}) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectionError, setSelectionError] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  const updatePart = (runId, partNumber, changes) => {
    updateUpload(runId, (upload) => {
      const parts = {
        ...upload.parts,
        [partNumber]: {...upload.parts[partNumber], ...changes},
      };
      const uploadedBytes = Object.values(parts)
        .filter((part) => part.status === 'succeeded')
        .reduce((total, part) => total + part.size, 0);
      return {...upload, parts, uploadedBytes};
    });
  };

  const finalizeUpload = async (
    runId,
    uploadId,
    manifest,
    completedObject = null,
  ) => {
    updateUpload(runId, (upload) => ({
      ...upload,
      status: 'finalizing',
      manifest,
      error: '',
    }));
    let object = completedObject;
    try {
      if (!object) {
        const signedRequest = await requestMultipartCompletion(
          runId,
          uploadId,
          manifest,
        );
        object = await completeMultipartUpload(signedRequest);
        updateUpload(runId, (upload) => ({
          ...upload,
          completedObject: object,
        }));
      }
      const confirmation = await confirmMultipartUpload(
        runId,
        object.objectEtag,
        object.objectVersionId,
      );
      const shouldMonitorProcessing = ['QUEUED', 'PROCESSING'].includes(
        confirmation.status,
      );
      updateUpload(runId, (upload) => ({
        ...upload,
        status: confirmation.status === 'QUEUED' ? 'queued' : 'confirmed',
        completedObject: object,
        error: '',
        monitorProcessing: shouldMonitorProcessing,
      }));
      rememberRun(runId);
      if (shouldMonitorProcessing) {
        void refreshRunSnapshot(runId);
      }
    } catch (error) {
      updateUpload(runId, (upload) => ({
        ...upload,
        status: 'finalization_failed',
        completedObject: object,
        error: error.message,
      }));
    }
  };

  const uploadParts = async (runId, file, run) => {
    const allPartNumbers = Array.from(
      {length: run.total_parts},
      (_, index) => index + 1,
    );
    let allSucceeded = true;
    const manifest = [];

    for (
      let offset = 0;
      offset < allPartNumbers.length;
      offset += run.part_url_batch_limit
    ) {
      const batchNumbers = allPartNumbers.slice(
        offset,
        offset + run.part_url_batch_limit,
      );
      let signedParts;
      try {
        const response = await requestPartUrls(
          run.part_urls_endpoint,
          batchNumbers,
        );
        signedParts = response.parts;
      } catch (error) {
        allSucceeded = false;
        batchNumbers.forEach((partNumber) => {
          updatePart(runId, partNumber, {
            status: 'failed',
            error: error.message,
          });
        });
        continue;
      }

      signedParts.forEach(({part_number: partNumber, url}) => {
        updatePart(runId, partNumber, {url, status: 'queued', error: ''});
      });
      const results = await withConcurrency(
        signedParts,
        UPLOAD_CONCURRENCY,
        ({part_number: partNumber, url}) => uploadPart({
          file,
          partNumber,
          partSize: run.part_size_bytes,
          url,
          onChange: (changes) => updatePart(runId, partNumber, changes),
        }),
      );
      if (results.some((etag) => !etag)) {
        allSucceeded = false;
      }
      signedParts.forEach(({part_number: partNumber}, index) => {
        if (results[index]) {
          manifest.push({part_number: partNumber, etag: results[index]});
        }
      });
    }

    if (allSucceeded) {
      manifest.sort((left, right) => left.part_number - right.part_number);
      void finalizeUpload(runId, run.upload_id, manifest);
    } else {
      updateUpload(runId, (upload) => ({
        ...upload,
        status: 'upload_failed',
        error: 'Some parts failed. Retry them individually or abort the upload.',
      }));
    }
  };

  const selectFile = (event) => {
    const file = event.target.files?.[0] ?? null;
    const error = validateCsvFile(file);
    setSelectedFile(error ? null : file);
    setSelectionError(error);
  };

  const startUpload = async (event) => {
    event.preventDefault();
    const error = validateCsvFile(selectedFile);
    if (error) {
      setSelectionError(error);
      return;
    }

    setIsCreating(true);
    setSelectionError('');
    try {
      const run = await createIngestionRun(selectedFile);
      setUploads((current) => ({
        ...current,
        [run.run_id]: {
          runId: run.run_id,
          uploadId: run.upload_id,
          filename: selectedFile.name,
          file: selectedFile,
          size: selectedFile.size,
          uploadedBytes: 0,
          status: 'uploading',
          error: '',
          partSize: run.part_size_bytes,
          partUrlsEndpoint: run.part_urls_endpoint,
          parts: buildInitialParts(
            selectedFile.size,
            run.part_size_bytes,
            run.total_parts,
          ),
        },
      }));
      void uploadParts(run.run_id, selectedFile, run);
    } catch (requestError) {
      setSelectionError(requestError.message);
    } finally {
      setIsCreating(false);
    }
  };

  const retryPart = async (upload, partNumber) => {
    updatePart(upload.runId, partNumber, {
      status: 'requesting_url',
      error: '',
    });
    try {
      const response = await requestPartUrls(upload.partUrlsEndpoint, [partNumber]);
      const signedPart = response.parts[0];
      const etag = await uploadPart({
        file: upload.file,
        partNumber,
        partSize: upload.partSize,
        url: signedPart.url,
        onChange: (changes) => updatePart(upload.runId, partNumber, changes),
      });
      if (etag) {
        const updatedParts = {
          ...upload.parts,
          [partNumber]: {
            ...upload.parts[partNumber],
            status: 'succeeded',
            etag,
            error: '',
          },
        };
        const finished = Object.values(updatedParts).every(
          (part) => part.status === 'succeeded',
        );
        if (finished) {
          const manifest = Object.values(updatedParts)
            .sort((left, right) => left.number - right.number)
            .map((part) => ({part_number: part.number, etag: part.etag}));
          void finalizeUpload(upload.runId, upload.uploadId, manifest);
        }
      }
    } catch (retryError) {
      updatePart(upload.runId, partNumber, {
        status: 'failed',
        error: retryError.message,
      });
    }
  };

  const abortUpload = async (upload) => {
    updateUpload(upload.runId, (current) => ({
      ...current,
      status: 'aborting',
      error: '',
    }));
    try {
      await abortIngestionRun(upload.runId);
      updateUpload(upload.runId, (current) => ({
        ...current,
        status: 'aborted',
      }));
    } catch (error) {
      updateUpload(upload.runId, (current) => ({
        ...current,
        status: 'upload_failed',
        error: error.message,
      }));
    }
  };

  const retryFinalization = (upload) => {
    void finalizeUpload(
      upload.runId,
      upload.uploadId,
      upload.manifest,
      upload.completedObject,
    );
  };

  return {
    abortUpload,
    isCreating,
    retryFinalization,
    retryPart,
    selectedFile,
    selectionError,
    selectFile,
    startUpload,
  };
}

export default useMultipartIngestion;
