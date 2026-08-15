import { useCallback, useEffect, useRef } from 'react';

import { getIngestionRun, ingestionRunEventsUrl } from '../uploadApi';

const STORED_RUN_IDS_KEY = 'ingestion-run-ids';
const TERMINAL_RUN_STATUSES = new Set(['SUCCEEDED', 'PARTIALLY_FAILED', 'FAILED']);

function isTerminalRun(status) {
  return TERMINAL_RUN_STATUSES.has(status);
}

function rememberRun(runId) {
  try {
    const knownRunIds = new Set(JSON.parse(localStorage.getItem(STORED_RUN_IDS_KEY) || '[]'));
    knownRunIds.add(runId);
    localStorage.setItem(STORED_RUN_IDS_KEY, JSON.stringify([...knownRunIds]));
  } catch {
    // Local storage is an enhancement; PostgreSQL remains the source of truth.
  }
}

function useIngestionRunMonitor({uploads, updateUpload}) {
  const eventSources = useRef(new Map());

  const closeRunEvents = useCallback((runId) => {
    const source = eventSources.current.get(runId);
    if (source) {
      source.close();
      eventSources.current.delete(runId);
    }
  }, []);

  const applySnapshot = useCallback((runId, snapshot) => {
    updateUpload(runId, (upload) => {
      if (!upload) {
        return {
          runId,
          uploadId: '',
          filename: snapshot.original_filename,
          file: null,
          size: snapshot.size_bytes,
          uploadedBytes: snapshot.uploaded_bytes,
          status: snapshot.status.toLowerCase(),
          error: '',
          partSize: 0,
          partUrlsEndpoint: '',
          parts: {},
          monitorProcessing: !isTerminalRun(snapshot.status),
          runSnapshot: snapshot,
        };
      }
      return {
        ...upload,
        status: snapshot.status.toLowerCase(),
        runSnapshot: snapshot,
        monitorProcessing: !isTerminalRun(snapshot.status),
      };
    });
    if (isTerminalRun(snapshot.status)) {
      closeRunEvents(runId);
    }
  }, [closeRunEvents, updateUpload]);

  const refreshRunSnapshot = useCallback(async (runId) => {
    try {
      applySnapshot(runId, await getIngestionRun(runId));
    } catch (error) {
      updateUpload(runId, (upload) => upload ? {...upload, error: error.message} : upload);
    }
  }, [applySnapshot, updateUpload]);

  const applyProgress = useCallback((runId, progress) => {
    updateUpload(runId, (upload) => {
      if (!upload?.runSnapshot) return upload;
      const snapshot = upload.runSnapshot;
      return {
        ...upload,
        status: progress.run_status.toLowerCase(),
        runSnapshot: {
          ...snapshot,
          status: progress.run_status,
          processing_progress_percent: progress.processing_progress_percent,
          tasks: snapshot.tasks.map((task) => (
            task.task_id === progress.task_id
              ? {
                ...task,
                status: progress.task_status,
                progress_percent: progress.task_progress_percent,
                processed_rows: progress.processed_rows,
              }
              : task
          )),
        },
      };
    });
    if (isTerminalRun(progress.run_status)) {
      closeRunEvents(runId);
      void refreshRunSnapshot(runId);
    }
  }, [closeRunEvents, refreshRunSnapshot, updateUpload]);

  useEffect(() => {
    try {
      const runIds = JSON.parse(localStorage.getItem(STORED_RUN_IDS_KEY) || '[]');
      runIds.forEach((runId) => void refreshRunSnapshot(runId));
    } catch {
      // Ignore unavailable or malformed local browser state.
    }
  }, [refreshRunSnapshot]);

  useEffect(() => {
    Object.values(uploads).forEach((upload) => {
      const status = upload.runSnapshot?.status || upload.status.toUpperCase();
      if (!upload.monitorProcessing || isTerminalRun(status) || eventSources.current.has(upload.runId)) {
        return;
      }
      if (typeof EventSource === 'undefined') {
        updateUpload(upload.runId, (current) => ({
          ...current,
          monitorProcessing: false,
        }));
        return;
      }
      const source = new EventSource(ingestionRunEventsUrl(upload.runId));
      eventSources.current.set(upload.runId, source);
      source.addEventListener('snapshot', (event) => {
        applySnapshot(upload.runId, JSON.parse(event.data));
      });
      source.addEventListener('progress', (event) => {
        applyProgress(upload.runId, JSON.parse(event.data));
      });
      // EventSource calls onopen both initially and after a reconnect. Refetch
      // so progress lost during a Redis/API restart is recovered from Postgres.
      source.onopen = () => void refreshRunSnapshot(upload.runId);
    });
  }, [applyProgress, applySnapshot, refreshRunSnapshot, updateUpload, uploads]);

  useEffect(() => () => {
    eventSources.current.forEach((source) => source.close());
    eventSources.current.clear();
  }, []);

  return {rememberRun, refreshRunSnapshot};
}

export default useIngestionRunMonitor;
