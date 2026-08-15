import { useCallback, useState } from 'react';

import { Box, Chip, Container, Paper, Stack, Typography } from '@mui/material';

import IngestionRunCard from './components/IngestionRunCard';
import UploadForm from './components/UploadForm';
import useIngestionRunMonitor from './components/useIngestionRunMonitor';
import useMultipartIngestion from './components/useMultipartIngestion';

function App() {
  const [uploads, setUploads] = useState({});
  const updateUpload = useCallback((runId, update) => {
    setUploads((current) => ({
      ...current,
      [runId]: update(current[runId]),
    }));
  }, []);
  const {rememberRun, refreshRunSnapshot} = useIngestionRunMonitor({
    uploads,
    updateUpload,
  });
  const multipartUpload = useMultipartIngestion({
    setUploads,
    updateUpload,
    rememberRun,
    refreshRunSnapshot,
  });
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

      <UploadForm
        selectedFile={multipartUpload.selectedFile}
        selectionError={multipartUpload.selectionError}
        isCreating={multipartUpload.isCreating}
        onSelectFile={multipartUpload.selectFile}
        onSubmit={multipartUpload.startUpload}
      />

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
            {uploadEntries.map((upload) => (
              <IngestionRunCard
                key={upload.runId}
                upload={upload}
                onAbort={multipartUpload.abortUpload}
                onRetryFinalization={multipartUpload.retryFinalization}
                onRetryPart={multipartUpload.retryPart}
              />
            ))}
          </Stack>
        )}
      </Box>
    </Container>
  );
}

export default App;
