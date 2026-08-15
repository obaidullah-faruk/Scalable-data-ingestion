import { useState } from 'react';

import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import {
  abortIngestionRun,
  completeMultipartUpload,
  confirmMultipartUpload,
  createIngestionRun,
  requestMultipartCompletion,
  requestPartUrls,
} from './uploadApi';
import {
  MAX_UPLOAD_SIZE_BYTES,
  buildInitialParts,
  formatBytes,
  uploadPart,
  validateCsvFile,
  withConcurrency,
} from './multipartUpload';

const UPLOAD_CONCURRENCY = 3;

function App() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectionError, setSelectionError] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [uploads, setUploads] = useState({});

  const updateUpload = (runId, update) => {
    setUploads((current) => ({
      ...current,
      [runId]: update(current[runId]),
    }));
  };

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
      updateUpload(runId, (upload) => ({
        ...upload,
        status: confirmation.status === 'QUEUED' ? 'queued' : 'confirmed',
        completedObject: object,
        error: '',
      }));
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
        ({part_number: partNumber, url}) =>
          uploadPart({
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

  const uploadEntries = Object.values(uploads).reverse();

  return (
    <Container component="main" maxWidth="lg" sx={{py: {xs: 4, md: 8}}}>
      <Box
        component="section"
        sx={{
          display: 'grid',
          gridTemplateColumns: {xs: '1fr', md: 'minmax(0, 1fr) auto'},
          alignItems: 'end',
          gap: 4,
          pb: {xs: 5, md: 7},
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Box>
          <Typography variant="overline" color="primary.dark">
            Data ingestion pipeline
          </Typography>
          <Typography
            variant="h1"
            sx={{
              maxWidth: 780,
              mt: 1,
              mb: 2.5,
              fontFamily: 'Georgia, Times New Roman, serif',
              fontSize: {xs: '2.8rem', sm: '4rem', md: '5.4rem'},
              fontWeight: 500,
              letterSpacing: '-0.055em',
              lineHeight: 0.98,
            }}
          >
            Send a CSV straight to object storage.
          </Typography>
          <Typography color="text.secondary" sx={{maxWidth: 680, lineHeight: 1.75}}>
            FastAPI creates the upload session. Your browser sends every byte
            directly to S3 in retryable parts.
          </Typography>
        </Box>
        <Chip
          aria-label="Upload route"
          variant="outlined"
          label="Browser  →  S3"
          sx={{width: 'fit-content', fontWeight: 700}}
        />
      </Box>

      <Paper
        component="section"
        aria-labelledby="upload-heading"
        elevation={0}
        sx={{
          display: 'grid',
          gridTemplateColumns: {xs: '1fr', md: '0.75fr 1.25fr'},
          gap: {xs: 3, md: 6},
          my: {xs: 4, md: 6},
          p: {xs: 3, md: 4},
          border: 1,
          borderColor: 'divider',
          borderRadius: 3,
          boxShadow: '0 20px 60px rgba(42, 47, 38, 0.07)',
        }}
      >
        <Box>
          <Typography variant="overline" color="primary.dark">
            New ingestion run
          </Typography>
          <Typography id="upload-heading" variant="h5" sx={{mt: 0.5, mb: 1}}>
            Choose a CSV file
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Maximum size: {formatBytes(MAX_UPLOAD_SIZE_BYTES)}.
          </Typography>
        </Box>

        <Stack component="form" onSubmit={startUpload} spacing={1.5} sx={{justifyContent: 'center'}}>
          <Stack direction={{xs: 'column', sm: 'row'}} spacing={1.5}>
            <Button
              component="label"
              variant="outlined"
              sx={{
                minHeight: 50,
                minWidth: 0,
                flex: 1,
                justifyContent: 'flex-start',
                overflow: 'hidden',
                textTransform: 'none',
              }}
            >
              <Box component="span" sx={{overflow: 'hidden', textOverflow: 'ellipsis'}}>
                {selectedFile ? selectedFile.name : 'Select .csv file'}
              </Box>
              <Box
                component="input"
                type="file"
                accept=".csv,text/csv"
                aria-label="Select .csv file"
                onChange={selectFile}
                sx={{position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)'}}
              />
            </Button>
            <Button
              type="submit"
              variant="contained"
              disabled={!selectedFile || isCreating}
              sx={{minHeight: 50, px: 3, whiteSpace: 'nowrap'}}
            >
              {isCreating ? 'Creating run…' : 'Start upload'}
            </Button>
          </Stack>
          {selectedFile && (
            <Typography variant="caption" color="text.secondary">
              {formatBytes(selectedFile.size)} selected
            </Typography>
          )}
          {selectionError && <Alert severity="error">{selectionError}</Alert>}
        </Stack>
      </Paper>

      <Box component="section" aria-live="polite">
        <Stack
          direction="row"
          sx={{justifyContent: 'space-between', alignItems: 'end', mb: 2.5}}
        >
          <Box>
            <Typography variant="overline" color="primary.dark">
              Upload activity
            </Typography>
            <Typography variant="h5">Ingestion runs</Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {uploadEntries.length} local {uploadEntries.length === 1 ? 'run' : 'runs'}
          </Typography>
        </Stack>

        {uploadEntries.length === 0 ? (
          <Paper
            variant="outlined"
            sx={{p: 6, borderStyle: 'dashed', textAlign: 'center', color: 'text.secondary'}}
          >
            No uploads started in this browser session.
          </Paper>
        ) : (
          <Stack spacing={2}>
            {uploadEntries.map((upload) => {
              const progress = Math.round((upload.uploadedBytes / upload.size) * 100);
              const failedParts = Object.values(upload.parts).filter(
                (part) => part.status === 'failed',
              );
              const completedParts = Object.values(upload.parts).filter(
                (part) => part.status === 'succeeded',
              ).length;
              const statusColor = ['confirmed', 'queued'].includes(upload.status)
                ? 'success'
                : ['upload_failed', 'finalization_failed', 'aborted'].includes(upload.status)
                  ? 'error'
                  : 'default';

              return (
                <Paper
                  component="article"
                  variant="outlined"
                  key={upload.runId}
                  sx={{p: {xs: 2.5, md: 3.5}, borderRadius: 2.5}}
                >
                  <Stack
                    direction={{xs: 'column', sm: 'row'}}
                    spacing={2}
                    sx={{
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      mb: 3.5,
                    }}
                  >
                    <Box sx={{minWidth: 0}}>
                      <Typography variant="h6">{upload.filename}</Typography>
                      <Typography
                        component="code"
                        variant="caption"
                        color="text.secondary"
                        sx={{overflowWrap: 'anywhere'}}
                      >
                        {upload.runId}
                      </Typography>
                    </Box>
                    <Chip
                      size="small"
                      color={statusColor}
                      label={upload.status.replaceAll('_', ' ')}
                      sx={{fontWeight: 800, textTransform: 'uppercase'}}
                    />
                  </Stack>

                  <Stack direction="row" sx={{justifyContent: 'space-between', mb: 1}}>
                    <Typography variant="body2" color="text.secondary">
                      {formatBytes(upload.uploadedBytes)} / {formatBytes(upload.size)}
                    </Typography>
                    <Typography variant="body2" fontWeight={800}>{progress}%</Typography>
                  </Stack>
                  <LinearProgress
                    variant="determinate"
                    value={progress}
                    aria-label={`${progress}% uploaded`}
                    sx={{height: 9, borderRadius: 99}}
                  />
                  <Typography variant="caption" color="text.secondary" display="block" mt={1}>
                    {completedParts} of {Object.keys(upload.parts).length} parts uploaded
                  </Typography>

                  {['confirmed', 'queued'].includes(upload.status) && (
                    <Alert severity="success" sx={{mt: 2.5}}>
                      Upload finalized and verified in object storage.
                    </Alert>
                  )}
                  {upload.error && <Alert severity="error" sx={{mt: 2.5}}>{upload.error}</Alert>}

                  {upload.status === 'finalization_failed' && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => retryFinalization(upload)}
                      sx={{mt: 2.5}}
                    >
                      Retry finalization
                    </Button>
                  )}

                  {failedParts.length > 0 && (
                    <Stack spacing={1} mt={3}>
                      <Typography variant="subtitle2">Failed parts</Typography>
                      {failedParts.map((part) => (
                        <Paper variant="outlined" key={part.number} sx={{p: 1.5}}>
                          <Stack
                            direction={{xs: 'column', sm: 'row'}}
                            spacing={1.5}
                            sx={{
                              justifyContent: 'space-between',
                              alignItems: {xs: 'stretch', sm: 'center'},
                            }}
                          >
                            <Typography variant="body2" color="error.main">
                              Part {part.number}: {part.error}
                            </Typography>
                            <Button
                              size="small"
                              variant="outlined"
                              color="error"
                              onClick={() => retryPart(upload, part.number)}
                            >
                              Retry part
                            </Button>
                          </Stack>
                        </Paper>
                      ))}
                    </Stack>
                  )}

                  {!['aborted', 'aborting', 'confirmed', 'finalizing', 'queued'].includes(upload.status) &&
                    !upload.completedObject && (
                    <Button
                      size="small"
                      variant="text"
                      color="error"
                      onClick={() => abortUpload(upload)}
                      sx={{mt: 2.5}}
                    >
                      Abort upload
                    </Button>
                  )}
                </Paper>
              );
            })}
          </Stack>
        )}
      </Box>
    </Container>
  );
}

export default App;
