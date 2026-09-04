- The queue board's **Working Now** rows now have a one-click **kill** button
  (shown on hover): it releases the worker from queue staffing and terminates
  its process, via the new `POST /api/wt/workers/kill` endpoint. Releasing
  before the SIGTERM closes the race where the worker claims a fresh ticket on
  its way down.
