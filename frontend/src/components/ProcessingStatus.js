import {
  Alert,
  Box,
  Chip,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';

function ProcessingStatus({runSnapshot}) {
  const runStatus = runSnapshot.status;
  const processingProgress = runSnapshot.processing_progress_percent;

  return (
    <Stack spacing={2} mt={3}>
      <Stack direction="row" sx={{justifyContent: 'space-between', alignItems: 'center'}}>
        <Box>
          <Typography variant="subtitle2">Processing progress</Typography>
          <Typography variant="caption" color="text.secondary">
            {runSnapshot.completed_task_count} of {runSnapshot.total_task_count} tasks complete
          </Typography>
        </Box>
        <Typography variant="body2" fontWeight={800}>{processingProgress}%</Typography>
      </Stack>
      <LinearProgress
        variant="determinate"
        value={processingProgress}
        aria-label={`${processingProgress}% processed`}
        color={runStatus === 'FAILED' ? 'error' : 'primary'}
        sx={{height: 9, borderRadius: 99}}
      />
      <Stack spacing={1}>
        {runSnapshot.tasks.map((task) => (
          <Box
            key={task.task_id}
            sx={{display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 1, alignItems: 'center'}}
          >
            <Box>
              <Stack direction="row" spacing={1} sx={{alignItems: 'center'}}>
                <Typography variant="body2" fontWeight={700}>
                  {task.task_type.replaceAll('_', ' ')}
                </Typography>
                <Chip size="small" variant="outlined" label={task.status} />
              </Stack>
              <Typography variant="caption" color="text.secondary">
                {task.processed_rows.toLocaleString()} rows processed
              </Typography>
            </Box>
            <Typography variant="body2" fontWeight={800}>{task.progress_percent}%</Typography>
          </Box>
        ))}
      </Stack>

      {runSnapshot.validation_profile && (
        <Paper variant="outlined" sx={{p: 2, bgcolor: 'background.default'}}>
          <Typography variant="subtitle2" gutterBottom>Validation profile</Typography>
          <Typography variant="body2" color="text.secondary">
            {runSnapshot.validation_profile.row_count.toLocaleString()} rows · {' '}
            {runSnapshot.validation_profile.missing_data_value_count.toLocaleString()} missing values · {' '}
            {runSnapshot.validation_profile.invalid_period_count.toLocaleString()} invalid periods · {' '}
            {runSnapshot.validation_profile.invalid_data_value_count.toLocaleString()} invalid values
          </Typography>
        </Paper>
      )}

      {runSnapshot.series_summaries.length > 0 && (
        <Paper variant="outlined" sx={{p: 2, bgcolor: 'background.default'}}>
          <Typography variant="subtitle2">
            Series summaries ({runSnapshot.series_summaries.length.toLocaleString()})
          </Typography>
          <Stack spacing={0.5} mt={1}>
            {runSnapshot.series_summaries.slice(0, 5).map((summary) => (
              <Typography key={summary.series_reference} variant="body2" color="text.secondary">
                {summary.series_reference}: {summary.valid_observation_count.toLocaleString()} observations, latest {' '}
                {summary.latest_value ?? '—'} {summary.units || ''}
              </Typography>
            ))}
          </Stack>
        </Paper>
      )}

      {runStatus === 'SUCCEEDED' && (
        <Alert severity="success">Processing completed successfully.</Alert>
      )}
      {['PARTIALLY_FAILED', 'FAILED'].includes(runStatus) && (
        <Alert severity="error">
          Processing finished with failures. Review the task status and validation results above.
        </Alert>
      )}
    </Stack>
  );
}

export default ProcessingStatus;
