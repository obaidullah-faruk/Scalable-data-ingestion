import {
  Alert,
  Box,
  Button,
  Paper,
  Stack,
  Typography,
} from '@mui/material';

import { MAX_UPLOAD_SIZE_BYTES, formatBytes } from '../multipartUpload';

function UploadForm({selectedFile, selectionError, isCreating, onSelectFile, onSubmit}) {
  return (
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

      <Stack component="form" onSubmit={onSubmit} spacing={1.5} sx={{justifyContent: 'center'}}>
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
              onChange={onSelectFile}
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
  );
}

export default UploadForm;
