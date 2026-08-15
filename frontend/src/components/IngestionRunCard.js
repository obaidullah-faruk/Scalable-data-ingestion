import {
  Alert,
  Box,
  Button,
  Chip,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';

import { formatBytes } from '../multipartUpload';
import ProcessingStatus from './ProcessingStatus';

function IngestionRunCard({upload, onAbort, onRetryFinalization, onRetryPart}) {
  const progress = Math.round((upload.uploadedBytes / upload.size) * 100);
  const runSnapshot = upload.runSnapshot;
  const runStatus = runSnapshot?.status || upload.status.toUpperCase();
  const failedParts = Object.values(upload.parts).filter(
    (part) => part.status === 'failed',
  );
  const completedParts = Object.values(upload.parts).filter(
    (part) => part.status === 'succeeded',
  ).length;
  const statusColor = ['QUEUED', 'PROCESSING', 'SUCCEEDED'].includes(runStatus)
    ? 'success'
    : ['PARTIALLY_FAILED', 'FAILED'].includes(runStatus) || ['upload_failed', 'finalization_failed', 'aborted'].includes(upload.status)
      ? 'error'
      : 'default';

  return (
    <Paper
      component="article"
      variant="outlined"
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
          label={runStatus.replaceAll('_', ' ')}
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

      {['confirmed', 'queued'].includes(upload.status) && !runSnapshot && (
        <Alert severity="success" sx={{mt: 2.5}}>
          Upload finalized and verified in object storage.
        </Alert>
      )}
      {runSnapshot && <ProcessingStatus runSnapshot={runSnapshot} />}
      {upload.error && <Alert severity="error" sx={{mt: 2.5}}>{upload.error}</Alert>}

      {upload.status === 'finalization_failed' && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => onRetryFinalization(upload)}
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
                  onClick={() => onRetryPart(upload, part.number)}
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
          onClick={() => onAbort(upload)}
          sx={{mt: 2.5}}
        >
          Abort upload
        </Button>
      )}
    </Paper>
  );
}

export default IngestionRunCard;
